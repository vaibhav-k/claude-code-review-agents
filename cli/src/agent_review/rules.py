"""Deterministic `rule_id` derivation for a `Finding`.

Milestone 1 ("finding lifecycle and CI policy") requires every structured
finding to carry a stable rule ID -- but explicitly, and separately,
requires that the ID **not depend on the specialist's own free-form
prose** (`Finding.title`/`impact`/`fix`). That rules out the seemingly
obvious approach (keyword-match the finding's own text) for anything
beyond display, so this module derives `rule_id` from two structured,
non-prose signals only:

1. **Which specialist agent produced the finding** (`Finding.agent`) --
   always available, maps 1:1 to a fixed domain prefix (SEC, DATA, CONC,
   REL, PERF, API, TEST).
2. **Deterministic keyword patterns matched against the routed file's own
   diff text** -- the same diff hunk `orchestrator.py` already has in
   hand *before* calling the model (see `_review_one_file`), not
   anything the model generated. This mirrors `routing.py`'s own
   `_AGENT_PATTERNS` table (same idea: cheap, deterministic regex
   matching against the diff, zero model cost) but is a separate,
   independent table here, not a refactor of routing.py's -- routing
   decides *whether* to call a specialist at all and must not change
   behavior as a side effect of this feature; rule-id categorization is
   a purely additive concern layered on top, computed once per
   (file, agent) and applied to every finding that specialist reports
   for that file.

Granularity, honestly stated: this is a per **(file, agent)** category,
not a per-finding one. If a specialist reports two findings of genuinely
different categories for the same file (e.g. security-review flags both
a hardcoded secret and a SQL injection in the same diff), both currently
get the same narrower category if it matched first in the diff text, or
both fall back to that domain's `*-GENERAL-001` id together. This is a
deliberate trade-off to keep `rule_id` fully deterministic and completely
independent of the model's own prose, per the milestone's explicit
requirement -- finer, per-finding granularity would require the
specialist's own output contract to emit a structured category (a prompt
change across all seven `.claude/agents/*.md` files plus `CLAUDE.md`'s
shared Output Contract), which is out of scope for this focused
milestone (see CHANGELOG / DESIGN.md "Finding lifecycle and CI policy"
section for the deferral rationale: it would invalidate every specialist's
recorded cassette fixtures and require a live Foundry re-record this
project's own CI has already been burned by once, for a single agent).

A finding replayed from `.agent-cache/` (`Finding.agent == "cached"`) has
no diff text attributable to one specific specialist at reconstruction
time (see `sarif.py`'s module docstring for the same pre-existing cache
limitation) -- it always gets a single generic fallback id, documented
below.
"""

from __future__ import annotations

import dataclasses
import re

from .findings import Finding

# agent name -> fixed domain prefix used in every rule_id this agent can
# produce. Deliberately keyed by the *specialist* name (structured
# metadata baked into every real FileReviewResult / Finding), never by
# anything the model wrote.
_DOMAIN_PREFIX: dict[str, str] = {
    "security-review": "SEC",
    "data-integrity-review": "DATA",
    "concurrency-resource-review": "CONC",
    "reliability-availability-review": "REL",
    "performance-review": "PERF",
    "api-type-contract-review": "API",
    "testing-coverage-review": "TEST",
}

CACHED_RULE_ID = "CACHED-GENERIC-001"
_CUSTOM_RULE_ID_TEMPLATE = "CUSTOM-{}-001"


@dataclasses.dataclass(frozen=True)
class _Category:
    slug: str
    patterns: tuple[re.Pattern, ...]


