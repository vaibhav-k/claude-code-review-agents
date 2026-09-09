def test_background_job_completes():
    submit_job(job_id="123")
    time.sleep(0.5)  # assume the worker is done by now
    assert get_job_status("123") == "complete"
