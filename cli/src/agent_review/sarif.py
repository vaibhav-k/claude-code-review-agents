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
a cached finding isn't preserved. Such a finding's rule_id is always
`rules.CACHED_RULE_ID` (see `rules.py`) rather than a domain-specific one.
This is a pre-existing property of the cache, not something introduced by
SARIF rendering, and it affects `--json` output identically.

**Finding lifecycle and CI policy (milestone 1) additions:** `ruleId` is
now the finding's stable `rule_id` (see `rules.py`), not the reviewing
agent's name -- SARIF's own rule concept ("a stable, small, enumerable
set of things a tool can report") fits a rule_id taxonomy more precisely
than it fits "which of the 7 specialists produced this," and this is
exactly what the milestone's requirement "the rule ID must map cleanly to
SARIF ruleId" asks for. The specialist that produced a finding is still
preserved, just moved to `properties.agent` on each result rather than
being the rule identity itself. `partialFingerprints` now uses the
shared, versioned algorithm in `fingerprint.py` (previously a
sarif.py-local, unversioned 16-hex-character hash of `agent:path:title`)
-- the full 64-hex-character sha256 digest, so `--json`, `--sarif`, and
`.agent-review/baseline.json` all agree on exactly one fingerprint per
finding instead of three separate near-duplicate implementations. SARIF's
own `baselineState` result property (`"new"` / `"unchanged"`) is set when
a baseline was supplied to `to_sarif()` -- see that function's docstring.
"""

from __future__ import annotations

from . import __version__
from . import baseline as baseline_mod
from . import fingerprint as fingerprint_mod
from . import rules as rules_mod
from .baseline import Baseline
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


def _rule_id_order(findings: list[Finding]) -> list[tuple[str, str]]:
    """Distinct (rule_id, a representative agent that produced it), in
    first-seen order, for a stable rules[] array -- findings are already
    severity-sorted by the time this runs (see ReviewRun.all_findings),
    so "first-seen" here also means "highest severity first," which is a
    reasonable tie-break and, more importantly, deterministic across runs
    with the same findings. The representative agent is only used as a
    fallback for a rule_id this module has no static metadata for (see
    `_description_for`) -- when several findings share a rule_id but
    different agents produced them (possible for the *-GENERAL-001
    domain fallback), the first one seen wins; this only affects a
    fallback description string, never routing or identity.
    """
    seen: dict[str, str] = {}
    for finding in findings:
        rule_id = rules_mod.effective_rule_id(finding)
        seen.setdefault(rule_id, finding.agent)
    return list(seen.items())


def _rule_name(agent: str) -> str:
    return "".join(part.capitalize() for part in agent.split("-")) or agent


def _description_for(rule_id: str, agent: str) -> str:
    metadata = rules_mod.RULE_METADATA.get(rule_id)
    if metadata is not None:
        return metadata["description"]
    return f"Custom specialist: {agent}"


def _name_for(rule_id: str, agent: str) -> str:
    metadata = rules_mod.RULE_METADATA.get(rule_id)
    if metadata is not None:
        return metadata["name"]
    return _rule_name(agent)


def _artifact_uri(path: str) -> str:
    # SARIF wants forward-slash relative URIs -- delegates to
    # fingerprint.normalize_path (the single canonical path-normalization
    # implementation in this codebase) rather than keeping its own copy.
    return fingerprint_mod.normalize_path(path)


def _result_for(
    finding: Finding,
    rule_index: dict[str, int],
    status_by_fingerprint: dict[str, str] | None,
) -> dict:
    path, line = fingerprint_mod.split_location(finding.location)
    physical_location: dict = {"artifactLocation": {"uri": _artifact_uri(path)}}
    if line is not None:
        physical_location["region"] = {"startLine": line}
    rule_id = rules_mod.effective_rule_id(finding)
    fp = fingerprint_mod.compute(finding)
    result: dict = {
        "ruleId": rule_id,
        "ruleIndex": rule_index[rule_id],
        "level": _LEVEL_BY_SEVERITY.get(finding.severity, _DEFAULT_LEVEL),
        "message": {"text": f"{finding.title}\n\nImpact: {finding.impact}\nFix: {finding.fix}"},
        "locations": [{"physicalLocation": physical_location}],
        "partialFingerprints": {"agentReview/v1": fp},
        "properties": {"severity": finding.severity, "agent": finding.agent},
    }
    if status_by_fingerprint is not None:
        # SARIF's own vocabulary for exactly this concept (result.7.2 in
        # the spec): "new" for a result not seen in a previous baseline
        # run, "unchanged" for one that was. Only set when a baseline was
        # actually supplied to to_sarif() -- see its docstring for why
        # omitting it entirely (rather than claiming "new" for
        # everything) is the honest choice when there's no real baseline
        # to compare against.
        our_status = status_by_fingerprint.get(fp, "new")
        result["baselineState"] = "unchanged" if our_status == "existing" else "new"
    return result


def _notifications(run: ReviewRun) -> list[dict]:
    """Everything `cli._print_review`/`_review_run_to_dict` surface as a
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


def to_sarif(run: ReviewRun, baseline: Baseline | None = None, new_only: bool = False) -> dict:
    """The full SARIF 2.1.0 log for one ReviewRun, as a plain dict ready
    for `json.dumps` -- mirrors `cli._review_run_to_dict` in spirit
    (same input, a different consumer-facing shape) but lives in its own
    module since the SARIF object model (rules, results, notifications,
    fingerprints) is a domain of its own, not a couple of dict-comprehension
    lines.

    `baseline` (a `baseline.Baseline`, optional) and `new_only` mirror
    `--baseline`/`--new-only` (see cli.py's `cmd_review`): when a
    baseline is given, each result gets SARIF's own `baselineState`
    property (`"new"`/`"unchanged"`); when `new_only` is also set, only
    NEW results are included at all. Both default to their "milestone 1
    didn't happen" values (`None`/`False`), so a caller that doesn't pass
    them gets byte-for-byte the same SARIF log this function produced
    before those flags existed.
    """
    findings = run.all_findings
    status_by_fingerprint: dict[str, str] | None = None
    if baseline is not None:
        classified = baseline_mod.classify(findings, baseline)
        status_by_fingerprint = {c.fingerprint: c.status for c in classified}
        if new_only:
            findings = [c.finding for c in classified if c.status == "new"]

    rule_entries = _rule_id_order(findings)
    rule_index = {rule_id: i for i, (rule_id, _agent) in enumerate(rule_entries)}
    rules = [
        {
            "id": rule_id,
            "name": _name_for(rule_id, agent),
            "shortDescription": {"text": _description_for(rule_id, agent)},
        }
        for rule_id, agent in rule_entries
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
                "results": [_result_for(f, rule_index, status_by_fingerprint) for f in findings],
            }
        ],
    }
