"""One durable submit path shared by students and the classroom deadline."""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

from .canonical_json import atomic_write_json
from .classroom_ai_analysis_input import build_analysis_input
from .classroom_mastery import evaluate_knowledge_points
from .platform_client import PlatformClientError
from .platform_context_store import RegisteredPlatformContext

_SUBMISSION_DIRECTORY: Final = "platform-submissions"
_STATE_FILENAME_SUFFIX: Final = ".json"
_STATE_SCHEMA_VERSION: Final = 1
_REASONS: Final = frozenset({"student_manual", "system_deadline"})
_LOCK_GUARD = threading.RLock()
_LOCKS: dict[tuple[str, str], threading.RLock] = {}


class SubmissionCoordinatorError(RuntimeError):
    """Raised when a local classroom session cannot be submitted safely."""


@dataclass(frozen=True)
class SubmissionResult:
    session_id: str
    status: str
    reason: str
    brief_id: str | None
    revision: int | None
    remote_status: str | None


def _canonical_session_id(value: object) -> str:
    if not isinstance(value, str):
        raise SubmissionCoordinatorError("session_id must be a canonical UUID.")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise SubmissionCoordinatorError("session_id must be a canonical UUID.") from error
    if str(parsed) != value:
        raise SubmissionCoordinatorError("session_id must be a canonical UUID.")
    return value


