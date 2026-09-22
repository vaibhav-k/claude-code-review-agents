import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review import routing


def test_is_excluded_from_review_matches_common_lockfiles():
    for path in (
        "package-lock.json",
        "frontend/package-lock.json",
        "yarn.lock",
        "poetry.lock",
        "Cargo.lock",
        "go.sum",
    ):
        assert routing.is_excluded_from_review(path), path


def test_is_excluded_from_review_matches_vendored_and_build_dirs():
    for path in (
        "node_modules/left-pad/index.js",
        "vendor/github.com/pkg/errors/errors.go",
        "dist/bundle.js",
        "app.min.js",
    ):
        assert routing.is_excluded_from_review(path), path


def test_is_excluded_from_review_leaves_ordinary_source_and_migrations_alone():
    # Migrations are data-integrity-review's own scope -- must never be
    # excluded just because they're "generated" in some looser sense.
    for path in ("src/handlers.py", "migrations/0007_add_email_index.py", "app.js"):
        assert not routing.is_excluded_from_review(path), path


def test_sql_injection_diff_routes_security():
    diff = """
+def get_user(request):
+    user_id = request.args.get("id")
+    query = f"SELECT * FROM users WHERE id = {user_id}"
+    return db.execute(query).fetchone()
"""
    decision = routing.route_file("handlers.py", diff)
    assert "security-review" in decision.agents


def test_migration_diff_routes_data_integrity():
    diff = """
+migrationBuilder.AlterColumn<string>(
+    name: "Email",
+    table: "Users",
+    type: "varchar(50)");
"""
    decision = routing.route_file("Migrations/x.cs", diff)
    assert "data-integrity-review" in decision.agents


def test_lock_diff_routes_concurrency():
    diff = """
+public String fetchName(int id) throws SQLException {
+    Connection conn = pool.getConnection();
+    return conn.toString();
+}
"""
    decision = routing.route_file("Db.java", diff)
    assert "concurrency-resource-review" in decision.agents


def test_retry_diff_routes_reliability():
    diff = """
+for attempt in range(3):
+    try:
+        return call()
+    except Exception:
+        continue
"""
    decision = routing.route_file("client.py", diff)
    assert "reliability-availability-review" in decision.agents


def test_test_file_routes_testing_coverage():
    diff = """
+def test_something():
+    assert compute(1) == 2
"""
    decision = routing.route_file("test_thing.py", diff)
    assert "testing-coverage-review" in decision.agents


def test_new_import_diff_routes_api_type_contract():
    # Mirrors triage-router.md's "Architecture/dependency" signal: a new
    # import statement added by this diff routes to api-type-contract-review
    # so it can judge whether the new edge closes a cycle or crosses a
    # layer -- routing itself doesn't need to know which.
    diff = """
+from billing.ledger import render_ledger_entry
 def format_invoice_line(entry):
     return entry
"""
    decision = routing.route_file("billing/invoice.py", diff)
    assert "api-type-contract-review" in decision.agents


def test_new_using_statement_diff_routes_api_type_contract():
    diff = """
+using MyApp.Infrastructure;
 public class OrderService {
 }
"""
    decision = routing.route_file("OrderService.cs", diff)
    assert "api-type-contract-review" in decision.agents


def test_unchanged_context_import_alone_does_not_route_api_type_contract():
    # The `^\+` anchor must match an ADDED import line, not one merely
    # visible as unchanged context around an unrelated change -- otherwise
    # nearly every diff near the top of a file would over-route.
    diff = """
 import os
 import sys
-x = 1
+x = 2
"""
    decision = routing.route_file("script.py", diff)
    assert "api-type-contract-review" not in decision.agents


def test_unrelated_diff_does_not_route_everything():
    diff = """
+GREETING = "hello"
"""
    decision = routing.route_file("constants.py", diff)
    assert decision.agents == []


def test_identical_line_readded_is_semantic_noise():
    # 'return 1' below has no +/- prefix -- it's unchanged context. The
    # only real diff lines are an identical 'def foo():' removed and
    # re-added, which is genuinely a no-op (e.g. pure reformatting).
    diff = """
-def foo():
+def foo():
     return 1
"""
    assert routing.is_semantic_noise(diff) is True


def test_pure_whitespace_change_is_noise():
    diff = "-x = 1\n+x = 1   \n"
    assert routing.is_semantic_noise(diff) is True


def test_real_content_change_is_not_noise():
    diff = "-x = 1\n+x = 2\n"
    assert routing.is_semantic_noise(diff) is False


def test_reordered_identical_lines_is_not_noise():
    # Regression test: reordering can change real behavior (e.g. swapping
    # which of two statements runs first), so a diff that only reorders
    # otherwise-identical lines must NOT be classified as a no-op just
    # because the same lines appear on both sides -- it needs to reach a
    # specialist like any other real change. This also covers the
    # innocuous case (import order) the old, deliberately looser behavior
    # was designed around: it's now simply routed like anything else,
    # which costs one likely-clean API call instead of silently
    # guaranteeing zero review of a reordered diff.
    diff = "-import b\n-import a\n+import a\n+import b\n"
    assert routing.is_semantic_noise(diff) is False


def test_reordering_with_real_semantic_effect_is_not_noise():
    # The concrete failure scenario the fix above closes: swapping a
    # COMMIT to run before the write it's meant to follow is a real bug,
    # not a no-op -- and the same lines appearing on both sides (just
    # reordered) must never let it slip past as "noise" before
    # data-integrity-review's own COMMIT/ROLLBACK pattern gets a chance to
    # see it.
    diff = (
        "-cursor.execute('COMMIT')\n"
        "-cursor.execute('UPDATE t SET x=1')\n"
        "+cursor.execute('UPDATE t SET x=1')\n"
        "+cursor.execute('COMMIT')\n"
    )
    assert routing.is_semantic_noise(diff) is False
