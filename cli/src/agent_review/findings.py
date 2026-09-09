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

_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
_HEADER_RE = re.compile(
    r"^\[(?P<severity>CRITICAL|HIGH|MEDIUM|LOW)]"
    r"[ \t]*(?P<location>\S+)[ \t]*[-—][ \t]*(?P<title>[^\r\n]+)$"
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


def sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 99), f.location),
    )
