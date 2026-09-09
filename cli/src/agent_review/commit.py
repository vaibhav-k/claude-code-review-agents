"""
Semantic commit message generation for the TARGET repository's staged
diff.

This is deliberately unrelated to any attribution policy Claude itself
follows when authoring commits in *this* project's own repository. Here,
the CLI is generating a message on behalf of the user, describing the
user's own staged changes, for the user's own commit in their own target
repo -- so per the user's own spec ("semantic commit generation... without
co-author trailers"), no trailer of any kind is added, and `_sanitize()`
actively strips one out if a model ever adds one despite instructions.
"""

from __future__ import annotations

from pathlib import Path

from . import git_utils
from .agents_client import Reviewer

SYSTEM_PROMPT = """You write a single, concise, conventional-commit-style \
commit message summarizing a git diff of STAGED changes.

Rules:
- Output ONLY the commit message text. No preamble, no explanation, no \
markdown code fences, and no trailers of any kind -- specifically, never \
include a "Co-Authored-By", "Signed-off-by", or "Claude-Session" line.
- First line: `<type>(<optional scope>): <summary>`, imperative mood, at \
most 72 characters. Types: feat, fix, refactor, perf, test, docs, chore, \
build, ci.
- If the diff is small enough to describe in one line, output only that \
line and nothing else.
- Only if genuinely necessary, add one blank line then up to 3 short \
bullet points of body detail covering non-obvious rationale -- never a \
line-by-line restatement of the diff.
"""

_TRAILER_PREFIXES = ("co-authored-by", "signed-off-by", "claude-session")


class NoStagedChangesError(RuntimeError):
    """Raised when there is nothing staged to summarize."""


def generate_commit_message(repo_root: Path, reviewer: Reviewer) -> str:
    diff_text = git_utils.staged_diff(repo_root)
    if not diff_text.strip():
        raise NoStagedChangesError(
            "No staged changes to summarize (`git diff --staged` is empty). "
            "Stage the changes you want summarized with `git add` first."
        )
    user_message = f"Staged diff:\n```diff\n{diff_text}\n```"
    raw = reviewer.complete(SYSTEM_PROMPT, user_message)
    return _sanitize(raw)


def _sanitize(text: str) -> str:
    """
    Belt-and-suspenders cleanup: strip code fences and any trailer-like
    line the model might add despite instructions. This is the one
    feature in the whole system that must never emit attribution
    trailers, so it doesn't rely on prompt compliance alone.
    """
    lines = [line for line in text.strip().splitlines() if not line.strip().startswith("```")]
    cleaned = [line for line in lines if not line.strip().lower().startswith(_TRAILER_PREFIXES)]
    # Drop trailing blank lines left behind by a stripped trailer block.
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    return "\n".join(cleaned).strip()
