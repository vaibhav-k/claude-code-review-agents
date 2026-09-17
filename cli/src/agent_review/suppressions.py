"""
Feedback loop: lets a team mark a specialist's finding as an accepted
false positive or accepted risk, so `agent-review` stops re-surfacing it
on every run without silencing that specialist for the whole file.

Suppressions are declared in an optional `.claude/ignore-findings.yml` at
the TARGET repo root (mirroring `.gitignore`'s own discoverability and
location convention -- see SUPPRESSIONS_PATH). Each entry matches on the
reviewing agent's name (or `"*"` for any agent) and a glob pattern against
`Finding.location` (`file:line`), deliberately NOT an exact line number:
line numbers drift with every unrelated edit to the file, so an
exact-line suppression would silently stop matching -- and thus silently
stop suppressing -- on the very next unrelated commit.

Suppression is applied in orchestrator.run_review() AFTER parsing, never
before caching: the cache still stores the raw, unsuppressed finding, so
editing this file to add, remove, or fix a suppression takes effect
immediately on the very next run without invalidating a single cache
entry or spending a fresh model call.

Example `.claude/ignore-findings.yml`:

    suppressions:
      - agent: security-review
        location: "src/handlers.py:*"
        reason: "input is validated upstream in middleware.py (see PR #42)"
      - agent: "*"
        location: "generated/*"
        reason: "machine-written tree, reviewed once as a batch"
"""

from __future__ import annotations

import dataclasses
import fnmatch
import time
from pathlib import Path

from . import findings as findings_mod
from .cache import CACHE_DIRNAME

SUPPRESSIONS_PATH = Path(".claude") / "ignore-findings.yml"
SUPPRESSIONS_LOG_PATH = Path(CACHE_DIRNAME) / "suppressions.log"
_AGENT_WILDCARD = "*"


@dataclasses.dataclass(frozen=True)
class Suppression:
    agent: str  # the exact routed agent name, or "*" to match any agent
    location_pattern: str  # fnmatch glob tested against Finding.location
    reason: str


@dataclasses.dataclass(frozen=True)
class SuppressedFinding:
    path: str
    finding: findings_mod.Finding
    reason: str


def _coerce_entry(raw: object) -> Suppression | None:
    """
    Validates one parsed YAML entry defensively. A hand-edited config
    file with a typo'd or missing field must never crash the whole review
    run (same "malformed input degrades to being ignored, not a crash"
    contract as cache.py's corrupt-manifest handling) -- it just isn't
    applied as a suppression.
    """
    if not isinstance(raw, dict):
        return None
    agent = raw.get("agent")
    location = raw.get("location")
    reason = raw.get("reason")
    if not isinstance(agent, str) or not agent.strip():
        return None
    if not isinstance(location, str) or not location.strip():
        return None
    if not isinstance(reason, str) or not reason.strip():
        return None
    return Suppression(
        agent=agent.strip(), location_pattern=location.strip(), reason=reason.strip()
    )


def load_suppressions(repo_root: Path) -> list[Suppression]:
    """
    Returns [] -- not an error -- when the file doesn't exist, PyYAML
    isn't installed, or the file fails to parse. Most repos won't have a
    suppressions file at all, and that must cost nothing and change
    nothing about how they're reviewed.
    """
    path = repo_root / SUPPRESSIONS_PATH
    if not path.is_file():
        return []
    try:
        import yaml  # noqa: PLC0415 -- deferred so importing this module

        # never requires PyYAML for the (common) case of a repo with no
        # suppressions file at all.
    except ImportError:
        return []
    try:
        raw_text = path.read_text(encoding="utf-8")
        parsed = yaml.safe_load(raw_text)
    except (OSError, yaml.YAMLError):
        # A malformed suppressions file must never break a review run --
        # same philosophy as cache.py treating a corrupt manifest as
        # empty rather than raising.
        return []
    if not isinstance(parsed, dict):
        return []
    raw_entries = parsed.get("suppressions")
    if not isinstance(raw_entries, list):
        return []
    return [entry for raw_entry in raw_entries if (entry := _coerce_entry(raw_entry)) is not None]


def _matches(finding: findings_mod.Finding, suppression: Suppression) -> bool:
    if suppression.agent != _AGENT_WILDCARD and suppression.agent != finding.agent:
        return False
    return fnmatch.fnmatch(finding.location, suppression.location_pattern)


def filter_findings(
    path: str,
    findings: list[findings_mod.Finding],
    suppressions: list[Suppression],
) -> tuple[list[findings_mod.Finding], list[SuppressedFinding]]:
    """
    Splits `findings` into (kept, suppressed) against the loaded
    suppression rules, preserving order. The first matching rule (in the
    order declared in the config file) is what's recorded against a
    suppressed finding.
    """
    if not suppressions:
        return findings, []
    kept: list[findings_mod.Finding] = []
    suppressed: list[SuppressedFinding] = []
    for finding in findings:
        match = next((s for s in suppressions if _matches(finding, s)), None)
        if match is None:
            kept.append(finding)
        else:
            suppressed.append(SuppressedFinding(path=path, finding=finding, reason=match.reason))
    return kept, suppressed


def log_suppressions(repo_root: Path, suppressed: list[SuppressedFinding]) -> None:
    """
    Appends one line per suppressed finding to `.agent-cache/
    suppressions.log` -- the whole `.agent-cache/` directory is
    gitignored (see cache.ensure_gitignored), so this is a local audit
    trail for whoever maintains this repo's agent prompts, never
    something that ends up in a PR diff. Deliberately logs every
    occurrence on every run, not just the first: how often a given
    pattern keeps firing is itself useful signal for a maintainer
    deciding whether a specialist's prompt needs tightening, per this
    project's own evidence-based philosophy applied to maintaining the
    agents themselves.

    Never raises: an audit log is a nice-to-have, not something a review
    run should fail over (e.g. a read-only .agent-cache/ in some CI
    sandboxes).
    """
    if not suppressed:
        return
    log_path = repo_root / SUPPRESSIONS_LOG_PATH
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        lines = [
            f"{timestamp}\t{s.finding.agent}\t{s.finding.location}\t{s.reason}" for s in suppressed
        ]
        with log_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass
