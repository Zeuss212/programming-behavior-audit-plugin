#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class DemoVerificationError(RuntimeError):
    """Raised when the latest Demo session is not a successful real-AI run."""


@dataclass(frozen=True)
class DemoVerification:
    session_id: str
    session_dir: Path
    event_count: int
    analysis_status: str
    model_name: str
    legacy_projection: Path | None


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise DemoVerificationError(f"missing required file: {path.name}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise DemoVerificationError(f"invalid JSON file: {path.name}") from error
    if not isinstance(payload, dict):
        raise DemoVerificationError(f"JSON root must be an object: {path.name}")
    return payload


def _parse_ended_at(value: object, session_id: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DemoVerificationError(f"missing ended_at for session: {session_id}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DemoVerificationError(
            f"invalid ended_at for session: {session_id}"
        ) from error
    if parsed.tzinfo is None:
        raise DemoVerificationError(f"ended_at must include timezone: {session_id}")
    return parsed


def _latest_finalized_session(log_root: Path) -> tuple[Path, dict[str, Any]]:
    sessions_root = log_root / "sessions"
    if not sessions_root.is_dir():
        raise DemoVerificationError("no Demo sessions directory found")
    candidates: list[tuple[datetime, Path, dict[str, Any]]] = []
    for session_path in sorted(sessions_root.glob("*/session.json")):
        session = _load_object(session_path)
        if session.get("status") != "finalized":
            continue
        session_id = session_path.parent.name
        ended_at = _parse_ended_at(session.get("ended_at"), session_id)
        candidates.append((ended_at, session_path.parent, session))
    if not candidates:
        raise DemoVerificationError("no finalized Demo session found")
    _, session_dir, session = max(candidates, key=lambda item: item[0])
    return session_dir, session


def _required_object(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise DemoVerificationError(f"missing object in training record: {key}")
    return value


def _required_nonempty_string(parent: dict[str, Any], key: str, label: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DemoVerificationError(f"missing {label}: {key}")
    return value.strip()


def _legacy_projection(
    log_root: Path,
    session: dict[str, Any],
) -> Path | None:
    relative = session.get("legacy_projection_path")
    if relative is None:
        return None
    if not isinstance(relative, str) or not relative.strip():
        raise DemoVerificationError("invalid legacy projection path")
    root = log_root.resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise DemoVerificationError("legacy projection escapes Demo log root")
    return candidate if candidate.is_file() else None


def verify_latest_demo(log_root: Path) -> DemoVerification:
    root = log_root.expanduser().resolve()
    session_dir, session_document = _latest_finalized_session(root)
    session_id = session_dir.name
    if session_document.get("session_id") != session_id:
        raise DemoVerificationError("session ID does not match session directory")

    record = _load_object(session_dir / "training_record.json")
    session = _required_object(record, "session")
    if session.get("session_id") != session_id:
        raise DemoVerificationError("training record belongs to another session")
    if session.get("status") != "finalized":
        raise DemoVerificationError("training record session is not finalized")

    integrity = _required_object(record, "integrity")
    if integrity.get("complete") is not True:
        raise DemoVerificationError("training record integrity is incomplete")

    analysis_status = session.get("analysis_status")
    if analysis_status != "ready":
        raise DemoVerificationError(
            f"AI analysis is not ready: {analysis_status or 'missing'}"
        )
    analysis = _required_object(record, "ai_analysis")
    if analysis.get("status") != "ready":
        raise DemoVerificationError(
            f"AI analysis artifact is not ready: {analysis.get('status') or 'missing'}"
        )
    dimensions = analysis.get("dimension_results")
    if not isinstance(dimensions, list) or not dimensions:
        raise DemoVerificationError("AI analysis has no dimension results")

    events = record.get("behavior_events")
    if not isinstance(events, list):
        raise DemoVerificationError("training record has no behavior events")
    event_count = session.get("event_count")
    if not isinstance(event_count, int) or event_count <= 0:
        raise DemoVerificationError("event count must be a positive integer")
    if event_count != len(events):
        raise DemoVerificationError(
            f"event count mismatch: session={event_count}, events={len(events)}"
        )

    has_edit = any(
        isinstance(event, dict) and event.get("segment_type") == "code_writing"
        for event in events
    )
    has_failure = any(
        isinstance(event, dict)
        and event.get("segment_type") == "code_execution"
        and event.get("execution_result") in {"failure", "error"}
        for event in events
    )
    has_success = any(
        isinstance(event, dict)
        and event.get("segment_type") == "code_execution"
        and event.get("execution_result") == "success"
        for event in events
    )
    for present, label in (
        (has_edit, "code edit"),
        (has_failure, "execution error"),
        (has_success, "execution success"),
    ):
        if not present:
            raise DemoVerificationError(
                f"missing required behavior event: {label}"
            )

    provenance = _required_object(analysis, "provenance")
    model_name = _required_nonempty_string(
        provenance,
        "model_name",
        "analysis provenance",
    )
    _required_nonempty_string(
        provenance,
        "prompt_version",
        "analysis provenance",
    )
    _required_nonempty_string(
        provenance,
        "input_snapshot_hash",
        "analysis provenance",
    )

    logs_dir = session_dir / "logs"
    operation_log = _load_object(logs_dir / "operation_log.json")
    operation_session = _required_object(operation_log, "session")
    if operation_session.get("session_id") != session_id:
        raise DemoVerificationError(
            "operation log belongs to another session"
        )
    operation_events = operation_log.get("events")
    expected_events = sorted(
        events,
        key=lambda event: (
            event.get("session_seq", 0) if isinstance(event, dict) else 0
        ),
    )
    if operation_events != expected_events:
        raise DemoVerificationError(
            "operation log events do not match training record"
        )
    if operation_log.get("integrity") != integrity:
        raise DemoVerificationError(
            "operation log integrity does not match training record"
        )

    analysis_log = _load_object(logs_dir / "analysis_log.json")
    analysis_session = _required_object(analysis_log, "session")
    if (
        analysis_session.get("session_id") != session_id
        or analysis_session.get("analysis_status") != "ready"
    ):
        raise DemoVerificationError(
            "analysis log belongs to another session or is not ready"
        )
    if analysis_log.get("ai_analysis") != analysis:
        raise DemoVerificationError(
            "analysis log does not match training record"
        )
    if analysis_log.get("teacher_reviews") != record.get(
        "teacher_reviews",
        [],
    ):
        raise DemoVerificationError(
            "analysis log reviews do not match training record"
        )
    if analysis_log.get("integrity") != integrity:
        raise DemoVerificationError(
            "analysis log integrity does not match training record"
        )

    process_path = logs_dir / "process_log.md"
    try:
        process_contents = process_path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise DemoVerificationError(
            "missing required file: process_log.md"
        ) from error
    except (OSError, UnicodeError) as error:
        raise DemoVerificationError(
            "invalid text file: process_log.md"
        ) from error
    required_process_markers = (
        "# 编程行为过程日志",
        "## 会话摘要",
        f"`{session_id}`",
        "## 时间线",
        "## 行为明细",
    )
    if any(marker not in process_contents for marker in required_process_markers):
        raise DemoVerificationError(
            "process log is incomplete or belongs to another session"
        )

    return DemoVerification(
        session_id=session_id,
        session_dir=session_dir,
        event_count=event_count,
        analysis_status=analysis_status,
        model_name=model_name,
        legacy_projection=_legacy_projection(root, session_document),
    )


def _sha256(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def export_verified_demo(log_root: Path, export_dir: Path) -> Path:
    verification = verify_latest_demo(log_root)
    sources: dict[str, Path] = {
        "session/training_record.json": verification.session_dir
        / "training_record.json",
        "session/session.json": verification.session_dir / "session.json",
        "session/profile.json": verification.session_dir / "profile.json",
        "session/signal_dictionary.json": verification.session_dir
        / "signal_dictionary.json",
        "session/raw_events.jsonl": verification.session_dir
        / "raw_events.jsonl",
        "session/logs/operation_log.json": verification.session_dir
        / "logs"
        / "operation_log.json",
        "session/logs/process_log.md": verification.session_dir
        / "logs"
        / "process_log.md",
        "session/logs/analysis_log.json": verification.session_dir
        / "logs"
        / "analysis_log.json",
    }
    if verification.legacy_projection is not None:
        sources["legacy/session.md"] = verification.legacy_projection

    archive_contents: dict[str, bytes] = {}
    for archive_name, source in sources.items():
        try:
            archive_contents[archive_name] = source.read_bytes()
        except FileNotFoundError as error:
            raise DemoVerificationError(
                f"missing required export file: {source.name}"
            ) from error
        except OSError as error:
            raise DemoVerificationError(
                f"cannot read required export file: {source.name}"
            ) from error

    exported_at = datetime.now(timezone.utc)
    manifest = {
        "schema_version": 1,
        "session_id": verification.session_id,
        "exported_at": exported_at.isoformat(),
        "files": [
            {"path": name, "sha256": _sha256(contents)}
            for name, contents in sorted(archive_contents.items())
        ],
    }
    manifest_bytes = (
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")

    target_dir = export_dir.expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = exported_at.strftime("%Y%m%dT%H%M%S%fZ")
    archive_path = target_dir / (
        f"demo-{verification.session_id}-{timestamp}.zip"
    )
    try:
        with zipfile.ZipFile(
            archive_path,
            mode="x",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for name, contents in sorted(archive_contents.items()):
                archive.writestr(name, contents)
            archive.writestr("manifest.json", manifest_bytes)
    except FileExistsError as error:
        raise DemoVerificationError(
            f"refusing to overwrite existing export: {archive_path.name}"
        ) from error
    except OSError as error:
        raise DemoVerificationError("failed to write Demo export") from error
    return archive_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify and optionally export the latest macOS real-AI Demo."
    )
    parser.add_argument("--log-root", type=Path, required=True)
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--export-dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.export and arguments.export_dir is None:
        print("CONFIG ERROR: --export requires --export-dir", file=sys.stderr)
        return 3
    try:
        verification = verify_latest_demo(arguments.log_root)
        archive = (
            export_verified_demo(arguments.log_root, arguments.export_dir)
            if arguments.export
            else None
        )
    except DemoVerificationError as error:
        print(f"DEMO FAIL: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"CONFIG ERROR: {error}", file=sys.stderr)
        return 3

    print("DEMO PASS")
    print(f"Session: {verification.session_id}")
    print(f"Analysis: {verification.analysis_status}")
    print(f"Events: {verification.event_count}")
    print(f"Model: {verification.model_name}")
    if archive is not None:
        print(f"Export: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
