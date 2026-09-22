from db.orders_repository import (
    OrdersRepository,
)  # existing, used elsewhere in this file
from db.connection import get_raw_connection  # also already used elsewhere in this file


def generate_summary(month):
    repo = OrdersRepository()
    orders = repo.for_month(month)  # goes through the repository, pre-existing

    # bypasses the repository -- but this exact pattern already appears
    # twice earlier in this same file, untouched by this diff
    conn = get_raw_connection()
    raw_totals = conn.execute("SELECT ...").fetchall()
    return orders, raw_totals


def generate_annual_summary(year):
    conn = get_raw_connection()  # pre-existing bypass, same pattern, unchanged
    return conn.execute("SELECT ...").fetchall()


def generate_quarter_summary(quarter):
    conn = get_raw_connection()  # pre-existing bypass, same pattern, unchanged
    return conn.execute("SELECT ...").fetchall()
