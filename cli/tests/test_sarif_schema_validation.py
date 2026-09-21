"""Validates a generated SARIF log against the real, official SARIF 2.1.0
JSON Schema -- not just "does sarif.to_sarif() run without raising," but
"is the result actually a document a SARIF consumer (GitHub code
scanning, Azure DevOps, etc.) will accept."

The schema lives at tests/support/sarif-schema-2.1.0.json, a bundled
local copy rather than a live fetch -- same reasoning as
tests/support/cassette.py's record/replay design one directory over:
this suite must stay deterministic and runnable with zero network access
on every PR, not flake on a GitHub outage or a moved URL. That last part
already happened once during development here: this test's own first
draft, fetching the schema at the URL initially hardcoded in
sarif.SARIF_SCHEMA_URI, 404'd -- OASIS had moved the file. Re-fetching
from the corrected path (see sarif.py's comment on SARIF_SCHEMA_URI) is
exactly how this bundled copy was obtained. Re-fetch and replace this
file (not by hand -- see the module-level comment for the working URL)
if SARIF ever ships a point release this project adopts.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review.findings import Finding
from agent_review.orchestrator import FileReviewResult, ReviewRun
from agent_review.sarif import to_sarif
from agent_review.suppressions import SuppressedFinding

jsonschema = pytest.importorskip("jsonschema")

_SCHEMA_PATH = Path(__file__).parent / "support" / "sarif-schema-2.1.0.json"


def _comprehensive_review_run() -> ReviewRun:
    """One ReviewRun that exercises every branch sarif.py's rendering
    touches -- multiple severities (multiple SARIF levels), a finding
    with no line number, a failed file, missing/malformed agents, a
    truncated file, a budget-skipped file, a suppressed finding, and a
    cache-replayed finding -- so a schema violation anywhere in that
    surface area is caught by this one document, the same way the real
    tool's output can combine any of these in a single run.
    """
    sqli = Finding(
        severity="CRITICAL",
        location="handlers.py:4",
        title="SQL injection via unparameterized user_id",
        impact="attacker-controlled input reaches the query",
        fix="parameterize the query",
        agent="security-review",
    )
    dup_logic = Finding(
        severity="MEDIUM",
        location="billing.py:9",
        title="duplicated VIP-discount rule",
        impact="a future change to one copy silently diverges from the other",
        fix="extract the shared rule into one function both call",
        agent="data-integrity-review",
    )
    stale_doc = Finding(
        severity="LOW",
        location="README.md",  # no line number
        title="stale claim about cassette recording status",
        impact="a reader trusts an out-of-date statement",
        fix="update the paragraph",
        agent="testing-coverage-review",
    )
    files = [
        FileReviewResult(
            path="handlers.py",
            agents=["security-review"],
            from_cache=False,
            findings=[sqli],
        ),
        FileReviewResult(
            path="billing.py",
            agents=["data-integrity-review"],
            from_cache=False,
            findings=[dup_logic],
        ),
        FileReviewResult(
            path="README.md",
            agents=["testing-coverage-review"],
            from_cache=False,
            findings=[stale_doc],
        ),
        FileReviewResult(
            path="broken.py",
            agents=["security-review"],
            from_cache=False,
            findings=[],
            error="Foundry request timed out",
        ),
        FileReviewResult(
            path="cached.py",
            agents=["cached"],
            from_cache=True,
            findings=[
                Finding(
                    severity="HIGH",
                    location="cached.py:1",
                    title="replayed finding",
                    impact="impact",
                    fix="fix",
                    agent="cached",
                )
            ],
        ),
        FileReviewResult(
            path="huge.py",
            agents=["security-review"],
            from_cache=False,
            findings=[],
            truncated=True,
            missing_agents=["performance-review"],
            malformed_agents=["reliability-availability-review"],
        ),
    ]
    return ReviewRun(
        base_ref="origin/main",
        files=files,
        skipped_for_budget=["narrow.py"],
        suppressed=[
            SuppressedFinding(path="handlers.py", finding=sqli, reason="triaged as a non-issue")
        ],
    )


def test_generated_sarif_validates_against_the_real_sarif_schema():
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    log = to_sarif(_comprehensive_review_run())

    jsonschema.validate(instance=log, schema=schema)


def test_empty_run_also_validates():
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    empty_run = ReviewRun(base_ref="origin/main", files=[])

    jsonschema.validate(instance=to_sarif(empty_run), schema=schema)
