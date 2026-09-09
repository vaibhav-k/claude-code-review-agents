"""
Git plumbing for a target repository given by path.

Every function here takes the target repo's root as an explicit argument
and calls out to the user's own `git` binary via subprocess -- there is no
assumption that the target repo is the current working directory, so this
module is what lets the CLI point at *any* repo on disk.
"""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

# Every git subprocess call in this module is bounded by this timeout, and
# never inherits the caller's stdin (subprocess.DEVNULL) -- without both, a
# blocked git process (a credential prompt, an LFS/textconv smudge filter,
# a hook waiting on input) hangs forever, and since orchestrator.py runs
# these calls inside a ThreadPoolExecutor, one stuck file review would
# otherwise hang the entire run with no way to recover.
DEFAULT_GIT_TIMEOUT_SECONDS = 30

# Empty-tree object hash: diffing against this shows the whole repo as
# "added," which is the correct behavior for a brand-new repository with
# no other resolvable base ref. Named so tests can assert against it
# directly instead of only checking truthiness.
EMPTY_TREE_SENTINEL = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class GitError(RuntimeError):
    """Raised when a git subprocess call fails."""


@dataclasses.dataclass(frozen=True)
class ChangedFile:
    path: str
    status: str  # 'A' added, 'M' modified, 'D' deleted, 'R' renamed, etc.


def _run(repo: Path, *args: str, timeout: int = DEFAULT_GIT_TIMEOUT_SECONDS) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise GitError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(
            f"git {' '.join(args)} in {repo} did not finish within {timeout}s -- "
            "a hook, credential prompt, or LFS/textconv filter may be blocking it"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise GitError(f"git {' '.join(args)} failed in {repo}: {exc.stderr.strip()}") from exc
    return result.stdout


def is_git_repo(repo: Path) -> bool:
    try:
        _run(repo, "rev-parse", "--is-inside-work-tree")
    except GitError:
        return False
    return True


def resolve_base_ref(repo: Path, requested: str | None) -> str:
    """Pick a sensible base ref to diff against.

    Honors an explicit `requested` ref if it resolves. Otherwise tries
    `origin/main`, then `origin/master`, then falls back to the repo's
    first commit (diff against an empty tree) so a fresh repo with no
    remote still works.
    """
    candidates = [requested] if requested else []
    candidates += ["origin/main", "origin/master", "main", "master"]
    for ref in candidates:
        if not ref:
            continue
        try:
            _run(repo, "rev-parse", "--verify", ref)
            return ref
        except GitError:
            continue
    return EMPTY_TREE_SENTINEL


def changed_files(repo: Path, base_ref: str) -> list[ChangedFile]:
    """Files that differ between base_ref and the current working tree,
    including uncommitted changes (staged and unstaged) *and* untracked
    files git doesn't know about yet.

    `git diff --name-status` alone only reports paths git already tracks
    at some point in its history -- a brand-new file the user just wrote
    and hasn't `git add`ed yet is invisible to it. Since a review tool's
    whole point is to catch problems in code someone is about to commit,
    a not-yet-staged new file is exactly the case that must not be
    silently skipped, so it's folded in here as an 'A' (added) entry.
    """
    output = _run(repo, "diff", "--name-status", base_ref, "--")
    files: dict[str, ChangedFile] = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status, *paths = parts
        # Renames report as "R100\told\tnew" -- take the new path.
        path = paths[-1]
        files[path] = ChangedFile(path=path, status=status[0])

    untracked = _run(repo, "ls-files", "--others", "--exclude-standard")
    for path in untracked.splitlines():
        if path.strip():
            files[path] = ChangedFile(path=path, status="A")

    return list(files.values())


def diff_for_file(repo: Path, base_ref: str, path: str) -> str:
    """Unified diff for a single file, base_ref..working tree.

    Untracked files aren't reachable through `git diff <ref> -- <path>`
    (git has no record of them at any ref), so those are instead diffed
    against `/dev/null` with `--no-index`, which produces a normal unified
    diff showing the whole file as added -- the correct representation for
    "this file is new."
    """
    full_path = repo / path
    is_tracked = True
    try:
        _run(repo, "ls-files", "--error-unmatch", path)
    except GitError:
        is_tracked = False

    if is_tracked or not full_path.exists():
        return _run(repo, "diff", base_ref, "--", path)

    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "diff",
                "--no-index",
                "--",
                "/dev/null",
                str(full_path),
            ],
            capture_output=True,
            text=True,
            timeout=DEFAULT_GIT_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            # --no-index exits 1 when there IS a difference -- expected, not an error.
            check=False,
        )
        return result.stdout
    except FileNotFoundError as exc:
        raise GitError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(
            f"git diff --no-index in {repo} did not finish within {DEFAULT_GIT_TIMEOUT_SECONDS}s"
        ) from exc


def staged_diff(repo: Path) -> str:
    return _run(repo, "diff", "--staged")


def blob_hash(repo: Path, path: str) -> str | None:
    """Git's own content hash for the current working-tree version of
    `path`. Returns None if the file doesn't exist (e.g. it was deleted).
    Used by the cache to detect "this file is unchanged since we last
    analyzed it" without re-reading or re-diffing its content.
    """
    full = repo / path
    if not full.exists():
        return None
    try:
        return _run(repo, "hash-object", str(full)).strip()
    except GitError:
        return None


def blob_hashes(repo: Path, paths: list[str]) -> dict[str, str | None]:
    """Batched form of blob_hash(): one `git hash-object` call per chunk
    of paths (chunked to stay comfortably under OS command-line length
    limits) instead of one subprocess per file. Used by run_review() to
    hash every routed file for a run in a handful of calls rather than
    one-per-file -- this matters once the routed set gets large (e.g. a
    fresh-repo/agent-init scan, which diffs against the empty-tree
    sentinel and so treats every tracked file in the repo as "added").

    A path that doesn't exist on disk maps to None, same as blob_hash().
    If a chunk's `git hash-object` call itself fails (a permissions issue,
    a file removed in the moment between listing and hashing), that
    chunk's paths are left mapped to None rather than aborting the whole
    batch -- mirroring blob_hash()'s own graceful degrade-to-None
    behavior for a single path.
    """
    result: dict[str, str | None] = dict.fromkeys(paths, None)
    existing = [p for p in paths if (repo / p).exists()]
    chunk_size = 200
    for i in range(0, len(existing), chunk_size):
        chunk = existing[i : i + chunk_size]
        try:
            output = _run(repo, "hash-object", *[str(repo / p) for p in chunk])
        except GitError:
            continue
        hashes = output.strip().splitlines()
        for path, blob in zip(chunk, hashes, strict=False):
            result[path] = blob
    return result


def current_commit(repo: Path) -> str:
    return _run(repo, "rev-parse", "HEAD").strip()
