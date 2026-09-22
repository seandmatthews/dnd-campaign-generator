"""Job error path and the ~40-line log_tail cap."""

import jobs

from conftest import wait_for_job


def test_job_error_path_and_log_tail_cap(client):
    def noisy_failure(log):
        for i in range(100):
            log(f"line-{i}")
        raise ValueError("engine exploded")

    job_id = jobs.submit("noisy", noisy_failure)
    job = wait_for_job(client, job_id)
    assert job["status"] == "error"
    assert job["error"] == "ValueError: engine exploded"
    assert job["result"] is None
    # log_tail is capped at ~40 lines and keeps the TAIL
    assert len(job["log_tail"]) == 40
    assert job["log_tail"][0] == "line-60"
    assert job["log_tail"][-1] == "line-99"
