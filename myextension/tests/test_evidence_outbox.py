from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from threading import Event

import pytest

from myextension.evidence_outbox import (
    EvidenceChunk,
    EvidenceOutbox,
    EvidenceOutboxWorker,
)
from myextension.platform_client import EvidenceUploadReceipt, PlatformClientError
from myextension.tests.test_platform_registration import context


def test_outbox_persists_a_chunk_before_retry_and_recovers_it_after_restart(
    tmp_path: Path,
) -> None:
    session_id = "39e65774-a89a-4f05-961e-3527b13a6dd2"
    body = b"\x1f\x8bclassroom-evidence"
    chunk = EvidenceChunk(
        sequence=1,
        first_event_sequence=1,
        last_event_sequence=2,
        body=body,
        created_at=datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc),
    )

    outbox = EvidenceOutbox(tmp_path)

    entry = outbox.enqueue(session_id, chunk)

    expected_sha256 = sha256(body).hexdigest()
    session_directory = tmp_path / "platform-outbox" / session_id
    assert entry.content_sha256 == expected_sha256
    assert entry.state == "pending"
    assert (
        session_directory / f"00000001-{expected_sha256}.json"
    ).is_file()
    assert (session_directory / "state.json").is_file()

    recovered = EvidenceOutbox(tmp_path).recover_pending()

    assert len(recovered) == 1
    assert recovered[0].session_id == session_id
    assert recovered[0].sequence == 1
    assert recovered[0].body == body
    assert EvidenceOutbox(tmp_path).list_entries(session_id) == recovered

    duplicate = EvidenceOutbox(tmp_path).enqueue(session_id, chunk)

    assert duplicate.content_sha256 == expected_sha256
    assert duplicate.sequence == 1
    assert len(list(session_directory.glob("00000001-*.json"))) == 1


def test_flush_marks_a_chunk_delivered_only_after_a_matching_remote_receipt(
    tmp_path: Path,
) -> None:
    class ContextStore:
        def read_registered_context(self):
            return context()

        def save_registered_context(self, value):
            return value

    class RecordingClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int, bytes, int, int]] = []

        def upload_evidence(
            self,
            stored_context,
            *,
            sequence: int,
            body: bytes,
            first_event_sequence: int,
            last_event_sequence: int,
        ) -> EvidenceUploadReceipt:
            assert stored_context == context()
            self.calls.append(
                (
                    stored_context.session_id,
                    sequence,
                    body,
                    first_event_sequence,
                    last_event_sequence,
                )
            )
            return EvidenceUploadReceipt(
                evidence_id="4ea8479f-c4bb-4645-9c1f-1593afdc187a",
                session_id=stored_context.session_id,
                sequence=sequence,
                content_sha256=sha256(body).hexdigest(),
            )

    body = b"\x1f\x8bclassroom-evidence"
    client = RecordingClient()
    outbox = EvidenceOutbox(
        tmp_path,
        client=client,
        context_store=ContextStore(),
    )
    outbox.enqueue(
        context().session_id,
        EvidenceChunk(
            sequence=1,
            first_event_sequence=1,
            last_event_sequence=2,
            body=body,
            created_at=datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc),
        ),
    )

    report = outbox.flush_once()

    assert report.delivered == 1
    assert report.deferred == 0
    assert client.calls == [(context().session_id, 1, body, 1, 2)]
    assert EvidenceOutbox(tmp_path).recover_pending() == []
    state = (tmp_path / "platform-outbox" / context().session_id / "state.json").read_text(
        encoding="utf-8"
    )
    assert '"state":"delivered"' in state


def test_flush_defers_service_failures_without_deleting_the_source_chunk(
    tmp_path: Path,
) -> None:
    class ContextStore:
        def read_registered_context(self):
            return context()

        def save_registered_context(self, value):
            return value

    class UnavailableClient:
        def upload_evidence(self, *_args, **_kwargs):
            raise PlatformClientError("platform_evidence_failed")

    now = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)
    body = b"\x1f\x8bclassroom-evidence"
    outbox = EvidenceOutbox(
        tmp_path,
        client=UnavailableClient(),
        context_store=ContextStore(),
        clock=lambda: now,
        jitter=lambda: 0,
    )
    entry = outbox.enqueue(
        context().session_id,
        EvidenceChunk(
            sequence=1,
            first_event_sequence=1,
            last_event_sequence=2,
            body=body,
            created_at=now,
        ),
    )

    report = outbox.flush_once()

    assert report.delivered == 0
    assert report.deferred == 1
    session_directory = tmp_path / "platform-outbox" / context().session_id
    assert (session_directory / f"00000001-{entry.content_sha256}.json").is_file()
    recovered = EvidenceOutbox(tmp_path).recover_pending()
    assert recovered[0].state == "deferred"
    assert recovered[0].attempts == 1
    assert recovered[0].next_retry_at == now + timedelta(seconds=1)


