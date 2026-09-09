def compute_discount(order):
    if order.customer.is_vip and order.total > 500:
        return order.total * 0.20
    return order.total * 0.05
