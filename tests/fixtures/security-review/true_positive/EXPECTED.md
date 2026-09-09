Expected finding:

[CRITICAL] handlers.py:4 - SQL injection via unparameterized user_id
Impact: attacker-controlled id is concatenated directly into SQL, allowing arbitrary query injection.
Fix: use a parameterized query, e.g. db.execute("SELECT * FROM users WHERE id = %s", (user_id,)).
