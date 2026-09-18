def compute_checkout_total(order):
    if order.customer.is_vip and order.total > 500:
        return order.total * 0.80
    return order.total

def compute_invoice_total(order):
    # copy-pasted from compute_checkout_total when invoicing was added
    if order.customer.is_vip and order.total > 500:
        return order.total * 0.80
    return order.total