def test_worker_flushes_recovered_evidence_when_the_server_extension_starts() -> None:
    class Outbox:
        def __init__(self) -> None:
            self.recovered = 0
            self.flushed = Event()

        def recover_pending(self):
            self.recovered += 1
            return []

        def flush_once(self):
            self.flushed.set()

    outbox = Outbox()
    worker = EvidenceOutboxWorker(outbox, interval_seconds=60)

    worker.start()
    try:
        assert outbox.flushed.wait(timeout=1)
        assert outbox.recovered == 1
    finally:
        worker.shutdown()


def test_flush_refreshes_an_expired_token_then_replays_the_same_chunk(
    tmp_path: Path,
) -> None:
    refreshed = replace(context(), access_token="refreshed-plugin-token")

    class ContextStore:
        def __init__(self) -> None:
            self.current = context()
            self.saved = []

        def read_registered_context(self):
            return self.current

        def save_registered_context(self, value):
            self.saved.append(value)
            self.current = value
            return value

    class RefreshingClient:
        def __init__(self) -> None:
            self.tokens: list[str] = []
            self.refreshes = 0

        def upload_evidence(self, stored_context, *, sequence, body, **_kwargs):
            self.tokens.append(stored_context.access_token)
            if stored_context.access_token == context().access_token:
                raise PlatformClientError("platform_evidence_unauthorized")
            return EvidenceUploadReceipt(
                evidence_id="4ea8479f-c4bb-4645-9c1f-1593afdc187a",
                session_id=stored_context.session_id,
                sequence=sequence,
                content_sha256=sha256(body).hexdigest(),
            )

        def refresh(self, stored_context):
            assert stored_context == context()
            self.refreshes += 1
            return refreshed

    now = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)
    store = ContextStore()
    client = RefreshingClient()
    outbox = EvidenceOutbox(tmp_path, client=client, context_store=store)
    outbox.enqueue(
        context().session_id,
        EvidenceChunk(
            sequence=1,
            first_event_sequence=1,
            last_event_sequence=1,
            body=b"\x1f\x8bclassroom-evidence",
            created_at=now,
        ),
    )

    report = outbox.flush_once()

    assert report.delivered == 1
    assert client.refreshes == 1
    assert client.tokens == ["short-lived-plugin-token", "refreshed-plugin-token"]
    assert store.saved == [refreshed]
    assert EvidenceOutbox(tmp_path).recover_pending() == []


def test_flush_quarantines_conflicting_evidence_without_deleting_its_source(
    tmp_path: Path,
) -> None:
    class ContextStore:
        def read_registered_context(self):
            return context()

        def save_registered_context(self, value):
            return value

    class ConflictingClient:
        def upload_evidence(self, *_args, **_kwargs):
            raise PlatformClientError("platform_evidence_conflict")

    body = b"\x1f\x8bclassroom-evidence"
    outbox = EvidenceOutbox(
        tmp_path,
        client=ConflictingClient(),
        context_store=ContextStore(),
    )
    entry = outbox.enqueue(
        context().session_id,
        EvidenceChunk(
            sequence=1,
            first_event_sequence=1,
            last_event_sequence=1,
            body=body,
            created_at=datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc),
        ),
    )

    report = outbox.flush_once()

    assert report.quarantined == 1
    assert EvidenceOutbox(tmp_path).recover_pending() == []
    session_directory = tmp_path / "platform-outbox" / context().session_id
    assert (session_directory / f"00000001-{entry.content_sha256}.json").is_file()
    assert '"state":"quarantined"' in (session_directory / "state.json").read_text(
        encoding="utf-8"
    )


def test_restart_replays_a_chunk_when_the_process_dies_after_remote_receipt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class ContextStore:
        def read_registered_context(self):
            return context()

        def save_registered_context(self, value):
            return value

    class IdempotentClient:
        def __init__(self) -> None:
            self.calls = 0

        def upload_evidence(self, stored_context, *, sequence, body, **_kwargs):
            self.calls += 1
            return EvidenceUploadReceipt(
                evidence_id="4ea8479f-c4bb-4645-9c1f-1593afdc187a",
                session_id=stored_context.session_id,
                sequence=sequence,
                content_sha256=sha256(body).hexdigest(),
            )

    now = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)
    client = IdempotentClient()
    interrupted = EvidenceOutbox(
        tmp_path,
        client=client,
        context_store=ContextStore(),
    )
    interrupted.enqueue(
        context().session_id,
        EvidenceChunk(
            sequence=1,
            first_event_sequence=1,
            last_event_sequence=1,
            body=b"\x1f\x8bclassroom-evidence",
            created_at=now,
        ),
    )

    def interrupt_after_remote_receipt(*_args, **_kwargs):
        raise RuntimeError("synthetic process interruption")

    monkeypatch.setattr(
        interrupted,
        "_mark",
        interrupt_after_remote_receipt,
    )

    with pytest.raises(RuntimeError, match="process interruption"):
        interrupted.flush_once()

    report = EvidenceOutbox(
        tmp_path,
        client=client,
        context_store=ContextStore(),
    ).flush_once()

    assert client.calls == 2
    assert report.delivered == 1
    assert EvidenceOutbox(tmp_path).recover_pending() == []
