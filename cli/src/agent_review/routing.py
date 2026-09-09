"""
Deterministic, zero-API-cost routing.

The original triage-router.md agent (in .claude/agents/) spends one model
call reading diff metadata and keyword signals to decide which specialists
should run. Everything it actually keys off -- file extension, changed
file type, and simple keyword/pattern matches in the diff hunk -- is
mechanically checkable in plain Python. So this CLI reimplements that same
rule table as regex/substring matching and spends zero tokens on routing,
reserving every API call for a specialist that will actually produce a
finding. This is a deliberate efficiency choice beyond what the Claude Code
agent version does, not a shortcut that changes behavior: the rule table
below is a direct port of triage-router.md's Step 3 table.

If a diff's signals are genuinely ambiguous, the bias here matches the
original agent's instruction: route it in rather than skip it. A specialist
that finds nothing costs one cache-miss API call; a skipped specialist that
should have run costs a missed defect.
"""

from __future__ import annotations

import dataclasses
import re

# One entry per specialist agent, each a list of compiled patterns tested
# against the diff hunk text for a single file. Any match routes that file
# to that agent. Keep these patterns intentionally broad (matching the
# routing table's own "when in doubt, route in" bias) -- precision is the
# specialist's job once it actually reads the diff, not the router's.
_AGENT_PATTERNS: dict[str, list[re.Pattern]] = {
    "security-review": [
        re.compile(p, re.IGNORECASE)
        for p in [
            r"\bpassword\b",
            r"\bsecret\b",
            r"\btoken\b",
            r"\bauth\w*\b",
            r"\bsession\b",
            r"\bcrypto\b",
            r"\bhash\b",
            r"\bjwt\b",
            r"\bcors\b",
            r"\beval\s*\(",
            r"\bexec\s*\(",
            r"\bsubprocess\b",
            r"os\.system",
            r"Runtime\.exec",
            r"ProcessBuilder",
            r"\bpickle\b",
            r"yaml\.load\b(?!\s*\(.*Loader)",
            r"ObjectInputStream",
            r"BinaryFormatter",
            # String-built query/command signals: an f-string, %-format, or
            # concatenation that contains a SQL keyword, or shell metachars
            # ($(), backticks) used for command interpolation. These are
            # exactly the injection-shaped patterns security-review's own
            # scope targets, independent of whether a keyword like "auth" or
            # "password" also happens to appear.
            r"""f["'][^"']*\b(SELECT|INSERT|UPDATE|DELETE|DROP)\b""",
            r"""["'][^"']*\b(SELECT|INSERT|UPDATE|DELETE|DROP)\b[^"']*["']\s*\+""",
            r"\$\([^)]*\$",
            r"`[^`]*\$\{",
        ]
    ],
    "data-integrity-review": [
        re.compile(p, re.IGNORECASE)
        for p in [
            r"\bCOMMIT\b",
            r"\bROLLBACK\b",
            r"\bTRANSACTION\b",
            r"\bALTER\s+TABLE\b",
            r"\bmigration\w*\b",
            r"migrationBuilder",
            r"\bAlterColumn\b",
            r"\bCreateTable\b",
            r"\bUPDATE\s+\w+\s+SET\b",
            r"\bDELETE\s+FROM\b",
            r"\bdecimal\b",
            r"\bcurrency\b",
            r"\btimezone\b",
            r"\brounding\b",
        ]
    ],
    "concurrency-resource-review": [
        re.compile(p, re.IGNORECASE)
        for p in [
            r"\basync\b",
            r"\bawait\b",
            r"\bthread\b",
            r"\block\b",
            r"\bmutex\b",
            r"\bsemaphore\b",
            r"\bsynchronized\b",
            r"\bPromise\b",
            r"\.close\s*\(",
            r"\bDispose\b",
            r"\bfinally\b",
            r"\busing\s*\(",
            r"\bwith\s+\w+\s+as\b",
            r"try-with-resources",
            # Resource-ACQUISITION signals, deliberately independent of
            # whether a matching release is also present in the same hunk --
            # "does this diff correctly release what it acquires" is exactly
            # the specialist's own judgment call, not something the router
            # should try to pre-decide by requiring an unclosed pattern.
            r"getConnection\(",
            r"\bnew\s+\w*(Socket|Stream|Channel)\b",
            r"\.connect\(",
            r"\bopen\s*\(",
        ]
    ],
    "reliability-availability-review": [
        re.compile(p, re.IGNORECASE)
        for p in [
            r"\bcatch\s*\(",
            r"\bexcept\b",
            r"\bretry\b",
            r"\bbackoff\b",
            r"\btimeout\b",
            r"\bcircuit.?breaker\b",
            r"\bhealth.?check\b",
            r"\breadiness\b",
            r"\backnowledge\b",
            r"\bdead.?letter\b",
        ]
    ],
    "performance-review": [
        re.compile(p, re.IGNORECASE)
        for p in [
            r"\bfor\s+.+\s+in\b.*\n.*(query|fetch|get|select)",
            r"\bN\+1\b",
            r"\bpagination\b",
            r"\bbatch.?size\b",
            r"\bindex\b",
            r"\bcache\b",
        ]
    ],
    "api-type-contract-review": [
        re.compile(p, re.IGNORECASE)
        for p in [
            r"\bpublic\s+\w+\s+\w+\(",
            r"\bexport\s+(function|class|interface)\b",
            r"\bdef\s+\w+\(",
            r"\bendpoint\b",
            r"\bDTO\b",
            r"\bschema\b",
            r":\s*any\b",
            r"\bnullable\b",
            r"\benum\b",
        ]
    ],
    "testing-coverage-review": [
        re.compile(p, re.IGNORECASE)
        for p in [
            r"\bassert\b",
            r"\btest_\w+\b",
            r"\bdef test",
            r"\bit\(",
            r"\bdescribe\(",
            r"\bmock\b",
            r"\bfixture\b",
        ]
    ],
}


