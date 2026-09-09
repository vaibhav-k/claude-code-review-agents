"""
Shared filesystem layout constants for the target-repo scaffolding that
`agent-init` (init.py) writes and `prompts.py` reads back -- kept in one
place so the two can never independently drift (e.g. init.py scaffolding
into a directory prompts.py no longer looks for, or vice versa).
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

RULES_DIRNAME = ".agent-rules"
CLAUDE_CODE_DIRNAME = ".claude"
AGENTS_SUBDIRNAME = "agents"
CLAUDE_MD_FILENAME = "CLAUDE.md"
DESIGN_MD_FILENAME = "DESIGN.md"


def bundled_default_dir() -> Path:
    """
    The default_rules/ directory bundled inside this package -- the
    fallback `prompts.py` uses when a target repo has neither
    .agent-rules/ nor .claude/ set up, and the same source `init_repo()`
    scaffolds a fresh .agent-rules/ from.
    """
    with resources.as_file(resources.files("agent_review") / "default_rules") as p:
        return p