def _aware_time(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise SubmissionCoordinatorError(f"{field} must include a timezone.")
    return value.astimezone(timezone.utc)


class SubmissionCoordinator:
    """Persist the submit outcome so every trigger observes one logical brief."""

    def __init__(
        self,
        root: Path,
        *,
        session_store,
        session_log_service,
        outbox,
        client,
        context_store,
    ) -> None:
        self._root = Path(root)
        self._session_store = session_store
        self._session_log_service = session_log_service
        self._outbox = outbox
        self._client = client
        self._context_store = context_store

    def submit(
        self,
        session_id: str,
        *,
        reason: str,
        cutoff_at: datetime,
        request_ai_analysis: bool = False,
    ) -> SubmissionResult:
        canonical_session_id = _canonical_session_id(session_id)
        if reason not in _REASONS:
            raise SubmissionCoordinatorError("submission reason is invalid.")
        cutoff = _aware_time(cutoff_at, field="cutoff_at")
        with self._lock_for(canonical_session_id):
            existing = self._read_state(canonical_session_id)
            if existing is not None and existing["status"] == "submitted":
                return self._result_from_state(existing)

            context = self._registered_context(canonical_session_id)
            if existing is None:
                payload = self._prepare_payload(
                    canonical_session_id,
                    request_ai_analysis=request_ai_analysis,
                )
                state = {
                    "schema_version": _STATE_SCHEMA_VERSION,
                    "session_id": canonical_session_id,
                    "reason": reason,
                    "cutoff_at": cutoff.isoformat(),
                    "status": "submitting",
                    "payload": payload,
                    "remote_receipt": None,
                }
                self._write_state(canonical_session_id, state)
            else:
                state = existing
                payload = state["payload"]
                if not isinstance(payload, dict):
                    raise SubmissionCoordinatorError("Stored submission payload is invalid.")
                if payload.get("request_ai_analysis") is not request_ai_analysis:
                    raise SubmissionCoordinatorError(
                        "AI analysis consent does not match the durable submission."
                    )

            try:
                receipt = self._client.submit_brief(context, payload)
                receipt_data = self._receipt_data(canonical_session_id, receipt)
            except PlatformClientError:
                pending = {**state, "status": "pending_upload"}
                self._write_state(canonical_session_id, pending)
                return SubmissionResult(
                    session_id=canonical_session_id,
                    status="pending_upload",
                    reason=str(state["reason"]),
                    brief_id=None,
                    revision=None,
                    remote_status=None,
                )

            submitted = {
                **state,
                "status": "submitted",
                "remote_receipt": receipt_data,
            }
            self._write_state(canonical_session_id, submitted)
            return self._result_from_state(submitted)

    def _prepare_payload(
        self,
        session_id: str,
        *,
        request_ai_analysis: bool,
    ) -> dict[str, object]:
        session = self._session_store.read(session_id)
        status = session.get("status")
        if status == "abandoned":
            self._session_store.recover(
                session_id,
                actor="classroom_submission_coordinator",
                reason="deadline_or_manual_submission",
            )
            session = self._session_store.read(session_id)
            status = session.get("status")
        if status == "collecting":
            last_sequence = session.get("last_contiguous_sequence")
            if not isinstance(last_sequence, int) or isinstance(last_sequence, bool):
                raise SubmissionCoordinatorError("Local session sequence is invalid.")
            self._session_store.finalize(session_id, last_sequence=last_sequence)
        elif status != "finalized":
            raise SubmissionCoordinatorError("Local session cannot be finalized.")

        brief = self._session_log_service.export_classroom_brief(session_id)
        self._outbox.flush_once()
        profile = self._session_store.read_profile(session_id)
        delivered_entries = self._delivered_entries(session_id)
        detail = self._session_log_service.get_detail(session_id)
        delivered_detail = self._delivered_detail(detail, delivered_entries)
        evidence_refs = self._evidence_refs(delivered_detail, delivered_entries)
        mastery_rows = (
            evaluate_knowledge_points(
                profile,
                delivered_detail,
                evidence_refs,
            )
            if self._has_automatic_evaluation_rules(profile)
            else None
        )
        payload = self._remote_payload(
            session_id,
            profile,
            brief,
            evidence_refs=evidence_refs,
            mastery_rows=mastery_rows,
        )
        payload["submission_id"] = str(uuid4())
        payload["request_ai_analysis"] = request_ai_analysis
        if request_ai_analysis:
            payload["analysis_input"] = build_analysis_input(
                profile,
                detail,
                delivered_entries,
            )
        return payload

    def _remote_payload(
        self,
        session_id: str,
        profile: Mapping[str, object],
        brief: Mapping[str, object],
        *,
        evidence_refs: list[str],
        mastery_rows: Sequence[Mapping[str, object]] | None,
    ) -> dict[str, object]:
        knowledge_points = profile.get("knowledge_points")
        if not isinstance(knowledge_points, list) or not knowledge_points:
            raise SubmissionCoordinatorError("Published plan has no knowledge points.")

        points: list[tuple[str, str]] = []
        for point in knowledge_points:
            if not isinstance(point, Mapping):
                raise SubmissionCoordinatorError("Published knowledge point is invalid.")
            point_id = point.get("id")
            name = point.get("name")
            if not isinstance(point_id, str) or not isinstance(name, str):
                raise SubmissionCoordinatorError("Published knowledge point is invalid.")
            points.append((point_id, name))

        rows: list[dict[str, object]] = []
        if mastery_rows is not None:
            if len(mastery_rows) != len(points):
                raise SubmissionCoordinatorError("Automatic mastery result is invalid.")
            for row, (point_id, name) in zip(mastery_rows, points, strict=True):
                if row.get("knowledge_point_id") != point_id or row.get("name") != name:
                    raise SubmissionCoordinatorError("Automatic mastery result is invalid.")
                rows.append(dict(row))
        else:
            for point_id, name in points:
                rows.append(
                    {
                        "knowledge_point_id": point_id,
                        "name": name,
                        "status": "not_demonstrated",
                        "evidence_refs": evidence_refs,
                        "demonstrated": "基础简报未进行逐项自动判定。",
                        "gap": "需要结合过程证据确认该知识点的掌握情况。",
                        "teacher_suggestion": "查看关联过程证据，并就关键步骤追问学生。",
                    }
                )
        highlights = brief.get("process_highlights")
        process_overview = (
            [item for item in highlights if isinstance(item, str)][:3]
            if isinstance(highlights, list)
            else []
        )
        run_summary = brief.get("run_summary")
        if isinstance(run_summary, str) and run_summary:
            process_overview.append(run_summary)
        attention = brief.get("attention_message")
        issues = [attention] if isinstance(attention, str) and attention else []
        summary = "；".join(process_overview) or "已生成本节课基础行为简报。"
        return {
            "summary": summary[:1000],
            "knowledge_points": rows,
            "process_overview": process_overview[:5],
            "issues": issues[:3],
            "reason": "",  # Filled from the durable state immediately before submit.
        }

    @staticmethod
    def _has_automatic_evaluation_rules(profile: Mapping[str, object]) -> bool:
        knowledge_points = profile.get("knowledge_points")
        return isinstance(knowledge_points, list) and any(
            isinstance(point, Mapping) and "automatic_evaluation" in point
            for point in knowledge_points
        )

    def _delivered_entries(self, session_id: str) -> list[object]:
        return [
            entry
            for entry in self._outbox.list_entries(session_id)
            if getattr(entry, "state", None) == "delivered"
        ]

    @classmethod
    def _delivered_detail(
        cls,
        detail: Mapping[str, object],
        entries: Sequence[object],
    ) -> dict[str, object]:
        raw_events = detail.get("behavior_events")
        events = [
            event
            for event in raw_events
            if isinstance(event, Mapping)
            and cls._remote_event_id(event.get("session_seq"), entries) is not None
        ] if isinstance(raw_events, list) else []
        return {**detail, "behavior_events": events}

    @classmethod
    def _evidence_refs(
        cls,
        detail: Mapping[str, object],
        entries: Sequence[object],
    ) -> list[str]:
        raw_events = detail.get("behavior_events")
        references = [
            event_id
            for event in raw_events
            if isinstance(event, Mapping)
            and (event_id := cls._remote_event_id(event.get("session_seq"), entries))
            is not None
        ] if isinstance(raw_events, list) else []
        return references[:10] or ["session#missing-evidence"]

    @staticmethod
    def _remote_event_id(sequence: object, entries: Sequence[object]) -> str | None:
        if not isinstance(sequence, int) or isinstance(sequence, bool):
            return None
        for entry in entries:
            chunk = getattr(entry, "sequence", None)
            first = getattr(entry, "first_event_sequence", None)
            last = getattr(entry, "last_event_sequence", None)
            if (
                isinstance(chunk, int)
                and not isinstance(chunk, bool)
                and isinstance(first, int)
                and not isinstance(first, bool)
                and isinstance(last, int)
                and not isinstance(last, bool)
                and first <= sequence <= last
            ):
                return f"chunk-{chunk}#event-{sequence}"
        return None

    def _registered_context(self, session_id: str) -> RegisteredPlatformContext:
        context = self._context_store.read_registered_context()
        if context is None or context.session_id != session_id:
            raise SubmissionCoordinatorError("Classroom session is not registered locally.")
        return context

    def _state_path(self, session_id: str) -> Path:
        return self._root / _SUBMISSION_DIRECTORY / f"{session_id}{_STATE_FILENAME_SUFFIX}"

    def _lock_for(self, session_id: str) -> threading.RLock:
        key = (str(self._root.resolve()), session_id)
        with _LOCK_GUARD:
            return _LOCKS.setdefault(key, threading.RLock())

    def _read_state(self, session_id: str) -> dict[str, object] | None:
        path = self._state_path(session_id)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SubmissionCoordinatorError("Stored submission state is unreadable.") from error
        required = {
            "schema_version",
            "session_id",
            "reason",
            "cutoff_at",
            "status",
            "payload",
            "remote_receipt",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise SubmissionCoordinatorError("Stored submission state is invalid.")
        if value.get("schema_version") != _STATE_SCHEMA_VERSION or value.get("session_id") != session_id:
            raise SubmissionCoordinatorError("Stored submission state is invalid.")
        if value.get("reason") not in _REASONS or value.get("status") not in {
            "submitting",
            "pending_upload",
            "submitted",
        }:
            raise SubmissionCoordinatorError("Stored submission state is invalid.")
        return value

    def _write_state(self, session_id: str, state: Mapping[str, object]) -> None:
        path = self._state_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = dict(state)
        reason = payload.get("reason")
        if not isinstance(reason, str):
            raise SubmissionCoordinatorError("Stored submission reason is invalid.")
        remote_payload = payload.get("payload")
        if not isinstance(remote_payload, dict):
            raise SubmissionCoordinatorError("Stored submission payload is invalid.")
        remote_payload["reason"] = reason
        atomic_write_json(path, payload)

    @staticmethod
    def _receipt_data(session_id: str, value: object) -> dict[str, object]:
        if isinstance(value, Mapping):
            response_session_id = value.get("session_id")
            brief_id = value.get("brief_id")
            revision = value.get("revision")
            status = value.get("status")
        else:
            response_session_id = getattr(value, "session_id", None)
            brief_id = getattr(value, "brief_id", None)
            revision = getattr(value, "revision", None)
            status = getattr(value, "status", None)
        if (
            response_session_id != session_id
            or not isinstance(brief_id, str)
            or not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision < 1
            or status not in {"completed", "partial"}
        ):
            raise SubmissionCoordinatorError("Classroom service submit receipt is invalid.")
        return {"brief_id": brief_id, "revision": revision, "status": status}

    @staticmethod
    def _result_from_state(state: Mapping[str, object]) -> SubmissionResult:
        remote = state.get("remote_receipt")
        if not isinstance(remote, Mapping):
            raise SubmissionCoordinatorError("Stored submission receipt is invalid.")
        brief_id = remote.get("brief_id")
        revision = remote.get("revision")
        remote_status = remote.get("status")
        if not isinstance(brief_id, str) or not isinstance(revision, int):
            raise SubmissionCoordinatorError("Stored submission receipt is invalid.")
        return SubmissionResult(
            session_id=_canonical_session_id(state.get("session_id")),
            status="submitted",
            reason=str(state["reason"]),
            brief_id=brief_id,
            revision=revision,
            remote_status=remote_status if isinstance(remote_status, str) else None,
        )
