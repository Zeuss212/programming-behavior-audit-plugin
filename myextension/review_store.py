"""Append-only teacher review history for persisted analyses."""

from __future__ import annotations

import json
import os
import re
import stat
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from .canonical_json import canonical_json_bytes, normalize_json_value


class ReviewConflictError(RuntimeError):
    """Raised when a teacher submits a stale expected revision."""


class ReviewIntegrityError(RuntimeError):
    """Raised when review history cannot be trusted."""


_CORRECTION_KEYS = {
    "revision",
    "decision_status",
    "evidence_status",
    "level_code",
    "evidence_event_ids",
    "reason_code",
    "comment",
}
_RECORD_KEYS = {
    "schema_version",
    "analysis_id",
    "dimension_code",
    *_CORRECTION_KEYS,
    "reviewed_at",
}
_DIMENSION_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_LOCKS_GUARD = threading.RLock()
_LOCKS: dict[tuple[str, str], threading.RLock] = {}


def _canonical_uuid(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a canonical UUID.")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as error:
        raise ValueError(f"{field} must be a canonical UUID.") from error
    if str(parsed) != value:
        raise ValueError(f"{field} must be a canonical UUID.")
    return value


def _dimension_code(value: object) -> str:
    if not isinstance(value, str) or _DIMENSION_CODE.fullmatch(value) is None:
        raise ValueError("dimension_code must be a safe canonical code.")
    return value


def _aware_timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise ReviewIntegrityError("reviewed_at is invalid.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ReviewIntegrityError("reviewed_at is invalid.") from error
    if parsed.tzinfo is None:
        raise ReviewIntegrityError("reviewed_at is not timezone-aware.")
    return value


def _write_all(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("Review append made no progress.")
        remaining = remaining[written:]


class ReviewStore:
    """Store private review records without mutating analysis results."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _lock_for(self, analysis_id: str) -> threading.RLock:
        key = (str(self.root.resolve()), analysis_id)
        with _LOCKS_GUARD:
            return _LOCKS.setdefault(key, threading.RLock())

    def _path(self, analysis_id: object) -> Path:
        canonical = _canonical_uuid(analysis_id, field="analysis_id")
        return (
            self.root
            / "analyses"
            / canonical
            / "review_history.jsonl"
        )

    def _assert_safe_path(self, path: Path) -> Path:
        root_absolute = self.root.absolute()
        candidate = Path(path).absolute()
        try:
            relative = candidate.relative_to(root_absolute)
        except ValueError as error:
            raise ReviewIntegrityError(
                "Review path escapes the configured root."
            ) from error
        cursor = root_absolute
        for part in relative.parts:
            cursor = cursor / part
            try:
                mode = cursor.lstat().st_mode
            except FileNotFoundError:
                continue
            except OSError as error:
                raise ReviewIntegrityError(
                    "Review path cannot be inspected."
                ) from error
            if stat.S_ISLNK(mode):
                raise ReviewIntegrityError(
                    "Review path traverses a symbolic link."
                )
        try:
            candidate.resolve(strict=False).relative_to(self.root.resolve())
        except ValueError as error:
            raise ReviewIntegrityError(
                "Review path escapes the configured root."
            ) from error
        return candidate

    @staticmethod
    def _validate_correction(
        correction: Mapping[str, object],
    ) -> dict[str, object]:
        try:
            normalized = normalize_json_value(correction)
        except (TypeError, ValueError) as error:
            raise ValueError("correction must be canonical JSON.") from error
        if not isinstance(normalized, dict) or set(normalized) != _CORRECTION_KEYS:
            raise ValueError("correction has unexpected or missing fields.")
        revision = normalized["revision"]
        if (
            not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision < 0
        ):
            raise ValueError("correction revision is invalid.")
        decision = normalized["decision_status"]
        evidence = normalized["evidence_status"]
        level = normalized["level_code"]
        reason = normalized["reason_code"]
        event_ids = normalized["evidence_event_ids"]
        comment = normalized["comment"]
        if decision not in {"resolved", "needs_review"}:
            raise ValueError("decision_status is invalid.")
        if evidence not in {
            "observed",
            "not_observed",
            "insufficient_evidence",
            "not_computable",
            None,
        }:
            raise ValueError("evidence_status is invalid.")
        if level not in {"possible", "clear", None}:
            raise ValueError("level_code is invalid.")
        if reason not in {
            "teacher_confirmed",
            "teacher_correction",
            "uncertain",
        }:
            raise ValueError("reason_code is invalid.")
        if (
            not isinstance(event_ids, list)
            or len(event_ids) > 100
            or not all(
                isinstance(item, str)
                and 0 < len(item) <= 200
                and not any(ord(character) < 32 for character in item)
                for item in event_ids
            )
        ):
            raise ValueError("evidence_event_ids is invalid.")
        if (
            not isinstance(comment, str)
            or not comment.strip()
            or len(comment.strip()) > 1_000
            or "\x00" in comment
        ):
            raise ValueError("comment must contain 1 to 1000 characters.")
        if decision == "needs_review" and (
            evidence is not None or level is not None
        ):
            raise ValueError(
                "needs_review must not assert evidence or a level."
            )
        if decision == "resolved" and evidence is None:
            raise ValueError("resolved requires evidence_status.")
        if evidence == "observed" and (
            level not in {"possible", "clear"} or not event_ids
        ):
            raise ValueError(
                "observed requires a level and at least one evidence event."
            )
        if evidence != "observed" and level is not None:
            raise ValueError(
                "Only observed evidence may carry a level."
            )
        normalized["comment"] = comment.strip()
        return normalized

    def _validate_record(
        self,
        value: object,
        *,
        expected_analysis_id: str,
    ) -> dict[str, object]:
        if (
            not isinstance(value, dict)
            or set(value) != _RECORD_KEYS
            or value.get("schema_version") != 1
        ):
            raise ReviewIntegrityError("Review record is invalid.")
        try:
            analysis_id = _canonical_uuid(
                value.get("analysis_id"), field="analysis_id"
            )
            dimension = _dimension_code(value.get("dimension_code"))
            correction = self._validate_correction(
                {key: value[key] for key in _CORRECTION_KEYS}
            )
        except ValueError as error:
            raise ReviewIntegrityError("Review record is invalid.") from error
        if analysis_id != expected_analysis_id:
            raise ReviewIntegrityError(
                "Review identity does not match its path."
            )
        _aware_timestamp(value.get("reviewed_at"))
        return {
            "schema_version": 1,
            "analysis_id": analysis_id,
            "dimension_code": dimension,
            **correction,
            "reviewed_at": value["reviewed_at"],
        }

    def _read_all(self, analysis_id: str) -> list[dict[str, object]]:
        path = self._assert_safe_path(self._path(analysis_id))
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return []
        except OSError as error:
            raise ReviewIntegrityError(
                "Review history cannot be inspected."
            ) from error
        if not stat.S_ISREG(mode):
            raise ReviewIntegrityError(
                "Review history is not a regular file."
            )
        if stat.S_IMODE(mode) != 0o600:
            raise ReviewIntegrityError(
                "Review history does not have private permissions."
            )
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise ReviewIntegrityError(
                "Review history is unreadable."
            ) from error
        if raw and not raw.endswith(b"\n"):
            raise ReviewIntegrityError(
                "Review history has an incomplete tail."
            )
        records: list[dict[str, object]] = []
        last_revision_by_dimension: dict[str, int] = {}
        for raw_line in raw.splitlines():
            try:
                value = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ReviewIntegrityError(
                    "Review history contains invalid JSON."
                ) from error
            record = self._validate_record(
                value,
                expected_analysis_id=analysis_id,
            )
            code = str(record["dimension_code"])
            expected = last_revision_by_dimension.get(code, 0) + 1
            if record["revision"] != expected:
                raise ReviewIntegrityError(
                    "Review history revisions are not continuous."
                )
            last_revision_by_dimension[code] = expected
            records.append(record)
        return records

    def append(
        self,
        analysis_id: str,
        dimension_code: str,
        *,
        expected_revision: int,
        correction: Mapping[str, object],
    ) -> dict[str, object]:
        canonical_analysis_id = _canonical_uuid(
            analysis_id, field="analysis_id"
        )
        canonical_dimension = _dimension_code(dimension_code)
        if (
            not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision < 0
        ):
            raise ValueError("expected_revision must be non-negative.")
        validated = self._validate_correction(correction)
        if validated["revision"] != expected_revision:
            raise ValueError(
                "correction.revision must equal expected_revision."
            )

        with self._lock_for(canonical_analysis_id):
            history = self._read_all(canonical_analysis_id)
            current = max(
                (
                    int(record["revision"])
                    for record in history
                    if record["dimension_code"] == canonical_dimension
                ),
                default=0,
            )
            if current != expected_revision:
                raise ReviewConflictError(
                    "Review revision no longer matches."
                )
            record = {
                "schema_version": 1,
                "analysis_id": canonical_analysis_id,
                "dimension_code": canonical_dimension,
                **validated,
                "revision": current + 1,
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
            }
            path = self._path(canonical_analysis_id)
            self._assert_safe_path(path.parent).mkdir(
                mode=0o700, parents=True, exist_ok=True
            )
            safe_path = self._assert_safe_path(path)
            try:
                existing_mode = safe_path.lstat().st_mode
            except FileNotFoundError:
                existing_mode = None
            if existing_mode is not None and not stat.S_ISREG(existing_mode):
                raise ReviewIntegrityError(
                    "Review history is not a regular file."
                )
            descriptor = os.open(
                safe_path,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_APPEND
                | os.O_NONBLOCK
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ReviewIntegrityError(
                        "Review history is not a regular file."
                    )
                os.fchmod(descriptor, 0o600)
                _write_all(
                    descriptor,
                    canonical_json_bytes(record) + b"\n",
                )
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return record

    def list(
        self,
        analysis_id: str,
        dimension_code: str,
    ) -> list[dict[str, object]]:
        canonical_analysis_id = _canonical_uuid(
            analysis_id, field="analysis_id"
        )
        canonical_dimension = _dimension_code(dimension_code)
        with self._lock_for(canonical_analysis_id):
            return [
                record
                for record in self._read_all(canonical_analysis_id)
                if record["dimension_code"] == canonical_dimension
            ]

    def list_all(self, analysis_id: str) -> list[dict[str, object]]:
        """Return validated append history in durable file order."""

        canonical_analysis_id = _canonical_uuid(
            analysis_id, field="analysis_id"
        )
        with self._lock_for(canonical_analysis_id):
            return [
                dict(normalize_json_value(record))
                for record in self._read_all(canonical_analysis_id)
            ]
