import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review import findings


def test_no_findings_sentinel_parses_to_empty():
    assert findings.parse("security-review", "No high-impact issues found.") == []
    assert findings.parse("security-review", "  No high-impact issues found.  \n") == []


def test_parses_single_finding():
    text = (
        "[CRITICAL] handlers.py:4 — SQL injection via unparameterized user_id\n"
        "Impact: attacker-controlled user_id reaches the query unescaped.\n"
        "Fix: use a parameterized query.\n"
    )
    parsed = findings.parse("security-review", text)
    assert len(parsed) == 1
    f = parsed[0]
    assert f.severity == "CRITICAL"
    assert f.location == "handlers.py:4"
    assert "SQL injection" in f.title
    assert "unescaped" in f.impact
    assert "parameterized" in f.fix
    assert f.agent == "security-review"


def test_parses_multiple_findings_from_one_agent():
    text = (
        "[HIGH] a.py:1 — first issue\n"
        "Impact: impact one\n"
        "Fix: fix one\n"
        "[MEDIUM] b.py:2 — second issue\n"
        "Impact: impact two\n"
        "Fix: fix two\n"
    )
    parsed = findings.parse("data-integrity-review", text)
    assert [f.severity for f in parsed] == ["HIGH", "MEDIUM"]
    assert [f.location for f in parsed] == ["a.py:1", "b.py:2"]


def test_malformed_text_yields_no_crash_and_no_findings():
    assert findings.parse("security-review", "the model said something weird") == []
    assert findings.parse("security-review", "") == []


def test_sort_orders_by_severity_then_location():
    raw = [
        findings.Finding("LOW", "z.py:1", "t", "i", "f", "a1"),
        findings.Finding("CRITICAL", "b.py:1", "t", "i", "f", "a2"),
        findings.Finding("CRITICAL", "a.py:1", "t", "i", "f", "a3"),
        findings.Finding("HIGH", "m.py:1", "t", "i", "f", "a4"),
    ]
    ordered = findings.sort_findings(raw)
    assert [f.location for f in ordered] == ["a.py:1", "b.py:1", "m.py:1", "z.py:1"]
