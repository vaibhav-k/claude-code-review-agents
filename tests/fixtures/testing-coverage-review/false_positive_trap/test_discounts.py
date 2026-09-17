from types import SimpleNamespace

from pricing import compute_discount


def make_order(vip: bool, total: float):
    return SimpleNamespace(customer=SimpleNamespace(is_vip=vip), total=total)


def test_vip_discount_over_500():
    assert compute_discount(make_order(vip=True, total=600)) == 120


def test_non_vip_discount():
    assert compute_discount(make_order(vip=False, total=600)) == 30


def test_vip_discount_at_boundary_not_over_500():
    assert compute_discount(make_order(vip=True, total=500)) == 25
