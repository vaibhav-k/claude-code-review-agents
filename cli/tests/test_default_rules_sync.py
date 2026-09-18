"""Guards against the exact bug found 2026-09-18: the CLI's bundled
`default_rules/` -- the prompts a target repo gets when it has neither
`.agent-rules/` nor `.claude/` of its own (see prompts.py's lookup order)
-- had silently drifted from this repo's own root `CLAUDE.md` and
`.claude/agents/*.md`, missing every round of live-verified fixes made
across this whole project's six-round `--live` tuning history (see
DESIGN.md's real `--live` run write-ups). All 7 agent files and CLAUDE.md
itself had drifted -- discovered only because
cli/tests/cassettes/integration.json's live recording came back wrapped in
a markdown code fence, which the CURRENT root CLAUDE.md explicitly
forbids but the stale bundled copy never mentioned.

`default_rules/` is documented (prompts.py's own module docstring) as "a
snapshot of this project's own agents at the time this CLI was built" --
a deliberate point-in-time copy, not a symlink -- so drift after an edit
is expected until the next sync. What was missing is anything that makes
an un-synced edit *visible* rather than silently shipping to every real
CLI user whose target repo has no `.claude/`/`.agent-rules/` override of
its own. This test is that visibility: it fails loudly, naming the exact
file, the moment a root prompt changes without its bundled copy following.

If this test fails after an intentional prompt edit, the fix is to copy
the changed file(s) from the root/`.claude/agents/` location into
`cli/src/agent_review/default_rules/` (see the sync loop this test's own
setup mirrors) and, if `cli/tests/cassettes/integration.json` covers the
changed agent, re-record it with `AGENT_REVIEW_RECORD_LIVE=1 pytest
cli/tests/test_cli_integration_live.py` before committing -- a stale
bundled prompt and a cassette recorded against a stale bundled prompt are
the same class of staleness this whole test file exists to catch.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review.layout import bundled_default_dir

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_bundled_claude_md_matches_repo_root():
    root = REPO_ROOT / "CLAUDE.md"
    bundled = bundled_default_dir() / "CLAUDE.md"
    root_text = root.read_text(encoding="utf-8")
    bundled_text = bundled.read_text(encoding="utf-8")
    assert bundled_text == root_text, (
        f"{bundled} has drifted from {root} -- copy the root file over the "
        "bundled one (see this test file's module docstring) before committing."
    )


def test_bundled_agent_prompts_match_dot_claude_agents():
    # triage-router.md is deliberately excluded: routing.py is a direct,
    # zero-cost code port of its rule table (see cli/README.md's "How it
    # works"), so the CLI never loads it as a system prompt and ships no
    # bundled copy of it -- see test_default_rules_encoding.py's own
    # `== 8` (CLAUDE.md + 7 specialists) file-count assumption, which this
    # test's file-count check mirrors for the same reason.
    root_agents_dir = REPO_ROOT / ".claude" / "agents"
    bundled_agents_dir = bundled_default_dir() / "agents"

    root_files = sorted(p for p in root_agents_dir.glob("*.md") if p.name != "triage-router.md")
    assert len(root_files) == 7, (
        f"expected 7 specialist prompts under {root_agents_dir}, found "
        f"{len(root_files)} -- update this test if a specialist was added "
        "or removed"
    )

    for root_path in root_files:
        bundled_path = bundled_agents_dir / root_path.name
        assert bundled_path.exists(), (
            f"{bundled_path} does not exist -- every specialist under "
            f"{root_agents_dir} needs a bundled counterpart"
        )
        root_text = root_path.read_text(encoding="utf-8")
        bundled_text = bundled_path.read_text(encoding="utf-8")
        assert bundled_text == root_text, (
            f"{bundled_path} has drifted from {root_path} -- copy the root "
            "file over the bundled one (see this test file's module "
            "docstring) before committing."
        )
