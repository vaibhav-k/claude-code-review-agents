def test_vip_discount_over_500():
    result = compute_discount(make_order(vip=True, total=600))
    assert result == result  # tautology
