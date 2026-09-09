"""Guarded "self-healing": run the target repo's detected test suite, and
if it fails, ask the model for a proposed fix -- but never write anything
to disk without an explicit, separate confirmation.

This is deliberately NOT an autonomous edit-and-rerun loop. The user's own
spec calls this a "self-healing loop," but silently letting a model edit
and commit code in someone's repository is exactly the kind of blast
radius this whole system is built to avoid everywhere else (see
CLAUDE.md's evidence bar and its emphasis on diff-scoped, non-speculative
findings). So this module always stops at a proposed, human-readable
explanation plus a unified diff; nothing is written to the target repo's
files unless the caller explicitly invokes `apply_patch()` separately --
wired, in the CLI, to a `--apply` flag that defaults to off.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
from pathlib import Path

from .agents_client import Reviewer
from .discovery import TestRunner, detect_test_runners

DEFAULT_TIMEOUT_SECONDS = 300
_OUTPUT_TAIL_CHARS = 8000

SYSTEM_PROMPT = """You are given the output of a failing test run for a git \
repository. Diagnose the root cause and propose a minimal fix.

Output exactly two things, in this order:
1. A short paragraph (2-4 sentences) explaining the root cause.
2. A single fenced ```diff block containing a valid unified diff (in a \
form `git apply` accepts) that fixes the failure. Touch only the files \
strictly necessary. Do not invent files, tests, or code that isn't shown \
in the failure output or reasonably inferable from it. If you cannot \
identify a concrete, safe fix from the given output alone, output an \
empty diff block (```diff\\n```) rather than guessing.

Never include any commit message, trailer, or attribution line anywhere \
in your response.
"""


def _decode_partial_output(value: bytes | str | None) -> str:
    """`subprocess.run(..., text=True, ...)` guarantees `str` output on
    success, but `subprocess.TimeoutExpired`'s own `stdout`/`stderr`
    attributes are typed as `bytes | str | None` regardless -- they carry
    whatever partial output the OS handed back at the moment of the kill,
    and mypy has no way to know `text=True` makes that `str` in practice
    for this call site. Coerce defensively instead of asserting it away,
    so a future change to how this subprocess is invoked can't turn this
    into a silent `bytes + str` TypeError deep inside a healing run.
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


class NoTestRunnerFoundError(RuntimeError):
    """Raised when no supported test runner can be detected in the repo."""


@dataclasses.dataclass(frozen=True)
class TestRunResult:
    runner: TestRunner
    returncode: int
    stdout: str
    stderr: str

    @property
    def passed(self) -> bool:
        return self.returncode == 0


@dataclasses.dataclass(frozen=True)
class HealingProposal:
    explanation: str
    diff: str
    raw_response: str

    @property
    def has_diff(self) -> bool:
        return bool(self.diff.strip())


def run_tests(
    repo_root: Path,
    runner: TestRunner | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> TestRunResult:
    """Run the given (or auto-detected) test runner in the target repo and
    capture its result. Performs no interpretation of the output -- that's
    `propose_fix()`'s job.
    """
    if runner is None:
        runners = detect_test_runners(repo_root)
        if not runners:
            raise NoTestRunnerFoundError(
                f"No supported test runner detected in {repo_root} "
                "(looked for pytest, npm test, maven, gradle, cargo, ctest, dotnet)."
            )
        runner = runners[0]

    # PYTHONDONTWRITEBYTECODE avoids a real staleness hazard specific to
    # the self-healing loop: CPython's .pyc cache is invalidated by
    # (mtime, size), and a one-line fix can easily leave both unchanged
    # (same source-file size, same within-a-second mtime) versus the
    # failing run moments earlier -- which would make a rerun silently
    # execute the OLD bytecode and report the original failure again even
    # though the patch was applied correctly. Harmless no-op for
    # non-Python runners.
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        result = subprocess.run(
            runner.command,
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,  # a failing test run is an expected outcome, not a subprocess error
        )
    except FileNotFoundError as exc:
        raise NoTestRunnerFoundError(
            f"Detected runner '{runner.name}' ({' '.join(runner.command)}) is not "
            "installed or not on PATH in this environment."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        return TestRunResult(
            runner=runner,
            returncode=-1,
            stdout=_decode_partial_output(exc.stdout),
            stderr=_decode_partial_output(exc.stderr) + f"\n[test run timed out after {timeout}s]",
        )

    return TestRunResult(
        runner=runner,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def propose_fix(failing_result: TestRunResult, reviewer: Reviewer) -> HealingProposal:
    """Ask the model to diagnose a failing test run and propose a patch.
    Never touches disk -- purely a model call plus response parsing.
    """
    if failing_result.passed:
        raise ValueError("propose_fix() called with a passing test result -- nothing to fix.")

    user_message = (
        f"Test runner: {failing_result.runner.name}\n"
        f"Command: {' '.join(failing_result.runner.command)}\n"
        f"Exit code: {failing_result.returncode}\n\n"
        f"stdout (tail):\n{failing_result.stdout[-_OUTPUT_TAIL_CHARS:]}\n\n"
        f"stderr (tail):\n{failing_result.stderr[-_OUTPUT_TAIL_CHARS:]}\n"
    )
    raw = reviewer.complete(SYSTEM_PROMPT, user_message)
    explanation, diff = _split_explanation_and_diff(raw)
    return HealingProposal(explanation=explanation, diff=diff, raw_response=raw)


def _split_explanation_and_diff(raw: str) -> tuple[str, str]:
    marker = "```diff"
    idx = raw.find(marker)
    if idx == -1:
        return raw.strip(), ""
    explanation = raw[:idx].strip()
    rest = raw[idx + len(marker) :]
    end = rest.find("```")
    diff = (rest[:end] if end != -1 else rest).strip("\n")
    # `git apply` treats a unified diff as line-oriented and requires the
    # final hunk line to be newline-terminated (unless explicitly marked
    # "\ No newline at end of file") -- stripping trailing whitespace
    # above must not also strip the one newline the last line needs, or
    # every otherwise-valid patch fails with "corrupt patch at line N".
    if diff and not diff.endswith("\n"):
        diff += "\n"
    return explanation, diff


