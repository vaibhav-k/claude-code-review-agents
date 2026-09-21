"""Render a ReviewRun as a SARIF 2.1.0 log.

SARIF (Static Analysis Results Interchange Format) is the format GitHub
code scanning, Azure DevOps, and most CI security/quality dashboards
expect from a static analysis tool -- this is what lets `agent-review`'s
findings show up as inline PR annotations and a persistent alerts list
instead of only living in a build log. This module is a pure rendering
layer, exactly like `cli._review_run_to_dict` (the existing `--json`
format) beside it: it never changes what was found, only how a finished
ReviewRun is serialized.

Reference: https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html

Known limitation, documented here rather than hidden: a finding replayed
from `.agent-cache/` (see cache.py) has its `Finding.agent` field set to
the literal string ``"cached"`` -- the cache stores combined text per
file, not attributed per specialist, so the original agent that produced
a cached finding isn't preserved. Such findings are grouped under a
``cached`` SARIF rule (see `_RULE_DESCRIPTIONS` below) rather than under
the specialist that actually found them. This is a pre-existing property
of the cache, not something introduced by SARIF rendering, and it
affects `--json` output identically (the same `"cached"` string is
already in `finding["agent"]` there).
"""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath

from . import __version__
from .findings import Finding
from .orchestrator import ReviewRun

SARIF_VERSION = "2.1.0"
# Verified live (2026-09-21) against the installed `jsonschema` package,
# not assumed: a first guess at this path
# (.../master/Schemata/sarif-schema-2.1.0.json) 404s -- OASIS moved the
# schema under sarif-2.1/schema/ at some point after that path was
# originally documented in various third-party examples. A hand-built
# SARIF log validated against the schema at *this* URL with
# jsonschema.validate() and passed; this is the same "check the real
# thing, don't assume the plausible-sounding path is correct" discipline
# this project's own CHANGELOG (0.9.16) exists to reinforce.
SARIF_SCHEMA_URI = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/sarif-2.1/schema/"
    "sarif-schema-2.1.0.json"
)
_TOOL_NAME = "agent-review"
_INFORMATION_URI = "https://github.com/vaibhav-k/claude-code-review-agents"

# Kept in sync with the README's "Agent roster" table by hand -- there is
# no automated cross-check (unlike default_rules/, which
# test_default_rules_sync.py does keep in sync), because unlike a prompt
# body, drifting a couple of words behind the README costs nothing but
# cosmetics here: worst case a rule's SARIF description reads slightly
# stale, it never changes what's reported or how it's routed.
_RULE_DESCRIPTIONS: dict[str, str] = {
    "security-review": (
        "Injection, authN/authZ, secrets, unsafe deserialization, "
        "SSRF/path traversal, crypto misuse."
    ),
    "data-integrity-review": (
        "Data loss/corruption and functional correctness -- transactions, "
        "migrations, SQL correctness, business-logic arithmetic, and "
        "correctness risk from duplicated or dead logic."
    ),
    "concurrency-resource-review": (
        "Races, deadlocks, unsynchronized shared state, leaked handles/connections/memory."
    ),
    "reliability-availability-review": (
        "Error handling that hides failure, missing timeouts/retries, "
        "cascading-failure risk, startup/shutdown/health-check correctness."
    ),
    "performance-review": (
        "N+1 queries, algorithmic complexity regressions, blocking calls in "
        "non-blocking contexts, unbounded growth."
    ),
    "api-type-contract-review": (
        "Breaking signature/schema changes, unsafe type widenings, contract "
        "drift across language boundaries."
    ),
    "testing-coverage-review": (
        "Untested non-trivial new logic, tests that can't fail, weakened "
        "assertions, flaky-prone or isolation-breaking test patterns."
    ),
    "cached": (
        "Finding replayed from a previous review of unchanged content -- "
        "the specialist that originally produced it is not preserved by "
        "the cache (see this module's docstring)."
    ),
}

# SARIF's four result levels. CRITICAL and HIGH both map to "error"
# because SARIF has no finer-grained built-in severity than these four --
# the original four-way severity is not lost, though: it's carried
# through unchanged in each result's `properties.severity` for any
# consumer that wants it back.
_LEVEL_BY_SEVERITY: dict[str, str] = {
    "CRITICAL": "error",
    "HIGH": "error",
    "MEDIUM": "warning",
    "LOW": "note",
}
_DEFAULT_LEVEL = "warning"


def _rule_id_order(findings: list[Finding]) -> list[str]:
    """
    Distinct agent names, in first-seen order, for a stable rules[]
    array -- findings are already severity-sorted by the time this runs
    (see ReviewRun.all_findings), so "first-seen" here also means
    "highest severity first," which is a reasonable tie-break and, more
    importantly, deterministic across runs with the same findings.
    """
    seen: dict[str, None] = {}
    for finding in findings:
        seen.setdefault(finding.agent, None)
    return list(seen)


def _rule_name(agent: str) -> str:
    return "".join(part.capitalize() for part in agent.split("-")) or agent


def _split_location(location: str) -> tuple[str, int | None]:
    """
    "file.py:9" -> ("file.py", 9). Falls back to treating the whole
    string as the path (no line region emitted) for anything that
    doesn't end in a plain integer after a colon -- every real finding
    from `findings.parse()` matches `path:line` (enforced by its own
    `_HEADER_RE`), but this stays defensive rather than raising on a
    hand-built Finding (e.g. in a test) that doesn't.
    """
    path, sep, tail = location.rpartition(":")
    if sep and tail.isdigit():
        return path, int(tail)
    return location, None


