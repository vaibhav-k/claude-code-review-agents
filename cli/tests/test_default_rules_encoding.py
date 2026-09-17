"""Guards against the exact class of bug found 2026-09-17: a real user's
Windows machine had a mojibake'd `default_rules/CLAUDE.md` -- an em dash
that had been round-tripped through a `write_text()`/similar call with no
explicit encoding, silently falling back to a non-UTF-8 platform default
(cp1252) somewhere upstream of this checkout, turning one multi-byte UTF-8
character into an invalid single byte. `shutil.copyfile` (what
`init_repo()` actually uses to scaffold a target repo) never re-encodes
anything, so it can't introduce this on its own -- but it also can't
detect an already-corrupt source, and every other production write in
this codebase already specifies encoding="utf-8" explicitly, so this test
exists purely as an early warning if that ever regresses, or if a bundled
prompt file gets corrupted by some future tooling change before it's ever
shipped to a user.

Deliberately checks the actual files on disk -- both the ones bundled
inside the `cli/` package (what `agent-init` scaffolds from) and the
Claude-Code-native ones this same repo carries at its root (`CLAUDE.md`,
`.claude/agents/*.md`) -- not just that Python *can* decode some string
in memory.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review.layout import bundled_default_dir

REPO_ROOT = Path(__file__).resolve().parents[2]


def _assert_valid_utf8(path: Path) -> None:
    try:
        path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AssertionError(
            f"{path} is not valid UTF-8 ({exc}) -- likely round-tripped through "
            "a write without an explicit encoding on a non-UTF-8-default platform"
        ) from exc


def test_bundled_default_rules_are_valid_utf8():
    default_dir = bundled_default_dir()
    md_files = [
        default_dir / "CLAUDE.md",
        *sorted((default_dir / "agents").glob("*.md")),
    ]
    assert len(md_files) == 8  # CLAUDE.md + 7 specialists (no triage-router prompt file)
    for path in md_files:
        _assert_valid_utf8(path)


def test_repo_root_claude_code_native_files_are_valid_utf8():
    md_files = [
        REPO_ROOT / "CLAUDE.md",
        *sorted((REPO_ROOT / ".claude" / "agents").glob("*.md")),
    ]
    assert len(md_files) >= 8
    for path in md_files:
        _assert_valid_utf8(path)
