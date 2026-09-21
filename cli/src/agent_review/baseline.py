"""Persisted baseline of previously observed findings, for CI gating.

`--baseline path` (see cli.py) classifies each of a review's findings as
NEW or EXISTING by fingerprint (see fingerprint.py); `--new-only` can
narrow display/evaluation to just the NEW ones; `--fail-on` can then gate
CI on only that filtered set. A normal review run only ever *reads* this
file -- only an explicit `--update-baseline` run writes it (enforced
structurally in cli.py: `write_baseline` below has exactly one caller,
gated by that one flag). See DESIGN.md's "Finding lifecycle and CI
policy" section for the full design rationale.

Schema (versioned; `version: 1` below):

    {
      "version": 1,
      "findings": [
        {
          "fingerprint": "<sha256 hex, see fingerprint.py>",
          "rule_id": "SEC-INJECTION-001",
          "severity": "HIGH",
          "agent": "security-review",
          "location": "src/foo.py:42",
          "title": "SQL injection via unparameterized user_id"
        }
      ]
    }

`title` is kept (short, useful context for a human auditing the file, and
for the "resolved" note in `_print_review`) but `impact`/`fix` are
deliberately NOT stored -- that is exactly the model's own free-form
prose this project already treats as unnecessary to persist (see
fingerprint.py's docstring for the same reasoning applied to hashing);
keeping the baseline to the structured fields above is also what keeps a
diff of this file, when a team reviews `--update-baseline`'s output in a
PR, readable rather than dominated by paragraphs of prose.

Deliberately strict about malformed input, in explicit contrast to
`suppressions.py`'s `.claude/ignore-findings.yml` (a small, hand-edited
file where a typo'd entry quietly not applying is the right, low-stakes
default -- see that module's own docstring). A baseline is meant to be a
machine-written, version-controlled artifact a CI pipeline's pass/fail
decision depends on: silently treating a corrupt or version-mismatched
baseline as "empty" would silently turn every previously-known finding
into a fresh "NEW" one, which could flip a CI gate's outcome in a way
nobody asked for and nobody would notice until it already had. So any
structural problem here raises `BaselineError` loudly instead, always
caught at the cli.py boundary and turned into a clean `SystemExit` (never
a raw traceback).
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import tempfile
from pathlib import Path

from . import fingerprint as fingerprint_mod
from . import rules as rules_mod
from .findings import Finding

DEFAULT_BASELINE_PATH = Path(".agent-review") / "baseline.json"
BASELINE_SCHEMA_VERSION = 1
_SUPPORTED_VERSIONS = (BASELINE_SCHEMA_VERSION,)
_REQUIRED_ENTRY_FIELDS = (
    "fingerprint",
    "rule_id",
    "severity",
    "agent",
    "location",
    "title",
)


class BaselineError(Exception):
    """Anything wrong with a baseline file: not found (when a path was
    explicitly requested for reading), malformed JSON, a missing/
    unsupported schema version, a wrong top-level shape, or a finding
    entry missing a required field. Also raised (wrapping the underlying
    OSError) if `write_baseline` can't actually write to disk (e.g. a
    read-only baseline path).
    """


@dataclasses.dataclass(frozen=True)
class BaselineEntry:
    fingerprint: str
    rule_id: str
    severity: str
    agent: str
    location: str
    title: str


@dataclasses.dataclass(frozen=True)
class Baseline:
    version: int
    entries: tuple[BaselineEntry, ...]

    @property
    def fingerprints(self) -> frozenset[str]:
        return frozenset(e.fingerprint for e in self.entries)


@dataclasses.dataclass(frozen=True)
class Classification:
    """One finding, paired with its fingerprint and NEW/EXISTING status
    against whatever baseline (possibly none) it was classified against.
    """

    finding: Finding
    fingerprint: str
    status: str  # "new" or "existing"


def _entry_from_finding(finding: Finding) -> BaselineEntry:
    return BaselineEntry(
        fingerprint=fingerprint_mod.compute(finding),
        rule_id=rules_mod.effective_rule_id(finding),
        severity=finding.severity,
        agent=finding.agent,
        location=finding.location,
        title=finding.title,
    )


def build_baseline(findings: list[Finding]) -> Baseline:
    """Deterministic entry order (sorted by fingerprint), so writing the
    identical finding set twice in a row -- even if this run's internal
    collection order differed (e.g. thread completion order upstream) --
    produces byte-identical file content, and so a real content change is
    the only thing that ever shows up in a `git diff` of this file.
    """
    entries = tuple(sorted((_entry_from_finding(f) for f in findings), key=lambda e: e.fingerprint))
    return Baseline(version=BASELINE_SCHEMA_VERSION, entries=entries)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BaselineError(message)


def _entry_from_dict(raw: object, index: int) -> BaselineEntry:
    if not isinstance(raw, dict):
        raise BaselineError(f"findings[{index}] is not a JSON object")
    for key in _REQUIRED_ENTRY_FIELDS:
        _require(key in raw, f"findings[{index}] is missing required field {key!r}")
        _require(isinstance(raw[key], str), f"findings[{index}].{key} must be a string")
    return BaselineEntry(**{key: raw[key] for key in _REQUIRED_ENTRY_FIELDS})


def load_baseline(path: Path) -> Baseline:
    """Raises `BaselineError` for anything short of a well-formed,
    supported-version baseline file -- see this module's docstring for
    why that's the deliberate contract. Callers that want "missing file
    means no baseline" behavior (see cli.py's `cmd_review`) check
    `path.is_file()` themselves before calling this -- this function
    itself always treats "file not found" as an error, since by the time
    it's called the caller has already decided this path SHOULD exist.
    """
    if not path.is_file():
        raise BaselineError(f"baseline file not found: {path}")
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BaselineError(f"could not read baseline file {path}: {exc}") from exc
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise BaselineError(f"baseline file {path} is not valid JSON: {exc}") from exc

    _require(isinstance(parsed, dict), f"baseline file {path} must contain a JSON object")
    version = parsed.get("version")
    _require(
        isinstance(version, int) and version in _SUPPORTED_VERSIONS,
        f"baseline file {path} has unsupported or missing schema version "
        f"{version!r} (this build of agent-review supports: "
        f"{', '.join(str(v) for v in _SUPPORTED_VERSIONS)})",
    )
    raw_findings = parsed.get("findings")
    _require(
        isinstance(raw_findings, list),
        f"baseline file {path}'s 'findings' must be a list",
    )
    entries = tuple(_entry_from_dict(raw, i) for i, raw in enumerate(raw_findings))
    return Baseline(version=version, entries=entries)


def write_baseline(path: Path, findings: list[Finding]) -> Baseline:
    """Creates or replaces `path` with a fresh baseline built from
    `findings`, written atomically: content goes to a temp file in the
    SAME directory first, then `os.replace()` renames it onto `path` --
    atomic on every platform this project supports (POSIX `rename(2)`;
    on Windows, `os.replace` uses `MoveFileEx` with
    `MOVEFILE_REPLACE_EXISTING`) -- so a process killed mid-write can
    never leave `path` holding truncated or half-written JSON.

    Never partially applies: any failure (can't create the parent
    directory, can't write the temp file, can't rename it -- e.g. a
    read-only baseline path or directory) raises `BaselineError` and
    leaves the ORIGINAL `path` untouched; the temp file is cleaned up on
    a best-effort basis.
    """
    baseline = build_baseline(findings)
    payload = {
        "version": baseline.version,
        "findings": [dataclasses.asdict(e) for e in baseline.entries],
    }
    tmp_name: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        os.replace(tmp_name, path)
    except OSError as exc:
        if tmp_name is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
        raise BaselineError(f"could not write baseline file {path}: {exc}") from exc
    return baseline


def classify(findings: list[Finding], loaded: Baseline | None) -> list[Classification]:
    """Classifies each of `findings` as "new" or "existing" against
    `loaded` (by fingerprint). `loaded=None` means "no baseline was
    supplied at all" -- every finding is then "new" (see cli.py's
    `--baseline`/`--new-only` docs: "no baseline -> all findings are
    new"), which is a real, well-defined classification, not a
    placeholder -- `--new-only` is a harmless no-op in that case since
    nothing is ever filtered out.
    """
    known = loaded.fingerprints if loaded is not None else frozenset()
    classified = []
    for finding in findings:
        fp = fingerprint_mod.compute(finding)
        status = "existing" if fp in known else "new"
        classified.append(Classification(finding=finding, fingerprint=fp, status=status))
    return classified


def resolved_entries(findings: list[Finding], loaded: Baseline) -> list[BaselineEntry]:
    """Baseline entries whose fingerprint is absent from `findings`'
    current fingerprints -- previously known (in `loaded`), not reported
    by this run at all. Never treated as a failure anywhere in this
    project (a resolved finding is good news) -- purely informational,
    surfaced as a "Note:" in `_print_review` and a `resolved` list in
    `--json` output, the same way other non-blocking signals (suppressed
    findings, skipped-for-budget files) already are.
    """
    current = {fingerprint_mod.compute(f) for f in findings}
    return [e for e in loaded.entries if e.fingerprint not in current]
