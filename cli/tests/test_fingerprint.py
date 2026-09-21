import dataclasses
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review import fingerprint
from agent_review.findings import Finding

_BASE = Finding(
    severity="CRITICAL",
    location="src/handlers.py:4",
    title="SQL injection via unparameterized user_id",
    impact="attacker-controlled input reaches the query",
    fix="parameterize the query",
    agent="security-review",
    rule_id="SEC-INJECTION-001",
)


def _with(**changes) -> Finding:
    return dataclasses.replace(_BASE, **changes)


# 1. identical finding -> identical fingerprint
def test_identical_finding_yields_identical_fingerprint():
    assert fingerprint.compute(_BASE) == fingerprint.compute(_with())


# 2. different finding -> different fingerprint
def test_different_rule_id_yields_different_fingerprint():
    other = _with(rule_id="SEC-AUTHZ-001")
    assert fingerprint.compute(_BASE) != fingerprint.compute(other)


def test_different_path_yields_different_fingerprint():
    other = _with(location="src/other.py:4")
    assert fingerprint.compute(_BASE) != fingerprint.compute(other)


def test_different_title_yields_different_fingerprint():
    other = _with(title="Command injection via unsanitized shell argument")
    assert fingerprint.compute(_BASE) != fingerprint.compute(other)


# 3. changed wording -> same fingerprint when the structured identity is
# unchanged (cosmetic-only wording differences: case, trailing
# punctuation, extra whitespace)
def test_case_difference_in_title_does_not_change_fingerprint():
    other = _with(title=_BASE.title.upper())
    assert fingerprint.compute(_BASE) == fingerprint.compute(other)


def test_trailing_punctuation_and_whitespace_do_not_change_fingerprint():
    other = _with(title=f"  {_BASE.title}.   ")
    assert fingerprint.compute(_BASE) == fingerprint.compute(other)


def test_internal_whitespace_collapsing_does_not_change_fingerprint():
    other = _with(title="SQL   injection  via unparameterized user_id")
    assert fingerprint.compute(_BASE) == fingerprint.compute(other)


def test_impact_and_fix_wording_never_affect_fingerprint():
    other = _with(impact="completely different wording", fix="a totally different fix")
    assert fingerprint.compute(_BASE) == fingerprint.compute(other)


# 4/5. path normalization + Windows/POSIX handling
def test_windows_backslash_path_normalizes_the_same_as_posix():
    posix = _with(location="src/pkg/handlers.py:9")
    windows = _with(location="src\\pkg\\handlers.py:9")
    assert fingerprint.compute(posix) == fingerprint.compute(windows)


def test_leading_dot_slash_is_normalized_away():
    plain = _with(location="src/handlers.py:4")
    dotted = _with(location="./src/handlers.py:4")
    assert fingerprint.compute(plain) == fingerprint.compute(dotted)


def test_normalize_path_is_repo_relative_forward_slash():
    assert fingerprint.normalize_path("src\\pkg\\handlers.py") == "src/pkg/handlers.py"
    assert fingerprint.normalize_path("./src/handlers.py") == "src/handlers.py"
    assert fingerprint.normalize_path("/src/handlers.py") == "src/handlers.py"


# 6. reasonable line movement behavior -- line number is excluded from
# the fingerprint entirely (see fingerprint.py's module docstring for
# why), so movement of any size never changes it.
def test_line_number_movement_never_changes_fingerprint():
    moved_a_little = _with(location="src/handlers.py:11")
    moved_a_lot = _with(location="src/handlers.py:9000")
    assert fingerprint.compute(_BASE) == fingerprint.compute(moved_a_little)
    assert fingerprint.compute(_BASE) == fingerprint.compute(moved_a_lot)


