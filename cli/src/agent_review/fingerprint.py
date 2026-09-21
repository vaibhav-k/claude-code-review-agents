"""
Deterministic, versioned fingerprint for a `Finding`.

The question a fingerprint answers: "is this the same underlying finding
that was reported in an earlier review?" -- so `baseline.py` can classify
today's findings as NEW or EXISTING against a persisted baseline, and
`sarif.py` can give SARIF consumers (GitHub code scanning, etc.) a stable
`partialFingerprints` value that survives an unrelated later edit shifting
line numbers.

Algorithm (version "v1", see FINGERPRINT_VERSION):

    sha256("v1\\n{rule_id}\\n{normalized_path}\\n{normalized_title}")

- **rule_id**: `rules.effective_rule_id(finding)` -- structured, never the
  model's own free-form prose (see rules.py's own docstring for why).
- **normalized_path**: repo-relative, forward-slash, no leading "./" or
  "/" (see `normalize_path`) -- so a Windows checkout and a POSIX one
  fingerprint identically for the same logical file.
- **normalized_title**: `finding.title`, lowercased, internal whitespace
  collapsed, trailing punctuation/whitespace stripped (see
  `normalize_title`) -- tolerates cosmetic wording differences (case,
  a trailing period, extra spacing) between two runs describing the same
  issue, without attempting anything resembling semantic/NLP matching. A
  *substantively* reworded title (different words, not just different
  formatting) is, by design, a different fingerprint -- see Limitations.

Deliberately excluded, and why:

- **Line number.** Line numbers drift on every unrelated edit to a file
  -- exactly the reasoning `suppressions.py`'s own module docstring
  already gives for matching suppressions against a glob pattern instead
  of an exact line ("an exact-line suppression would silently stop
  matching -- and thus silently stop suppressing -- on the very next
  unrelated commit"). Applying the same reasoning here means line
  movement of ANY size never breaks the fingerprint, which is a stronger
  and simpler guarantee than trying to tolerate "small" movements via
  some bucketing/nearest-anchor heuristic. The milestone's own guidance
  is explicit that this is an acceptable, even preferable, trade-off:
  "It is acceptable for some refactors to produce a new fingerprint. It
  is preferable to classify a finding as new rather than incorrectly
  merge two genuinely different defects" -- excluding line number never
  causes a false NEW classification from line drift, at the honest cost
  described below.
- **impact/fix text.** Free-form model prose, most likely to be
  reworded between two runs describing the identical issue; including it
  would defeat the fingerprint's entire purpose (this is the same
  reasoning the pre-existing SARIF-only fingerprint already used, see
  DESIGN.md's SARIF section -- this module formalizes and centralizes
  that same design, now shared by baseline.py too instead of living only
  in sarif.py).
- **Timestamps, LLM calls.** Never used -- the whole point is a
  same-input-same-output, zero-cost, zero-network computation.

Limitations (read before assuming this is a semantic defect identity):

1. **Coarse collision risk.** Two DIFFERENT defects that share a rule_id,
   file, and a title that happens to normalize identically (rare, but
   possible for a generically-worded title) will collide onto the same
   fingerprint. This is the same honest trade-off `rules.py` documents
   for rule_id itself: coarse-but-deterministic beats
   clever-but-unreliable.
2. **Line-independent by design (see above).** Two textually-similar
   findings in the same file, same rule_id, reported at different lines,
   are NOT distinguished by this fingerprint at all -- the algorithm
   only ever asks "same rule_id + same file + same normalized title,"
   never "same rule_id + same file + same LINE."
3. **A substantively reworded title breaks the match.** This fingerprint
   is not a semantic/NLP matcher (a deliberate non-goal per the
   milestone's own guidance against "unreliable semantic matching") -- a
   materially different description of the same underlying issue across
   two runs will fingerprint differently, and will show up as a new
   baseline entry rather than being recognized as "the same finding,
   reworded." This is intentionally the safer failure mode: a false NEW
   (a human re-triages something they've already seen) rather than a
   false EXISTING (a genuinely new defect gets silently absorbed into an
   old, already-accepted baseline entry).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath

from . import rules as rules_mod
from .findings import Finding

FINGERPRINT_VERSION = "v1"

_WHITESPACE_RE = re.compile(r"\s+")


def split_location(location: str) -> tuple[str, int | None]:
    """
    Parses a location string of the form "src/foo.py:42".

    Example:
        "src/foo.py:42" -> ("src/foo.py", 42). Falls back to treating the
    whole string as the path (no line) for anything that doesn't end in
    a plain integer after a colon -- every real finding from
    findings.parse() matches `path:line` (enforced by its own
    `_HEADER_RE`), but a hand-built Finding (e.g. in a test) may not, and
    this must never raise over that.

    This is the single canonical implementation -- sarif.py imports it
    rather than keeping its own copy, so there is exactly one
    location-parsing rule in this codebase, not two that could drift
    apart.
    """
    path, sep, tail = location.rpartition(":")
    if sep and tail.isdigit():
        return path, int(tail)
    return location, None


def normalize_path(raw_path: str) -> str:
    """
    Normalizes a file path to be repo-relative, use forward slashes,
    and have no leading "./".

    Repo-relative, forward-slash, no leading "./" -- git diff output
    (what `Finding.location` is built from) is already forward-slash on
    every platform, so this is a defensive normalization for a
    hand-built path (a test, or a finding constructed some other way)
    that might use OS-native separators, not a real conversion of real
    review output.
    """
    posix = raw_path.replace("\\", "/").lstrip("/")
    parts = [part for part in PurePosixPath(posix).parts if part != "."]
    return "/".join(parts)


def _rstrip_dots_and_space(text: str) -> str:
    """
    Strips trailing dots and whitespace from a string.

    Equivalent to the former `re.sub(r"[.\\s]+$", "", text)`, but a
    plain right-to-left character scan instead of a backtracking regex.
    `X+$` run through `.sub()`/`.search()` is a classic super-linear
    trap: for a string that does NOT end in a match, the engine retries
    the same greedy-then-backtrack dance from every position where a "."
    or whitespace character occurs, not just the true suffix -- e.g.
    `"." * n + "a"` costs O(n) work at position 0 alone (grab all n dots,
    fail the trailing `$`, give back one dot at a time), and the engine
    then repeats a shorter version of that same dance starting at
    position 1, 2, 3, ... -- O(n) + O(n-1) + ... + O(1) = O(n^2) overall.
    A manual scan from the end has no such retry: it inspects each
    character at most once, so it's O(n) with no backtracking possible
    even in principle. `str.isspace()` is used (not a fixed ASCII set)
    specifically to keep matching `\\s`'s own Unicode-whitespace scope,
    since `title` is free-form model-generated text that could contain
    non-ASCII whitespace.
    """
    end = len(text)
    while end > 0 and (text[end - 1] == "." or text[end - 1].isspace()):
        end -= 1
    return text[:end]


def normalize_title(title: str) -> str:
    """
    Normalizes a finding title by lowercasing it, collapsing whitespace,
    and stripping trailing punctuation.

    Lowercase, whitespace-collapsed, trailing-punctuation-stripped --
    enough to make a purely cosmetic wording difference (capitalization,
    a trailing period, extra internal spacing) fingerprint identically,
    without attempting anything resembling semantic matching. See this
    module's docstring, point 3, for what this deliberately does NOT
    tolerate.
    """
    collapsed = _WHITESPACE_RE.sub(" ", title.strip().lower())
    return _rstrip_dots_and_space(collapsed)


def compute(finding: Finding) -> str:
    """
    The full (64 hex character) sha256 fingerprint for `finding`. See
    this module's docstring for the algorithm and its documented
    limitations.
    """
    path, _line = split_location(finding.location)
    basis = "\n".join(
        [
            FINGERPRINT_VERSION,
            rules_mod.effective_rule_id(finding),
            normalize_path(path),
            normalize_title(finding.title),
        ]
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()
