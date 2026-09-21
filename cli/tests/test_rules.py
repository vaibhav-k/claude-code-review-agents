import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review import rules
from agent_review.findings import Finding


def test_domain_general_fallback_when_no_diff_text_given():
    assert rules.rule_id_for("security-review") == "SEC-GENERAL-001"
    assert rules.rule_id_for("data-integrity-review") == "DATA-GENERAL-001"
    assert rules.rule_id_for("concurrency-resource-review") == "CONC-GENERAL-001"
    assert rules.rule_id_for("reliability-availability-review") == "REL-GENERAL-001"
    assert rules.rule_id_for("performance-review") == "PERF-GENERAL-001"
    assert rules.rule_id_for("api-type-contract-review") == "API-GENERAL-001"
    assert rules.rule_id_for("testing-coverage-review") == "TEST-GENERAL-001"


def test_cached_agent_always_gets_the_documented_generic_id():
    assert rules.rule_id_for("cached") == rules.CACHED_RULE_ID
    # Diff text is irrelevant for "cached" -- the original specialist
    # isn't preserved by the cache, so there's nothing to narrow against.
    assert rules.rule_id_for("cached", "SELECT * FROM users") == rules.CACHED_RULE_ID


def test_unknown_custom_agent_gets_a_distinguishable_fallback():
    a = rules.rule_id_for("my-custom-review")
    b = rules.rule_id_for("another-custom-review")
    assert a != b
    assert a.startswith("CUSTOM-")
    assert "MY-CUSTOM-REVIEW" in a


def test_security_injection_category_is_derived_from_diff_text_not_prose():
    diff = 'query = f"SELECT * FROM users WHERE id = {user_id}"'
    assert rules.rule_id_for("security-review", diff) == "SEC-INJECTION-001"


def test_security_authz_category():
    diff = "def check_password(token):\n    pass\n"
    assert rules.rule_id_for("security-review", diff) == "SEC-AUTHZ-001"


def test_security_deserialization_category():
    diff = "obj = pickle.loads(payload)\n"
    assert rules.rule_id_for("security-review", diff) == "SEC-DESERIALIZATION-001"


def test_data_transaction_category():
    diff = "cursor.execute('COMMIT')\n"
    assert rules.rule_id_for("data-integrity-review", diff) == "DATA-TRANSACTION-001"


def test_data_migration_category():
    diff = "op.alter_column('users', 'email')\nALTER TABLE users ADD COLUMN x\n"
    assert rules.rule_id_for("data-integrity-review", diff) == "DATA-MIGRATION-001"


def test_concurrency_race_category():
    diff = "async def handler():\n    await lock.acquire()\n"
    assert rules.rule_id_for("concurrency-resource-review", diff) == "CONC-RACE-001"


def test_reliability_timeout_category():
    diff = "requests.get(url, timeout=30)\nretry_count = 3\n"
    assert rules.rule_id_for("reliability-availability-review", diff) == "REL-TIMEOUT-001"


def test_performance_nplus1_category():
    diff = "for user in users:\n    fetch(user.id)\n"
    assert rules.rule_id_for("performance-review", diff) == "PERF-NPLUS1-001"


def test_api_breaking_category():
    diff = "public String getName() {\n"
    assert rules.rule_id_for("api-type-contract-review", diff) == "API-BREAKING-001"


def test_testing_weak_category():
    diff = "mock.return_value = True\n"
    assert rules.rule_id_for("testing-coverage-review", diff) == "TEST-WEAK-001"


def test_first_matching_category_wins_when_diff_matches_several():
    # security-review checks INJECTION before AUTHZ (declared order) --
    # a diff matching both must deterministically pick the first.
    diff = 'password = "x"\neval("1+1")\n'
    assert rules.rule_id_for("security-review", diff) == "SEC-INJECTION-001"


def test_no_category_match_falls_back_to_domain_general():
    diff = "x = 1  # nothing category-shaped here\n"
    assert rules.rule_id_for("security-review", diff) == "SEC-GENERAL-001"


def test_rule_id_for_is_deterministic_across_repeated_calls():
    diff = 'f"SELECT * FROM t WHERE id={x}"'
    first = rules.rule_id_for("security-review", diff)
    for _ in range(5):
        assert rules.rule_id_for("security-review", diff) == first


def test_attach_rule_ids_returns_new_findings_with_rule_id_set():
    findings = [
        Finding("HIGH", "a.py:1", "t1", "i1", "f1", "security-review"),
        Finding("LOW", "b.py:2", "t2", "i2", "f2", "security-review"),
    ]
    diff = 'f"SELECT * FROM t WHERE id={x}"'
    updated = rules.attach_rule_ids("security-review", diff, findings)
    assert [f.rule_id for f in updated] == ["SEC-INJECTION-001", "SEC-INJECTION-001"]
    # Originals (frozen dataclasses) are untouched.
    assert findings[0].rule_id == ""


def test_attach_rule_ids_on_empty_list_is_a_no_op():
    assert rules.attach_rule_ids("security-review", "anything", []) == []


def test_effective_rule_id_prefers_already_set_value():
    f = Finding("HIGH", "a.py:1", "t", "i", "fx", "security-review", rule_id="SEC-CRYPTO-001")
    assert rules.effective_rule_id(f) == "SEC-CRYPTO-001"


def test_effective_rule_id_falls_back_when_unset():
    f = Finding("HIGH", "a.py:1", "t", "i", "fx", "data-integrity-review")
    assert rules.effective_rule_id(f) == "DATA-GENERAL-001"


def test_every_produced_rule_id_except_custom_has_metadata():
    # CUSTOM-* ids are agent-name-dependent and intentionally not
    # enumerable in RULE_METADATA (see rules.py's own comment) -- every
    # other id this module can produce should have a name+description so
    # sarif.py never falls back to a generic label for one of this
    # project's own seven agents.
    for agent in (
        "security-review",
        "data-integrity-review",
        "concurrency-resource-review",
        "reliability-availability-review",
        "performance-review",
        "api-type-contract-review",
        "testing-coverage-review",
    ):
        assert rules.rule_id_for(agent) in rules.RULE_METADATA
    assert rules.CACHED_RULE_ID in rules.RULE_METADATA