# 7. fingerprint behavior when optional location fields are absent
def test_missing_line_number_does_not_crash_and_is_still_deterministic():
    no_line = _with(location="src/handlers.py")  # no ":line" suffix at all
    # And it differs from a finding that DOES carry a line number for the
    # same path/rule_id/title, since split_location() only strips a
    # trailing ":<int>" -- "src/handlers.py" as a bare path is already
    # what normalize_path() would produce from "src/handlers.py:4" too,
    # so in this specific case the fingerprint is actually identical --
    # documenting that explicitly here rather than asserting the opposite
    # (which would be wrong: this fingerprint never uses the line number
    # at all, by design -- see module docstring).
    assert fingerprint.compute(no_line) == fingerprint.compute(_BASE)


def test_split_location_falls_back_to_whole_string_without_crashing():
    assert fingerprint.split_location("src/handlers.py") == ("src/handlers.py", None)
    assert fingerprint.split_location("src/handlers.py:4") == ("src/handlers.py", 4)
    assert fingerprint.split_location("src/handlers.py:not-a-number") == (
        "src/handlers.py:not-a-number",
        None,
    )


def test_effective_rule_id_fallback_is_used_when_rule_id_is_unset():
    unset = Finding(
        severity="HIGH",
        location="a.py:1",
        title="an issue",
        impact="i",
        fix="f",
        agent="data-integrity-review",
    )  # rule_id left at its default ""
    with_explicit = Finding(
        severity="HIGH",
        location="a.py:1",
        title="an issue",
        impact="i",
        fix="f",
        agent="data-integrity-review",
        rule_id="DATA-GENERAL-001",  # what effective_rule_id() falls back to
    )
    assert fingerprint.compute(unset) == fingerprint.compute(with_explicit)


def test_normalize_title_is_deterministic():
    assert fingerprint.normalize_title("  Foo   Bar.  ") == "foo bar"
    assert fingerprint.normalize_title("Foo Bar") == "foo bar"


def test_compute_returns_a_64_char_hex_sha256_digest():
    fp = fingerprint.compute(_BASE)
    assert len(fp) == 64
    int(fp, 16)  # raises ValueError if not valid hex


def test_rstrip_dots_and_space_matches_the_former_regex_semantics():
    # normalize_title() used to strip trailing "[.\\s]+" via a regex;
    # _rstrip_dots_and_space() replaced it with a manual scan for
    # performance (see that function's own docstring) -- these cases
    # pin down that the observable behavior didn't change.
    assert fingerprint._rstrip_dots_and_space("hello...") == "hello"
    assert fingerprint._rstrip_dots_and_space("hello.  .\t\n") == "hello"
    assert fingerprint._rstrip_dots_and_space("hello") == "hello"
    assert fingerprint._rstrip_dots_and_space("...") == ""
    assert fingerprint._rstrip_dots_and_space("") == ""
    # Non-ASCII whitespace (str.isspace(), same scope re's \s covers for
    # str patterns) must still be stripped, not just plain ASCII spaces.
    # Built with chr() (NO-BREAK SPACE, EM SPACE) rather than a literal
    # character in the source, so there's no ambiguous-looking glyph here.
    non_ascii_ws = "hello" + chr(0xA0) + "." + chr(0x2003)
    assert fingerprint._rstrip_dots_and_space(non_ascii_ws) == "hello"
    # A dot/space in the MIDDLE must never be touched -- this is rstrip,
    # not strip.
    assert fingerprint._rstrip_dots_and_space("a.b. c ") == "a.b. c"


def test_pathological_title_normalizes_fast_instead_of_quadratically():
    # Regression guard for the former `re.sub(r"[.\\s]+$", "", ...)`: a
    # long run of dots immediately followed by one non-dot character is
    # the exact shape that made the old regex O(n^2) -- the string never
    # ends in a match at all (it ends in "x"), so `[.\s]+$` retried the
    # same greedy-then-backtrack dance from every one of the 200,000 dot
    # positions before giving up. There is nothing to strip here (the
    # string doesn't end in "."/whitespace), so the correct result is the
    # string unchanged -- the manual scan confirms that by checking only
    # the last character once, not by scanning/backtracking through the
    # whole run.
    title = ("." * 200_000) + "x"
    start = time.monotonic()
    result = fingerprint.normalize_title(title)
    elapsed = time.monotonic() - start
    assert result == title
    assert elapsed < 0.5
