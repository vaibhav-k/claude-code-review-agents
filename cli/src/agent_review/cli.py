"""
Console-script entry points.

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
import sys
from pathlib import Path

from . import git_utils
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
from .orchestrator import ReviewRun, run_review
from .prompts import load_agent_prompts

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
        from dotenv import find_dotenv, load_dotenv  # noqa: PLC0415 -- optional

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


def _print_review(run: ReviewRun) -> None:
    print(f"Base ref: {run.base_ref}")
    print(
        f"Files analyzed: {len(run.files)}  "
        f"(cache hits: {run.cache_hits}, cache misses: {run.cache_misses})"
    )
    failed = run.failed_files
    if failed:
        print(f"Failed to review {len(failed)} file(s) (not cached -- will retry next run):")
        for f in failed:
            print(f"  {f.path}: {f.error}")
    missing = sorted({(f.path, agent) for f in run.files for agent in f.missing_agents})
    if missing:
        print(
            f"Warning: {len(missing)} routed specialist(s) had no loaded prompt and were skipped:"
        )
        for path, agent in missing:
            print(f"  {path}: {agent}")
    print()
    if not run.all_findings:
        print("No high-impact issues found.")
        return
    for finding in run.all_findings:
        print(finding.render())
        print()


def cmd_review(args: argparse.Namespace) -> int:
    repo = _resolve_git_repo(args.path)
    prompts = load_agent_prompts(repo)
    cache = AgentCache(repo)
    reviewer = _build_reviewer(args)
    try:
        run = run_review(repo, args.base, prompts, cache, reviewer, max_workers=args.jobs)
    except Exception as exc:
        # Surface API/network failures cleanly, not as a raw traceback
        # from inside a worker thread.
        raise SystemExit(f"error: review failed: {exc}") from exc
    _print_review(run)
    if args.fail_on_findings:
        has_blocking = any(f.severity in ("CRITICAL", "HIGH") for f in run.all_findings)
        return 1 if has_blocking else 0
    return 0


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
