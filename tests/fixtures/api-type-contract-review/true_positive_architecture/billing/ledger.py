# Unchanged elsewhere in this diff; already present before it. This module
# already imports from billing.invoice.
from billing.invoice import format_invoice_line


def render_ledger_entry(entry):
    return format_invoice_line(entry)
