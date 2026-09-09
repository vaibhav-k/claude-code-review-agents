def get_user(request):
    user_id = request.args.get("id")
    query = "SELECT * FROM users WHERE id = %s"
    return db.execute(query, (user_id,)).fetchone()
