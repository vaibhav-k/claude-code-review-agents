from billing.ledger import get_balance, get_last_entry, get_account_status


def summarize_invoice(entry):
    status = get_account_status(entry.account_id)  # pre-existing, unchanged
    balance = get_balance(entry.account_id)  # pre-existing, unchanged
    last = get_last_entry(entry.account_id)  # NEW in this diff
    return f"{status}: {balance} (last: {last})"
