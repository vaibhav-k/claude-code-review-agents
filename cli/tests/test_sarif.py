import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review import __version__
from agent_review.findings import Finding
from agent_review.orchestrator import FileReviewResult, ReviewRun
from agent_review.sarif import to_sarif
from agent_review.suppressions import SuppressedFinding

_SQLI = Finding(
    severity="CRITICAL",
    location="handlers.py:4",
    title="SQL injection via unparameterized user_id",
    impact="attacker-controlled input reaches the query",
    fix="parameterize the query",
    agent="security-review",
)
_DUP_LOGIC = Finding(
    severity="MEDIUM",
    location="billing.py:9",
    title="compute_invoice_total duplicates compute_checkout_total's VIP-discount rule",
    impact="a future discount change only made in one place silently diverges",
    fix="extract the shared rule into one function both call",
    agent="data-integrity-review",
)


def _run(files, skipped_for_budget=None, suppressed=None):
    return ReviewRun(
        base_ref="origin/main",
        files=files,
        skipped_for_budget=skipped_for_budget or [],
        suppressed=suppressed or [],
    )


def _file(path, findings=None, **kwargs):
    kwargs.setdefault("agents", ["security-review"])
    kwargs.setdefault("from_cache", False)
    return FileReviewResult(path=path, findings=findings or [], **kwargs)


def test_empty_run_is_valid_sarif_with_no_results():
    run = _run([_file("a.py")])
    log = to_sarif(run)

    assert log["version"] == "2.1.0"
    assert log["$schema"].startswith("https://")
    driver = log["runs"][0]["tool"]["driver"]
    assert driver["name"] == "agent-review"
    assert driver["version"] == __version__
    assert driver["rules"] == []
    assert log["runs"][0]["results"] == []
    assert log["runs"][0]["invocations"][0]["executionSuccessful"] is True
    assert log["runs"][0]["invocations"][0]["toolExecutionNotifications"] == []


def test_findings_become_results_with_matching_rules():
    run = _run(
        [
            _file("handlers.py", findings=[_SQLI]),
            _file("billing.py", agents=["data-integrity-review"], findings=[_DUP_LOGIC]),
        ]
    )
    log = to_sarif(run)
    sarif_run = log["runs"][0]

    rule_ids = [r["id"] for r in sarif_run["tool"]["driver"]["rules"]]
    assert rule_ids == [
        "security-review",
        "data-integrity-review",
    ]  # CRITICAL sorts first

    results = sarif_run["results"]
    assert len(results) == 2

    sqli_result = next(r for r in results if r["ruleId"] == "security-review")
    location = sqli_result["locations"][0]["physicalLocation"]
    assert sqli_result["level"] == "error"  # CRITICAL -> error
    assert sqli_result["properties"]["severity"] == "CRITICAL"
    assert location["artifactLocation"]["uri"] == "handlers.py"
    assert location["region"]["startLine"] == 4
    assert "SQL injection" in sqli_result["message"]["text"]
    assert "Fix:" in sqli_result["message"]["text"]

    dup_result = next(r for r in results if r["ruleId"] == "data-integrity-review")
    assert dup_result["level"] == "warning"  # MEDIUM -> warning


def test_rule_index_matches_the_rules_array_position():
    run = _run(
        [
            _file("handlers.py", findings=[_SQLI]),
            _file("billing.py", agents=["data-integrity-review"], findings=[_DUP_LOGIC]),
        ]
    )
    log = to_sarif(run)
    sarif_run = log["runs"][0]
    rules = sarif_run["tool"]["driver"]["rules"]
    for result in sarif_run["results"]:
        assert rules[result["ruleIndex"]]["id"] == result["ruleId"]


def test_fingerprint_is_stable_across_line_number_drift():
    moved = Finding(
        severity=_SQLI.severity,
        location="handlers.py:11",  # same file, different line
        title=_SQLI.title,
        impact="different wording this time",
        fix="different wording this time too",
        agent=_SQLI.agent,
    )
    run_a = _run([_file("handlers.py", findings=[_SQLI])])
    run_b = _run([_file("handlers.py", findings=[moved])])

    fp_a = to_sarif(run_a)["runs"][0]["results"][0]["partialFingerprints"]["agentReview/v1"]
    fp_b = to_sarif(run_b)["runs"][0]["results"][0]["partialFingerprints"]["agentReview/v1"]
    assert fp_a == fp_b


