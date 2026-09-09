"""
Local, per-repo cache (`.agent-cache/`) that makes repeat runs
incremental instead of re-analyzing every file from scratch.

The cache lives inside the TARGET repository (not globally, and not only
in this process's memory), keyed by each file's git blob hash. If a file's
blob hash hasn't changed since the last run that covered it, and the set of
agents routed to it hasn't changed, its previous findings are reused
instead of spending a model call on it again.

This is deliberately a flat JSON file rather than a database: the cache is
small (one entry per reviewed file), human-inspectable, and needs no
dependency beyond the standard library.
"""

from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path
from typing import Any

CACHE_DIRNAME = ".agent-cache"
MANIFEST_FILENAME = "manifest.json"
CACHE_SCHEMA_VERSION = 1


@dataclasses.dataclass
class CachedFileEntry:
    blob_hash: str
    agents: list[str]
    findings_text: str
    reviewed_at: float


class AgentCache:
    """Read/write handle for one target repository's .agent-cache/."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.cache_dir = repo_root / CACHE_DIRNAME
        self.manifest_path = self.cache_dir / MANIFEST_FILENAME
        self._entries: dict[str, CachedFileEntry] = {}
        self._loaded = False

    # -- lifecycle -----------------------------------------------------

    def load(self) -> None:
        self._loaded = True
        if not self.manifest_path.exists():
            self._entries = {}
            return
        try:
            raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A corrupt or unreadable cache should never break a review --
            # treat it as empty and let the run repopulate it.
            self._entries = {}
            return
        if raw.get("schema_version") != CACHE_SCHEMA_VERSION:
            # Schema changed (or file predates versioning) -- start fresh
            # rather than trying to interpret an incompatible shape.
            self._entries = {}
            return
        entries: dict[str, CachedFileEntry] = {}
        for path, data in raw.get("files", {}).items():
            try:
                entries[path] = CachedFileEntry(
                    blob_hash=data["blob_hash"],
                    agents=list(data["agents"]),
                    findings_text=data["findings_text"],
                    reviewed_at=float(data["reviewed_at"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
        self._entries = entries

    def save(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "files": {path: dataclasses.asdict(entry) for path, entry in self._entries.items()},
        }
        tmp_path = self.manifest_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
        tmp_path.replace(self.manifest_path)  # atomic on POSIX and Windows

    # -- lookups ---------------------------------------------------------

    def get_if_fresh(self, path: str, blob_hash: str, agents: list[str]) -> str | None:
        """Return cached findings text for `path` if the cache has an
        entry for the exact same blob hash and the exact same set of
        routed agents. Otherwise None (cache miss -- must re-analyze).
        A changed agent set counts as a miss even with the same blob hash,
        since a different (or expanded) set of agents may find something
        the cached run never looked for.
        """
        if not self._loaded:
            self.load()
        entry = self._entries.get(path)
        if entry is None:
            return None
        if entry.blob_hash != blob_hash:
            return None
        if sorted(entry.agents) != sorted(agents):
            return None
        return entry.findings_text

    def put(self, path: str, blob_hash: str, agents: list[str], findings_text: str) -> None:
        if not self._loaded:
            self.load()
        self._entries[path] = CachedFileEntry(
            blob_hash=blob_hash,
            agents=list(agents),
            findings_text=findings_text,
            reviewed_at=time.time(),
        )

    def prune_missing(self, still_present_paths: set[str]) -> None:
        """Drop cache entries for files no longer in the diff, so the
        manifest doesn't grow forever with stale paths."""
        if not self._loaded:
            self.load()
        self._entries = {
            path: entry for path, entry in self._entries.items() if path in still_present_paths
        }


def ensure_gitignored(repo_root: Path) -> bool:
    """Make sure `.agent-cache/` is listed in the target repo's
    .gitignore. Returns True if it added a new entry, False if one was
    already present (or .gitignore didn't need touching).
    """
    gitignore_path = repo_root / ".gitignore"
    entry = f"{CACHE_DIRNAME}/"
    existing = ""
    if gitignore_path.exists():
        existing = gitignore_path.read_text(encoding="utf-8", errors="replace")
        if entry in existing.splitlines():
            return False
    with gitignore_path.open("a", encoding="utf-8") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(f"{entry}\n")
    return True
