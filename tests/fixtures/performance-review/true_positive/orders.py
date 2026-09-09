def get_order_summaries(order_ids):
    summaries = []
    for oid in order_ids:
        order = db.query(Order).filter(Order.id == oid).first()
        summaries.append(order.total)
    return summaries
