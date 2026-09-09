Expected finding:

[CRITICAL] payments.ts:2 - Unbounded immediate retry around a non-idempotent charge call
Impact: any transient failure causes a tight retry loop with no backoff or cap, which can both hammer the payment gateway and double-charge the customer if the first attempt actually succeeded upstream before the error surfaced.
Fix: cap retry attempts, add exponential backoff, and pass an idempotency key to paymentGateway.charge so retried attempts cannot double-charge.
