"""
agent_review: run the claude-code-review-agents specialists against any
local git repository, from the command line, independent of Claude Code.

This package is a companion to the .claude/agents/ prompts in the parent
repository, not a replacement for them: those files still work directly
inside Claude Code / VS Code. This CLI exists for running the same review
logic against a repo where Claude Code itself isn't driving the session --
CI, a pre-commit hook, or a plain terminal.
"""

from importlib import metadata

try:
    # "agent-review" -- the [project.name] in pyproject.toml, not this
    # importable module's name ("agent_review") -- importlib.metadata
    # looks up installed *distributions*, which are keyed by the former.
    __version__ = metadata.version("agent-review")
except metadata.PackageNotFoundError:
    # Only hit when this package is imported without ever having been
    # installed (e.g. running straight from a source checkout with src/
    # manually added to sys.path, as the test suite's conftest does) --
    # never leave __version__ silently missing in that case.
    __version__ = "0.0.0+unknown"
