"""Console-script entry points.

Two binaries get installed (see ``pyproject.toml``'s ``[project.scripts]``):

- ``agent-review`` -- ``main()`` below. ``agent-review --path /path/to/repo``
  runs a review with no subcommand needed (matching the CLI shape asked
  for); ``agent-review commit --path ...`` and ``agent-review heal --path
  ...`` are additional subcommands for the other two portable hooks
  (semantic commit messages, guarded self-healing).
- ``agent-init`` -- ``main_init()`` below. Scaffolds a target repo.

Every command takes ``--path`` and operates on that repository, never on
whatever directory the tool happens to be installed in -- that's what
makes this a global, multi-repo tool rather than something that only
works from inside its own source checkout.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from . import baseline as baseline_mod
from . import findings as findings_mod
from . import git_utils
from . import rules as rules_mod
from .agents_client import (
    DEFAULT_MODEL,
    ENV_API_KEY,
    ENV_MODEL,
    ENV_RESOURCE,
    ENV_USE_ENTRA_ID,
    AnthropicFoundryReviewer,
)
from .cache import AgentCache
from .commit import NoStagedChangesError, generate_commit_message
from .healing import (
    HealingProposal,
    NoTestRunnerFoundError,
    apply_patch,
    check_patch_applies,
    propose_fix,
    run_tests,
)
from .init import init_repo
from .orchestrator import FileReviewResult, ReviewRun, run_review
from .prompts import load_agent_prompts
from .sarif import to_sarif
from .suppressions import SUPPRESSIONS_PATH

# ("CRITICAL", "HIGH", "MEDIUM", "LOW") -- dict preserves insertion order.
_VALID_FAIL_ON_SEVERITIES = tuple(findings_mod.SEVERITY_ORDER)

_REVIEW_CMD = "review"
_COMMIT_CMD = "commit"
_HEAL_CMD = "heal"
# Declared once, adjacent to the three subparsers.add_parser(...) calls
# below (and to the argv-prepend line in main()) so every literal usage
# stays derived from these instead of being retyped -- a renamed/added
# subcommand only needs a change here plus the corresponding
# add_parser() call to stay consistent everywhere.
_KNOWN_COMMANDS = frozenset({_REVIEW_CMD, _COMMIT_CMD, _HEAL_CMD})


def _load_dotenv_if_present() -> None:
    """Load a `.env` file (ANTHROPIC_FOUNDRY_RESOURCE/API_KEY/etc., see
    `.env.example`) into the environment, if one is found -- searched
    upward from the current directory, matching how most `.env`-aware
    tools behave (put `.env` where you run `agent-review`/`agent-init`
    from, or in any parent of it). Never overrides a variable already set
    in the real environment: an explicit `$env:FOO=...`/`export FOO=...`
    always wins over whatever a `.env` file has (`load_dotenv()`'s
    default `override=False`).

    This is a convenience, not a requirement -- if python-dotenv somehow
    isn't installed (it's a normal dependency, but editable/vendored
    installs vary), silently skip it rather than crashing every command
    over a nice-to-have.
    """
    try:
        from dotenv import find_dotenv, load_dotenv  # noqa: PLC0415 -- optional,

        # deferred so a missing python-dotenv can never break commands
        # that don't need .env support at all (e.g. --help).
    except ImportError:
        return
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path)


def _resolve_git_repo(raw_path: str) -> Path:
    repo = Path(raw_path).expanduser().resolve()
    if not repo.is_dir():
        raise SystemExit(f"error: {repo} is not a directory")
    if not git_utils.is_git_repo(repo):
        raise SystemExit(f"error: {repo} is not a git repository")
    return repo


def _build_reviewer(args: argparse.Namespace) -> AnthropicFoundryReviewer:
    try:
        return AnthropicFoundryReviewer(
            model=args.model, resource=args.resource, use_entra_id=args.use_entra_id
        )
    except RuntimeError as exc:
        raise SystemExit(f"error: {exc}") from exc


# _print_review used to be one flat function with seven independent
# "if there's something to report, print a header then loop over it"
# blocks in a row -- functionally simple (each block is independent of
# every other), but each `if ...: \n for ...:` pair nests a loop inside a
# branch, which is exactly what SonarQube's Cognitive Complexity metric
# penalizes hardest (a nested loop costs its base score *plus* one for
# every enclosing branch/loop). That pushed the single function to a
# score of 20 against the project's allowed 15, even though there was
# never any real interaction between the seven blocks for a reader to
# hold in their head at once.
#
# The fix is the standard one for exactly this shape: give each
# independent block its own function with a guard-clause early return
# (`if not x: return`) instead of a positive `if x:` wrapping the rest of
# the body. That turns "loop nested inside a branch" into "loop that
# follows a guard clause at the same nesting level," which is what
# collapses each helper's own score to 2 (guard `if` + flat `for`) and
# _print_review itself to a plain, branch-free sequence of calls. Pure
# refactor -- every helper below prints exactly what its inlined block
# used to, so `cmd_review`'s stdout is byte-for-byte unchanged; the
# existing capsys-based CLI integration tests are what confirm that.


def _print_failed_files(failed: list[FileReviewResult]) -> None:
    if not failed:
        return
    print(f"Failed to review {len(failed)} file(s) (not cached -- will retry next run):")
    for f in failed:
        print(f"  {f.path}: {f.error}")


def _print_missing_agents(run: ReviewRun) -> None:
    missing = sorted({(f.path, agent) for f in run.files for agent in f.missing_agents})
    if not missing:
        return
    print(f"Warning: {len(missing)} routed specialist(s) had no loaded prompt and were skipped:")
    for path, agent in missing:
        print(f"  {path}: {agent}")


def _print_malformed_agents(run: ReviewRun) -> None:
    malformed = sorted({(f.path, agent) for f in run.files for agent in f.malformed_agents})
    if not malformed:
        return
    print(
        f"Warning: {len(malformed)} specialist call(s) returned a response "
        "that didn't match the expected output format -- not cached, will "
        "retry next run, but this diff was NOT actually reviewed by them "
        "this time:"
    )
    for path, agent in malformed:
        print(f"  {path}: {agent}")


def _print_truncated_files(run: ReviewRun) -> None:
    truncated = sorted(f.path for f in run.files if f.truncated)
    if not truncated:
        return
    print(
        f"Note: {len(truncated)} file(s) had a diff too large to send in full "
        "-- only a truncated prefix was reviewed:"
    )
    for path in truncated:
        print(f"  {path}")


def _print_skipped_for_budget(run: ReviewRun) -> None:
    if not run.skipped_for_budget:
        return
    print(
        f"Note: {len(run.skipped_for_budget)} file(s) were skipped due to "
        "--max-files and were not reviewed at all this run:"
    )
    for path in run.skipped_for_budget:
        print(f"  {path}")


def _print_suppressed(run: ReviewRun) -> None:
    if not run.suppressed:
        return
    print(f"Note: {len(run.suppressed)} finding(s) suppressed by {SUPPRESSIONS_PATH.as_posix()}:")
    for s in run.suppressed:
        print(f"  {s.path}: [{s.finding.severity}] {s.finding.location} — {s.reason}")


def _print_resolved(resolved: list[baseline_mod.BaselineEntry]) -> None:
    # Never a failure signal (see baseline.resolved_entries' own
    # docstring) -- purely informational, in the same "Note:" family as
    # _print_suppressed/_print_skipped_for_budget above.
    if not resolved:
        return
    print(
        f"Note: {len(resolved)} finding(s) in the baseline were not reported this run "
        "(resolved, or no longer detected):"
    )
    for entry in resolved:
        print(f"  {entry.location}: [{entry.severity}] {entry.rule_id} — {entry.title}")


def _print_findings(run: ReviewRun) -> None:
    # The original, baseline-unaware rendering -- used only when neither
    # --baseline nor --new-only was given, so this stays byte-for-byte
    # identical to every prior release's default output (see
    # backward-compatibility requirements in DESIGN.md's "Finding
    # lifecycle and CI policy" section).
    if not run.all_findings:
        print("No high-impact issues found.")
        return
    for finding in run.all_findings:
        print(finding.render())
        print()


# NEW/EXISTING padded to the same column width ("EXISTING" is the longer
# of the two words) so a human scanning a mixed list can visually align
# the locations that follow, e.g.:
#   [HIGH] NEW       src/auth.py:143 — ...
#   [MEDIUM] EXISTING src/foo.py:81 — ...
_STATUS_COLUMN_WIDTH = len("EXISTING") + 1


def _print_findings_with_status(classified: list[baseline_mod.Classification]) -> None:
    if not classified:
        print("No high-impact issues found.")
        return
    for c in classified:
        f = c.finding
        status_label = c.status.upper().ljust(_STATUS_COLUMN_WIDTH)
        print(f"[{f.severity}] {status_label}{f.location} — {f.title}")
        print(f"Impact: {f.impact}")
        print(f"Fix: {f.fix}")
        print()


def _print_review(
    run: ReviewRun,
    classified: list[baseline_mod.Classification] | None = None,
    resolved: list[baseline_mod.BaselineEntry] | None = None,
) -> None:
    """`classified`/`resolved` are only non-None when --baseline and/or
    --new-only were given (see cmd_review) -- with neither, this prints
    exactly what every prior release printed. `classified` is already
    whatever --new-only did or didn't filter it down to; this function
    itself never re-filters.
    """
    print(f"Base ref: {run.base_ref}")
    print(
        f"Files analyzed: {len(run.files)}  "
        f"(cache hits: {run.cache_hits}, cache misses: {run.cache_misses})"
    )
    _print_failed_files(run.failed_files)
    _print_missing_agents(run)
    _print_malformed_agents(run)
    _print_truncated_files(run)
    _print_skipped_for_budget(run)
    _print_suppressed(run)
    if resolved:
        _print_resolved(resolved)
    print()
    if classified is None:
        _print_findings(run)
    else:
        _print_findings_with_status(classified)


def _finding_to_dict(c: baseline_mod.Classification, include_status: bool) -> dict:
    """One finding's JSON representation -- extends the pre-existing
    plain `dataclasses.asdict(finding)` shape additively: every field
    that shape already had is unchanged, `rule_id` (already a Finding
    field, but always resolved through rules.effective_rule_id here so
    it's never an empty string) and `fingerprint` are always added, and
    `status` ("new"/"existing") is added only when a baseline was
    actually supplied -- see cmd_review's `include_status`, and
    DESIGN.md's compatibility note on why status is omitted rather than
    defaulted to "new" for callers that never asked for baseline
    classification at all.
    """
    d = dataclasses.asdict(c.finding)
    d["rule_id"] = rules_mod.effective_rule_id(c.finding)
    d["fingerprint"] = c.fingerprint
    if include_status:
        d["status"] = c.status
    return d


def _review_run_to_dict(
    run: ReviewRun,
    classified: list[baseline_mod.Classification],
    baseline: baseline_mod.Baseline | None,
    resolved: list[baseline_mod.BaselineEntry],
    baseline_updated_path: str | None,
) -> dict:
    """Structured form of a ReviewRun for `--json`, so a CI/CD pipeline
    can consume findings (and the same warnings _print_review shows a
    human) programmatically -- without regex-parsing this tool's own
    human-readable stdout, which is exactly the brittleness this is meant
    to remove. Deliberately built here, not on ReviewRun/Finding
    themselves: this is an output-format concern, kept alongside the rest
    of this module's rendering (_print_review, the plain-text default).

    `classified` is the (possibly --new-only-filtered) list that becomes
    the "findings" key -- same list cmd_review already computed for
    human output, so JSON and text output are always in agreement about
    which findings were selected. `baseline`/`resolved` are only used to
    decide whether to add the additive "baseline" key at all (None means
    --baseline was never supplied, so the whole key -- and each
    finding's "status" -- is omitted for full backward compatibility with
    a consumer written against the pre-milestone-1 schema).
    """
    payload: dict = {
        "base_ref": run.base_ref,
        "files_analyzed": len(run.files),
        "cache_hits": run.cache_hits,
        "cache_misses": run.cache_misses,
        "failed_files": [{"path": f.path, "error": f.error} for f in run.failed_files],
        "missing_agents": [
            {"path": f.path, "agent": agent} for f in run.files for agent in f.missing_agents
        ],
        "malformed_agents": [
            {"path": f.path, "agent": agent} for f in run.files for agent in f.malformed_agents
        ],
        "truncated_files": [f.path for f in run.files if f.truncated],
        "skipped_for_budget": run.skipped_for_budget,
        "suppressed": [
            {
                "path": s.path,
                "agent": s.finding.agent,
                "location": s.finding.location,
                "severity": s.finding.severity,
                "reason": s.reason,
            }
            for s in run.suppressed
        ],
        "findings": [_finding_to_dict(c, include_status=baseline is not None) for c in classified],
    }
    if baseline is not None:
        payload["baseline"] = {
            "new_count": sum(1 for c in classified if c.status == "new"),
            "existing_count": sum(1 for c in classified if c.status == "existing"),
            "resolved": [dataclasses.asdict(e) for e in resolved],
        }
    if baseline_updated_path is not None:
        payload["baseline_updated"] = baseline_updated_path
    return payload


def _resolve_baseline_path(repo: Path, raw: str | None) -> Path:
    candidate = Path(raw) if raw else baseline_mod.DEFAULT_BASELINE_PATH
    return candidate if candidate.is_absolute() else repo / candidate


def _parse_fail_on(raw: str | None) -> int | None:
    """Parses --fail-on's comma-separated severity list (e.g.
    "critical,high") into a single threshold rank using
    findings.SEVERITY_ORDER, where "at or above the configured threshold"
    means "rank <= this value." Multiple severities collapse to the rank
    of the LEAST severe one given -- "critical,high" and "high" alone are
    therefore equivalent, since "at or above high" already covers
    critical; this is what lets a non-contiguous-looking list still mean
    exactly what a reader expects ("fail on anything this bad or worse").
    Returns None when --fail-on wasn't given at all (no additional
    gating, independent of the pre-existing --fail-on-findings flag).
    Raises ValueError -- turned into a clean SystemExit by the caller --
    for an empty or unrecognized severity name, so a typo like
    "--fail-on hihg" fails loudly at argument time instead of silently
    gating on nothing.
    """
    if raw is None:
        return None
    severities = [s.strip().upper() for s in raw.split(",") if s.strip()]
    if not severities:
        raise ValueError("--fail-on requires at least one severity")
    invalid = sorted({s for s in severities if s not in _VALID_FAIL_ON_SEVERITIES})
    if invalid:
        raise ValueError(
            f"--fail-on: unknown severity/severities {invalid} -- valid values are "
            f"{', '.join(s.lower() for s in _VALID_FAIL_ON_SEVERITIES)} (comma-separated)"
        )
    return max(findings_mod.SEVERITY_ORDER[s] for s in severities)


def cmd_review(args: argparse.Namespace) -> int:
    repo = _resolve_git_repo(args.path)

    try:
        fail_on_threshold = _parse_fail_on(args.fail_on)
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from exc

    # Baseline loading is a cheap, local, no-network precondition -- same
    # "fail fast before spending API access" philosophy cmd_commit already
    # uses for its own staged-changes check -- so it happens before
    # _build_reviewer, which requires a valid Foundry configuration.
    # baseline_path stays None (and nothing is read) unless --baseline or
    # --update-baseline was actually given: a plain `agent-review --path
    # X` must never start consulting a baseline just because one happens
    # to exist on disk (see baseline.py's module docstring).
    loaded_baseline: baseline_mod.Baseline | None = None
    baseline_path: Path | None = None
    if args.baseline or args.update_baseline:
        baseline_path = _resolve_baseline_path(repo, args.baseline)
        if baseline_path.is_file():
            try:
                loaded_baseline = baseline_mod.load_baseline(baseline_path)
            except baseline_mod.BaselineError as exc:
                raise SystemExit(f"error: {exc}") from exc
        elif args.baseline and not args.update_baseline:
            raise SystemExit(
                f"error: baseline file not found: {baseline_path} "
                "(pass --update-baseline to create it)"
            )
        # else: the baseline file doesn't exist yet and --update-baseline
        # is set -- not an error, this run will create it.

    prompts = load_agent_prompts(repo)
    cache = AgentCache(repo)
    reviewer = _build_reviewer(args)
    try:
        run = run_review(
            repo,
            args.base,
            prompts,
            cache,
            reviewer,
            max_workers=args.jobs,
            max_files=args.max_files,
        )
    except Exception as exc:
        # Surface API/network failures cleanly, not as a raw traceback
        # from inside a worker thread.
        raise SystemExit(f"error: review failed: {exc}") from exc

    # Suppression already happened inside run_review(); baseline
    # classification is a second, independent, purely presentational
    # layer on top of whatever survived that -- see baseline.py's module
    # docstring on why the two concepts are kept distinct (a suppressed
    # finding is never in run.all_findings, so it can never be
    # classified as NEW or EXISTING here; that's covered by --update-
    # baseline's own separate suppressed-findings handling below).
    all_classified = baseline_mod.classify(run.all_findings, loaded_baseline)
    display = [c for c in all_classified if c.status == "new"] if args.new_only else all_classified
    resolved = (
        baseline_mod.resolved_entries(run.all_findings, loaded_baseline)
        if loaded_baseline is not None
        else []
    )

    baseline_updated_path: str | None = None
    if args.update_baseline:
        assert baseline_path is not None  # guaranteed by the block above
        # Includes suppressed findings: a suppression is a display-time
        # decision, not evidence the underlying finding stopped existing
        # (see baseline.py's module docstring) -- so an unsuppressed
        # finding never spuriously reappears as "new" just because it
        # happened to be suppressed the last time the baseline was
        # captured. --new-only never affects what's written here: the
        # baseline always captures the FULL current state, independent of
        # what this particular run chose to display or evaluate.
        findings_for_baseline = run.all_findings + [s.finding for s in run.suppressed]
        try:
            baseline_mod.write_baseline(baseline_path, findings_for_baseline)
        except baseline_mod.BaselineError as exc:
            raise SystemExit(f"error: {exc}") from exc
        baseline_updated_path = str(baseline_path)

    if args.sarif:
        print(
            json.dumps(
                to_sarif(run, baseline=loaded_baseline, new_only=args.new_only),
                indent=2,
            )
        )
    elif args.json:
        print(
            json.dumps(
                _review_run_to_dict(
                    run,
                    classified=display,
                    baseline=loaded_baseline,
                    resolved=resolved,
                    baseline_updated_path=baseline_updated_path,
                ),
                indent=2,
            )
        )
    else:
        show_status = loaded_baseline is not None or args.new_only or args.update_baseline
        _print_review(
            run,
            classified=display if show_status else None,
            resolved=resolved if show_status else None,
        )
        if baseline_updated_path is not None:
            print(f"Baseline written to {baseline_updated_path}.")

    exit_code = 0
    # Unchanged from every prior release: --fail-on-findings always
    # evaluates the full, baseline/--new-only-independent finding set --
    # see cli/README.md's flag docs for why the two flags are kept fully
    # independent rather than one superseding the other.
    has_blocking = any(f.severity in ("CRITICAL", "HIGH") for f in run.all_findings)
    if args.fail_on_findings and has_blocking:
        exit_code = 1
    if fail_on_threshold is not None:
        eval_findings = [c.finding for c in display]
        if any(
            findings_mod.SEVERITY_ORDER.get(f.severity, 99) <= fail_on_threshold
            for f in eval_findings
        ):
            exit_code = 1
    return exit_code


def cmd_commit(args: argparse.Namespace) -> int:
    repo = _resolve_git_repo(args.path)
    # Check for staged changes before building a reviewer (which requires
    # a valid API key) -- no point demanding API access for a command
    # that's going to fail on a cheap, local, no-network precondition.
    try:
        has_staged_changes = bool(git_utils.staged_diff(repo).strip())
    except git_utils.GitError as exc:
        # Same clean-error-message contract every other command here
        # guarantees -- e.g. a stale .git/index.lock from a concurrently
        # running git process must not surface as a raw traceback.
        raise SystemExit(f"error: {exc}") from exc
    if not has_staged_changes:
        raise SystemExit(
            "error: No staged changes to summarize (`git diff --staged` is empty). "
            "Stage the changes you want summarized with `git add` first."
        )
    reviewer = _build_reviewer(args)
    try:
        message = generate_commit_message(repo, reviewer)
    except NoStagedChangesError as exc:
        raise SystemExit(f"error: {exc}") from exc
    except Exception as exc:
        raise SystemExit(f"error: could not generate a commit message: {exc}") from exc
    print(message)
    return 0


def cmd_heal(args: argparse.Namespace) -> int:
    repo = _resolve_git_repo(args.path)
    try:
        result = run_tests(repo)
    except NoTestRunnerFoundError as exc:
        raise SystemExit(f"error: {exc}") from exc

    if result.passed:
        print(f"{result.runner.name}: tests passed. Nothing to heal.")
        return 0

    print(f"{result.runner.name} failed (exit code {result.returncode}).")
    print("Asking for a proposed fix...")
    print()
    reviewer = _build_reviewer(args)
    try:
        proposal: HealingProposal = propose_fix(result, reviewer)
    except Exception as exc:
        raise SystemExit(f"error: could not get a proposed fix: {exc}") from exc

    print(proposal.explanation or "(no explanation returned)")
    print()
    if not proposal.has_diff:
        print("No diff was proposed -- nothing to apply.")
        return 1

    print("Proposed patch:")
    print(proposal.diff)
    print()

    if not args.apply:
        print(
            "Nothing has been written to disk. Re-run with --apply to write "
            "this patch and re-run the test suite."
        )
        return 1

    ok, err = check_patch_applies(repo, proposal)
    if not ok:
        raise SystemExit(f"error: proposed patch does not apply cleanly: {err}")
    apply_patch(repo, proposal)
    print("Patch applied.")

    rerun = run_tests(repo, runner=result.runner)
    print(f"Re-ran {rerun.runner.name}: {'PASSED' if rerun.passed else 'still FAILING'}")
    return 0 if rerun.passed else 1


def cmd_init(args: argparse.Namespace) -> int:
    repo = Path(args.path).expanduser().resolve()
    if not repo.is_dir():
        raise SystemExit(f"error: {repo} is not a directory")
    result = init_repo(repo)
    if result.already_initialized:
        print(f"{repo} is already initialized -- nothing to do.")
        return 0
    for f in result.created_files:
        print(f"created {f}")
    for f in result.skipped_files:
        print(f"skipped {f} (already exists)")
    if result.gitignore_updated:
        print("updated .gitignore to exclude .agent-cache/")
    return 0


_PATH_HELP = "Path to the target git repository (default: current directory)."


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--path", default=".", help=_PATH_HELP)
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Claude model to use -- must be a model actually deployed "
            "under the target Foundry resource, not just any valid model "
            f"ID (default: the {ENV_MODEL} environment "
            f"variable, or {DEFAULT_MODEL} if that's also unset)."
        ),
    )
    parser.add_argument(
        "--resource",
        default=None,
        help=f"Microsoft Foundry resource name (default: the {ENV_RESOURCE} environment variable).",
    )
    parser.add_argument(
        "--use-entra-id",
        action="store_true",
        default=None,
        help=(
            "Authenticate to Microsoft Foundry via Entra ID (azure-identity's "
            f"DefaultAzureCredential) instead of an API key. Default: on if "
            f"{ENV_USE_ENTRA_ID} is set, otherwise API key auth "
            f"via {ENV_API_KEY}."
        ),
    )


def build_review_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-review",
        description=(
            "Evidence-based, diff-scoped code review for any git repository. "
            f"Talks to Claude exclusively via Microsoft Foundry (Azure AI "
            f"Foundry) -- reads {ENV_RESOURCE}, "
            f"{ENV_API_KEY} (or {ENV_USE_ENTRA_ID}), "
            f"and optionally {ENV_MODEL} from the environment."
        ),
    )
    subparsers = parser.add_subparsers(dest="command")

    review = subparsers.add_parser(
        _REVIEW_CMD,
        help="Review the diff between HEAD and a base ref (default action).",
    )
    _add_common_args(review)
    review.add_argument(
        "--base",
        default=None,
        help=(
            "Base ref to diff against (default: auto-detect origin/main, "
            "origin/master, main, master, or the repo's first commit)."
        ),
    )
    review.add_argument(
        "--jobs", type=int, default=4, help="Max concurrent model calls (default: 4)."
    )
    review.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="Exit with status 1 if any CRITICAL/HIGH finding is reported (for CI).",
    )
    review.add_argument(
        "--max-files",
        type=int,
        default=None,
        help=(
            "Cap the number of routed files reviewed in one run (default: no "
            "cap). Files matched by the most specialists (routing.py's own "
            "decision) are kept first; the rest are skipped entirely for this "
            "run, reported separately from findings, and picked up again "
            "whenever a later run's file set fits under the cap. For a "
            "pathologically wide diff (a huge rename, a generated-content "
            "commit) rather than for everyday use."
        ),
    )
    review.add_argument(
        "--baseline",
        default=None,
        metavar="PATH",
        help=(
            "Path to a baseline file used to classify each finding as NEW or "
            f"EXISTING by fingerprint (default when --update-baseline is given "
            f"without this flag: {baseline_mod.DEFAULT_BASELINE_PATH.as_posix()}). "
            "A normal review never writes to this file -- only --update-baseline "
            "does. Missing when explicitly given (and --update-baseline isn't "
            "also set), or malformed/an unsupported schema version: a clear "
            "error, not a silent empty baseline."
        ),
    )
    review.add_argument(
        "--update-baseline",
        action="store_true",
        help=(
            "Create or replace the baseline file (at --baseline, or the default "
            f"{baseline_mod.DEFAULT_BASELINE_PATH.as_posix()} if --baseline isn't "
            "given) with this run's findings, written atomically. Findings "
            f"suppressed by {SUPPRESSIONS_PATH.as_posix()} are still included, "
            "for auditability -- see baseline.py's module docstring."
        ),
    )
    review.add_argument(
        "--new-only",
        action="store_true",
        help=(
            "Only display/evaluate findings whose fingerprint is not already in "
            "the baseline (requires --baseline to have any effect; a harmless "
            'no-op without one, since every finding is then already "new").'
        ),
    )
    review.add_argument(
        "--fail-on",
        default=None,
        metavar="SEVERITY[,SEVERITY...]",
        help=(
            "Exit with status 1 if any finding selected for evaluation (after "
            "--baseline/--new-only filtering, if given) is at or above the "
            "least severe threshold in this comma-separated list -- e.g. "
            "'high' or 'critical,high'. Valid values: "
            f"{', '.join(s.lower() for s in _VALID_FAIL_ON_SEVERITIES)}. "
            "Independent of --fail-on-findings above."
        ),
    )
    output_format = review.add_mutually_exclusive_group()
    output_format.add_argument(
        "--json",
        action="store_true",
        help=(
            "Print findings (and the same warnings the default output shows) as "
            "JSON instead of human-readable text, for CI/CD pipelines to consume "
            "programmatically instead of parsing this tool's own text output."
        ),
    )
    output_format.add_argument(
        "--sarif",
        action="store_true",
        help=(
            "Print findings as a SARIF 2.1.0 log instead of human-readable text "
            "-- the format GitHub code scanning, Azure DevOps, and most CI "
            "security dashboards expect, for inline PR annotations and a "
            "persistent alerts list instead of a build-log-only report. "
            "Mutually exclusive with --json."
        ),
    )
    review.set_defaults(func=cmd_review)

    commit = subparsers.add_parser(
        _COMMIT_CMD,
        help=(
            "Generate a semantic commit message for the staged diff "
            "(never adds a co-author trailer)."
        ),
    )
    _add_common_args(commit)
    commit.set_defaults(func=cmd_commit)

    heal = subparsers.add_parser(
        _HEAL_CMD,
        help=(
            "Run the detected test suite and, on failure, propose a fix. "
            "Never writes without --apply."
        ),
    )
    _add_common_args(heal)
    heal.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Write the proposed patch to disk and re-run the tests. "
            "Without this flag, the patch is only shown."
        ),
    )
    heal.set_defaults(func=cmd_heal)

    return parser


def build_init_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-init",
        description=(
            "Scaffold a target repository with .agent-rules/, a gitignored "
            ".agent-cache/, and a starter DESIGN.md."
        ),
    )
    parser.add_argument("--path", default=".", help=_PATH_HELP)
    parser.set_defaults(func=cmd_init)
    return parser


def main(argv: list[str] | None = None) -> int:
    _load_dotenv_if_present()
    argv = list(sys.argv[1:] if argv is None else argv)
    # `agent-review --path X` (no subcommand) is the primary documented
    # usage, so an unrecognized/absent first token implies "review"
    # rather than requiring `agent-review review --path X` every time.
    if not argv or (argv[0] not in _KNOWN_COMMANDS and argv[0] not in ("-h", "--help")):
        argv = [_REVIEW_CMD, *argv]
    parser = build_review_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def main_init(argv: list[str] | None = None) -> int:
    _load_dotenv_if_present()
    parser = build_init_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
