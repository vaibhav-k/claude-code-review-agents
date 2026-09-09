def test_vip_discount_over_500():
    assert compute_discount(make_order(vip=True, total=600)) == 120

def test_non_vip_discount():
    assert compute_discount(make_order(vip=False, total=600)) == 30
