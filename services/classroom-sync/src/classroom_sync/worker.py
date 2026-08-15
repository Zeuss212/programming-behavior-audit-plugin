"""Process entry point for safe, repeatable classroom background work."""

from __future__ import annotations

from classroom_sync.application import ClassroomServices
from classroom_sync.services.deadlines import DeadlineService


def run_due_deadlines(service: DeadlineService, worker_id: str) -> int:
    """Claim and close every due session once; a later run can reclaim expired leases."""

    jobs = service.claim_due_jobs(worker_id)
    for job in jobs:
        service.close_session(job.session_id, worker_id=worker_id)
    return len(jobs)


def run_due_classroom_jobs(services: ClassroomServices, worker_id: str) -> int:
    """Run deadline closure and configured AI analysis jobs in one restartable tick."""

    deadline_count = (
        run_due_deadlines(services.deadline_service, worker_id)
        if services.deadline_service is not None
        else 0
    )
    analysis_count = (
        services.brief_analysis_service.run_due_jobs(worker_id)
        if services.brief_analysis_service is not None
        else 0
    )
    return deadline_count + analysis_count