@dataclasses.dataclass(frozen=True)
class RoutingDecision:
    path: str
    agents: list[str]


def route_file(path: str, diff_text: str) -> RoutingDecision:
    is_test_file = bool(re.search(r"(^|/)(test_|_test\.|\.test\.)", path)) or (
        "/tests/" in path or path.startswith("tests/")
    )
    matched: list[str] = []
    for agent, patterns in _AGENT_PATTERNS.items():
        if agent == "testing-coverage-review":
            # Route in either because it's a test file, or because a
            # production file changed non-trivially (has an added function
            # or branch) -- approximated here by "diff adds a def/function
            # and touches a non-test file", since confirming "no matching
            # test in this diff" precisely is exactly the specialist's own
            # job, not the router's.
            if is_test_file:
                matched.append(agent)
                continue
            if re.search(r"^\+\s*(def |function |public |private )", diff_text, re.MULTILINE):
                matched.append(agent)
                continue
        for pattern in patterns:
            if pattern.search(diff_text):
                matched.append(agent)
                break
    return RoutingDecision(path=path, agents=matched)


def is_semantic_noise(diff_text: str) -> bool:
    """True if a diff hunk carries no real content change: every removed
    line has a matching added line, IN THE SAME ORDER, once whitespace is
    stripped out entirely -- this covers pure reformatting (re-indentation,
    whitespace-only edits) where nothing actually moved.

    Lines are compared as an ordered sequence, not a sorted multiset.
    Comparing sorted multisets would also classify a pure *reordering* of
    otherwise-identical lines as noise -- but reordering can change real
    behavior (e.g. swapping which of two statements runs first, such as a
    COMMIT relative to the write it's meant to follow), so it must never
    be silently treated as a no-op just because the same lines appear on
    both sides. Only an exact, order-preserving match is safe to skip.
    """
    removed: list[str] = []
    added: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith("+"):
            added.append(re.sub(r"\s+", "", line[1:]))
        elif line.startswith("-"):
            removed.append(re.sub(r"\s+", "", line[1:]))
    if not removed and not added:
        return True
    return removed == added
