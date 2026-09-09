Expected finding:

[MEDIUM] pricing.py:2 - New VIP-discount branch has no test coverage in this diff
Impact: the 20%-vs-5% discount boundary (VIP + total > 500) is exactly the kind of condition that regresses silently on a future refactor; nothing in the test suite currently pins either branch's output.
Fix: add tests asserting compute_discount returns 20% for a VIP order over 500 and 5% for a non-VIP or under-500 order.
