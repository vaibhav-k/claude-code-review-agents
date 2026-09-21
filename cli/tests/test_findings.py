import sys
import time
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


def test_is_malformed_response_true_for_empty_text():
    assert findings.is_malformed_response("", []) is True
    assert findings.is_malformed_response("   \n  ", []) is True


def test_is_malformed_response_true_for_off_contract_text_with_no_findings():
    # The exact silent-failure case this exists to catch: a response that
    # drifted off the output contract (extra prose, no [SEVERITY] header)
    # parses to zero findings, same as a genuine clean review -- but it
    # is NOT NO_FINDINGS_TEXT, so it must be flagged, not treated as clean.
    raw = "I looked at the diff and didn't see anything worth flagging."
    assert findings.is_malformed_response(raw, findings.parse("security-review", raw)) is True


def test_is_malformed_response_false_for_genuine_no_findings_sentinel():
    raw = "No high-impact issues found."
    assert findings.is_malformed_response(raw, findings.parse("security-review", raw)) is False


def test_is_malformed_response_false_when_findings_were_actually_parsed():
    raw = "[HIGH] a.py:1 — an issue\nImpact: impact\nFix: fix\n"
    parsed = findings.parse("security-review", raw)
    assert parsed  # sanity: this test's own premise
    assert findings.is_malformed_response(raw, parsed) is False


def test_location_with_a_literal_dash_still_parses_correctly():
    # `_HEADER_RE`'s location group can't span whitespace, so a dash
    # embedded IN a filename (no surrounding spaces) is never confused
    # with the " - "/" — " separator, regardless of the bounded
    # quantifiers introduced to cap backtracking (see that regex's own
    # comment).
    text = "[HIGH] src/my-component.tsx:12 — a real issue\nImpact: i\nFix: f\n"
    parsed = findings.parse("api-type-contract-review", text)
    assert len(parsed) == 1
    assert parsed[0].location == "src/my-component.tsx:12"
    assert parsed[0].title == "a real issue"


def test_location_at_the_bounded_length_cap_still_parses():
    # `\S{1,300}` is the cap -- exactly 300 characters must still match;
    # this pins the boundary itself, not just "some long value works."
    location = "a" * 300
    text = f"[LOW] {location} — a title\nImpact: i\nFix: f\n"
    parsed = findings.parse("performance-review", text)
    assert len(parsed) == 1
    assert parsed[0].location == location


def test_multiple_spaces_around_severity_and_separator_still_parse():
    # `\s{0,4}` -- a couple of stray extra spaces (still realistic model
    # output) must keep working, not just the exact single-space case.
    text = "[MEDIUM]   a.py:1   -   an issue\nImpact: i\nFix: f\n"
    parsed = findings.parse("data-integrity-review", text)
    assert len(parsed) == 1
    assert parsed[0].location == "a.py:1"
    assert parsed[0].title == "an issue"


def test_pathological_header_line_fails_fast_instead_of_hanging():
    # Regression guard for the super-linear-backtracking version of
    # `_HEADER_RE` (unbounded `\S+`/`\s*`): a single huge non-whitespace
    # run with no separator anywhere used to force the engine to give
    # back one character at a time before giving up. The bounded
    # quantifiers cap that at a small constant -- this must return
    # (with no match) near-instantly, not just "eventually."
    line = "[HIGH] " + ("a" * 200_000)
    start = time.monotonic()
    match = findings._HEADER_RE.match(line)
    elapsed = time.monotonic() - start
    assert match is None
    assert elapsed < 0.1


def test_sort_orders_by_severity_then_location():
    raw = [
        findings.Finding("LOW", "z.py:1", "t", "i", "f", "a1"),
        findings.Finding("CRITICAL", "b.py:1", "t", "i", "f", "a2"),
        findings.Finding("CRITICAL", "a.py:1", "t", "i", "f", "a3"),
        findings.Finding("HIGH", "m.py:1", "t", "i", "f", "a4"),
    ]
    ordered = findings.sort_findings(raw)
    assert [f.location for f in ordered] == ["a.py:1", "b.py:1", "m.py:1", "z.py:1"]


def test_sort_is_deterministic_even_when_severity_and_location_tie():
    # Regression test for a real cache-vs-live ordering mismatch: two
    # findings from different specialists at the identical location and
    # severity used to rely on Python's stable sort preserving whatever
    # order they happened to arrive in `findings` -- which a cache-hit
    # replay's `per_agent` dict (alphabetically reordered by
    # `json.dumps(..., sort_keys=True)` on every save/load round trip --
    # see cache.py) does NOT guarantee matches a live call's own
    # `agents_for_file` routing order. Feeding the exact same two
    # findings in BOTH possible input orders must produce the identical
    # output order either way -- that's what "deterministic" means here.
    security_finding = findings.Finding(
        "CRITICAL", "handlers.py:2", "SQL injection", "i1", "f1", "security-review"
    )
    performance_finding = findings.Finding(
        "CRITICAL", "handlers.py:2", "SQL injection", "i2", "f2", "performance-review"
    )
    order_a = findings.sort_findings([security_finding, performance_finding])
    order_b = findings.sort_findings([performance_finding, security_finding])
    assert [f.render() for f in order_a] == [f.render() for f in order_b]
