from __future__ import annotations

import json
from datetime import datetime, timezone
from gzip import decompress
from pathlib import Path

import myextension
from myextension.platform_config import PlatformConfig
from myextension.routes import _enqueue_classroom_evidence
from myextension.session_store import SessionStore


def test_student_segment_batches_are_queued_after_local_persistence() -> None:
    session_id = "39e65774-a89a-4f05-961e-3527b13a6dd2"

    class Outbox:
        def __init__(self) -> None:
            self.entries = []

        def enqueue(self, received_session_id, chunk):
            self.entries.append((received_session_id, chunk))

    class Worker:
        def __init__(self) -> None:
            self.notifications = 0

        def notify(self) -> None:
            self.notifications += 1

    outbox = Outbox()
    worker = Worker()
    config = PlatformConfig(
        mode="student",
        sync_base_url="https://sync.example",
        log_root=Path("/private/tmp"),
        deadline_poll_seconds=30,
    )
    batch = {
        "first_sequence": 1,
        "last_sequence": 2,
        "segments": [
            {
                "event_id": f"{session_id}:1",
                "session_seq": 1,
                "segment_type": "code_writing",
            },
            {
                "event_id": f"{session_id}:2",
                "session_seq": 2,
                "segment_type": "code_execution",
            },
        ],
    }

    _enqueue_classroom_evidence(
        {"myextension_evidence_outbox": outbox, "myextension_evidence_worker": worker},
        config,
        session_id,
        batch,
        created_at=datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc),
    )

    assert worker.notifications == 1
    assert len(outbox.entries) == 1
    received_session_id, chunk = outbox.entries[0]
    assert received_session_id == session_id
    assert chunk.sequence == 1
    assert json.loads(decompress(chunk.body))["events"] == batch["segments"]


def test_student_server_startup_owns_one_evidence_delivery_worker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class JobStore:
        def recover_interrupted(self):
            return []

        def list_queued(self):
            return []

    class AnalysisWorker:
        def __init__(self, root, *, job_store, **_kwargs) -> None:
            self.session_store = SessionStore(root)
            self.job_store = job_store
            self.started = 0

        def enqueue(self, _job_id):
            return None

        def start(self):
            self.started += 1

        def shutdown(self):
            return None

    class Janitor:
        def __init__(self, *_args, **_kwargs) -> None:
            self.started = 0

        def start(self):
            self.started += 1

        def shutdown(self):
            return None

    class Outbox:
        instances = []

        def __init__(self, root, *, client, context_store) -> None:
            self.root = root
            self.client = client
            self.context_store = context_store
            self.instances.append(self)

    class EvidenceWorker:
        instances = []

        def __init__(self, outbox) -> None:
            self.outbox = outbox
            self.started = 0
            self.instances.append(self)

        def start(self):
            self.started += 1

        def shutdown(self):
            return None

    class WebApp:
        def __init__(self) -> None:
            self.settings = {"base_url": "/"}

    server = type(
        "Server",
        (),
        {
            "web_app": WebApp(),
            "log": type("Log", (), {"info": lambda self, _message: None})(),
        },
    )()
    registrations = []
    monkeypatch.setenv("JUPYTERLAB_BEHAVIOR_AUDIT_PLATFORM_MODE", "student")
    monkeypatch.setenv("JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL", "https://sync.example")
    monkeypatch.setenv("JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(myextension, "setup_route_handlers", lambda _app: None)
    monkeypatch.setattr(myextension, "resolve_log_root", lambda: tmp_path)
    monkeypatch.setattr(myextension, "AnalysisJobStore", lambda _root: JobStore())
    monkeypatch.setattr(myextension, "AnalysisWorker", AnalysisWorker)
    monkeypatch.setattr(myextension, "SessionJanitor", Janitor)
    monkeypatch.setattr(myextension, "EvidenceOutbox", Outbox, raising=False)
    monkeypatch.setattr(
        myextension,
        "EvidenceOutboxWorker",
        EvidenceWorker,
        raising=False,
    )
    monkeypatch.setattr(myextension.atexit, "register", registrations.append)

    myextension._load_jupyter_server_extension(server)

    assert len(Outbox.instances) == 1
    assert len(EvidenceWorker.instances) == 1
    assert EvidenceWorker.instances[0].started == 1
    assert server.web_app.settings["myextension_evidence_outbox"] is Outbox.instances[0]
    assert server.web_app.settings["myextension_evidence_worker"] is EvidenceWorker.instances[0]
