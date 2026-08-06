"""Private, restart-safe storage for Pilot analysis jobs and attempts."""

from __future__ import annotations

import json
import os
import re
import stat
import threading
from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from .canonical_json import atomic_write_json, canonical_json_bytes


class AnalysisJobConflictError(RuntimeError):
    """Raised when one finalized session is replayed with different input."""


class AnalysisJobIntegrityError(RuntimeError):
    """Raised when a durable job or attempt fails closed validation."""


class AnalysisJobStateError(RuntimeError):
    """Raised for an illegal or conflicting job state transition."""


class AnalysisJobNotFoundError(KeyError):
    """Raised when the canonical job directory does not exist."""


_JOB_KEYS = {
    "schema_version",
    "job_id",
    "session_id",
    "input_snapshot_hash",
    "status",
    "active_attempt_id",
    "attempt_ids",
    "analysis_id",
    "error_code",
    "created_at",
    "updated_at",
}
_PUBLIC_RESULT_KEYS = {
    "schema_version",
    "analysis_id",
    "job_id",
    "attempt_id",
    "session_id",
    "profile_id",
    "profile_version",
    "profile_content_hash",
    "status",
    "dimension_results",
    "provenance",
}
_ATTEMPT_KEYS = {
    "schema_version",
    "attempt_id",
    "job_id",
    "session_id",
    "attempt_number",
    "status",
    "analysis_id",
    "error_code",
    "prompt_snapshot_hash",
    "raw_response_snapshot_hash",
    "retry_reason",
    "started_at",
    "finished_at",
}
_HEX_HASH = re.compile(r"^[0-9a-fA-F]{64}$")
_STABLE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PROCESS_LOCKS_GUARD = threading.RLock()
_PROCESS_LOCKS: dict[tuple[str, str], threading.RLock] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _hash(value: object, *, field: str, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or _HEX_HASH.fullmatch(value) is None:
        raise ValueError(f"{field} must be a SHA-256 hex digest.")
    return value.lower()


def _stable_code(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _STABLE_CODE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a stable code.")
    return value


def _bounded_reason(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("reason must be a non-empty string.")
    normalized = value.strip()
    if not normalized or len(normalized) > 200:
        raise ValueError("reason must contain 1 to 200 characters.")
    if any(ord(character) < 32 for character in normalized):
        raise ValueError("reason must not contain control characters.")
    return normalized


def _aware_time(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnalysisJobIntegrityError(f"{field} is not a timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AnalysisJobIntegrityError(f"{field} is not a timestamp.") from error
    if parsed.tzinfo is None:
        raise AnalysisJobIntegrityError(f"{field} is not timezone-aware.")
    return value


def _write_all(descriptor: int, value: bytes) -> None:
    remaining = memoryview(value)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("Append made no progress.")
        remaining = remaining[written:]


class AnalysisJobStore:
    """Persist one-process Pilot analysis jobs with auditable attempts."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._jobs_root = self.root / "jobs"

    def _lock_for(self, identity: str) -> threading.RLock:
        key = (str(self.root.resolve()), identity)
        with _PROCESS_LOCKS_GUARD:
            return _PROCESS_LOCKS.setdefault(key, threading.RLock())

    def _assert_safe_path(self, path: Path) -> Path:
        root_absolute = self.root.absolute()
        candidate = Path(path).absolute()
        try:
            relative = candidate.relative_to(root_absolute)
        except ValueError as error:
            raise AnalysisJobIntegrityError(
                "Stored path escapes the configured root."
            ) from error
        cursor = root_absolute
        for part in relative.parts:
            cursor = cursor / part
            try:
                mode = cursor.lstat().st_mode
            except FileNotFoundError:
                continue
            except OSError as error:
                raise AnalysisJobIntegrityError(
                    "Stored path cannot be safely inspected."
                ) from error
            if stat.S_ISLNK(mode):
                raise AnalysisJobIntegrityError(
                    "Stored path traverses a symbolic link."
                )
        try:
            candidate.resolve(strict=False).relative_to(self.root.resolve())
        except ValueError as error:
            raise AnalysisJobIntegrityError(
                "Stored path escapes the configured root."
            ) from error
        return candidate

    def _job_dir(self, job_id: object) -> Path:
        return self._jobs_root / _canonical_uuid(job_id, field="job_id")

    def _attempt_path(self, job_id: str, attempt_id: object) -> Path:
        canonical_attempt = _canonical_uuid(attempt_id, field="attempt_id")
        return (
            self._job_dir(job_id)
            / "attempts"
            / f"{canonical_attempt}.json"
        )

    def _read_json(self, path: Path) -> dict[str, object]:
        safe_path = self._assert_safe_path(path)
        try:
            mode = safe_path.lstat().st_mode
        except FileNotFoundError as error:
            raise KeyError(path.name) from error
        except OSError as error:
            raise AnalysisJobIntegrityError(
                f"{path.name} cannot be safely inspected."
            ) from error
        if not stat.S_ISREG(mode):
            raise AnalysisJobIntegrityError(
                f"{path.name} is not a regular file."
            )
        if stat.S_IMODE(mode) != 0o600:
            raise AnalysisJobIntegrityError(
                f"{path.name} does not have private file permissions."
            )
        try:
            raw = safe_path.read_bytes()
            value = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AnalysisJobIntegrityError(
                f"{path.name} is not a valid JSON object."
            ) from error
        if not isinstance(value, dict):
            raise AnalysisJobIntegrityError(
                f"{path.name} is not a JSON object."
            )
        return value

    def _write_json(self, path: Path, value: Mapping[str, object]) -> None:
        safe_parent = self._assert_safe_path(path.parent)
        safe_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        safe_path = self._assert_safe_path(path)
        try:
            existing_mode = safe_path.lstat().st_mode
        except FileNotFoundError:
            existing_mode = None
        except OSError as error:
            raise AnalysisJobIntegrityError(
                f"{path.name} cannot be safely inspected."
            ) from error
        if existing_mode is not None and not stat.S_ISREG(existing_mode):
            raise AnalysisJobIntegrityError(
                f"{path.name} is not a regular file."
            )
        atomic_write_json(safe_path, value)
        os.chmod(safe_path, 0o600)
        descriptor = os.open(safe_parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _validate_job(
        self,
        value: Mapping[str, object],
        *,
        expected_job_id: str,
    ) -> dict[str, object]:
        if set(value) != _JOB_KEYS or value.get("schema_version") != 1:
            raise AnalysisJobIntegrityError(
                "Job projection has unexpected or missing fields."
            )
        try:
            job_id = _canonical_uuid(value.get("job_id"), field="job_id")
            session_id = _canonical_uuid(
                value.get("session_id"), field="session_id"
            )
            input_hash = _hash(
                value.get("input_snapshot_hash"),
                field="input_snapshot_hash",
            )
        except ValueError as error:
            raise AnalysisJobIntegrityError(
                "Job projection identity or hash is invalid."
            ) from error
        if job_id != expected_job_id:
            raise AnalysisJobIntegrityError(
                "Job projection identity does not match its path."
            )
        status_value = value.get("status")
        if status_value not in {
            "queued",
            "running",
            "ready",
            "partial",
            "error",
        }:
            raise AnalysisJobIntegrityError("Job status is invalid.")
        attempt_ids_value = value.get("attempt_ids")
        if not isinstance(attempt_ids_value, list):
            raise AnalysisJobIntegrityError("Job attempt_ids is invalid.")
        try:
            attempt_ids = [
                _canonical_uuid(item, field="attempt_id")
                for item in attempt_ids_value
            ]
        except ValueError as error:
            raise AnalysisJobIntegrityError(
                "Job contains an invalid attempt id."
            ) from error
        if len(attempt_ids) != len(set(attempt_ids)):
            raise AnalysisJobIntegrityError("Job contains duplicate attempts.")
        active = value.get("active_attempt_id")
        analysis = value.get("analysis_id")
        error_code = value.get("error_code")
        try:
            active_id = (
                _canonical_uuid(active, field="active_attempt_id")
                if active is not None
                else None
            )
            analysis_id = (
                _canonical_uuid(analysis, field="analysis_id")
                if analysis is not None
                else None
            )
            normalized_error = (
                _stable_code(error_code, field="error_code")
                if error_code is not None
                else None
            )
        except ValueError as error:
            raise AnalysisJobIntegrityError(
                "Job terminal fields are invalid."
            ) from error
        if active_id is not None and active_id not in attempt_ids:
            raise AnalysisJobIntegrityError(
                "Job active attempt is absent from attempt history."
            )
        if status_value == "queued" and (
            active_id is not None
            or analysis_id is not None
            or normalized_error is not None
        ):
            raise AnalysisJobIntegrityError("Queued job has terminal fields.")
        if status_value == "running" and (
            active_id is None
            or analysis_id is not None
            or normalized_error is not None
        ):
            raise AnalysisJobIntegrityError("Running job fields disagree.")
        if status_value == "ready" and (
            active_id is None
            or analysis_id is None
            or normalized_error is not None
        ):
            raise AnalysisJobIntegrityError("Ready job fields disagree.")
        if status_value == "partial" and (
            active_id is None or analysis_id is None
        ):
            raise AnalysisJobIntegrityError("Partial job fields disagree.")
        if status_value == "error" and (
            active_id is None
            or analysis_id is not None
            or normalized_error is None
        ):
            raise AnalysisJobIntegrityError("Error job fields disagree.")
        created_at = _aware_time(value.get("created_at"), field="created_at")
        updated_at = _aware_time(value.get("updated_at"), field="updated_at")
        return {
            **dict(value),
            "job_id": job_id,
            "session_id": session_id,
            "input_snapshot_hash": input_hash,
            "attempt_ids": attempt_ids,
            "active_attempt_id": active_id,
            "analysis_id": analysis_id,
            "error_code": normalized_error,
            "created_at": created_at,
            "updated_at": updated_at,
        }

    def _validate_attempt(
        self,
        value: Mapping[str, object],
        *,
        expected_job: Mapping[str, object],
        expected_attempt_id: str,
    ) -> dict[str, object]:
        if set(value) != _ATTEMPT_KEYS or value.get("schema_version") != 1:
            raise AnalysisJobIntegrityError(
                "Attempt projection has unexpected or missing fields."
            )
        try:
            attempt_id = _canonical_uuid(
                value.get("attempt_id"), field="attempt_id"
            )
            job_id = _canonical_uuid(value.get("job_id"), field="job_id")
            session_id = _canonical_uuid(
                value.get("session_id"), field="session_id"
            )
        except ValueError as error:
            raise AnalysisJobIntegrityError(
                "Attempt projection identity is invalid."
            ) from error
        if (
            attempt_id != expected_attempt_id
            or job_id != expected_job.get("job_id")
            or session_id != expected_job.get("session_id")
        ):
            raise AnalysisJobIntegrityError(
                "Attempt projection identity does not match its path or job."
            )
        number = value.get("attempt_number")
        if (
            not isinstance(number, int)
            or isinstance(number, bool)
            or number < 1
        ):
            raise AnalysisJobIntegrityError("Attempt number is invalid.")
        status_value = value.get("status")
        if status_value not in {"running", "ready", "partial", "error"}:
            raise AnalysisJobIntegrityError("Attempt status is invalid.")
        try:
            analysis_id = (
                _canonical_uuid(value.get("analysis_id"), field="analysis_id")
                if value.get("analysis_id") is not None
                else None
            )
            error_code = (
                _stable_code(value.get("error_code"), field="error_code")
                if value.get("error_code") is not None
                else None
            )
            prompt_hash = _hash(
                value.get("prompt_snapshot_hash"),
                field="prompt_snapshot_hash",
                optional=True,
            )
            raw_hash = _hash(
                value.get("raw_response_snapshot_hash"),
                field="raw_response_snapshot_hash",
                optional=True,
            )
        except ValueError as error:
            raise AnalysisJobIntegrityError(
                "Attempt terminal fields are invalid."
            ) from error
        retry_reason = value.get("retry_reason")
        if retry_reason is not None:
            try:
                retry_reason = _bounded_reason(retry_reason)
            except ValueError as error:
                raise AnalysisJobIntegrityError(
                    "Attempt retry reason is invalid."
                ) from error
        started_at = _aware_time(value.get("started_at"), field="started_at")
        finished_at = value.get("finished_at")
        if status_value == "running":
            if (
                analysis_id is not None
                or error_code is not None
                or prompt_hash is not None
                or raw_hash is not None
                or finished_at is not None
            ):
                raise AnalysisJobIntegrityError(
                    "Running attempt has terminal fields."
                )
        else:
            if finished_at is None:
                raise AnalysisJobIntegrityError(
                    "Terminal attempt has no finished timestamp."
                )
            _aware_time(finished_at, field="finished_at")
            if status_value in {"ready", "partial"} and analysis_id is None:
                raise AnalysisJobIntegrityError(
                    "Successful attempt has no analysis id."
                )
            if status_value == "ready" and error_code is not None:
                raise AnalysisJobIntegrityError(
                    "Ready attempt has an error code."
                )
            if status_value == "error" and (
                analysis_id is not None or error_code is None
            ):
                raise AnalysisJobIntegrityError(
                    "Error attempt fields disagree."
                )
        return {
            **dict(value),
            "attempt_id": attempt_id,
            "job_id": job_id,
            "session_id": session_id,
            "analysis_id": analysis_id,
            "error_code": error_code,
            "prompt_snapshot_hash": prompt_hash,
            "raw_response_snapshot_hash": raw_hash,
            "retry_reason": retry_reason,
            "started_at": started_at,
            "finished_at": finished_at,
        }

    def _read_job(self, job_id: str) -> dict[str, object]:
        return self._validate_job(
            self._read_json(self._job_dir(job_id) / "job.json"),
            expected_job_id=job_id,
        )

    def _read_complete_job(self, job_id: str) -> dict[str, object]:
        job = self._read_job(job_id)
        expected_ids = list(job["attempt_ids"])  # type: ignore[arg-type]
        attempts_dir = self._assert_safe_path(
            self._job_dir(job_id) / "attempts"
        )
        discovered: set[str] = set()
        if attempts_dir.exists():
            if attempts_dir.is_symlink() or not attempts_dir.is_dir():
                raise AnalysisJobIntegrityError(
                    "Attempts path is not a safe directory."
                )
            for path in attempts_dir.iterdir():
                if path.is_symlink():
                    raise AnalysisJobIntegrityError(
                        "Attempts directory contains a symbolic link."
                    )
                name = path.name
                if name.endswith((".prompt.json", ".raw_response.json")):
                    if not path.is_file():
                        raise AnalysisJobIntegrityError(
                            "Private artifact is not a regular file."
                        )
                    suffix = (
                        ".prompt.json"
                        if name.endswith(".prompt.json")
                        else ".raw_response.json"
                    )
                    try:
                        artifact_attempt_id = _canonical_uuid(
                            name.removesuffix(suffix),
                            field="attempt_id",
                        )
                    except ValueError as error:
                        raise AnalysisJobIntegrityError(
                            "Private artifact has a malformed identity."
                        ) from error
                    if artifact_attempt_id not in expected_ids:
                        raise AnalysisJobIntegrityError(
                            "Private artifact has no matching attempt."
                        )
                    if stat.S_IMODE(path.stat().st_mode) != 0o600:
                        raise AnalysisJobIntegrityError(
                            "Private artifact is not private."
                        )
                    continue
                if not name.endswith(".json") or not path.is_file():
                    raise AnalysisJobIntegrityError(
                        "Attempts directory contains an unexpected entry."
                    )
                try:
                    attempt_id = _canonical_uuid(
                        name.removesuffix(".json"),
                        field="attempt_id",
                    )
                except ValueError as error:
                    raise AnalysisJobIntegrityError(
                        "Attempts directory contains a malformed identity."
                    ) from error
                discovered.add(attempt_id)
        if discovered != set(expected_ids):
            raise AnalysisJobIntegrityError(
                "Attempt history and durable files disagree."
            )
        attempts = [
            self._read_attempt(job, attempt_id)
            for attempt_id in expected_ids
        ]
        for number, attempt in enumerate(attempts, start=1):
            if attempt["attempt_number"] != number:
                raise AnalysisJobIntegrityError(
                    "Attempt numbers do not match append order."
                )
        running = [
            attempt
            for attempt in attempts
            if attempt["status"] == "running"
        ]
        if job["status"] == "running":
            if (
                len(running) != 1
                or running[0]["attempt_id"] != job["active_attempt_id"]
                or not expected_ids
                or job["active_attempt_id"] != expected_ids[-1]
            ):
                raise AnalysisJobIntegrityError(
                    "Running job and attempt history disagree."
                )
        elif running:
            raise AnalysisJobIntegrityError(
                "Non-running job has a running attempt."
            )
        if job["status"] in {"ready", "partial", "error"}:
            if (
                not expected_ids
                or job["active_attempt_id"] != expected_ids[-1]
                or attempts[-1]["status"] != job["status"]
                or attempts[-1]["analysis_id"] != job["analysis_id"]
                or attempts[-1]["error_code"] != job["error_code"]
            ):
                raise AnalysisJobIntegrityError(
                    "Terminal job and latest attempt disagree."
                )
        retry_records = self._read_retry_records(
            str(job["job_id"])
        )
        retry_by_number = {
            int(record["next_attempt_number"]): record
            for record in retry_records
        }
        expected_record_count = (
            0
            if not attempts
            else len(attempts)
            if job["status"] == "queued"
            else len(attempts) - 1
        )
        if len(retry_records) != expected_record_count:
            raise AnalysisJobIntegrityError(
                "Retry audit is not continuous with attempt history."
            )
        for attempt in attempts:
            number = int(attempt["attempt_number"])
            if number == 1:
                if attempt["retry_reason"] is not None:
                    raise AnalysisJobIntegrityError(
                        "Initial attempt must not have a retry reason."
                    )
                continue
            record = retry_by_number.get(number)
            if (
                record is None
                or attempt["retry_reason"] is None
                or attempt["retry_reason"] != record["reason"]
            ):
                raise AnalysisJobIntegrityError(
                    "Retry audit does not match its attempt."
                )
        if job["status"] == "queued" and attempts:
            pending_number = len(attempts) + 1
            if pending_number not in retry_by_number:
                raise AnalysisJobIntegrityError(
                    "Queued retry has no pending audit record."
                )
        return job

    def _read_existing_complete_job(
        self,
        job_id: str,
    ) -> dict[str, object]:
        job_dir = self._assert_safe_path(self._job_dir(job_id))
        try:
            mode = job_dir.lstat().st_mode
        except FileNotFoundError as error:
            raise AnalysisJobNotFoundError(job_id) from error
        except OSError as error:
            raise AnalysisJobIntegrityError(
                "Job directory cannot be safely inspected."
            ) from error
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise AnalysisJobIntegrityError(
                "Job path is not a safe directory."
            )
        try:
            return self._read_complete_job(job_id)
        except KeyError as error:
            raise AnalysisJobIntegrityError(
                "Present job is missing a required durable dependency."
            ) from error

    @contextmanager
    def deletion_reservation(self):
        """Hold root then sorted job locks through one deletion transaction."""

        with self._lock_for("__root__"):
            jobs_root = self._assert_safe_path(self._jobs_root)
            job_ids: list[str] = []
            if jobs_root.exists():
                if not jobs_root.is_dir():
                    raise AnalysisJobIntegrityError(
                        "Jobs root is not a directory."
                    )
                for candidate in sorted(
                    jobs_root.iterdir(),
                    key=lambda path: path.name,
                ):
                    if candidate.is_symlink() or not candidate.is_dir():
                        raise AnalysisJobIntegrityError(
                            "Jobs root contains an unsafe entry."
                        )
                    try:
                        job_ids.append(
                            _canonical_uuid(
                                candidate.name,
                                field="job_id",
                            )
                        )
                    except ValueError as error:
                        raise AnalysisJobIntegrityError(
                            "Jobs root contains a malformed identity."
                        ) from error
            with ExitStack() as locks:
                for job_id in sorted(job_ids):
                    locks.enter_context(self._lock_for(job_id))
                yield

    def _read_attempt(
        self,
        job: Mapping[str, object],
        attempt_id: str,
    ) -> dict[str, object]:
        return self._validate_attempt(
            self._read_json(
                self._attempt_path(str(job["job_id"]), attempt_id)
            ),
            expected_job=job,
            expected_attempt_id=attempt_id,
        )

    def _append_retry_audit(
        self,
        job_id: str,
        *,
        reason: str,
        next_attempt_number: int,
    ) -> None:
        path = self._job_dir(job_id) / "retry_history.jsonl"
        self._assert_safe_path(path.parent).mkdir(
            mode=0o700, parents=True, exist_ok=True
        )
        safe_path = self._assert_safe_path(path)
        try:
            existing_mode = safe_path.lstat().st_mode
        except FileNotFoundError:
            existing_mode = None
        if existing_mode is not None and not stat.S_ISREG(existing_mode):
            raise AnalysisJobIntegrityError(
                "Retry audit destination is not a regular file."
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
                raise AnalysisJobIntegrityError(
                    "Retry audit destination is not a regular file."
                )
            os.fchmod(descriptor, 0o600)
            _write_all(
                descriptor,
                canonical_json_bytes(
                    {
                        "schema_version": 1,
                        "job_id": job_id,
                        "next_attempt_number": next_attempt_number,
                        "reason": reason,
                        "recorded_at": _now_iso(),
                    }
                )
                + b"\n",
            )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _retry_reason(
        self,
        job_id: str,
        *,
        attempt_number: int,
    ) -> str | None:
        match: str | None = None
        for record in self._read_retry_records(job_id):
            if record["next_attempt_number"] == attempt_number:
                match = str(record["reason"])
        return match

    def _read_retry_records(
        self,
        job_id: str,
    ) -> list[dict[str, object]]:
        path = self._job_dir(job_id) / "retry_history.jsonl"
        try:
            mode = self._assert_safe_path(path).lstat().st_mode
        except FileNotFoundError:
            return []
        if not stat.S_ISREG(mode):
            raise AnalysisJobIntegrityError(
                "Retry audit destination is not a regular file."
            )
        if stat.S_IMODE(mode) != 0o600:
            raise AnalysisJobIntegrityError(
                "Retry audit does not have private permissions."
            )
        try:
            lines = path.read_bytes().splitlines(keepends=True)
        except OSError as error:
            raise AnalysisJobIntegrityError("Retry audit is unreadable.") from error
        records: list[dict[str, object]] = []
        expected_number = 2
        for raw_line in lines:
            if not raw_line.endswith(b"\n"):
                raise AnalysisJobIntegrityError(
                    "Retry audit has an incomplete tail."
                )
            try:
                record = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise AnalysisJobIntegrityError(
                    "Retry audit contains invalid JSON."
                ) from error
            if (
                not isinstance(record, dict)
                or set(record)
                != {
                    "schema_version",
                    "job_id",
                    "next_attempt_number",
                    "reason",
                    "recorded_at",
                }
                or record.get("schema_version") != 1
                or record.get("job_id") != job_id
            ):
                raise AnalysisJobIntegrityError(
                    "Retry audit record is invalid."
                )
            try:
                reason = _bounded_reason(record.get("reason"))
            except ValueError as error:
                raise AnalysisJobIntegrityError(
                    "Retry audit reason is invalid."
                ) from error
            _aware_time(record.get("recorded_at"), field="recorded_at")
            number = record.get("next_attempt_number")
            if (
                not isinstance(number, int)
                or isinstance(number, bool)
                or number != expected_number
            ):
                raise AnalysisJobIntegrityError(
                    "Retry audit attempt numbers are not continuous."
                )
            expected_number += 1
            records.append(
                {
                    **record,
                    "next_attempt_number": number,
                    "reason": reason,
                }
            )
        return records

    def _session_deletion_started(self, session_id: str) -> bool:
        """Fail closed once a durable cascade intent exists for a session."""

        path = self._assert_safe_path(
            self.root / "audit" / "session_deletions.jsonl"
        )
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return False
        except OSError as error:
            raise AnalysisJobIntegrityError(
                "Session deletion audit cannot be safely inspected."
            ) from error
        if (
            not stat.S_ISREG(mode)
            or stat.S_IMODE(mode) != 0o600
        ):
            raise AnalysisJobIntegrityError(
                "Session deletion audit is not a private regular file."
            )
        try:
            lines = path.read_bytes().splitlines(keepends=True)
        except OSError as error:
            raise AnalysisJobIntegrityError(
                "Session deletion audit is unreadable."
            ) from error
        deleted = False
        for raw_line in lines:
            if not raw_line.endswith(b"\n"):
                raise AnalysisJobIntegrityError(
                    "Session deletion audit has an incomplete tail."
                )
            try:
                record = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise AnalysisJobIntegrityError(
                    "Session deletion audit contains invalid JSON."
                ) from error
            event = record.get("event") if isinstance(record, dict) else None
            timestamp_field = (
                "recorded_at"
                if event == "deletion_intent"
                else "deleted_at"
                if event == "deletion_completed"
                else None
            )
            expected_keys = (
                {
                    "schema_version",
                    "event",
                    "deleted_session_id",
                    "deleted_job_ids",
                    "deleted_analysis_ids",
                    timestamp_field,
                    "actor",
                }
                if timestamp_field is not None
                else set()
            )
            if (
                not isinstance(record, dict)
                or timestamp_field is None
                or set(record) != expected_keys
                or record.get("schema_version") != 1
            ):
                raise AnalysisJobIntegrityError(
                    "Session deletion audit record is invalid."
                )
            try:
                deleted_session_id = _canonical_uuid(
                    record.get("deleted_session_id"),
                    field="deleted_session_id",
                )
                for field in ("deleted_job_ids", "deleted_analysis_ids"):
                    identifiers = record.get(field)
                    if not isinstance(identifiers, list):
                        raise ValueError(f"{field} must be a list.")
                    for identifier in identifiers:
                        _canonical_uuid(identifier, field=field)
                _aware_time(record.get(timestamp_field), field=timestamp_field)
                actor = record.get("actor")
                if not isinstance(actor, str) or not actor.strip():
                    raise ValueError("actor must be a non-empty string.")
            except ValueError as error:
                raise AnalysisJobIntegrityError(
                    "Session deletion audit record is invalid."
                ) from error
            if deleted_session_id == session_id:
                deleted = True
        return deleted

    def create(
        self,
        *,
        session: Mapping[str, object],
        input_snapshot_hash: str,
    ) -> dict[str, object]:
        if not isinstance(session, Mapping):
            raise ValueError("session must be a mapping.")
        session_id = _canonical_uuid(
            session.get("session_id"), field="session_id"
        )
        if session.get("status") != "finalized":
            raise AnalysisJobStateError(
                "Analysis jobs require a finalized session."
            )
        normalized_hash = _hash(
            input_snapshot_hash,
            field="input_snapshot_hash",
        )
        with self._lock_for("__root__"):
            if self._session_deletion_started(session_id):
                raise AnalysisJobStateError(
                    "Deleted sessions cannot create analysis jobs."
                )
            self._assert_safe_path(self._jobs_root).mkdir(
                mode=0o700, parents=True, exist_ok=True
            )
            for candidate in sorted(self._jobs_root.iterdir()):
                if candidate.is_symlink():
                    raise AnalysisJobIntegrityError(
                        "Jobs root contains a symbolic link."
                    )
                if not candidate.is_dir():
                    raise AnalysisJobIntegrityError(
                        "Jobs root contains an unexpected entry."
                    )
                try:
                    existing_id = _canonical_uuid(
                        candidate.name, field="job_id"
                    )
                except ValueError as error:
                    raise AnalysisJobIntegrityError(
                        "Jobs root contains a malformed identity."
                    ) from error
                with self._lock_for(existing_id):
                    existing = self._read_complete_job(existing_id)
                if existing["session_id"] != session_id:
                    continue
                if existing["input_snapshot_hash"] != normalized_hash:
                    raise AnalysisJobConflictError(
                        "Session already has a job for different input."
                    )
                return existing

            job_id = str(uuid4())
            job_dir = self._job_dir(job_id)
            self._assert_safe_path(job_dir)
            job_dir.mkdir(mode=0o700)
            (job_dir / "attempts").mkdir(mode=0o700)
            now = _now_iso()
            job: dict[str, object] = {
                "schema_version": 1,
                "job_id": job_id,
                "session_id": session_id,
                "input_snapshot_hash": normalized_hash,
                "status": "queued",
                "active_attempt_id": None,
                "attempt_ids": [],
                "analysis_id": None,
                "error_code": None,
                "created_at": now,
                "updated_at": now,
            }
            self._write_json(job_dir / "job.json", job)
            return self._validate_job(job, expected_job_id=job_id)

    def get(self, job_id: str) -> dict[str, object]:
        canonical_job_id = _canonical_uuid(job_id, field="job_id")
        with self._lock_for(canonical_job_id):
            return self._read_existing_complete_job(canonical_job_id)

    def get_attempt(
        self,
        job_id: str,
        attempt_id: str,
    ) -> dict[str, object]:
        canonical_job_id = _canonical_uuid(job_id, field="job_id")
        canonical_attempt_id = _canonical_uuid(
            attempt_id,
            field="attempt_id",
        )
        with self._lock_for(canonical_job_id):
            job = self._read_existing_complete_job(canonical_job_id)
            try:
                return self._read_attempt(job, canonical_attempt_id)
            except KeyError as error:
                raise AnalysisJobIntegrityError(
                    "Present job is missing its requested attempt."
                ) from error

    def load_public_result(
        self,
        job_id: str,
        *,
        session_store,
    ) -> dict[str, object]:
        """Load and validate the terminal result through trusted stores."""

        from .analysis_worker import AnalysisWorker

        canonical_job_id = _canonical_uuid(job_id, field="job_id")
        with self._lock_for(canonical_job_id):
            job = self._read_existing_complete_job(canonical_job_id)
            if job["status"] not in {"ready", "partial"}:
                raise AnalysisJobStateError(
                    "Analysis result is not available."
                )
            analysis_id = job["analysis_id"]
            attempt_id = job["active_attempt_id"]
            if not isinstance(analysis_id, str) or not isinstance(
                attempt_id, str
            ):
                raise AnalysisJobIntegrityError(
                    "Terminal job does not identify its result."
                )
            attempt = self._read_attempt(job, attempt_id)
            result_path = self._assert_safe_path(
                self.root
                / "analyses"
                / analysis_id
                / "result.json"
            )
            try:
                result = self._read_json(result_path)
            except KeyError as error:
                raise AnalysisJobIntegrityError(
                    "Terminal job is missing its public result."
                ) from error
            if set(result) != _PUBLIC_RESULT_KEYS:
                raise AnalysisJobIntegrityError(
                    "Public result shape is not closed."
                )
        session_id = str(job["session_id"])
        session = session_store.read(session_id)
        profile = session_store.read_profile(session_id)
        dictionary = session_store.read_signal_dictionary(session_id)
        with self._lock_for(canonical_job_id):
            current_job = self._read_existing_complete_job(
                canonical_job_id
            )
            if canonical_json_bytes(current_job) != canonical_json_bytes(job):
                raise AnalysisJobStateError(
                    "Analysis job changed while loading its result."
                )
            current_attempt = self._read_attempt(current_job, attempt_id)
            try:
                current_result = self._read_json(result_path)
            except KeyError as error:
                raise AnalysisJobIntegrityError(
                    "Terminal job is missing its public result."
                ) from error
            if (
                canonical_json_bytes(current_attempt)
                != canonical_json_bytes(attempt)
                or canonical_json_bytes(current_result)
                != canonical_json_bytes(result)
            ):
                raise AnalysisJobIntegrityError(
                    "Analysis result changed while it was being loaded."
                )
            validated = AnalysisWorker._public_result(
                result,
                job=job,
                attempt=attempt,
                session=session,
                profile=profile,
                signal_dictionary=dictionary,
            )
            if (
                validated.get("analysis_id") != analysis_id
                or validated.get("job_id") != canonical_job_id
                or validated.get("attempt_id") != attempt_id
                or validated.get("session_id") != session_id
            ):
                raise AnalysisJobIntegrityError(
                    "Public result identity does not match its job."
                )
            return validated

    def list_queued(self) -> list[str]:
        """Return sorted, fully validated queued job identities."""

        with self._lock_for("__root__"):
            if not self._jobs_root.exists():
                return []
            self._assert_safe_path(self._jobs_root)
            if not self._jobs_root.is_dir():
                raise AnalysisJobIntegrityError(
                    "Jobs root is not a directory."
                )
            queued: list[str] = []
            for job_dir in sorted(self._jobs_root.iterdir()):
                if job_dir.is_symlink() or not job_dir.is_dir():
                    raise AnalysisJobIntegrityError(
                        "Jobs root contains an unsafe entry."
                    )
                try:
                    job_id = _canonical_uuid(
                        job_dir.name, field="job_id"
                    )
                except ValueError as error:
                    raise AnalysisJobIntegrityError(
                        "Jobs root contains a malformed identity."
                    ) from error
                with self._lock_for(job_id):
                    job = self._read_complete_job(job_id)
                    if job["status"] == "queued":
                        queued.append(job_id)
            return queued

    def begin_attempt(self, job_id: str) -> dict[str, object]:
        canonical_job_id = _canonical_uuid(job_id, field="job_id")
        with self._lock_for(canonical_job_id):
            job = self._read_existing_complete_job(canonical_job_id)
            if job["status"] != "queued":
                raise AnalysisJobStateError(
                    "Only a queued job can begin an attempt."
                )
            attempt_number = len(job["attempt_ids"]) + 1  # type: ignore[arg-type]
            attempt_id = str(uuid4())
            attempt: dict[str, object] = {
                "schema_version": 1,
                "attempt_id": attempt_id,
                "job_id": canonical_job_id,
                "session_id": job["session_id"],
                "attempt_number": attempt_number,
                "status": "running",
                "analysis_id": None,
                "error_code": None,
                "prompt_snapshot_hash": None,
                "raw_response_snapshot_hash": None,
                "retry_reason": self._retry_reason(
                    canonical_job_id,
                    attempt_number=attempt_number,
                ),
                "started_at": _now_iso(),
                "finished_at": None,
            }
            # Publish the durable attempt before the job can point at it.
            self._write_json(
                self._attempt_path(canonical_job_id, attempt_id),
                attempt,
            )
            attempts = list(job["attempt_ids"])  # type: ignore[arg-type]
            attempts.append(attempt_id)
            job.update(
                {
                    "status": "running",
                    "active_attempt_id": attempt_id,
                    "attempt_ids": attempts,
                    "analysis_id": None,
                    "error_code": None,
                    "updated_at": _now_iso(),
                }
            )
            self._write_json(self._job_dir(canonical_job_id) / "job.json", job)
            return self._validate_attempt(
                attempt,
                expected_job=job,
                expected_attempt_id=attempt_id,
            )

    def finish_attempt(
        self,
        job_id: str,
        attempt_id: str,
        *,
        status: Literal["ready", "partial", "error"],
        analysis_id: str | None,
        error_code: str | None,
        prompt_snapshot_hash: str | None = None,
        raw_response_snapshot_hash: str | None = None,
    ) -> dict[str, object]:
        canonical_job_id = _canonical_uuid(job_id, field="job_id")
        canonical_attempt_id = _canonical_uuid(
            attempt_id, field="attempt_id"
        )
        if status not in {"ready", "partial", "error"}:
            raise ValueError("status must be ready, partial, or error.")
        canonical_analysis_id = (
            _canonical_uuid(analysis_id, field="analysis_id")
            if analysis_id is not None
            else None
        )
        normalized_error = (
            _stable_code(error_code, field="error_code")
            if error_code is not None
            else None
        )
        prompt_hash = _hash(
            prompt_snapshot_hash,
            field="prompt_snapshot_hash",
            optional=True,
        )
        response_hash = _hash(
            raw_response_snapshot_hash,
            field="raw_response_snapshot_hash",
            optional=True,
        )
        if status in {"ready", "partial"} and canonical_analysis_id is None:
            raise ValueError("ready and partial require analysis_id.")
        if status == "ready" and normalized_error is not None:
            raise ValueError("ready does not accept error_code.")
        if status == "error" and (
            canonical_analysis_id is not None or normalized_error is None
        ):
            raise ValueError("error requires error_code and no analysis_id.")

        with self._lock_for(canonical_job_id):
            job = self._read_existing_complete_job(canonical_job_id)
            attempt = self._read_attempt(job, canonical_attempt_id)
            expected_terminal = {
                "status": status,
                "analysis_id": canonical_analysis_id,
                "error_code": normalized_error,
                "prompt_snapshot_hash": prompt_hash,
                "raw_response_snapshot_hash": response_hash,
            }
            if attempt["status"] != "running":
                if all(
                    attempt.get(key) == value
                    for key, value in expected_terminal.items()
                ):
                    if (
                        job["status"] != status
                        or job["active_attempt_id"] != canonical_attempt_id
                        or job["analysis_id"] != canonical_analysis_id
                        or job["error_code"] != normalized_error
                    ):
                        raise AnalysisJobIntegrityError(
                            "Terminal attempt and job projection disagree."
                        )
                    return job
                raise AnalysisJobStateError(
                    "Terminal attempt is immutable."
                )
            if (
                job["status"] != "running"
                or job["active_attempt_id"] != canonical_attempt_id
            ):
                raise AnalysisJobIntegrityError(
                    "Running attempt and job projection disagree."
                )
            attempt.update(
                {
                    **expected_terminal,
                    "finished_at": _now_iso(),
                }
            )
            # Publish terminal attempt first, then its public job projection.
            self._write_json(
                self._attempt_path(canonical_job_id, canonical_attempt_id),
                attempt,
            )
            job.update(
                {
                    "status": status,
                    "analysis_id": canonical_analysis_id,
                    "error_code": normalized_error,
                    "updated_at": _now_iso(),
                }
            )
            self._write_json(self._job_dir(canonical_job_id) / "job.json", job)
            return self._validate_job(job, expected_job_id=canonical_job_id)

    def retry(self, job_id: str, *, reason: str) -> dict[str, object]:
        canonical_job_id = _canonical_uuid(job_id, field="job_id")
        normalized_reason = _bounded_reason(reason)
        with self._lock_for(canonical_job_id):
            job = self._read_existing_complete_job(canonical_job_id)
            if job["status"] == "queued":
                records = self._read_retry_records(canonical_job_id)
                pending_number = len(job["attempt_ids"]) + 1  # type: ignore[arg-type]
                pending = records[-1] if records else None
                if (
                    pending is not None
                    and pending.get("next_attempt_number")
                    == pending_number
                    and pending.get("reason") == normalized_reason
                ):
                    return job
                raise AnalysisJobStateError(
                    "Queued job is not the same pending retry."
                )
            if job["status"] not in {"partial", "error"}:
                raise AnalysisJobStateError(
                    "Only a partial or error job can be retried."
                )
            self._append_retry_audit(
                canonical_job_id,
                reason=normalized_reason,
                next_attempt_number=len(job["attempt_ids"]) + 1,  # type: ignore[arg-type]
            )
            job.update(
                {
                    "status": "queued",
                    "active_attempt_id": None,
                    "analysis_id": None,
                    "error_code": None,
                    "updated_at": _now_iso(),
                }
            )
            self._write_json(self._job_dir(canonical_job_id) / "job.json", job)
            return self._validate_job(job, expected_job_id=canonical_job_id)

    def recover_interrupted(self) -> list[str]:
        with self._lock_for("__root__"):
            if not self._jobs_root.exists():
                return []
            self._assert_safe_path(self._jobs_root)
            if not self._jobs_root.is_dir():
                raise AnalysisJobIntegrityError(
                    "Jobs root is not a directory."
                )
            recovered: list[str] = []
            for job_dir in sorted(self._jobs_root.iterdir()):
                if job_dir.is_symlink() or not job_dir.is_dir():
                    raise AnalysisJobIntegrityError(
                        "Jobs root contains an unsafe entry."
                    )
                try:
                    job_id = _canonical_uuid(job_dir.name, field="job_id")
                except ValueError as error:
                    raise AnalysisJobIntegrityError(
                        "Jobs root contains a malformed identity."
                    ) from error
                with self._lock_for(job_id):
                    job = self._read_complete_job(job_id)
                    if job["status"] != "running":
                        continue
                    active = job["active_attempt_id"]
                    if not isinstance(active, str):
                        raise AnalysisJobIntegrityError(
                            "Running job has no active attempt."
                        )
                    attempt = self._read_attempt(job, active)
                    if attempt["status"] != "running":
                        raise AnalysisJobIntegrityError(
                            "Running job points at a terminal attempt."
                        )
                    attempt.update(
                        {
                            "status": "error",
                            "analysis_id": None,
                            "error_code": "interrupted_after_restart",
                            "prompt_snapshot_hash": None,
                            "raw_response_snapshot_hash": None,
                            "finished_at": _now_iso(),
                        }
                    )
                    self._write_json(
                        self._attempt_path(job_id, active), attempt
                    )
                    self._append_retry_audit(
                        job_id,
                        reason="interrupted_after_restart",
                        next_attempt_number=len(job["attempt_ids"]) + 1,  # type: ignore[arg-type]
                    )
                    job.update(
                        {
                            "status": "queued",
                            "active_attempt_id": None,
                            "analysis_id": None,
                            "error_code": None,
                            "updated_at": _now_iso(),
                        }
                    )
                    self._write_json(job_dir / "job.json", job)
                    recovered.append(job_id)
            return recovered
