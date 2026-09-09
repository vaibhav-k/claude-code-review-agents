Expected finding:

[MEDIUM] billing.py:8 - compute_invoice_total duplicates compute_checkout_total's VIP-discount rule
Impact: the 20%-VIP-discount rule now exists in two places; a future change to the discount threshold or rate that only updates one of them will make checkout and invoicing silently disagree on the same order's total.
Fix: extract the shared rule into one function (e.g. apply_vip_discount) and have both call sites use it.
