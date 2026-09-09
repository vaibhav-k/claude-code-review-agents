Expected finding:

[HIGH] Db.java:2 - Connection acquired from pool is never released
Impact: every call leaks a pooled connection; under sustained load the pool exhausts and all subsequent DB calls block or fail.
Fix: acquire and release in a try-with-resources block, e.g. try (Connection conn = pool.getConnection(); ...) { ... }.
