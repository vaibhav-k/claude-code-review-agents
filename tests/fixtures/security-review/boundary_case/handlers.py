STATUS_ACTIVE = "active"

def get_active_users():
    status = STATUS_ACTIVE  # module-level constant, not user input
    query = f"SELECT * FROM users WHERE status = '{status}'"
    return db.execute(query).fetchall()
