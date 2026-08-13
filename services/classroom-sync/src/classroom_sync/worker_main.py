"""Long-running deadline worker entry point for a separately restartable container."""

from __future__ import annotations

import os
import time
from uuid import uuid4

from classroom_sync.config import Settings
from classroom_sync.runtime import create_runtime_services
from classroom_sync.worker import run_due_deadlines


def main() -> None:
    """Claim and close due sessions without sharing the API process lifecycle."""

    services = create_runtime_services(Settings.from_env())
    if services.deadline_service is None:
        raise RuntimeError("Deadline service is not configured.")
    worker_id = os.environ.get("CLASSROOM_WORKER_ID", f"classroom-worker-{uuid4()}")
    poll_seconds = float(os.environ.get("CLASSROOM_DEADLINE_POLL_SECONDS", "5"))
    if poll_seconds <= 0:
        raise RuntimeError("CLASSROOM_DEADLINE_POLL_SECONDS must be positive.")
    while True:
        run_due_deadlines(services.deadline_service, worker_id)
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
