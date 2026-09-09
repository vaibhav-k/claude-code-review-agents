def get_top3_summaries(top3_ids):
    # top3_ids is always exactly the 3 leaderboard positions
    return [db.query(Order).filter(Order.id == oid).first().total for oid in top3_ids]