def _compiled(*patterns: str) -> tuple[re.Pattern, ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


# agent -> ordered list of narrow categories, each a slug plus the
# patterns that select it. Matched, in order, against the diff text of
# the file a finding came from; the first category with any matching
# pattern wins. These patterns are hand-picked subsets of routing.py's
# own `_AGENT_PATTERNS` groups for the same agent (same source diff
# signals that already justified routing this file to this specialist in
# the first place) -- intentionally not a refactor of that table, so
# routing.py's own tested behavior is untouched by this feature. Kept
# deliberately small: one category per genuinely distinct sub-concern
# actually represented in that agent's existing pattern set, not an
# exhaustive taxonomy.
_CATEGORY_PATTERNS: dict[str, list[_Category]] = {
    "security-review": [
        _Category(
            "INJECTION",
            _compiled(
                r"\beval\s*\(",
                r"\bexec\s*\(",
                r"\bsubprocess\b",
                r"os\.system",
                r"Runtime\.exec",
                r"ProcessBuilder",
                r"""f["'][^"']*\b(SELECT|INSERT|UPDATE|DELETE|DROP)\b""",
                r"""["'][^"']*\b(SELECT|INSERT|UPDATE|DELETE|DROP)\b[^"']*["']\s*\+""",
                r"\$\([^)]*\$",
                r"`[^`]*\$\{",
            ),
        ),
        _Category(
            "DESERIALIZATION",
            _compiled(
                r"\bpickle\b",
                r"yaml\.load\b(?!\s*\(.*Loader)",
                r"ObjectInputStream",
                r"BinaryFormatter",
            ),
        ),
        _Category(
            "AUTHZ",
            _compiled(
                r"\bpassword\b",
                r"\bsecret\b",
                r"\btoken\b",
                r"\bauth\w*\b",
                r"\bsession\b",
                r"\bjwt\b",
                r"\bcors\b",
            ),
        ),
        _Category("CRYPTO", _compiled(r"\bcrypto\b", r"\bhash\b")),
    ],
    "data-integrity-review": [
        _Category(
            "TRANSACTION",
            _compiled(
                r"\bCOMMIT\b",
                r"\bROLLBACK\b",
                r"\bTRANSACTION\b",
                r"\bUPDATE\s+\w+\s+SET\b",
                r"\bDELETE\s+FROM\b",
            ),
        ),
        _Category(
            "MIGRATION",
            _compiled(
                r"\bALTER\s+TABLE\b",
                r"\bmigration\w*\b",
                r"migrationBuilder",
                r"\bAlterColumn\b",
                r"\bCreateTable\b",
            ),
        ),
        _Category(
            "ARITHMETIC",
            _compiled(r"\bdecimal\b", r"\bcurrency\b", r"\btimezone\b", r"\brounding\b"),
        ),
    ],
    "concurrency-resource-review": [
        _Category(
            "RACE",
            _compiled(
                r"\basync\b",
                r"\bawait\b",
                r"\bthread\b",
                r"\block\b",
                r"\bmutex\b",
                r"\bsemaphore\b",
                r"\bsynchronized\b",
                r"\bPromise\b",
            ),
        ),
        _Category(
            "RESOURCE-LEAK",
            _compiled(
                r"\.close\s*\(",
                r"\bDispose\b",
                r"\bfinally\b",
                r"\busing\s*\(",
                r"\bwith\s+\w+\s+as\b",
                r"try-with-resources",
                r"getConnection\(",
                r"\bnew\s+\w*(Socket|Stream|Channel)\b",
                r"\.connect\(",
                r"\bopen\s*\(",
            ),
        ),
    ],
    "reliability-availability-review": [
        _Category(
            "TIMEOUT",
            _compiled(r"\bretry\b", r"\bbackoff\b", r"\btimeout\b", r"\bcircuit.?breaker\b"),
        ),
        _Category("ERROR-HANDLING", _compiled(r"\bcatch\s*\(", r"\bexcept\b")),
        _Category(
            "HEALTHCHECK",
            _compiled(
                r"\bhealth.?check\b",
                r"\breadiness\b",
                r"\backnowledge\b",
                r"\bdead.?letter\b",
            ),
        ),
    ],
    "performance-review": [
        _Category(
            "NPLUS1",
            _compiled(r"\bfor\s+.+\s+in\b.*\n.*(query|fetch|get|select)", r"\bN\+1\b"),
        ),
        _Category(
            "SCALABILITY",
            _compiled(r"\bpagination\b", r"\bbatch.?size\b", r"\bindex\b", r"\bcache\b"),
        ),
    ],
    "api-type-contract-review": [
        _Category(
            "BREAKING",
            _compiled(
                r"\bpublic\s+\w+\s+\w+\(",
                r"\bexport\s+(function|class|interface)\b",
                r"\bdef\s+\w+\(",
                r"\bendpoint\b",
                r"\bDTO\b",
                r"\bschema\b",
            ),
        ),
        _Category("TYPE-WIDENING", _compiled(r":\s*any\b", r"\bnullable\b", r"\benum\b")),
    ],
    "testing-coverage-review": [
        _Category("WEAK", _compiled(r"\bmock\b", r"\bfixture\b")),
    ],
}


def _slugify_agent(agent: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", agent).strip("-").upper()
    return slug or "UNKNOWN"


def rule_id_for(agent: str, diff_text: str = "") -> str:
    """The deterministic rule_id for a finding reported by `agent` for a
    file whose diff text is `diff_text` (pass "" -- the default -- when
    diff text isn't available; this degrades gracefully to that domain's
    `*-GENERAL-001` id rather than raising, matching this whole module's
    "coarse is fine, wrong is not" philosophy).
    """
    if agent == "cached":
        return CACHED_RULE_ID
    prefix = _DOMAIN_PREFIX.get(agent)
    if prefix is None:
        # A custom specialist added via .agent-rules/agents/ in the
        # target repo -- not one of this project's own seven. Keeps that
        # agent distinguishable from every OTHER custom agent (unlike a
        # single shared "unknown" bucket) without needing any entry in
        # _DOMAIN_PREFIX for it.
        return _CUSTOM_RULE_ID_TEMPLATE.format(_slugify_agent(agent))
    for category in _CATEGORY_PATTERNS.get(agent, []):
        if any(pattern.search(diff_text) for pattern in category.patterns):
            return f"{prefix}-{category.slug}-001"
    return f"{prefix}-GENERAL-001"


def attach_rule_ids(agent: str, diff_text: str, findings: list[Finding]) -> list[Finding]:
    """Returns new Finding objects (Finding is frozen) with `rule_id` set
    from `rule_id_for(agent, diff_text)`. Called once per (file, agent)
    review call in orchestrator.py, so every finding that call produced
    shares that one rule_id -- see this module's docstring for why that
    granularity, not per-finding, is the honest contract here.
    """
    if not findings:
        return findings
    rule_id = rule_id_for(agent, diff_text)
    return [dataclasses.replace(f, rule_id=rule_id) for f in findings]


def effective_rule_id(finding: Finding) -> str:
    """`finding.rule_id` if it's already set (the normal case for any
    Finding that went through `attach_rule_ids` in a real review run),
    otherwise a graceful fallback derived from `finding.agent` alone (no
    diff text available) -- covers hand-built Finding objects (tests, or
    any future caller that constructs one directly) so every consumer of
    rule_id (sarif.py, baseline.py, cli.py's JSON output) always gets a
    non-empty, deterministic value without needing its own fallback
    logic.
    """
    return finding.rule_id or rule_id_for(finding.agent)


# -- rule metadata (name + description), for SARIF's rules[] array and any
# future documentation generation. Keyed by the exact rule_id strings this
# module can produce for its seven known agents plus the "cached"
# fallback; a rule_id this table doesn't recognize (the CUSTOM-* family,
# or any truly unexpected value) falls back to a generic, agent-name-based
# description in sarif.py rather than needing an entry here.
RULE_METADATA: dict[str, dict[str, str]] = {
    "SEC-INJECTION-001": {
        "name": "Security: Injection",
        "description": "Command, SQL, or eval/exec-style injection via unsanitized input.",
    },
    "SEC-DESERIALIZATION-001": {
        "name": "Security: Unsafe Deserialization",
        "description": "Unsafe deserialization of untrusted data (pickle, yaml.load, "
        "ObjectInputStream, BinaryFormatter, and similar).",
    },
    "SEC-AUTHZ-001": {
        "name": "Security: AuthN/AuthZ",
        "description": "Authentication, authorization, session, or secret/token handling risk.",
    },
    "SEC-CRYPTO-001": {
        "name": "Security: Crypto Misuse",
        "description": "Cryptographic or hashing misuse.",
    },
    "SEC-GENERAL-001": {
        "name": "Security: General",
        "description": "Injection, authN/authZ, secrets, unsafe deserialization, crypto "
        "misuse -- not narrowed to one of this tool's more specific security "
        "categories.",
    },
    "DATA-TRANSACTION-001": {
        "name": "Data Integrity: Transaction",
        "description": "Transaction/commit/rollback correctness.",
    },
    "DATA-MIGRATION-001": {
        "name": "Data Integrity: Migration",
        "description": "Schema migration correctness.",
    },
    "DATA-ARITHMETIC-001": {
        "name": "Data Integrity: Arithmetic",
        "description": "Business-logic arithmetic risk -- decimal/currency/timezone/rounding.",
    },
    "DATA-GENERAL-001": {
        "name": "Data Integrity: General",
        "description": "Data loss/corruption and functional correctness -- not narrowed to "
        "one of this tool's more specific data-integrity categories.",
    },
    "CONC-RACE-001": {
        "name": "Concurrency: Race",
        "description": "Races, unsynchronized shared state, or lock/deadlock risk.",
    },
    "CONC-RESOURCE-LEAK-001": {
        "name": "Concurrency: Resource Leak",
        "description": "Leaked handles, connections, or memory from unreleased resources.",
    },
    "CONC-GENERAL-001": {
        "name": "Concurrency: General",
        "description": "Races, deadlocks, unsynchronized shared state, leaked resources -- "
        "not narrowed to one of this tool's more specific concurrency categories.",
    },
    "REL-TIMEOUT-001": {
        "name": "Reliability: Timeout/Retry",
        "description": "Missing or incorrect timeout, retry, or backoff handling.",
    },
    "REL-ERROR-HANDLING-001": {
        "name": "Reliability: Error Handling",
        "description": "Error handling that hides or swallows failure.",
    },
    "REL-HEALTHCHECK-001": {
        "name": "Reliability: Health Check",
        "description": "Startup/shutdown/health-check correctness.",
    },
    "REL-GENERAL-001": {
        "name": "Reliability: General",
        "description": "Error handling that hides failure, missing timeouts/retries, "
        "cascading-failure risk -- not narrowed to one of this tool's more "
        "specific reliability categories.",
    },
    "PERF-NPLUS1-001": {
        "name": "Performance: N+1",
        "description": "N+1 query pattern.",
    },
    "PERF-SCALABILITY-001": {
        "name": "Performance: Scalability",
        "description": "Pagination, batching, indexing, or caching risk under scale.",
    },
    "PERF-GENERAL-001": {
        "name": "Performance: General",
        "description": "N+1 queries, algorithmic complexity regressions, blocking calls, "
        "unbounded growth -- not narrowed to one of this tool's more specific "
        "performance categories.",
    },
    "API-BREAKING-001": {
        "name": "API/Contract: Breaking Change",
        "description": "Breaking signature/schema change to a public contract surface.",
    },
    "API-TYPE-WIDENING-001": {
        "name": "API/Contract: Type Widening",
        "description": "Unsafe type widening -- `any`, nullable, or enum drift.",
    },
    "API-GENERAL-001": {
        "name": "API/Contract: General",
        "description": "Breaking signature/schema changes, unsafe type widenings, contract "
        "drift -- not narrowed to one of this tool's more specific API/contract "
        "categories.",
    },
    "TEST-WEAK-001": {
        "name": "Testing: Weak Test",
        "description": "A test that can't fail, a weakened assertion, or a mock/fixture "
        "that defeats the test's own purpose.",
    },
    "TEST-GENERAL-001": {
        "name": "Testing: General",
        "description": "Untested non-trivial new logic, tests that can't fail, weakened "
        "assertions -- not narrowed to one of this tool's more specific testing "
        "categories.",
    },
    CACHED_RULE_ID: {
        "name": "Cached",
        "description": "Finding replayed from a previous review of unchanged content -- "
        "the specialist that originally produced it is not preserved by the "
        "cache (see orchestrator.py's caching and sarif.py's module docstring).",
    },
}
