import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review.cache import AgentCache
from agent_review.orchestrator import run_review
from agent_review.prompts import load_agent_prompts


class FakeReviewer:
    """Records every call it receives and returns a scripted response
    keyed by which agent's system prompt was used (matched by the
    agent's `description` line, since system_prompt is the full prompt
    text and we don't want this fake coupled to prompts.py internals).
    """

    def __init__(self, responses_by_agent_hint: dict[str, str]):
        self.responses = responses_by_agent_hint
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.calls.append((system_prompt, user_message))
        for hint, response in self.responses.items():
            if hint in system_prompt:
                return response
        return "No high-impact issues found."


class ReviewerThatFailsOneFile:
    """Raises for any call whose user message contains `fail_marker`,
    otherwise returns a clean result -- used to simulate one file's model
    call failing (a transient Foundry timeout, a rate limit) without every
    other file's call also failing.
    """

    def __init__(self, fail_marker: str):
        self.fail_marker = fail_marker
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.calls.append((system_prompt, user_message))
        if self.fail_marker in user_message:
            raise RuntimeError("simulated Foundry failure")
        return "No high-impact issues found."


def make_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "handlers.py").write_text("def noop():\n    pass\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def test_end_to_end_routes_and_finds_sql_injection(tmp_path: Path):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(
        "def get_user(request):\n"
        '    user_id = request.args.get("id")\n'
        '    query = f"SELECT * FROM users WHERE id = {user_id}"\n'
        "    return db.execute(query).fetchone()\n"
    )
    prompts = load_agent_prompts(repo)
    cache = AgentCache(repo)
    reviewer = FakeReviewer(
        {
            "security vulnerabilities": (
                "[CRITICAL] handlers.py:3 — SQL injection\n"
                "Impact: attacker-controlled input reaches the query\n"
                "Fix: parameterize the query\n"
            ),
        }
    )
    run = run_review(repo, None, prompts, cache, reviewer)
    assert len(run.all_findings) == 1
    assert run.all_findings[0].severity == "CRITICAL"
    assert run.files[0].path == "handlers.py"
    assert "security-review" in run.files[0].agents
    assert reviewer.calls  # the model was actually invoked


def test_second_run_with_no_changes_is_a_pure_cache_hit(tmp_path: Path):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(
        "def get_user(request):\n"
        '    user_id = request.args.get("id")\n'
        '    query = f"SELECT * FROM users WHERE id = {user_id}"\n'
        "    return db.execute(query).fetchone()\n"
    )
    prompts = load_agent_prompts(repo)
    reviewer = FakeReviewer(
        {
            "security vulnerabilities": (
                "[CRITICAL] handlers.py:3 — SQL injection\nImpact: x\nFix: y\n"
            ),
        }
    )

    cache1 = AgentCache(repo)
    run_review(repo, None, prompts, cache1, reviewer)
    calls_after_first_run = len(reviewer.calls)
    assert calls_after_first_run > 0

    # Fresh AgentCache instance simulates a brand-new CLI invocation
    # reading the manifest .agent-cache/ left behind by the first run.
    cache2 = AgentCache(repo)
    run2 = run_review(repo, None, prompts, cache2, reviewer)
    assert len(reviewer.calls) == calls_after_first_run  # no new model calls
    assert run2.cache_hits == 1
    assert run2.cache_misses == 0
    assert len(run2.all_findings) == 1
    assert run2.all_findings[0].severity == "CRITICAL"


def test_unrelated_file_change_does_not_invalidate_other_files_cache(tmp_path: Path):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(
        "def get_user(request):\n"
        '    query = f"SELECT * FROM users WHERE id = {request.args.get(chr(105)+chr(100))}"\n'
        "    return db.execute(query).fetchone()\n"
    )
    (repo / "other.py").write_text(
        "def get_config():\n    conn = pool.getConnection()\n    return conn\n"
    )
    prompts = load_agent_prompts(repo)
    reviewer = FakeReviewer({})  # no findings from anyone

    cache1 = AgentCache(repo)
    run_review(repo, None, prompts, cache1, reviewer)
    first_call_count = len(reviewer.calls)

    # Now touch only handlers.py again with a real content change.
    (repo / "handlers.py").write_text(
        "def get_user(request):\n"
        '    query = f"SELECT * FROM users WHERE id = {request.args.get(chr(105))}"\n'
        "    return db.execute(query).fetchone()\n"
    )
    cache2 = AgentCache(repo)
    run_review(repo, None, prompts, cache2, reviewer)
    # Only handlers.py's agents should have been re-invoked; other.py's
    # cached result should have been reused, not recomputed.
    second_run_new_calls = len(reviewer.calls) - first_call_count
    assert second_run_new_calls > 0
    assert second_run_new_calls < first_call_count  # strictly fewer than a full re-run


