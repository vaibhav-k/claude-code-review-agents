Expected finding:

[MEDIUM] test_jobs.py:3 - Fixed sleep instead of waiting on job completion
Impact: under CI load the worker can legitimately take longer than 500ms, making this test fail intermittently regardless of whether the job logic is correct.
Fix: poll get_job_status with a timeout and short interval, or await an explicit completion signal, instead of a fixed sleep.
