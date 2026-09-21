import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review import fingerprint
from agent_review.cache import AgentCache
from agent_review.orchestrator import (
    MAX_DIFF_LINES,
    build_review_user_message,
    run_review,
)
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


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "handlers.py").write_text("def noop():\n    pass\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def test_end_to_end_routes_and_finds_sql_injection(tmp_path):
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


def test_second_run_with_no_changes_is_a_pure_cache_hit(tmp_path):
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


def test_cache_hit_preserves_agent_and_rule_id_from_the_original_live_call(tmp_path):
    # The bug this guards: cache schema version 1 merged every agent's
    # text into one string, so a cache-hit replay had to fall back to a
    # placeholder "cached" agent name -- which, once rule_id existed,
    # gave the SAME finding a DIFFERENT rule_id (and therefore a
    # DIFFERENT fingerprint) on a cache hit than it got on the live call
    # that originally cached it. See cache.py's module docstring.
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
    run1 = run_review(repo, None, prompts, cache1, reviewer)
    live_finding = run1.all_findings[0]
    assert live_finding.agent == "security-review"
    assert live_finding.rule_id == "SEC-INJECTION-001"

    cache2 = AgentCache(repo)
    run2 = run_review(repo, None, prompts, cache2, reviewer)
    assert run2.cache_hits == 1
    cached_finding = run2.all_findings[0]
    assert cached_finding.agent == live_finding.agent
    assert cached_finding.rule_id == live_finding.rule_id
    assert fingerprint.compute(cached_finding) == fingerprint.compute(live_finding)


def test_unrelated_file_change_does_not_invalidate_other_files_cache(tmp_path):
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


def test_no_findings_anywhere_yields_empty_list(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text("def noop():\n    return 1\n")
    prompts = load_agent_prompts(repo)
    cache = AgentCache(repo)
    reviewer = FakeReviewer({})
    run = run_review(repo, None, prompts, cache, reviewer)
    assert run.all_findings == []


def test_one_files_failed_model_call_does_not_discard_other_files_results(tmp_path):
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


def test_missing_prompt_is_not_cached_as_a_clean_review(tmp_path):
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


def test_malformed_response_is_not_cached_as_a_clean_review(tmp_path):
    # Regression test for the silent-failure gap this exists to close: a
    # specialist call that returns text off the output contract (no
    # [SEVERITY] header, not the exact NO_FINDINGS_TEXT sentinel) parses
    # to zero findings, indistinguishable from a genuine clean review --
    # confirm it's instead surfaced via malformed_agents and, like a
    # missing prompt, never cached as if the file had actually been
    # reviewed.
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(
        "def get_user(request):\n"
        '    query = f"SELECT * FROM users WHERE id = {request.args.get(chr(105))}"\n'
        "    return db.execute(query).fetchone()\n"
    )
    prompts = load_agent_prompts(repo)
    cache = AgentCache(repo)
    reviewer = FakeReviewer(
        {
            "security vulnerabilities": "I looked at this and it seems fine overall.",
        }
    )

    run = run_review(repo, None, prompts, cache, reviewer)
    file_result = run.files[0]
    assert "security-review" in file_result.malformed_agents
    assert "security-review" not in file_result.agents
    assert run.all_findings == []

    # Re-run against the same on-disk cache with a reviewer that now
    # responds on-contract: the previously-malformed agent must actually
    # get re-invoked, not be treated as already "clean" from the first
    # (malformed) run.
    cache2 = AgentCache(repo)
    reviewer2 = FakeReviewer(
        {
            "security vulnerabilities": (
                "[CRITICAL] handlers.py:3 — SQL injection\nImpact: x\nFix: y\n"
            ),
        }
    )
    run2 = run_review(repo, None, prompts, cache2, reviewer2)
    assert run2.cache_misses == 1
    assert len(run2.all_findings) == 1
    assert run2.all_findings[0].severity == "CRITICAL"
    assert run2.files[0].malformed_agents == []


def test_excludes_lockfiles_and_vendored_paths_before_routing(tmp_path):
    # Regression/coverage for routing.is_excluded_from_review(): a
    # lockfile or vendored file must cost zero model calls, not just be
    # filtered out of the final findings.
    repo = make_repo(tmp_path)
    (repo / "package-lock.json").write_text('{"lockfileVersion": 3}\n')
    (repo / "vendor").mkdir()
    (repo / "vendor" / "lib.py").write_text("def helper():\n    pass\n")
    prompts = load_agent_prompts(repo)
    cache = AgentCache(repo)
    reviewer = FakeReviewer({})
    run = run_review(repo, None, prompts, cache, reviewer)
    reviewed_paths = {f.path for f in run.files}
    assert "package-lock.json" not in reviewed_paths
    assert "vendor/lib.py" not in reviewed_paths
    assert reviewer.calls == []


def test_large_diff_is_truncated_before_being_sent_to_the_model(tmp_path):
    repo = make_repo(tmp_path)
    many_asserts = "\n".join(f"    assert x == {i}" for i in range(MAX_DIFF_LINES + 500))
    (repo / "test_generated.py").write_text(f"def test_thing():\n{many_asserts}\n")
    prompts = load_agent_prompts(repo)
    cache = AgentCache(repo)
    reviewer = FakeReviewer({})

    run = run_review(repo, None, prompts, cache, reviewer)

    file_result = run.files[0]
    assert file_result.truncated is True
    assert reviewer.calls, "expected at least one specialist to have been invoked"
    sent_diff = reviewer.calls[0][1]
    assert "diff truncated" in sent_diff
    assert sent_diff.count("\n") < MAX_DIFF_LINES + 50  # actually shorter than the real diff


def test_max_files_drops_lower_priority_files_deterministically(tmp_path):
    repo = make_repo(tmp_path)
    # wide.py routes to 3 specialists (security + api-type-contract +
    # testing-coverage, confirmed directly against routing.route_file);
    # narrow.py routes to 2 (api-type-contract + testing-coverage) --
    # a budget of 1 must keep the broader-risk file.
    (repo / "wide.py").write_text(
        "def get_user(request):\n"
        '    user_id = request.args.get("id")\n'
        '    query = f"SELECT * FROM users WHERE id = {user_id}"\n'
        "    return db.execute(query).fetchone()\n"
    )
    (repo / "narrow.py").write_text("CACHE_SIZE = 128\ndef compute(x):\n    return x * 2\n")
    prompts = load_agent_prompts(repo)
    cache = AgentCache(repo)
    reviewer = FakeReviewer({})

    run = run_review(repo, None, prompts, cache, reviewer, max_files=1)

    reviewed_paths = {f.path for f in run.files}
    assert reviewed_paths == {"wide.py"}
    assert run.skipped_for_budget == ["narrow.py"]


def test_build_review_user_message_includes_the_no_tool_access_note():
    # Regression test for a real live-model false negative (2026-09-17): a
    # specialist's own prompt tells it to "confirm by reading the test
    # diff... do not assume absence without checking," language written
    # for a live Claude Code session with real tools. Sent through this
    # CLI as a bare text completion, that instruction gave a real model a
    # defensible-sounding reason to withhold an otherwise-clear finding
    # ("I can't check, so I shouldn't assume"). This note exists to close
    # that gap -- confirm it's actually present, and after the diff (the
    # last thing a specialist reads), not swallowed by a refactor.
    message = build_review_user_message("File: handlers.py", "+x = 1\n")
    assert message.startswith("File: handlers.py\n\nDiff (base..working tree):\n```diff\n+x = 1")
    assert "no Read, Grep, or Bash tool calls available" in message
    assert message.index("```diff") < message.index("no Read, Grep, or Bash")


def test_review_one_file_sends_the_no_tool_access_note_to_the_reviewer(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(
        "def get_user(request):\n"
        '    user_id = request.args.get("id")\n'
        '    query = f"SELECT * FROM users WHERE id = {user_id}"\n'
        "    return db.execute(query).fetchone()\n"
    )
    prompts = load_agent_prompts(repo)
    cache = AgentCache(repo)
    reviewer = FakeReviewer({})
    run_review(repo, None, prompts, cache, reviewer)
    assert reviewer.calls, "expected at least one specialist to have been invoked"
    for _system_prompt, user_message in reviewer.calls:
        assert "no Read, Grep, or Bash tool calls available" in user_message


def test_suppressed_finding_is_filtered_from_report_but_still_cached_raw(tmp_path):
    # Regression/coverage for the feedback-loop design: a finding matched
    # by .claude/ignore-findings.yml must disappear from the report and
    # be logged, but the CACHE must still hold the raw, unsuppressed
    # finding -- so editing (or deleting) the suppression file takes
    # effect on the very next run with zero re-review, exactly like
    # un-suppressing something a team decides is worth surfacing again.
    repo = make_repo(tmp_path)
    (repo / "handlers.py").write_text(
        "def get_user(request):\n"
        '    user_id = request.args.get("id")\n'
        '    query = f"SELECT * FROM users WHERE id = {user_id}"\n'
        "    return db.execute(query).fetchone()\n"
    )
    claude_dir = repo / ".claude"
    claude_dir.mkdir()
    ignore_path = claude_dir / "ignore-findings.yml"
    ignore_path.write_text(
        "suppressions:\n"
        "  - agent: security-review\n"
        '    location: "handlers.py:*"\n'
        '    reason: "triaged as a non-issue"\n',
        encoding="utf-8",
    )
    prompts = load_agent_prompts(repo)
    cache = AgentCache(repo)
    reviewer = FakeReviewer(
        {
            "security vulnerabilities": (
                "[CRITICAL] handlers.py:3 — SQL injection\nImpact: x\nFix: y\n"
            ),
        }
    )

    run = run_review(repo, None, prompts, cache, reviewer)
    assert run.all_findings == []  # suppressed out of the report entirely
    assert len(run.suppressed) == 1
    assert run.suppressed[0].reason == "triaged as a non-issue"
    assert run.suppressed[0].finding.severity == "CRITICAL"
    assert run.suppressed[0].path == "handlers.py"

    log_path = repo / ".agent-cache" / "suppressions.log"
    assert log_path.exists()
    assert "triaged as a non-issue" in log_path.read_text(encoding="utf-8")

    # Delete the suppression rule and re-run against the SAME on-disk
    # cache, with a reviewer that raises if actually invoked -- proving
    # the finding was cached in its raw form and reappears purely from
    # editing the config, with no fresh model call.
    ignore_path.unlink()
    cache2 = AgentCache(repo)
    unreachable_reviewer = ReviewerThatFailsOneFile(fail_marker="")
    run2 = run_review(repo, None, prompts, cache2, unreachable_reviewer)
    assert run2.cache_hits == 1
    assert len(run2.all_findings) == 1
    assert run2.all_findings[0].severity == "CRITICAL"
    assert run2.suppressed == []


def test_no_suppressions_file_means_zero_overhead_and_no_log(tmp_path):
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
                "[CRITICAL] handlers.py:3 — SQL injection\nImpact: x\nFix: y\n"
            ),
        }
    )
    run = run_review(repo, None, prompts, cache, reviewer)
    assert len(run.all_findings) == 1
    assert run.suppressed == []
    assert not (repo / ".agent-cache" / "suppressions.log").exists()


def test_ignores_untracked_files_under_agent_cache_and_agent_rules(tmp_path):
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
