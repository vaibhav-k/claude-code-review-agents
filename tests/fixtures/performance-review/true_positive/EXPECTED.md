Expected finding:

[MEDIUM] orders.py:3 - N+1 query: one SELECT per order_id instead of a single batched query
Impact: for order_ids of realistic request size, this issues that many round trips per call, adding latency roughly linear in list size and multiplying DB load under concurrent requests.
Fix: replace with a single batched query, db.query(Order).filter(Order.id.in_(order_ids)).all(), then map results back to the input order.