def _artifact_uri(path: str) -> str:
    # SARIF wants forward-slash relative URIs. git already gives us
    # forward-slash relative paths on every platform (diff output is
    # never OS-path-separated), so this is a defensive normalization,
    # not a real conversion, for the same reason `_split_location` above
    # stays defensive: never crash rendering a result over a path shape
    # that isn't supposed to occur rather than can't.
    return PurePosixPath(path.replace("\\", "/")).as_posix()


def _fingerprint(finding: Finding) -> str:
    # A short, stable identity for a finding that survives line-number
    # drift across runs (a finding at line 9 today and line 11 next week,
    # after an unrelated earlier edit, is still "the same" finding to a
    # human triaging alerts) -- this is what lets GitHub code scanning
    # (and similar SARIF consumers) recognize a re-reported finding as
    # already-seen instead of flagging it as new on every run.
    # Deliberately excludes impact/fix text: those are free-form model
    # prose that can be worded slightly differently between runs for the
    # same underlying issue, which would defeat the whole point of a
    # fingerprint if included.
    path, _ = _split_location(finding.location)
    basis = f"{finding.agent}:{path}:{finding.title}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _result_for(finding: Finding, rule_index: dict[str, int]) -> dict:
    path, line = _split_location(finding.location)
    physical_location: dict = {"artifactLocation": {"uri": _artifact_uri(path)}}
    if line is not None:
        physical_location["region"] = {"startLine": line}
    return {
        "ruleId": finding.agent,
        "ruleIndex": rule_index[finding.agent],
        "level": _LEVEL_BY_SEVERITY.get(finding.severity, _DEFAULT_LEVEL),
        "message": {"text": f"{finding.title}\n\nImpact: {finding.impact}\nFix: {finding.fix}"},
        "locations": [{"physicalLocation": physical_location}],
        "partialFingerprints": {"agentReview/v1": _fingerprint(finding)},
        "properties": {"severity": finding.severity, "agent": finding.agent},
    }


def _notifications(run: ReviewRun) -> list[dict]:
    """
    Everything `cli._print_review`/`_review_run_to_dict` surface as a
    warning or note alongside findings, translated into SARIF's own home
    for exactly this kind of "not a finding, but you should know" signal
    -- so a SARIF-only consumer (e.g. a CI dashboard that never sees this
    tool's stdout) doesn't silently lose visibility into a failed file,
    a specialist that was skipped, or a suppressed finding.
    """
    notifications: list[dict] = []
    for f in run.failed_files:
        notifications.append(
            {
                "level": "error",
                "message": {"text": f"{f.path}: review failed -- {f.error}"},
                "descriptor": {"id": "agent-review/review-error"},
            }
        )
    for f in run.files:
        for agent in f.missing_agents:
            notifications.append(
                {
                    "level": "warning",
                    "message": {
                        "text": (
                            f"{f.path}: specialist '{agent}' had no loaded prompt and was skipped"
                        )
                    },
                    "descriptor": {"id": "agent-review/missing-agent"},
                }
            )
        for agent in f.malformed_agents:
            notifications.append(
                {
                    "level": "warning",
                    "message": {
                        "text": (
                            f"{f.path}: specialist '{agent}' returned a response "
                            "that didn't match the expected output format -- not "
                            "reviewed this run, will retry next run"
                        )
                    },
                    "descriptor": {"id": "agent-review/malformed-response"},
                }
            )
        if f.truncated:
            notifications.append(
                {
                    "level": "note",
                    "message": {
                        "text": f"{f.path}: diff too large, only a truncated prefix was reviewed"
                    },
                    "descriptor": {"id": "agent-review/truncated-diff"},
                }
            )
    for path in run.skipped_for_budget:
        notifications.append(
            {
                "level": "note",
                "message": {"text": f"{path}: skipped this run by --max-files budget"},
                "descriptor": {"id": "agent-review/skipped-for-budget"},
            }
        )
    for s in run.suppressed:
        notifications.append(
            {
                "level": "note",
                "message": {
                    "text": (
                        f"{s.path}: [{s.finding.severity}] {s.finding.location} "
                        f"suppressed -- {s.reason}"
                    )
                },
                "descriptor": {"id": "agent-review/suppressed-finding"},
            }
        )
    return notifications


def to_sarif(run: ReviewRun) -> dict:
    """
    The full SARIF 2.1.0 log for one ReviewRun, as a plain dict ready
    for `json.dumps` -- mirrors `cli._review_run_to_dict` in spirit
    (same input, a different consumer-facing shape) but lives in its own
    module since the SARIF object model (rules, results, notifications,
    fingerprints) is a domain of its own, not a couple of dict-comprehension
    lines.
    """
    findings = run.all_findings
    rule_ids = _rule_id_order(findings)
    rule_index = {agent: i for i, agent in enumerate(rule_ids)}
    rules = [
        {
            "id": agent,
            "name": _rule_name(agent),
            "shortDescription": {
                "text": _RULE_DESCRIPTIONS.get(agent, f"Custom specialist: {agent}")
            },
        }
        for agent in rule_ids
    ]
    return {
        "$schema": SARIF_SCHEMA_URI,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": _TOOL_NAME,
                        "informationUri": _INFORMATION_URI,
                        "version": __version__,
                        "rules": rules,
                    }
                },
                "invocations": [
                    {
                        "executionSuccessful": not run.failed_files,
                        "toolExecutionNotifications": _notifications(run),
                    }
                ],
                "results": [_result_for(f, rule_index) for f in findings],
            }
        ],
    }