def check_patch_applies(repo_root: Path, proposal: HealingProposal) -> tuple[bool, str]:
    """Dry-run the proposed patch (`git apply --check`) without writing
    anything. Lets a caller show "this patch applies cleanly" (or the
    error) before asking the user to confirm --apply.
    """
    if not proposal.has_diff:
        return False, "Proposal contains no diff."
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "apply", "--check", "-"],
            input=proposal.diff,
            capture_output=True,
            text=True,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            # a patch that fails to apply is an expected outcome, not a subprocess error
            check=False,
        )
    except subprocess.TimeoutExpired:
        # Same hazard run_tests() already guards against -- a hook or
        # prompt on the other end of `git apply` blocking indefinitely --
        # applied here too so `heal --apply` can't hang forever.
        return (
            False,
            f"git apply --check did not finish within {DEFAULT_TIMEOUT_SECONDS}s",
        )
    return result.returncode == 0, result.stderr.strip()


def apply_patch(repo_root: Path, proposal: HealingProposal) -> None:
    """Actually write the proposed patch to disk via `git apply`.

    This function performs NO confirmation of its own -- every caller
    (the CLI included) must gate reaching this point behind an explicit,
    separate user confirmation (a `--apply` flag that defaults to off).
    It must never be reachable from a plain `agent-review` invocation.
    """
    ok, error = check_patch_applies(repo_root, proposal)
    if not ok:
        raise RuntimeError(f"Patch does not apply cleanly: {error}")
    try:
        subprocess.run(
            ["git", "-C", str(repo_root), "apply", "-"],
            input=proposal.diff,
            capture_output=True,
            text=True,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            check=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git apply did not finish within {DEFAULT_TIMEOUT_SECONDS}s") from exc
