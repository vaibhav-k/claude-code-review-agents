"""
Loads agent system prompts and shared rules, dynamically locating them
inside the TARGET repository first, falling back to the copies bundled
with this package.

Lookup order (first one found wins, per artifact):

1. ``<target_repo>/.agent-rules/`` -- the layout `agent-init` scaffolds.
2. ``<target_repo>/.claude/`` -- a repo that already carries the Claude
   Code-native agent definitions (this project's own repo, for instance)
   is used as-is, so teams don't have to maintain two copies.
3. The defaults bundled inside this package (``default_rules/``), a
   snapshot of this project's own agents at the time this CLI was built.

This means a target repo can override any single agent's prompt (or the
shared CLAUDE.md rules) just by having its own copy on disk in either
recognized location -- nothing needs to be reinstalled or reconfigured.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from .layout import (
    AGENTS_SUBDIRNAME,
    CLAUDE_CODE_DIRNAME,
    CLAUDE_MD_FILENAME,
    RULES_DIRNAME,
    bundled_default_dir,
)

FRONTMATTER_DELIM = "---"


@dataclasses.dataclass(frozen=True)
class AgentPrompt:
    name: str
    description: str
    system_prompt: str  # shared_rules + this agent's own body, combined


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Very small YAML-frontmatter reader: good enough for the flat
    `key: value` fields these agent files use (name, description, model,
    color) -- deliberately not a general YAML parser, since the only
    fields this CLI reads are simple scalars.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != FRONTMATTER_DELIM:
        return {}, text
    try:
        end_index = lines[1:].index(FRONTMATTER_DELIM) + 1
    except ValueError:
        return {}, text
    header_lines = lines[1:end_index]
    body = "\n".join(lines[end_index + 1 :]).lstrip("\n")
    fields: dict[str, str] = {}
    current_key: str | None = None
    for line in header_lines:
        if line.startswith(("  -", "\t-")):
            continue  # list item (e.g. under `tools:`) -- not needed here
        if ":" in line and not line.startswith((" ", "\t")):
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            fields[key] = value
            current_key = key
        elif current_key:
            fields[current_key] += " " + line.strip()
    return fields, body


def _agent_from_file(path: Path, shared_rules: str) -> AgentPrompt:
    text = path.read_text(encoding="utf-8")
    fields, body = _split_frontmatter(text)
    name = fields.get("name", path.stem)
    description = fields.get("description", "")
    system_prompt = f"{shared_rules}\n\n---\n\n{body}" if shared_rules else body
    return AgentPrompt(name=name, description=description, system_prompt=system_prompt)


def _find_rules_root(repo_root: Path) -> tuple[Path, Path] | None:
    """Returns (claude_md_path, agents_dir_path) for the first location
    that actually has both pieces, or None if the repo has neither
    .agent-rules/ nor .claude/ set up (caller should fall back to
    bundled defaults in that case).
    """
    candidates = [
        (
            repo_root / RULES_DIRNAME / CLAUDE_MD_FILENAME,
            repo_root / RULES_DIRNAME / AGENTS_SUBDIRNAME,
        ),
        (
            repo_root / CLAUDE_MD_FILENAME,
            repo_root / CLAUDE_CODE_DIRNAME / AGENTS_SUBDIRNAME,
        ),
    ]
    for claude_md, agents_dir in candidates:
        if claude_md.exists() and agents_dir.is_dir():
            return claude_md, agents_dir
    return None


def load_agent_prompts(repo_root: Path) -> dict[str, AgentPrompt]:
    found = _find_rules_root(repo_root)
    if found is not None:
        claude_md_path, agents_dir = found
        shared_rules = claude_md_path.read_text(encoding="utf-8")
        agent_files = sorted(agents_dir.glob("*.md"))
    else:
        default_dir = bundled_default_dir()
        shared_rules = (default_dir / CLAUDE_MD_FILENAME).read_text(encoding="utf-8")
        agent_files = sorted((default_dir / AGENTS_SUBDIRNAME).glob("*.md"))

    prompts: dict[str, AgentPrompt] = {}
    for path in agent_files:
        if path.stem == "triage-router":
            continue  # routing is done in routing.py, not via a model call
        agent = _agent_from_file(path, shared_rules)
        prompts[agent.name] = agent
    return prompts
