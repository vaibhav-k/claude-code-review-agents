Expected finding:

[MEDIUM] test_discounts.py:3 - Added test asserts a tautology and cannot detect a regression
Impact: this test will pass regardless of what compute_discount returns, giving false confidence that the VIP-discount branch is covered when it is not.
Fix: assert against the expected literal value, e.g. assert result == 120.
