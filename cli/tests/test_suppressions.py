import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review import suppressions
from agent_review.findings import Finding


def _finding(agent="security-review", location="handlers.py:3", severity="CRITICAL"):
    return Finding(
        severity=severity,
        location=location,
        title="SQL injection",
        impact="attacker-controlled input reaches the query",
        fix="parameterize the query",
        agent=agent,
    )


def test_load_suppressions_returns_empty_list_when_file_is_missing(tmp_path):
    assert suppressions.load_suppressions(tmp_path) == []


def test_load_suppressions_parses_a_well_formed_file(tmp_path):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "ignore-findings.yml").write_text(
        "suppressions:\n"
        "  - agent: security-review\n"
        '    location: "handlers.py:*"\n'
        '    reason: "validated upstream"\n'
        '  - agent: "*"\n'
        '    location: "generated/*"\n'
        '    reason: "machine-written"\n',
        encoding="utf-8",
    )
    loaded = suppressions.load_suppressions(tmp_path)
    assert loaded == [
        suppressions.Suppression("security-review", "handlers.py:*", "validated upstream"),
        suppressions.Suppression("*", "generated/*", "machine-written"),
    ]


def test_load_suppressions_ignores_malformed_entries_without_crashing(tmp_path):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "ignore-findings.yml").write_text(
        "suppressions:\n"
        "  - agent: security-review\n"
        '    location: "handlers.py:*"\n'
        '    reason: "validated upstream"\n'
        "  - agent: security-review\n"  # missing location and reason
        "  - not-a-mapping\n"
        '  - agent: ""\n'
        '    location: "x"\n'
        '    reason: "y"\n',
        encoding="utf-8",
    )
    loaded = suppressions.load_suppressions(tmp_path)
    assert loaded == [
        suppressions.Suppression("security-review", "handlers.py:*", "validated upstream"),
    ]


def test_load_suppressions_returns_empty_list_on_invalid_yaml(tmp_path):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "ignore-findings.yml").write_text(
        "suppressions: [this is: not valid", encoding="utf-8"
    )
    assert suppressions.load_suppressions(tmp_path) == []


def test_load_suppressions_returns_empty_list_when_top_level_shape_is_wrong(tmp_path):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "ignore-findings.yml").write_text("just a string\n", encoding="utf-8")
    assert suppressions.load_suppressions(tmp_path) == []
    (claude_dir / "ignore-findings.yml").write_text("suppressions: not-a-list\n", encoding="utf-8")
    assert suppressions.load_suppressions(tmp_path) == []


def test_filter_findings_matches_on_agent_and_location_glob():
    rule = suppressions.Suppression("security-review", "handlers.py:*", "validated upstream")
    kept, suppressed = suppressions.filter_findings(
        "handlers.py", [_finding(location="handlers.py:42")], [rule]
    )
    assert kept == []
    assert len(suppressed) == 1
    assert suppressed[0].reason == "validated upstream"
    assert suppressed[0].path == "handlers.py"


def test_filter_findings_wildcard_agent_matches_any_agent():
    rule = suppressions.Suppression("*", "generated/*", "machine-written")
    kept, suppressed = suppressions.filter_findings(
        "generated/models.py",
        [_finding(agent="performance-review", location="generated/models.py:1")],
        [rule],
    )
    assert kept == []
    assert len(suppressed) == 1


def test_filter_findings_leaves_non_matching_findings_alone():
    rule = suppressions.Suppression("security-review", "other.py:*", "n/a")
    finding = _finding(location="handlers.py:3")
    kept, suppressed = suppressions.filter_findings("handlers.py", [finding], [rule])
    assert kept == [finding]
    assert suppressed == []


def test_filter_findings_agent_name_must_match_exactly_when_not_wildcard():
    rule = suppressions.Suppression("security-review", "handlers.py:*", "n/a")
    finding = _finding(agent="performance-review", location="handlers.py:3")
    kept, suppressed = suppressions.filter_findings("handlers.py", [finding], [rule])
    assert kept == [finding]
    assert suppressed == []


def test_filter_findings_with_no_suppressions_is_a_no_op():
    finding = _finding()
    kept, suppressed = suppressions.filter_findings("handlers.py", [finding], [])
    assert kept == [finding]
    assert suppressed == []


def test_log_suppressions_appends_one_line_per_suppressed_finding(tmp_path):
    entries = [
        suppressions.SuppressedFinding("handlers.py", _finding(location="handlers.py:3"), "r1"),
        suppressions.SuppressedFinding("other.py", _finding(location="other.py:9"), "r2"),
    ]
    suppressions.log_suppressions(tmp_path, entries)
    log_path = tmp_path / suppressions.SUPPRESSIONS_LOG_PATH
    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert "security-review" in lines[0]
    assert "handlers.py:3" in lines[0]
    assert "r1" in lines[0]

    # A second run appends rather than overwriting.
    suppressions.log_suppressions(tmp_path, entries[:1])
    lines_after = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines_after) == 3


def test_log_suppressions_with_no_entries_does_not_create_the_file(tmp_path):
    suppressions.log_suppressions(tmp_path, [])
    assert not (tmp_path / suppressions.SUPPRESSIONS_LOG_PATH).exists()
