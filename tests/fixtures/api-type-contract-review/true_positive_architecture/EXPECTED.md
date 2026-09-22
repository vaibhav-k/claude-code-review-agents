Expected finding:

[HIGH] billing/invoice.py:1 - New import from billing.ledger closes a circular dependency with billing/ledger.py
Impact: billing/ledger.py already imports format_invoice_line from billing/invoice.py; this diff's new reverse import makes the two modules mutually dependent, which can break on import order (whichever module imports first sees a partially-initialized version of the other) and makes either module impossible to test, deploy, or reason about in isolation from the other.
Fix: extract the shared piece both modules need (e.g. the ledger-context formatting logic) into a third module neither depends on, or invert one of the two dependencies so the relationship is one-directional.