def test_no_line_number_omits_region_instead_of_crashing():
    no_line = Finding(
        severity="LOW",
        location="README.md",  # no ":line" suffix
        title="stale claim",
        impact="minor",
        fix="update the paragraph",
        agent="testing-coverage-review",
    )
    run = _run([_file("README.md", agents=["testing-coverage-review"], findings=[no_line])])
    result = to_sarif(run)["runs"][0]["results"][0]
    assert "region" not in result["locations"][0]["physicalLocation"]
    assert result["level"] == "note"  # LOW -> note


def test_windows_backslash_path_is_normalized_to_posix_uri():
    backslashy = Finding(
        severity="HIGH",
        location="src\\pkg\\handlers.py:7",
        title="issue",
        impact="impact",
        fix="fix",
        agent="security-review",
    )
    run = _run([_file("src/pkg/handlers.py", findings=[backslashy])])
    result = to_sarif(run)["runs"][0]["results"][0]
    uri = result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
    assert uri == "src/pkg/handlers.py"


def test_cached_agent_gets_documented_fallback_rule():
    cached_finding = Finding(
        severity="HIGH",
        location="a.py:1",
        title="issue",
        impact="impact",
        fix="fix",
        agent="cached",
    )
    run = _run([_file("a.py", agents=["cached"], from_cache=True, findings=[cached_finding])])
    rules = to_sarif(run)["runs"][0]["tool"]["driver"]["rules"]
    assert rules[0]["id"] == "cached"
    assert "cache" in rules[0]["shortDescription"]["text"].lower()


def test_unknown_custom_agent_gets_generic_fallback_description():
    custom = Finding(
        severity="LOW",
        location="a.py:1",
        title="t",
        impact="i",
        fix="f",
        agent="my-custom-review",
    )
    run = _run([_file("a.py", agents=["my-custom-review"], findings=[custom])])
    rules = to_sarif(run)["runs"][0]["tool"]["driver"]["rules"]
    assert rules[0]["shortDescription"]["text"] == "Custom specialist: my-custom-review"


def test_failed_file_becomes_error_notification_and_flips_execution_unsuccessful():
    run = _run([_file("a.py", error="Foundry timeout")])
    invocation = to_sarif(run)["runs"][0]["invocations"][0]
    assert invocation["executionSuccessful"] is False
    notifications = invocation["toolExecutionNotifications"]
    assert len(notifications) == 1
    assert notifications[0]["level"] == "error"
    assert "Foundry timeout" in notifications[0]["message"]["text"]


def test_missing_and_malformed_agents_become_warning_notifications():
    run = _run(
        [
            _file(
                "a.py",
                agents=[],
                missing_agents=["security-review"],
                malformed_agents=["data-integrity-review"],
            )
        ]
    )
    notifications = to_sarif(run)["runs"][0]["invocations"][0]["toolExecutionNotifications"]
    ids = {n["descriptor"]["id"] for n in notifications}
    assert ids == {"agent-review/missing-agent", "agent-review/malformed-response"}
    assert all(n["level"] == "warning" for n in notifications)


def test_truncated_and_budget_skipped_become_note_notifications():
    run = _run([_file("huge.py", truncated=True)], skipped_for_budget=["narrow.py"])
    notifications = to_sarif(run)["runs"][0]["invocations"][0]["toolExecutionNotifications"]
    assert len(notifications) == 2
    assert all(n["level"] == "note" for n in notifications)


def test_suppressed_finding_becomes_note_notification_not_a_result():
    suppressed = SuppressedFinding(
        path="handlers.py", finding=_SQLI, reason="triaged as a non-issue"
    )
    run = _run([_file("handlers.py")], suppressed=[suppressed])
    sarif_run = to_sarif(run)["runs"][0]
    assert sarif_run["results"] == []  # suppressed, so not a reported result
    notifications = sarif_run["invocations"][0]["toolExecutionNotifications"]
    assert len(notifications) == 1
    assert "triaged as a non-issue" in notifications[0]["message"]["text"]
