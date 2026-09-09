Expected: no finding - the operation is a read (idempotent), retries are capped at 3, backoff is exponential, and failure is ultimately propagated.
