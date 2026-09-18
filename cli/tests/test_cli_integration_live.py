"""Opt-in integration test for the CLI's own orchestrator against a
*realistic* model response, not the fully scripted FakeReviewer
test_cli_integration.py uses.

Two modes, both exercised by the exact same test bodies:

  - Default (plain `pytest`): replays the committed cassette
    (cli/tests/cassettes/integration.json) via CassetteReviewer -- strict,
    deterministic, free, and runs as part of the normal test suite with no
    Foundry credentials needed.
  - Recording (`AGENT_REVIEW_RECORD_LIVE=1 pytest cli/tests/test_cli_integration_live.py`,
    with real ANTHROPIC_FOUNDRY_* credentials set): calls a real Microsoft
    Foundry resource via the real AnthropicFoundryReviewer, and records
    what it returns into the cassette. Run this, inspect the diff, and
    commit the refreshed cassette whenever agents_client.py, routing.py,
    orchestrator.py, or the bundled default_rules/ prompts change in a way
    that could plausibly change what a real model call returns.

This is the item explicitly missing before this file existed: cli/'s own
orchestration was only ever tested against a scripted fake, never against
anything resembling a real model's response shape (imperfect formatting,
extra prose, etc.) -- see DESIGN.md Section G's "Honest limitations."
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from support.cassette import Cassette, CassetteReviewer, RecordingReviewer

from agent_review.cache import AgentCache
from agent_review.orchestrator import run_review
from agent_review.prompts import load_agent_prompts

CASSETTE_PATH = Path(__file__).resolve().parent / "cassettes" / "integration.json"
RECORD_ENV_VAR = "AGENT_REVIEW_RECORD_LIVE"

# Routes to security-review + performance-review (verified directly
# against routing.py's real output on the real diff, not assumed --
# a bare SQL query pattern trips both agents' triggers). Kept to a
# small, fixed file/agent pair set so the cassette below only needs a
# handful of entries.
_VULNERABLE_FILE = (
    "user_id = get_request_id()\n"
    'query = f"SELECT * FROM users WHERE id = {user_id}"\n'
    "result = db.execute(query).fetchone()\n"
)
_SAFE_FILE = (
    "user_id = get_request_id()\n"
    'query = "SELECT * FROM users WHERE id = %s"\n'
    "result = db.execute(query, (user_id,)).fetchone()\n"
)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "base.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


@pytest.fixture
def reviewer_and_cassette():
    """The one fixture that decides replay vs. record for every test in
    this file. AGENT_REVIEW_RECORD_LIVE=1 switches to a real
    AnthropicFoundryReviewer (which itself raises a clear RuntimeError if
    Foundry credentials aren't configured -- no separate check needed
    here) wrapped in RecordingReviewer; otherwise a strict CassetteReviewer
    replays the committed cassette. Saves the cassette back to disk on
    teardown only in recording mode.
    """
    cassette = Cassette(CASSETTE_PATH)
    if os.environ.get(RECORD_ENV_VAR):
        from agent_review.agents_client import AnthropicFoundryReviewer  # noqa: PLC0415
        # -- deliberately deferred, same reasoning as agents_client.py's own
        # lazy `anthropic`/`azure.identity` imports: keeps a missing
        # `anthropic` package (or unset Foundry credentials) from breaking
        # collection/replay-mode runs of this file, which never take this
        # branch and don't need either.

        reviewer = RecordingReviewer(cassette, AnthropicFoundryReviewer())
        yield reviewer
        # Same bug (and same fix) as scripts/validate_fixtures.py's `run()`:
        # a stale "hand-authored, not live-recorded" _meta note from the
        # original placeholder seed persisted forever because nothing ever
        # cleared it, even after this fixture recorded genuine live
        # responses (2026-09-18 finding). Every test in this file runs
        # through this fixture, so a recording run always exercises the
        # whole cassette -- no `--agent`-style partial-run case to guard
        # against here the way the bigger fixture harness has.
        cassette.set_meta(None)
        cassette.save()
    else:
        yield CassetteReviewer(cassette)


def test_run_review_finds_the_injection_and_clears_the_safe_query(reviewer_and_cassette, tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "handlers.py").write_text(_VULNERABLE_FILE)
    (repo / "safe_handlers.py").write_text(_SAFE_FILE)

    prompts = load_agent_prompts(repo)  # temp repo has neither .agent-rules/
    # nor .claude/, so this exercises the bundled default_rules/ fallback
    # path for real, not just prompts.py's own unit tests of that path.
    cache = AgentCache(repo)

    run = run_review(
        repo, base_ref=None, prompts=prompts, cache=cache, reviewer=reviewer_and_cassette
    )

    by_path = {f.path: f for f in run.files}
    assert by_path["handlers.py"].findings, "expected security-review to flag the SQL injection"
    assert by_path["handlers.py"].findings[0].severity in ("CRITICAL", "HIGH")
    assert by_path["safe_handlers.py"].findings == [], (
        "expected the parameterized query to clear security-review with no findings"
    )
    assert not run.failed_files


def test_second_run_is_a_pure_cache_hit_with_no_further_reviewer_calls(
    reviewer_and_cassette, tmp_path
):
    repo = _make_repo(tmp_path)
    (repo / "handlers.py").write_text(_VULNERABLE_FILE)
    prompts = load_agent_prompts(repo)

    cache1 = AgentCache(repo)
    run1 = run_review(
        repo, base_ref=None, prompts=prompts, cache=cache1, reviewer=reviewer_and_cassette
    )
    assert run1.cache_misses == 1

    # A second run against unchanged content must be served entirely from
    # .agent-cache/ -- the reviewer is never called again, so this passes
    # identically whether reviewer_and_cassette is in replay or record mode.
    cache2 = AgentCache(repo)
    run2 = run_review(
        repo, base_ref=None, prompts=prompts, cache=cache2, reviewer=reviewer_and_cassette
    )
    assert run2.cache_hits == 1
    assert run2.cache_misses == 0
    # Compare rendered text, not full Finding equality: a cache hit
    # re-parses the cached combined text with agent="cached" (see
    # orchestrator._review_one_file), which legitimately differs from the
    # real per-agent name a fresh run tags each Finding with.
    assert [f.render() for f in run2.all_findings] == [f.render() for f in run1.all_findings]