def test_no_findings_anywhere_yields_empty_list(tmp_path: Path):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text("def noop():\n    return 1\n")
    prompts = load_agent_prompts(repo)
    cache = AgentCache(repo)
    reviewer = FakeReviewer({})
    run = run_review(repo, None, prompts, cache, reviewer)
    assert run.all_findings == []


def test_one_files_failed_model_call_does_not_discard_other_files_results(
    tmp_path: Path,
):
    # Regression test: a single file's failed model call used to propagate
    # out of run_review() entirely, discarding every other file's
    # already-computed findings and cache entries in the same batch.
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(
        "def get_user(request):\n"
        '    query = f"SELECT * FROM users WHERE id = {request.args.get(chr(105))}"\n'
        "    return db.execute(query).fetchone()\n"
    )
    (repo / "other.py").write_text(
        "def get_config():\n    conn = pool.getConnection()\n    return conn\n"
    )
    prompts = load_agent_prompts(repo)
    cache = AgentCache(repo)
    reviewer = ReviewerThatFailsOneFile(fail_marker="File: handlers.py")

    # Must not raise, even though handlers.py's model call fails.
    run = run_review(repo, None, prompts, cache, reviewer)

    failed_paths = {f.path for f in run.failed_files}
    assert failed_paths == {"handlers.py"}
    other_result = next(f for f in run.files if f.path == "other.py")
    assert other_result.error is None

    # other.py's successful result must have been cached despite the
    # run overall including a failure -- a fresh run (with a reviewer that
    # would fail on any call at all) should hit cache for it.
    cache2 = AgentCache(repo)
    unreachable_reviewer = ReviewerThatFailsOneFile(fail_marker="")
    run2 = run_review(repo, None, prompts, cache2, unreachable_reviewer)
    other_result_2 = next(f for f in run2.files if f.path == "other.py")
    assert other_result_2.from_cache is True


def test_missing_prompt_is_not_cached_as_a_clean_review(tmp_path: Path):
    # Regression test: an agent routed to a file but with no loaded prompt
    # (e.g. a customized .agent-rules/agents/ file was renamed or removed)
    # used to still get written into the cache as if it had cleanly
    # reviewed the file, permanently hiding it from every future run.
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(
        "def get_user(request):\n"
        '    query = f"SELECT * FROM users WHERE id = {request.args.get(chr(105))}"\n'
        "    return db.execute(query).fetchone()\n"
    )
    prompts_missing_security = load_agent_prompts(repo)
    del prompts_missing_security["security-review"]
    cache = AgentCache(repo)
    reviewer = FakeReviewer(
        {
            "security vulnerabilities": (
                "[CRITICAL] handlers.py:3 — SQL injection\nImpact: x\nFix: y\n"
            ),
        }
    )

    run = run_review(repo, None, prompts_missing_security, cache, reviewer)
    file_result = run.files[0]
    assert "security-review" in file_result.missing_agents
    assert "security-review" not in file_result.agents
    assert run.all_findings == []  # never invoked, so it never found anything

    # Restore the prompt and re-run against the same on-disk cache: the
    # previously-missing agent must actually get invoked now, not be
    # treated as already "clean" from the first (incomplete) run.
    prompts_restored = load_agent_prompts(repo)
    cache2 = AgentCache(repo)
    run2 = run_review(repo, None, prompts_restored, cache2, reviewer)
    assert run2.cache_misses == 1
    assert len(run2.all_findings) == 1
    assert run2.all_findings[0].severity == "CRITICAL"


def test_ignores_untracked_files_under_agent_cache_and_agent_rules(tmp_path: Path):
    # A regression here (typo, wrong constant) would let the tool spend
    # model calls reviewing its own cache manifest / rule files -- both
    # are untracked the moment they're first written (before a target
    # repo's own .gitignore has a chance to exclude them), so they must be
    # filtered by path prefix, not by gitignore status.
    repo = make_repo(tmp_path)
    (repo / ".agent-cache").mkdir()
    (repo / ".agent-cache" / "manifest.json").write_text("{}")
    (repo / ".agent-rules").mkdir()
    (repo / ".agent-rules" / "CLAUDE.md").write_text("# rules\n")
    prompts = load_agent_prompts(repo)
    cache = AgentCache(repo)
    reviewer = FakeReviewer({})
    run = run_review(repo, None, prompts, cache, reviewer)
    reviewed_paths = {f.path for f in run.files}
    assert not any(p.startswith((".agent-cache/", ".agent-rules/")) for p in reviewed_paths)
    assert reviewer.calls == []
