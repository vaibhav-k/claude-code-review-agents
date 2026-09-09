import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review import routing


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
