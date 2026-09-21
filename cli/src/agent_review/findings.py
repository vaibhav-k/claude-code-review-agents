"""
Parsing and sorting for the shared output contract:

    [SEVERITY] file:line - Short issue
    Impact: ...
    Fix: ...

or exactly `No high-impact issues found.` when nothing qualifies. This
module never generates findings itself -- it only parses what a
specialist's model response returned, so the CLI can merge findings from
several specialists into one severity-sorted report.
"""

from __future__ import annotations

import dataclasses
import re

# Public (renamed from the former _SEVERITY_ORDER) so other modules --
# cli.py's --fail-on threshold parsing, baseline.py's classification --
# have exactly one place to import this project's severity ranking from,
# instead of each re-declaring their own copy. Lower number = more severe.
SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
# Bounded, not unbounded, quantifiers around every piece except the
# trailing `title` (which needs none -- see below): `\s*` and the old
# unbounded `\S+` for `location` can each be forced into O(n) backtracking
# by a malformed/adversarial line (e.g. a single very long run of
# non-whitespace characters with no `-`/`—` anywhere in the line), since
# the engine must give back one character at a time from `\S+` -- and,
# separately, one whitespace character at a time from each `\s*` -- before
# concluding there's no match. `\s{0,4}` / `\S{1,300}` cap that
# backtracking at a small constant regardless of line length, with zero
# behavior change for every real line this project's own agents produce
# (single-space formatting, and a `file:line`-shaped location nowhere
# near 300 characters). `title` stays `.+$` unbounded on purpose: it's
# the last group before the end anchor, so it is always resolved in one
# greedy pass with no backtracking, capping it would only risk truncating
# a legitimately long finding title for no performance benefit.
_HEADER_RE = re.compile(
    r"^\[(?P<severity>CRITICAL|HIGH|MEDIUM|LOW)\]\s{0,4}"
    r"(?P<location>\S{1,300})\s{0,4}[-—]\s{0,4}(?P<title>.+)$"
)
NO_FINDINGS_TEXT = "No high-impact issues found."


@dataclasses.dataclass(frozen=True)
class Finding:
    severity: str
    location: str
    title: str
    impact: str
    fix: str
    agent: str
    # Stable rule identifier (e.g. "SEC-INJECTION-001"), assigned
    # deterministically by rules.py from structured, non-prose signals
    # (which specialist produced the finding, and -- where derivable --
    # deterministic keyword patterns matched against the routed file's
    # diff text, never the model's own free-form title/impact/fix). Left
    # as "" by default so every pre-existing call site that constructs a
    # Finding directly (this package's own test suite has many) keeps
    # working unchanged; orchestrator.py always sets it for a real review
    # run, and anything that reads this field for display/serialization
    # should go through rules.effective_rule_id(finding) rather than this
    # attribute directly, since that also covers the "" case gracefully.
    rule_id: str = ""

    def render(self) -> str:
        return (
            f"[{self.severity}] {self.location} — {self.title}\n"
            f"Impact: {self.impact}\n"
            f"Fix: {self.fix}"
        )


def parse(agent: str, raw_text: str) -> list[Finding]:
    """
    Parse one specialist's raw response into zero or more Findings.
    Text that doesn't match the contract at all (a misbehaving or
    truncated response) yields an empty list rather than raising --
    a parse failure should never crash the whole review run.
    """
    text = raw_text.strip()
    if not text or text == NO_FINDINGS_TEXT:
        return []

    findings: list[Finding] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        header = _HEADER_RE.match(lines[i].strip())
        if not header:
            i += 1
            continue
        impact = ""
        fix = ""
        j = i + 1
        while j < len(lines) and not _HEADER_RE.match(lines[j].strip()):
            stripped = lines[j].strip()
            if stripped.lower().startswith("impact:"):
                impact = stripped.split(":", 1)[1].strip()
            elif stripped.lower().startswith("fix:"):
                fix = stripped.split(":", 1)[1].strip()
            j += 1
        findings.append(
            Finding(
                severity=header.group("severity"),
                location=header.group("location"),
                title=header.group("title").strip(),
                impact=impact,
                fix=fix,
                agent=agent,
            )
        )
        i = j
    return findings


def is_malformed_response(raw_text: str, parsed: list[Finding]) -> bool:
    """
    True when a response can't be trusted as a genuine "clean" verdict.

    `parse()` deliberately returns an empty list both for a real clean
    review (`raw_text == NO_FINDINGS_TEXT`) and for text that doesn't
    match the output contract at all -- a truncated response, a stray
    code fence, a model that added prose around the required format. The
    two are indistinguishable from the parsed result alone, which is
    exactly the silent-failure risk: a specialist that drifted off the
    contract reports as "clean" instead of "broken." This function is
    that missing distinction, kept separate from `parse()` itself so
    every existing caller of `parse()` (there are many, across this
    package's own tests and `scripts/validate_fixtures.py`) keeps working
    unchanged -- callers that care about the distinction call this too,
    on the same two values they already have.

    An empty response counts as malformed as well as a non-matching one:
    a real specialist call always says *something*, even when that
    something is exactly NO_FINDINGS_TEXT.
    """
    text = raw_text.strip()
    if not text:
        return True
    return text != NO_FINDINGS_TEXT and not parsed


def sort_findings(findings: list[Finding]) -> list[Finding]:
    """
    Deterministic regardless of the input list's own order -- the key
    is (severity, location, title, agent), not just (severity, location).
    This matters beyond tidiness: two findings that legitimately tie on
    severity AND location (two different specialists both flagging the
    same line, for instance) used to fall back on Python's stable-sort
    behavior of preserving whatever order they arrived in `findings` --
    which is NOT itself a stable property across this project's own call
    sites. A live review call processes agents in `agents_for_file`'s
    routing order; a cache-hit replay processes `cache.get_if_fresh()`'s
    `per_agent` dict, whose key order survives a save/load round trip
    through `json.dumps(..., sort_keys=True)` -- i.e. ALPHABETICAL, not
    insertion order. A real run surfaced this directly: the identical two
    CRITICAL findings at the same location came back in one order from a
    live call and the opposite order from the very next (cache-hit) run
    of the same content, breaking an end-to-end test that (correctly)
    expects a cache hit to reproduce the live run's output exactly. Fully
    specifying the sort key removes the dependency on insertion order
    altogether, so this can never happen again regardless of which order
    any future caller happens to build its input list in.
    """
    return sorted(
        findings,
        key=lambda f: (
            SEVERITY_ORDER.get(f.severity, 99),
            f.location,
            f.title,
            f.agent,
        ),
    )
