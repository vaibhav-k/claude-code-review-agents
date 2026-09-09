"""
``agent-init``: scaffold a target repository for use with agent-review.

Injects three things into the target repo, all idempotently (re-running
this is always safe -- it never overwrites a file that's already there):

1. ``.agent-rules/`` -- an editable copy of the shared CLAUDE.md rules and
   the 7 specialist agent prompts, seeded from this package's bundled
   defaults (see ``default_rules/``). A team can hand-edit these to tune
   agent behavior for their repo without touching this package at all --
   ``prompts.py`` looks here first, before falling back to the bundled
   copies.
2. ``.agent-cache/`` -- ensured present in the target repo's
   ``.gitignore``. The directory itself is created lazily by
   ``AgentCache.save()`` on the first review run; there's nothing
   meaningful to write into it upfront, so init's job is just making sure
   it never gets accidentally committed.
3. A starter ``DESIGN.md`` at the target repo's root, if one doesn't
   already exist -- a place for the team to record architecture
   decisions, invariants, and tradeoffs the review agents should treat as
   background context.
"""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path

from .cache import ensure_gitignored
from .layout import (
    AGENTS_SUBDIRNAME,
    CLAUDE_MD_FILENAME,
    DESIGN_MD_FILENAME,
    RULES_DIRNAME,
    bundled_default_dir,
)

STARTER_DESIGN_MD = """# Design Notes

This file gives the automated review agents (and human reviewers) background
context that isn't visible from a diff alone. Keep it short and current --
its purpose is to prevent false-positive findings that stem from not knowing
*why* something is built the way it is, not to be exhaustive documentation.

## Architecture overview

<!-- One or two paragraphs: major components and how they fit together. -->

## Key invariants

<!-- Things that must always hold (e.g. "all money values are stored as
integer cents", "writes to the ledger table only ever happen inside
`with_transaction()`"). Agents treat a diff that appears to violate one of
these as a strong signal, not a false positive. -->

## Known tradeoffs / accepted risks

<!-- Deliberate decisions that might otherwise look like defects (e.g. "no
retry on this call by design, the caller already retries"). Listing these
here prevents agents from re-flagging them on every review. -->
"""


@dataclasses.dataclass(frozen=True)
class InitResult:
    rules_dir: Path
    created_files: list[str]
    skipped_files: list[str]
    gitignore_updated: bool

    @property
    def already_initialized(self) -> bool:
        return not self.created_files and not self.gitignore_updated


def init_repo(repo_root: Path) -> InitResult:
    created: list[str] = []
    skipped: list[str] = []

    rules_dir = repo_root / RULES_DIRNAME
    agents_dir = rules_dir / AGENTS_SUBDIRNAME
    agents_dir.mkdir(parents=True, exist_ok=True)

    default_dir = bundled_default_dir()

    claude_md_dest = rules_dir / CLAUDE_MD_FILENAME
    if claude_md_dest.exists():
        skipped.append(str(claude_md_dest.relative_to(repo_root)))
    else:
        shutil.copyfile(default_dir / CLAUDE_MD_FILENAME, claude_md_dest)
        created.append(str(claude_md_dest.relative_to(repo_root)))

    for agent_file in sorted((default_dir / AGENTS_SUBDIRNAME).glob("*.md")):
        dest = agents_dir / agent_file.name
        rel = str(dest.relative_to(repo_root))
        if dest.exists():
            skipped.append(rel)
            continue
        shutil.copyfile(agent_file, dest)
        created.append(rel)

    design_md = repo_root / DESIGN_MD_FILENAME
    if design_md.exists():
        skipped.append(DESIGN_MD_FILENAME)
    else:
        design_md.write_text(STARTER_DESIGN_MD, encoding="utf-8")
        created.append(DESIGN_MD_FILENAME)

    gitignore_updated = ensure_gitignored(repo_root)

    return InitResult(
        rules_dir=rules_dir,
        created_files=created,
        skipped_files=skipped,
        gitignore_updated=gitignore_updated,
    )
