"""Process entry point for safe, repeatable deadline closure work."""

from __future__ import annotations

from classroom_sync.services.deadlines import DeadlineService


def run_due_deadlines(service: DeadlineService, worker_id: str) -> int:
    """Claim and close every due session once; a later run can reclaim expired leases."""

    jobs = service.claim_due_jobs(worker_id)
    for job in jobs:
        service.close_session(job.session_id, worker_id=worker_id)
    return len(jobs)
