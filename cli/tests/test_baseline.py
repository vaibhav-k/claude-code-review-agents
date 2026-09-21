import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review import baseline, fingerprint
from agent_review.findings import Finding

_F1 = Finding(
    severity="HIGH",
    location="src/auth.py:143",
    title="missing authorization check",
    impact="any authenticated user can act on another user's data",
    fix="check ownership before the write",
    agent="security-review",
    rule_id="SEC-AUTHZ-001",
)
_F2 = Finding(
    severity="MEDIUM",
    location="src/foo.py:81",
    title="rounding error in totals",
    impact="totals drift by a cent over many transactions",
    fix="use Decimal instead of float",
    agent="data-integrity-review",
    rule_id="DATA-ARITHMETIC-001",
)


# -- building / writing -----------------------------------------------------


def test_build_baseline_has_one_entry_per_finding_sorted_by_fingerprint():
    b = baseline.build_baseline([_F1, _F2])
    assert b.version == baseline.BASELINE_SCHEMA_VERSION
    assert len(b.entries) == 2
    assert [e.fingerprint for e in b.entries] == sorted(e.fingerprint for e in b.entries)
    fps = {fingerprint.compute(_F1), fingerprint.compute(_F2)}
    assert {e.fingerprint for e in b.entries} == fps


def test_write_baseline_creates_a_new_file(tmp_path):
    path = tmp_path / ".agent-review" / "baseline.json"
    baseline.write_baseline(path, [_F1])
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert len(payload["findings"]) == 1
    assert payload["findings"][0]["rule_id"] == "SEC-AUTHZ-001"
    assert payload["findings"][0]["fingerprint"] == fingerprint.compute(_F1)


def test_write_baseline_replaces_an_existing_file(tmp_path):
    path = tmp_path / "baseline.json"
    baseline.write_baseline(path, [_F1])
    baseline.write_baseline(path, [_F2])
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert len(payload["findings"]) == 1
    assert payload["findings"][0]["rule_id"] == "DATA-ARITHMETIC-001"


def test_write_baseline_with_no_findings_writes_an_empty_list(tmp_path):
    path = tmp_path / "baseline.json"
    baseline.write_baseline(path, [])
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {"version": 1, "findings": []}


def test_write_baseline_is_deterministic_across_runs_with_the_same_findings(tmp_path):
    path = tmp_path / "baseline.json"
    baseline.write_baseline(path, [_F1, _F2])
    first = path.read_text(encoding="utf-8")
    baseline.write_baseline(path, [_F2, _F1])  # same findings, different order
    second = path.read_text(encoding="utf-8")
    assert first == second


def test_write_baseline_to_a_read_only_directory_raises_baseline_error(tmp_path):
    ro_dir = tmp_path / "readonly"
    ro_dir.mkdir()
    target = ro_dir / "baseline.json"
    os.chmod(ro_dir, 0o500)  # r-x: cannot create a file inside it
    try:
        if os.access(ro_dir, os.W_OK):
            pytest.skip("test running as a user that bypasses directory permissions (e.g. root)")
        with pytest.raises(baseline.BaselineError):
            baseline.write_baseline(target, [_F1])
        assert not target.exists()
    finally:
        os.chmod(ro_dir, 0o700)


# -- loading / errors ---------------------------------------------------


def test_load_baseline_round_trips_what_was_written(tmp_path):
    path = tmp_path / "baseline.json"
    baseline.write_baseline(path, [_F1, _F2])
    loaded = baseline.load_baseline(path)
    assert loaded.version == 1
    assert loaded.fingerprints == {fingerprint.compute(_F1), fingerprint.compute(_F2)}


def test_load_baseline_missing_file_raises_baseline_error(tmp_path):
    with pytest.raises(baseline.BaselineError, match="not found"):
        baseline.load_baseline(tmp_path / "does-not-exist.json")


def test_load_baseline_malformed_json_raises_baseline_error(tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(baseline.BaselineError, match="not valid JSON"):
        baseline.load_baseline(path)


def test_load_baseline_unsupported_version_raises_baseline_error(tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"version": 99, "findings": []}), encoding="utf-8")
    with pytest.raises(baseline.BaselineError, match="version"):
        baseline.load_baseline(path)


def test_load_baseline_missing_version_raises_baseline_error(tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"findings": []}), encoding="utf-8")
    with pytest.raises(baseline.BaselineError, match="version"):
        baseline.load_baseline(path)


def test_load_baseline_non_object_top_level_raises_baseline_error(tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(baseline.BaselineError):
        baseline.load_baseline(path)


def test_load_baseline_findings_not_a_list_raises_baseline_error(tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"version": 1, "findings": "nope"}), encoding="utf-8")
    with pytest.raises(baseline.BaselineError):
        baseline.load_baseline(path)


def test_load_baseline_entry_missing_a_field_raises_baseline_error(tmp_path):
    path = tmp_path / "baseline.json"
    entry = {
        "fingerprint": "abc",
        "rule_id": "SEC-AUTHZ-001",
        "severity": "HIGH",
        "agent": "security-review",
        "location": "a.py:1",
        # "title" deliberately omitted
    }
    path.write_text(json.dumps({"version": 1, "findings": [entry]}), encoding="utf-8")
    with pytest.raises(baseline.BaselineError, match="title"):
        baseline.load_baseline(path)


def test_load_baseline_never_silently_treats_malformed_as_empty(tmp_path):
    # The explicit milestone requirement: a malformed baseline is a loud
    # error, not a silent "no findings known" baseline that would make
    # every real finding look new.
    path = tmp_path / "baseline.json"
    path.write_text("not json at all", encoding="utf-8")
    with pytest.raises(baseline.BaselineError):
        loaded = baseline.load_baseline(path)
        assert loaded.entries == ()  # pragma: no cover -- must never reach here


# -- classification -------------------------------------------------------


def test_classify_with_no_baseline_marks_everything_new():
    classified = baseline.classify([_F1, _F2], None)
    assert {c.status for c in classified} == {"new"}


def test_classify_marks_known_fingerprints_existing_and_others_new():
    b = baseline.build_baseline([_F1])
    classified = baseline.classify([_F1, _F2], b)
    by_location = {c.finding.location: c.status for c in classified}
    assert by_location["src/auth.py:143"] == "existing"
    assert by_location["src/foo.py:81"] == "new"


def test_classify_preserves_input_order_and_count():
    b = baseline.build_baseline([_F1])
    classified = baseline.classify([_F2, _F1], b)
    assert [c.finding.location for c in classified] == [
        "src/foo.py:81",
        "src/auth.py:143",
    ]


def test_resolved_entries_reports_baseline_findings_absent_this_run():
    b = baseline.build_baseline([_F1, _F2])
    resolved = baseline.resolved_entries([_F1], b)  # F2 no longer reported
    assert len(resolved) == 1
    assert resolved[0].location == "src/foo.py:81"


def test_resolved_entries_is_empty_when_everything_still_reported():
    b = baseline.build_baseline([_F1, _F2])
    resolved = baseline.resolved_entries([_F1, _F2], b)
    assert resolved == []
