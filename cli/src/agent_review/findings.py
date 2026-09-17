"""Parsing and sorting for the shared output contract:

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

_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
_HEADER_RE = re.compile(
    r"^\[(?P<severity>CRITICAL|HIGH|MEDIUM|LOW)\]\s*(?P<location>\S+)\s*[-—]\s*(?P<title>.+)$"
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

    def render(self) -> str:
        return (
            f"[{self.severity}] {self.location} — {self.title}\n"
            f"Impact: {self.impact}\n"
            f"Fix: {self.fix}"
        )


def parse(agent: str, raw_text: str) -> list[Finding]:
    """Parse one specialist's raw response into zero or more Findings.
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
    """True when a response can't be trusted as a genuine "clean" verdict.

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
    return sorted(
        findings,
        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 99), f.location),
    )
