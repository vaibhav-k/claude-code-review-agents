from billing.ledger import render_ledger_entry  # new import added by this diff


def format_invoice_line(entry):
    if entry.needs_ledger_context:
        return render_ledger_entry(entry)
    return str(entry)
