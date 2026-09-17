#!/usr/bin/env python3
"""CI/local harness for tests/fixtures/: runs every case in
tests/fixtures/manifest.json through its target .claude/agents/*.md
specialist -- the actual prompt files in THIS repo, loaded the same way
the standalone CLI loads them (agent_review.prompts.load_agent_prompts)
-- and checks the response against that case's expected verdict
(must_fire / must_not_fire / must_not_fire_standalone).

This replaces the TODO-laden inline script that used to live in
.github/workflows/validate-agents.yml, which only printed each case's
EXPECTED.md without ever actually invoking an agent.

Each specialist agent's .md file grants it live tool access (Read, Grep,
Bash(git diff *), ...) for use inside a real Claude Code session -- but
this harness, like the standalone CLI's own agents_client.py, invokes it
as a single text completion instead: the agent's body becomes the system
prompt, and the fixture's diff is embedded directly in the user message
via orchestrator.build_review_user_message() -- the exact same message
shape (including its no-tool-access note, added after a real live run
surfaced a specialist hedging on an otherwise-clear finding because its
own prompt told it to "check" something it had no way to check here)
orchestrator.py's real _review_one_file uses. That reuse is deliberate,
not incidental -- it's the exact mechanism DESIGN.md Section G documents
the CLI already relying on to run these same prompts outside Claude Code,
so this harness piggybacks on already-validated machinery rather than
inventing a second way to invoke the same agents.

Two modes:
  - Replay (default): strict, deterministic, free. Every case must already
    have a recorded response in tests/fixtures/cassettes.json -- a miss is
    a hard failure (support.cassette.CassetteMissError), never a silent
    skip. This is what runs in .github/workflows/validate-agents.yml on
    every PR.
  - Live (--live): calls a real Microsoft Foundry resource (the same
    AnthropicFoundryReviewer the CLI itself uses) and records every
    response into the cassette as it goes. Requires ANTHROPIC_FOUNDRY_*
    environment variables. Run this, then commit the refreshed cassette,
    whenever an agent's prompt changes or a fixture is added -- that's
    what actually keeps replay mode honest.

Usage:
    python scripts/validate_fixtures.py                        # replay, all cases
    python scripts/validate_fixtures.py --agent security-review
    python scripts/validate_fixtures.py --live                  # needs Foundry creds
    python scripts/validate_fixtures.py --seed-placeholders-from-expected
        # Bootstrap only -- see that flag's own help text below.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_SRC = REPO_ROOT / "cli" / "src"
CLI_TESTS = REPO_ROOT / "cli" / "tests"
# Matches the sys.path.insert(...) convention already used by every
# cli/tests/*.py file, so this repo-root script can import both the
# installed-shape `agent_review` package and the tests/support/ cassette
# helpers without either being pip-installed.
sys.path.insert(0, str(CLI_SRC))
sys.path.insert(0, str(CLI_TESTS))

from agent_review import findings as findings_mod
from agent_review.agents_client import AnthropicFoundryReviewer
from agent_review.cli import _load_dotenv_if_present
from agent_review.orchestrator import build_review_user_message
from agent_review.prompts import AgentPrompt, load_agent_prompts
from support.cassette import (
    Cassette,
    CassetteMissError,
    CassetteReviewer,
    RecordingReviewer,
    Reviewer,
    request_key,
)

# These imports must come after the sys.path.insert calls above --
# support.cassette and, in a from-source checkout, agent_review itself
# aren't installed/importable before that, hence the import-order
# suppression comment (naming the E402 pycodestyle rule) on each one.
# Earlier revisions of this comment claimed ruff's default rule set didn't
# include E402 at all and removed these -- that was wrong (E402 is part
# of ruff's default select, under the E4 "import" group) and the comments
# belong here permanently, not as a one-time cleanup.

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
MANIFEST_PATH = FIXTURES_DIR / "manifest.json"
CASSETTE_PATH = FIXTURES_DIR / "cassettes.json"
DIFF_TIMEOUT_SECONDS = 30
_MUST_FIRE_VERDICTS = {"must_fire"}
_MUST_NOT_FIRE_VERDICTS = {"must_not_fire", "must_not_fire_standalone"}


def _diff_for_new_file(case_dir: Path, filename: str) -> str:
    """Same shape `git diff --no-index` produces for
    git_utils.diff_for_file()'s untracked-file branch: the whole file
    shown as added, against /dev/null. Run with cwd=case_dir and a
    relative filename (not an absolute path) so the diff header shows the
    bare filename -- matching how every EXPECTED.md refers to locations
    (e.g. "handlers.py:4", never a full nested fixture path) and how a
    real repo-relative diff would look in CI.
    """
    result = subprocess.run(
        ["git", "diff", "--no-index", "--", "/dev/null", filename],
        cwd=case_dir,
        capture_output=True,
        text=True,
        timeout=DIFF_TIMEOUT_SECONDS,
        stdin=subprocess.DEVNULL,
        check=False,  # --no-index exits 1 when there IS a difference -- expected
    )
    return result.stdout


def build_case_diff(case_dir: Path) -> tuple[str, list[str]]:
    """One combined synthetic diff for every non-EXPECTED.md file in a
    fixture case directory, sorted for determinism. Most cases are a
    single file directly in case_dir; a few (e.g. api-type-contract-review's
    cases) pair an interface/DTO file with its caller; a few more (e.g.
    data-integrity-review's migration cases) nest their file under a nested
    directory such as Migrations/. Walked recursively (rglob), not
    case_dir.iterdir(), specifically because that nesting exists -- a
    non-recursive listing would silently see zero files for those cases
    (an empty diff, not an error), which is exactly the kind of quiet
    wrong-answer this harness must not produce for the very fixtures that
    exercise it. Returns (diff_text, relative_filenames).

    Real-world friction (2026-09-17): a fixture directory that also
    contains a real, importable test file (see testing-coverage-review's
    cases) accumulates its own `__pycache__/*.pyc` the moment anything
    (a stray `pytest` invocation scoped too broadly, an IDE's test
    runner) imports it -- gitignored, so it never reaches a commit, but
    still on disk locally, and rglob() would otherwise fold it straight
    into the "diff" as if it were reviewable source, corrupting that
    case's result with binary noise. Excluded the same way EXPECTED.md
    already is.
    """
    filenames = sorted(
        p.relative_to(case_dir).as_posix()
        for p in case_dir.rglob("*")
        if p.is_file() and p.name != "EXPECTED.md" and "__pycache__" not in p.parts
    )
    diff_text = "\n".join(_diff_for_new_file(case_dir, name) for name in filenames)
    return diff_text, filenames


def _extract_expected_finding_block(expected_md: str) -> str | None:
    """For a must_fire case, EXPECTED.md's body (after the "Expected
    finding:" label) is already written in the exact
    `[SEVERITY] file:line - text` + `Impact:` + `Fix:` contract shape --
    see e.g. tests/fixtures/security-review/true_positive/EXPECTED.md.
    Returns None if the file doesn't follow that convention (a
    must_not_fire case's EXPECTED.md is prose, not a finding block).
    """
    marker = "Expected finding:"
    if marker not in expected_md:
        return None
    return expected_md.split(marker, 1)[1].strip()


@dataclasses.dataclass(frozen=True)
class CaseResult:
    agent: str
    case: str
    verdict: str
    passed: bool
    detail: str


def _check_verdict(verdict: str, agent: str, raw_response: str) -> tuple[bool, str]:
    stripped = raw_response.strip()
    if verdict in _MUST_FIRE_VERDICTS:
        parsed = findings_mod.parse(agent, raw_response)
        if parsed:
            headline = parsed[0].render().splitlines()[0]
            return True, f"{len(parsed)} finding(s), e.g. {headline}"
        return False, f"expected a finding, got: {stripped[:200]!r}"
    if verdict in _MUST_NOT_FIRE_VERDICTS:
        # The output contract requires the exact literal string, not
        # merely "zero parsed findings" -- a hedge or disclaimer instead
        # of silence is itself a contract violation this harness should
        # catch, not paper over (see CLAUDE.md's Output Contract section).
        if stripped == findings_mod.NO_FINDINGS_TEXT:
            return True, "no finding, as expected"
        return (
            False,
            f"expected exactly {findings_mod.NO_FINDINGS_TEXT!r}, got: {stripped[:200]!r}",
        )
    return False, f"unknown verdict {verdict!r} in manifest.json"


def _seed_placeholders(manifest: list[dict[str, str]]) -> None:
    cassette = Cassette(CASSETTE_PATH)
    prompts = load_agent_prompts(REPO_ROOT)
    seeded = 0
    for entry in manifest:
        agent_name, case, verdict = entry["agent"], entry["case"], entry["verdict"]
        case_dir = FIXTURES_DIR / agent_name / case
        agent = prompts.get(agent_name)
        if agent is None:
            print(f"skip {agent_name}/{case}: no loaded prompt for {agent_name!r}")
            continue
        diff_text, filenames = build_case_diff(case_dir)
        user_message = build_review_user_message(
            f"Files: {', '.join(filenames)}", diff_text
        )
        key = request_key(agent.system_prompt, user_message)
        if cassette.get(key) is not None:
            continue  # never overwrite an existing (possibly real) entry
        expected_md = (case_dir / "EXPECTED.md").read_text(encoding="utf-8")
        if verdict in _MUST_FIRE_VERDICTS:
            block = _extract_expected_finding_block(expected_md)
            if block is None:
                print(
                    f"warn {agent_name}/{case}: must_fire but EXPECTED.md has no finding block to seed from"
                )
                continue
            response = block
        else:
            response = findings_mod.NO_FINDINGS_TEXT
        cassette.put(key, ", ".join(filenames), response)
        seeded += 1
    cassette.set_meta(
        "Contains placeholder responses seeded from EXPECTED.md via "
        "--seed-placeholders-from-expected, NOT live-recorded model output. "
        "Refresh with `python scripts/validate_fixtures.py --live` against a "
        "real Foundry resource before trusting this cassette to catch a "
        "real prompt regression."
    )
    cassette.save()
    print(
        f"Seeded {seeded} new placeholder entries ({len(cassette)} total) into {CASSETTE_PATH}"
    )


def _filter_manifest_by_agent(
    manifest: list[dict[str, str]], agent_name: str | None
) -> list[dict[str, str]] | None:
    """The `--agent NAME` filter, split out of run() so an empty result
    (a typo'd or unknown agent name) is one early return here instead of
    another branch for run() itself to carry. None means "no matching
    entries" -- the error is already printed, caller just exits 2.
    """
    if not agent_name:
        return manifest
    filtered = [entry for entry in manifest if entry["agent"] == agent_name]
    if not filtered:
        print(f"error: no manifest entries for agent {agent_name!r}", file=sys.stderr)
        return None
    return filtered


def _build_reviewer(cassette: Cassette, live: bool) -> Reviewer:
    """Picks live-recording vs. replay, and prints the placeholder-data
    NOTE (only relevant in replay mode -- a live run doesn't consume
    placeholder data, it overwrites it).
    """
    if live:
        return RecordingReviewer(cassette, AnthropicFoundryReviewer())
    if cassette.meta:
        print(f"NOTE: {CASSETTE_PATH.name} is {cassette.meta}\n", file=sys.stderr)
    return CassetteReviewer(cassette)


def _run_one_case(
    entry: dict[str, str], prompts: dict[str, AgentPrompt], reviewer: Reviewer
) -> CaseResult:
    """One manifest entry, start to finish: load its prompt, build its
    diff, call the reviewer, check the verdict. Split out of run()'s loop
    body so a missing prompt or a cassette miss is a plain early return
    here, not another nested branch inside run()'s own loop.
    """
    agent_name, case, verdict = entry["agent"], entry["case"], entry["verdict"]
    agent = prompts.get(agent_name)
    if agent is None:
        return CaseResult(
            agent_name, case, verdict, False, f"no loaded prompt for {agent_name!r}"
        )

    case_dir = FIXTURES_DIR / agent_name / case
    diff_text, filenames = build_case_diff(case_dir)
    user_message = build_review_user_message(
        f"Files: {', '.join(filenames)}", diff_text
    )
    try:
        raw = reviewer.complete(agent.system_prompt, user_message)
    except CassetteMissError as exc:
        return CaseResult(agent_name, case, verdict, False, str(exc))

    passed, detail = _check_verdict(verdict, agent_name, raw)
    return CaseResult(agent_name, case, verdict, passed, detail)


def _run_all_cases(
    manifest: list[dict[str, str]], prompts: dict[str, AgentPrompt], reviewer: Reviewer
) -> list[CaseResult]:
    """Runs every case in manifest order, printing a progress marker before
    each one starts and its PASS/FAIL result the instant it finishes --
    rather than running the whole manifest silently and only printing once
    everything is done. This matters most in --live mode: each case is a
    real network call to a Foundry resource that can take anywhere from a
    couple of seconds to tens of seconds, and a 23-case run with zero
    output in between leaves a user with no way to tell "still working"
    from "hung," and no way to see which specific case a slow or wedged
    run stalled on. Replay mode is fast enough that this is mostly cosmetic,
    but the marker is harmless there too and keeps one code path for both.
    """
    total = len(manifest)
    results: list[CaseResult] = []
    for i, entry in enumerate(manifest, start=1):
        agent_name, case = entry["agent"], entry["case"]
        print(f"[{i}/{total}] {agent_name}/{case} ...", end=" ", flush=True)
        result = _run_one_case(entry, prompts, reviewer)
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        print(f"{status} ({result.verdict}): {result.detail}", flush=True)
    return results


def _summarize_results(results: list[CaseResult]) -> list[CaseResult]:
    """Prints the final pass-count summary and returns just the failures so
    run() can decide its exit code without re-deriving that list itself.
    Per-case PASS/FAIL lines are printed as each case finishes -- see
    _run_all_cases -- not batched here.
    """
    failed = [r for r in results if not r.passed]
    print(f"\n{len(results) - len(failed)}/{len(results)} cases passed")
    return failed


def run(args: argparse.Namespace) -> int:
    manifest: list[dict[str, str]] = json.loads(
        MANIFEST_PATH.read_text(encoding="utf-8")
    )
    filtered_manifest = _filter_manifest_by_agent(manifest, args.agent)
    if filtered_manifest is None:
        return 2
    manifest = filtered_manifest

    if args.seed_placeholders_from_expected:
        _seed_placeholders(manifest)
        return 0

    cassette = Cassette(CASSETTE_PATH)
    reviewer = _build_reviewer(cassette, args.live)
    prompts = load_agent_prompts(REPO_ROOT)

    results = _run_all_cases(manifest, prompts, reviewer)

    if args.live:
        cassette.save()
        print(f"Recorded {len(cassette)} total cassette entries to {CASSETTE_PATH}\n")

    failed = _summarize_results(results)
    return 1 if failed else 0


def main() -> int:
    # Real-world friction (2026-09-17): unlike `agent-review`/`agent-init`
    # (cli.py's own entry points), this standalone script never auto-loaded
    # `.env` -- so `--live` failed with "No Microsoft Foundry resource
    # configured" for a user who had real credentials sitting in `.env`
    # the whole time, just not exported into that shell. Same behavior as
    # cli.py: never overrides a variable already set in the real
    # environment, silently skipped if python-dotenv isn't installed.
    _load_dotenv_if_present()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--agent",
        default=None,
        help="Only run cases for this agent (e.g. security-review).",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Call a real Microsoft Foundry resource instead of replaying "
            "the cassette, and record every response into it. Requires "
            "ANTHROPIC_FOUNDRY_* environment variables."
        ),
    )
    parser.add_argument(
        "--seed-placeholders-from-expected",
        action="store_true",
        help=(
            "Bootstrap only: fill in cassette entries that don't exist yet "
            "with hand-derived placeholder responses built from each case's "
            "EXPECTED.md, clearly labeled as such (never overwrites an "
            "existing entry). This is NOT a substitute for --live -- it "
            "only gives this harness something to check before anyone has "
            "run it against a real Foundry resource."
        ),
    )
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
