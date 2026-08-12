"""Canonical, crash-recoverable storage for one-process pilot sessions."""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from .behavior_log_store import projection_index_lock, write_session_projection
from .canonical_json import (
    atomic_write_json,
    canonical_json_bytes,
    normalize_json_value,
    sha256_json,
)
from .session_log_artifacts import SESSION_LOG_FILENAMES


class InvalidSessionIdError(ValueError):
    """Raised before an untrusted session identifier reaches a path."""


class SegmentConflictError(RuntimeError):
    """Raised when a segment id is replayed with different content."""


class SequenceGapError(RuntimeError):
    """Raised when a batch or finalization skips canonical sequence numbers."""

    def __init__(self, missing_ranges: list[tuple[int, int]]) -> None:
        super().__init__(f"Missing session sequence ranges: {missing_ranges}")
        self.missing_ranges = missing_ranges


class SessionIntegrityError(RuntimeError):
    """Raised when durable session artifacts disagree."""


class SessionStateError(RuntimeError):
    """Raised for an invalid lifecycle transition."""


class SessionNotFoundError(KeyError):
    """Raised when the canonical session directory does not exist."""


_PROFILE_CONTENT_V1_KEYS = (
    "schema_version",
    "profile_id",
    "version",
    "problem_id",
    "title",
    "dimensions",
)
_PROFILE_CONTENT_KEYS_BY_SCHEMA = {
    1: _PROFILE_CONTENT_V1_KEYS,
    2: _PROFILE_CONTENT_V1_KEYS
    + (
        "problem_context",
        "knowledge_points",
        "assessment_tests",
        "confirmations",
    ),
}
_JOURNAL_KEYS = {
    "schema_version",
    "session_id",
    "segment_id",
    "first_sequence",
    "last_sequence",
    "content_hash",
    "segments",
}
_RECEIPT_KEYS = {
    "schema_version",
    "session_id",
    "segment_id",
    "content_hash",
    "accepted_count",
    "last_contiguous_sequence",
    "received_at",
}
_PROCESS_LOCKS_GUARD = threading.RLock()
_PROCESS_LOCKS: dict[tuple[str, str], threading.RLock] = {}


def _canonical_uuid(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        error_type = InvalidSessionIdError if field == "session_id" else ValueError
        raise error_type(f"{field} must be a canonical UUID.")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as error:
        error_type = InvalidSessionIdError if field == "session_id" else ValueError
        raise error_type(f"{field} must be a canonical UUID.") from error
    if str(parsed) != value:
        error_type = InvalidSessionIdError if field == "session_id" else ValueError
        raise error_type(f"{field} must be a canonical UUID.")
    return value


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_aware_time(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SessionIntegrityError(f"{field} must be an ISO-8601 timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SessionIntegrityError(
            f"{field} must be an ISO-8601 timestamp."
        ) from error
    if parsed.tzinfo is None:
        raise SessionIntegrityError(f"{field} must include a UTC offset.")
    return parsed


def _require_nonempty(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string.")
    return value.strip()


def _write_all(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("Session append made no progress.")
        remaining = remaining[written:]


class SessionStore:
    """Store canonical session artifacts for a one-process pilot.

    The log root is a trusted, operator-owned filesystem boundary. Session
    directories are created privately (``0700`` on POSIX), and path checks
    reject unsafe entries, but this store does not coordinate with untrusted
    cooperating processes that can race filesystem mutations.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._sessions_root = self.root / "sessions"

    def _lock_for(self, session_id: str) -> threading.RLock:
        canonical = _canonical_uuid(session_id, field="session_id")
        key = (str(self.root.resolve()), canonical)
        with _PROCESS_LOCKS_GUARD:
            return _PROCESS_LOCKS.setdefault(key, threading.RLock())

    def _session_dir(self, session_id: str) -> Path:
        canonical = _canonical_uuid(session_id, field="session_id")
        return self._sessions_root / canonical

    def _assert_safe_path(self, path: Path) -> Path:
        root_absolute = self.root.absolute()
        candidate = Path(path).absolute()
        try:
            relative = candidate.relative_to(root_absolute)
        except ValueError as error:
            raise SessionIntegrityError("Stored path escapes the log root.") from error

        cursor = root_absolute
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise SessionIntegrityError("Stored path traverses a symbolic link.")

        root_resolved = self.root.resolve()
        try:
            candidate.resolve(strict=False).relative_to(root_resolved)
        except ValueError as error:
            raise SessionIntegrityError("Stored path escapes the log root.") from error
        return candidate

    def _read_json(self, path: Path) -> dict[str, object]:
        safe_path = self._assert_safe_path(path)
        if not safe_path.is_file():
            raise KeyError(path.name)
        try:
            value = json.loads(safe_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SessionIntegrityError(
                f"{path.name} is not a valid JSON object."
            ) from error
        if not isinstance(value, dict):
            raise SessionIntegrityError(f"{path.name} is not a JSON object.")
        return value

    def _write_json(self, path: Path, value: object) -> None:
        self._assert_safe_path(path.parent)
        atomic_write_json(path, value)

    @staticmethod
    def _profile_hash(profile: Mapping[str, object]) -> str:
        schema_version = profile.get("schema_version")
        if (
            not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
            or schema_version not in _PROFILE_CONTENT_KEYS_BY_SCHEMA
        ):
            raise SessionIntegrityError(
                "Published profile schema version is unsupported."
            )
        content_keys = _PROFILE_CONTENT_KEYS_BY_SCHEMA[schema_version]
        if any(key not in profile for key in content_keys):
            raise SessionIntegrityError(
                "Published profile is missing immutable content fields."
            )
        content = {key: profile[key] for key in content_keys}
        try:
            return sha256_json(content)
        except (TypeError, ValueError) as error:
            raise SessionIntegrityError(
                "Published profile is not canonical JSON."
            ) from error

    @staticmethod
    def _load_packaged_signal_dictionary() -> dict[str, object]:
        path = (
            Path(__file__).parent
            / "resources"
            / "signal_dictionary"
            / "pilot-v1.json"
        )
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SessionIntegrityError(
                "Packaged pilot signal dictionary is unreadable."
            ) from error
        if not isinstance(value, dict) or value.get("version") != "pilot-v1":
            raise SessionIntegrityError(
                "Packaged pilot signal dictionary has the wrong version."
            )
        return value

    def start(
        self,
        *,
        problem_id: str,
        profile: Mapping[str, object],
    ) -> dict[str, object]:
        problem = _require_nonempty(problem_id, field="problem_id")
        if not isinstance(profile, Mapping):
            raise SessionIntegrityError("profile must be a published profile object.")
        profile_snapshot = dict(profile)
        profile_id = _canonical_uuid(
            profile_snapshot.get("profile_id"),
            field="profile_id",
        )
        version = profile_snapshot.get("version")
        if (
            not isinstance(version, int)
            or isinstance(version, bool)
            or version < 1
        ):
            raise SessionIntegrityError("Published profile version is invalid.")
        if profile_snapshot.get("problem_id") != problem:
            raise SessionIntegrityError(
                "Published profile does not belong to the requested problem."
            )
        expected_profile_hash = self._profile_hash(profile_snapshot)
        if profile_snapshot.get("content_hash") != expected_profile_hash:
            raise SessionIntegrityError(
                "Published profile content hash does not match."
            )

        dictionary = self._load_packaged_signal_dictionary()
        dictionary_hash = sha256_json(dictionary)
        started_at = _now_iso()
        self._assert_safe_path(self._sessions_root)
        self._sessions_root.mkdir(mode=0o700, parents=True, exist_ok=True)

        while True:
            session_id = str(uuid4())
            session_dir = self._session_dir(session_id)
            try:
                session_dir.mkdir(mode=0o700)
            except FileExistsError:
                continue
            break

        with self._lock_for(session_id):
            (session_dir / "batches").mkdir(mode=0o700)
            (session_dir / "receipts").mkdir(mode=0o700)
            self._write_json(session_dir / "profile.json", profile_snapshot)
            self._write_json(session_dir / "signal_dictionary.json", dictionary)
            raw_descriptor = os.open(
                session_dir / "raw_events.jsonl",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            try:
                os.fchmod(raw_descriptor, 0o600)
                os.fsync(raw_descriptor)
            finally:
                os.close(raw_descriptor)
            session: dict[str, object] = {
                "schema_version": 1,
                "session_id": session_id,
                "problem_id": problem,
                "profile_id": profile_id,
                "profile_version": version,
                "profile_content_hash": expected_profile_hash,
                "signal_dictionary_version": "pilot-v1",
                "signal_dictionary_hash": dictionary_hash,
                "status": "collecting",
                "last_contiguous_sequence": 0,
                "received_event_count": 0,
                "analysis_job_id": None,
                "legacy_projection_path": None,
                "started_at": started_at,
                "ended_at": None,
            }
            self._write_json(session_dir / "session.json", session)
            return dict(session)

    def _validate_snapshots(
        self,
        session_dir: Path,
        session: Mapping[str, object],
    ) -> None:
        try:
            profile = self._read_json(session_dir / "profile.json")
        except KeyError as error:
            raise SessionIntegrityError(
                "Stored profile snapshot is missing."
            ) from error
        if self._profile_hash(profile) != session.get("profile_content_hash"):
            raise SessionIntegrityError("Stored profile snapshot hash does not match.")
        if profile.get("content_hash") != session.get("profile_content_hash"):
            raise SessionIntegrityError(
                "Stored profile content hash field does not match."
            )
        try:
            dictionary = self._read_json(
                session_dir / "signal_dictionary.json"
            )
        except KeyError as error:
            raise SessionIntegrityError(
                "Stored signal dictionary snapshot is missing."
            ) from error
        try:
            dictionary_hash = sha256_json(dictionary)
        except (TypeError, ValueError) as error:
            raise SessionIntegrityError(
                "Stored signal dictionary is not canonical JSON."
            ) from error
        if (
            dictionary.get("version") != session.get("signal_dictionary_version")
            or dictionary_hash != session.get("signal_dictionary_hash")
        ):
            raise SessionIntegrityError(
                "Stored signal dictionary snapshot does not match."
            )

    def read(self, session_id: str) -> dict[str, object]:
        session_dir = self._session_dir(session_id)
        with self._lock_for(session_id):
            safe_dir = self._assert_safe_path(session_dir)
            try:
                mode = safe_dir.lstat().st_mode
            except FileNotFoundError as error:
                raise SessionNotFoundError(session_id) from error
            except OSError as error:
                raise SessionIntegrityError(
                    "Session directory cannot be safely inspected."
                ) from error
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise SessionIntegrityError(
                    "Session path is not a safe directory."
                )
            try:
                session = self._read_json(session_dir / "session.json")
            except KeyError as error:
                raise SessionIntegrityError(
                    "Present session is missing its projection."
                ) from error
            if session.get("session_id") != session_id:
                raise SessionIntegrityError(
                    "Stored session id does not match its directory."
                )
            self._validate_snapshots(session_dir, session)
            return session

    def read_signal_dictionary(self, session_id: str) -> dict[str, object]:
        session_dir = self._session_dir(session_id)
        with self._lock_for(session_id):
            session = self.read(session_id)
            try:
                dictionary = self._read_json(
                    session_dir / "signal_dictionary.json"
                )
            except KeyError as error:
                raise SessionIntegrityError(
                    "Stored signal dictionary snapshot is missing."
                ) from error
            if sha256_json(dictionary) != session["signal_dictionary_hash"]:
                raise SessionIntegrityError(
                    "Stored signal dictionary snapshot hash does not match."
                )
            return dictionary

    def read_profile(self, session_id: str) -> dict[str, object]:
        """Return the immutable profile snapshot after session validation."""

        session_dir = self._session_dir(session_id)
        with self._lock_for(session_id):
            session = self.read(session_id)
            try:
                profile = self._read_json(session_dir / "profile.json")
            except KeyError as error:
                raise SessionIntegrityError(
                    "Stored profile snapshot is missing."
                ) from error
            if (
                profile.get("profile_id") != session.get("profile_id")
                or profile.get("version") != session.get("profile_version")
                or profile.get("problem_id") != session.get("problem_id")
                or profile.get("content_hash")
                != session.get("profile_content_hash")
            ):
                raise SessionIntegrityError(
                    "Stored profile identity does not match the session."
                )
            return dict(profile)

    def list_session_ids(self) -> list[str]:
        root = self._assert_safe_path(self._sessions_root)
        if not root.exists():
            return []
        if root.is_symlink() or not root.is_dir():
            raise SessionIntegrityError("Session root is not a safe directory.")
        session_ids: list[str] = []
        for entry in root.iterdir():
            safe_entry = self._assert_safe_path(entry)
            if safe_entry.is_symlink() or not safe_entry.is_dir():
                raise SessionIntegrityError(
                    "Session root contains an unsafe entry."
                )
            try:
                canonical = _canonical_uuid(entry.name, field="session_id")
            except InvalidSessionIdError as error:
                raise SessionIntegrityError(
                    "Session root contains an invalid session directory."
                ) from error
            session_ids.append(canonical)
        return sorted(session_ids)

    def read_events_if_present(
        self,
        session_id: str,
    ) -> list[dict[str, object]] | None:
        session_dir = self._session_dir(session_id)
        with self._lock_for(session_id):
            self.read(session_id)
            path = self._assert_safe_path(session_dir / "raw_events.jsonl")
            if not path.exists():
                return None
            if path.is_symlink() or not path.is_file():
                raise SessionIntegrityError(
                    "Canonical raw event stream is not a safe file."
                )
            return self._read_events_locked(session_id, session_dir)

    def read_training_record(
        self,
        session_id: str,
    ) -> dict[str, object] | None:
        session_dir = self._session_dir(session_id)
        with self._lock_for(session_id):
            self.read(session_id)
            path = self._assert_safe_path(session_dir / "training_record.json")
            if not path.exists():
                return None
            try:
                mode = path.lstat().st_mode
            except FileNotFoundError as error:
                raise SessionIntegrityError(
                    "Training record disappeared while reading."
                ) from error
            except OSError as error:
                raise SessionIntegrityError(
                    "Training record cannot be safely inspected."
                ) from error
            if not stat.S_ISREG(mode):
                raise SessionIntegrityError(
                    "Training record is not a safe file."
                )
            try:
                return self._read_json(path)
            except KeyError as error:
                raise SessionIntegrityError(
                    "Training record disappeared while reading."
                ) from error

    def write_training_record(
        self,
        session_id: str,
        record: Mapping[str, object],
        *,
        require_raw_events: bool = False,
    ) -> None:
        session_dir = self._session_dir(session_id)
        with self._lock_for(session_id):
            self.read(session_id)
            if require_raw_events:
                self._read_events_locked(session_id, session_dir)
            self._write_json(
                self._assert_safe_path(session_dir / "training_record.json"),
                record,
            )

    def write_training_record_if_source_current(
        self,
        session_id: str,
        record: Mapping[str, object],
        source_is_current: Callable[[], bool],
    ) -> bool:
        """Validate the canonical source and write under one session lock."""

        session_dir = self._session_dir(session_id)
        with self._lock_for(session_id):
            self.read(session_id)
            self._read_events_locked(session_id, session_dir)
            if not source_is_current():
                return False
            # Keep the established write seam for storage tests and callers;
            # the RLock makes the nested acquisition part of this atomic scope.
            self.write_training_record(
                session_id,
                record,
                require_raw_events=True,
            )
            return True

    def read_classroom_brief(
        self,
        session_id: str,
    ) -> dict[str, object] | None:
        """Read the private classroom brief after rejecting unsafe entries."""

        session_dir = self._session_dir(session_id)
        with self._lock_for(session_id):
            self.read(session_id)
            path = self._assert_safe_path(session_dir / "classroom_brief.json")
            if not path.exists():
                return None
            try:
                mode = path.lstat().st_mode
            except FileNotFoundError as error:
                raise SessionIntegrityError(
                    "Classroom brief disappeared while reading."
                ) from error
            except OSError as error:
                raise SessionIntegrityError(
                    "Classroom brief cannot be safely inspected."
                ) from error
            if not stat.S_ISREG(mode):
                raise SessionIntegrityError(
                    "Classroom brief is not a safe file."
                )
            try:
                return self._read_json(path)
            except KeyError as error:
                raise SessionIntegrityError(
                    "Classroom brief disappeared while reading."
                ) from error

    def write_classroom_brief(
        self,
        session_id: str,
        brief: Mapping[str, object],
    ) -> None:
        """Atomically write one private classroom brief."""

        session_dir = self._session_dir(session_id)
        with self._lock_for(session_id):
            self.read(session_id)
            path = self._assert_safe_path(session_dir / "classroom_brief.json")
            if path.exists():
                try:
                    mode = path.lstat().st_mode
                except OSError as error:
                    raise SessionIntegrityError(
                        "Classroom brief cannot be safely inspected."
                    ) from error
                if not stat.S_ISREG(mode):
                    raise SessionIntegrityError(
                        "Classroom brief is not a safe file."
                    )
            self._write_json(path, brief)

    @staticmethod
    def _validated_log_filename(filename: object) -> str:
        if not isinstance(filename, str) or filename not in SESSION_LOG_FILENAMES:
            raise ValueError("Log artifact filename is not allowed.")
        return filename

    def _log_artifact_path(self, session_id: str, filename: object) -> Path:
        safe_filename = self._validated_log_filename(filename)
        return self._session_dir(session_id) / "logs" / safe_filename

    def write_log_artifact(
        self,
        session_id: str,
        filename: object,
        content: bytes,
    ) -> None:
        """Atomically write one exact allowlisted private session artifact."""

        if not isinstance(content, bytes):
            raise TypeError("Log artifact content must be bytes.")
        path = self._log_artifact_path(session_id, filename)
        with self._lock_for(session_id):
            self.read(session_id)
            logs_dir = self._assert_safe_path(path.parent)
            logs_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            safe_path = self._assert_safe_path(path)
            try:
                existing_mode = safe_path.lstat().st_mode
            except FileNotFoundError:
                existing_mode = None
            except OSError as error:
                raise SessionIntegrityError(
                    "Log artifact destination cannot be safely inspected."
                ) from error
            if existing_mode is not None and not stat.S_ISREG(existing_mode):
                raise SessionIntegrityError(
                    "Log artifact destination is not a regular file."
                )
            temporary_name: str | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=logs_dir,
                    prefix=f".{safe_path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as temporary:
                    temporary_name = temporary.name
                    os.fchmod(temporary.fileno(), 0o600)
                    temporary.write(content)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                os.replace(temporary_name, safe_path)
                temporary_name = None
                os.chmod(safe_path, 0o600)
                descriptor = os.open(logs_dir, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            finally:
                if temporary_name is not None:
                    try:
                        Path(temporary_name).unlink()
                    except FileNotFoundError:
                        pass

    def remove_log_artifact(self, session_id: str, filename: object) -> None:
        """Remove one allowlisted artifact if present, rejecting unsafe targets."""

        path = self._log_artifact_path(session_id, filename)
        with self._lock_for(session_id):
            self.read(session_id)
            safe_path = self._assert_safe_path(path)
            try:
                mode = safe_path.lstat().st_mode
            except FileNotFoundError:
                return
            except OSError as error:
                raise SessionIntegrityError(
                    "Log artifact cannot be safely inspected."
                ) from error
            if not stat.S_ISREG(mode):
                raise SessionIntegrityError("Log artifact is not a regular file.")
            safe_path.unlink()

    def read_log_artifact(
        self,
        session_id: str,
        filename: object,
        *,
        max_bytes: int | None = None,
    ) -> bytes:
        """Read one allowlisted regular artifact, optionally enforcing a cap."""

        if max_bytes is not None and (
            not isinstance(max_bytes, int)
            or isinstance(max_bytes, bool)
            or max_bytes < 0
        ):
            raise ValueError("max_bytes must be a non-negative integer or None.")
        with self.open_log_artifact(
            session_id,
            filename,
            max_bytes=max_bytes,
        ) as (stream, _metadata):
            try:
                return stream.read()
            except OSError as error:
                raise SessionIntegrityError("Log artifact is unreadable.") from error

    @contextmanager
    def open_log_artifact(
        self,
        session_id: str,
        filename: object,
        *,
        max_bytes: int | None = None,
    ) -> Iterator[tuple[BinaryIO, os.stat_result]]:
        """Open one artifact without following a replaced path or buffering it."""

        if max_bytes is not None and (
            not isinstance(max_bytes, int)
            or isinstance(max_bytes, bool)
            or max_bytes < 0
        ):
            raise ValueError("max_bytes must be a non-negative integer or None.")
        path = self._log_artifact_path(session_id, filename)
        descriptor = -1
        with self._lock_for(session_id):
            self.read(session_id)
            safe_path = self._assert_safe_path(path)
            flags = os.O_RDONLY
            if hasattr(os, "O_BINARY"):
                flags |= os.O_BINARY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                descriptor = os.open(safe_path, flags)
                metadata = os.fstat(descriptor)
                path_metadata = safe_path.lstat()
            except FileNotFoundError as error:
                if descriptor >= 0:
                    os.close(descriptor)
                    descriptor = -1
                raise KeyError(str(filename)) from error
            except OSError as error:
                if descriptor >= 0:
                    os.close(descriptor)
                    descriptor = -1
                raise SessionIntegrityError(
                    "Log artifact cannot be safely opened."
                ) from error
            try:
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or not stat.S_ISREG(path_metadata.st_mode)
                    or not os.path.samestat(metadata, path_metadata)
                ):
                    raise SessionIntegrityError(
                        "Log artifact changed during secure open."
                    )
                if stat.S_IMODE(metadata.st_mode) != 0o600:
                    raise SessionIntegrityError(
                        "Log artifact does not have private file permissions."
                    )
                if max_bytes is not None and metadata.st_size > max_bytes:
                    raise SessionIntegrityError(
                        "Log artifact exceeds the approved view limit."
                    )
                stream = os.fdopen(descriptor, "rb", closefd=True)
                descriptor = -1
                with stream:
                    yield stream, metadata
            finally:
                if descriptor >= 0:
                    os.close(descriptor)

    def stat_log_artifact(
        self,
        session_id: str,
        filename: object,
    ) -> os.stat_result | None:
        """Return safe metadata for an allowlisted artifact when it exists."""

        path = self._log_artifact_path(session_id, filename)
        with self._lock_for(session_id):
            self.read(session_id)
            safe_path = self._assert_safe_path(path)
            try:
                metadata = safe_path.lstat()
            except FileNotFoundError:
                return None
            except OSError as error:
                raise SessionIntegrityError(
                    "Log artifact cannot be safely inspected."
                ) from error
            if not stat.S_ISREG(metadata.st_mode):
                raise SessionIntegrityError("Log artifact is not a regular file.")
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                raise SessionIntegrityError(
                    "Log artifact does not have private file permissions."
                )
            return metadata

    def _append_audit(self, path: Path, record: Mapping[str, object]) -> None:
        descriptor = self._open_audit_descriptor(path)
        try:
            self._write_audit_record(descriptor, record)
        finally:
            os.close(descriptor)

    def _open_audit_descriptor(self, path: Path) -> int:
        self._assert_safe_path(path.parent)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            existing_mode = path.lstat().st_mode
        except FileNotFoundError:
            existing_mode = None
        except OSError as error:
            raise SessionIntegrityError(
                "Audit destination cannot be safely inspected."
            ) from error
        if existing_mode is not None and not stat.S_ISREG(existing_mode):
            raise SessionIntegrityError(
                "Audit destination is not a regular file."
            )
        self._assert_safe_path(path)
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_APPEND
                | os.O_NONBLOCK
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except OSError as error:
            raise SessionIntegrityError(
                "Audit destination cannot be safely opened."
            ) from error
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise SessionIntegrityError(
                    "Audit destination is not a regular file."
                )
            os.fchmod(descriptor, 0o600)
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    @staticmethod
    def _write_audit_record(
        descriptor: int,
        record: Mapping[str, object],
    ) -> None:
        _write_all(descriptor, canonical_json_bytes(record) + b"\n")
        os.fsync(descriptor)

    def _read_events_locked(
        self,
        session_id: str,
        session_dir: Path,
    ) -> list[dict[str, object]]:
        raw_path = self._assert_safe_path(session_dir / "raw_events.jsonl")
        if not raw_path.is_file():
            raise SessionIntegrityError("Canonical raw event stream is missing.")
        try:
            raw = raw_path.read_bytes()
        except OSError as error:
            raise SessionIntegrityError(
                "Canonical raw event stream is unreadable."
            ) from error

        events: list[dict[str, object]] = []
        offset = 0
        lines = raw.splitlines(keepends=True)
        for index, raw_line in enumerate(lines):
            line_start = offset
            offset += len(raw_line)
            terminated = raw_line.endswith(b"\n")
            payload = raw_line[:-1] if terminated else raw_line
            if payload.endswith(b"\r"):
                payload = payload[:-1]
            try:
                event = json.loads(payload)
            except (UnicodeDecodeError, json.JSONDecodeError):
                if index == len(lines) - 1 and not terminated:
                    with raw_path.open("r+b") as handle:
                        handle.truncate(line_start)
                        handle.flush()
                        os.fsync(handle.fileno())
                    self._append_audit(
                        session_dir / "session_recovery.jsonl",
                        {
                            "schema_version": 1,
                            "session_id": session_id,
                            "action": "truncated_incomplete_jsonl_tail",
                            "recovered_at": _now_iso(),
                        },
                    )
                    break
                raise SessionIntegrityError(
                    "Canonical raw event stream contains invalid JSON."
                )
            if not terminated:
                raise SessionIntegrityError(
                    "Canonical raw event stream has a non-terminated final record."
                )
            if not isinstance(event, dict):
                raise SessionIntegrityError(
                    "Canonical raw event stream contains a non-object."
                )
            expected_sequence = len(events) + 1
            if event.get("session_seq") != expected_sequence:
                raise SessionIntegrityError(
                    "Canonical raw event sequence is not continuous."
                )
            if event.get("event_id") != f"{session_id}:{expected_sequence}":
                raise SessionIntegrityError(
                    "Canonical raw event id does not match its sequence."
                )
            events.append(event)
        return events

    def read_events(self, session_id: str) -> list[dict[str, object]]:
        session_dir = self._session_dir(session_id)
        with self._lock_for(session_id):
            self.read(session_id)
            return self._read_events_locked(session_id, session_dir)

    @staticmethod
    def _validate_batch(
        session_id: str,
        *,
        first_sequence: int,
        last_sequence: int,
        content_hash: str,
        segments: Sequence[Mapping[str, object]],
    ) -> tuple[list[dict[str, object]], str]:
        if (
            not isinstance(first_sequence, int)
            or isinstance(first_sequence, bool)
            or first_sequence < 1
            or not isinstance(last_sequence, int)
            or isinstance(last_sequence, bool)
            or last_sequence < first_sequence
        ):
            raise ValueError("Batch sequence bounds are invalid.")
        try:
            normalized_segments = normalize_json_value(segments)
        except (TypeError, ValueError) as error:
            raise SessionIntegrityError(
                "Batch segments are not canonical JSON objects."
            ) from error
        if not isinstance(normalized_segments, list) or not all(
            isinstance(segment, dict) for segment in normalized_segments
        ):
            raise SessionIntegrityError(
                "Batch segments must be a sequence of JSON objects."
            )
        copied = [dict(segment) for segment in normalized_segments]
        if len(copied) != last_sequence - first_sequence + 1:
            raise SessionIntegrityError(
                "Batch length does not match its inclusive sequence range."
        )
        for offset, segment in enumerate(copied):
            expected_sequence = first_sequence + offset
            sequence_value = segment.get("session_seq")
            if (
                not isinstance(sequence_value, int)
                or isinstance(sequence_value, bool)
                or sequence_value != expected_sequence
            ):
                raise SessionIntegrityError(
                    "Segment session_seq does not match its batch position."
                )
            if segment.get("event_id") != f"{session_id}:{expected_sequence}":
                raise SessionIntegrityError(
                    "Segment event_id does not match its session sequence."
                )
        expected_hash = sha256_json(
            {
                "first_sequence": first_sequence,
                "last_sequence": last_sequence,
                "segments": copied,
            }
        )
        if content_hash != expected_hash:
            raise SessionIntegrityError("Batch content hash does not match.")
        return copied, expected_hash

    def _validate_stored_journal(
        self,
        session_id: str,
        segment_id: str,
        journal: Mapping[str, object],
    ) -> dict[str, object]:
        if set(journal) != _JOURNAL_KEYS or journal.get("schema_version") != 1:
            raise SessionIntegrityError(
                "Immutable batch journal has unexpected or missing fields."
            )
        try:
            stored_session_id = _canonical_uuid(
                journal.get("session_id"),
                field="session_id",
            )
            stored_segment_id = _canonical_uuid(
                journal.get("segment_id"),
                field="segment_id",
            )
        except (InvalidSessionIdError, ValueError) as error:
            raise SessionIntegrityError(
                "Immutable batch journal contains a noncanonical id."
            ) from error
        if stored_session_id != session_id or stored_segment_id != segment_id:
            raise SessionIntegrityError(
                "Immutable batch journal identity does not match its path."
            )
        try:
            segments, expected_hash = self._validate_batch(
                session_id,
                first_sequence=journal["first_sequence"],  # type: ignore[arg-type]
                last_sequence=journal["last_sequence"],  # type: ignore[arg-type]
                content_hash=journal["content_hash"],  # type: ignore[arg-type]
                segments=journal["segments"],  # type: ignore[arg-type]
            )
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, SessionIntegrityError):
                raise
            raise SessionIntegrityError(
                "Immutable batch journal is structurally invalid."
            ) from error
        normalized = {
            "schema_version": 1,
            "session_id": session_id,
            "segment_id": segment_id,
            "first_sequence": journal["first_sequence"],
            "last_sequence": journal["last_sequence"],
            "content_hash": expected_hash,
            "segments": segments,
        }
        if canonical_json_bytes(journal) != canonical_json_bytes(normalized):
            raise SessionIntegrityError(
                "Immutable batch journal is not canonical-equivalent."
            )
        return normalized

    def _validate_journal_receipt(
        self,
        session_id: str,
        segment_id: str,
        journal: Mapping[str, object],
        receipt: Mapping[str, object],
        events: Sequence[Mapping[str, object]],
    ) -> tuple[dict[str, object], datetime]:
        normalized_journal = self._validate_stored_journal(
            session_id,
            segment_id,
            journal,
        )
        if set(receipt) != _RECEIPT_KEYS or receipt.get("schema_version") != 1:
            raise SessionIntegrityError(
                "Immutable receipt has unexpected or missing fields."
            )
        try:
            receipt_session_id = _canonical_uuid(
                receipt.get("session_id"),
                field="session_id",
            )
            receipt_segment_id = _canonical_uuid(
                receipt.get("segment_id"),
                field="segment_id",
            )
        except (InvalidSessionIdError, ValueError) as error:
            raise SessionIntegrityError(
                "Immutable receipt contains a noncanonical id."
            ) from error
        accepted_count = receipt.get("accepted_count")
        receipt_last = receipt.get("last_contiguous_sequence")
        if (
            receipt_session_id != session_id
            or receipt_segment_id != segment_id
            or receipt.get("content_hash")
            != normalized_journal["content_hash"]
            or not isinstance(accepted_count, int)
            or isinstance(accepted_count, bool)
            or accepted_count != len(normalized_journal["segments"])  # type: ignore[arg-type]
            or not isinstance(receipt_last, int)
            or isinstance(receipt_last, bool)
            or receipt_last != normalized_journal["last_sequence"]
        ):
            raise SessionIntegrityError(
                "Immutable receipt does not match its batch journal."
            )
        received_at = _parse_aware_time(
            receipt.get("received_at"),
            field="received_at",
        )
        first_sequence = int(normalized_journal["first_sequence"])
        last_sequence = int(normalized_journal["last_sequence"])
        if len(events) < last_sequence:
            raise SessionIntegrityError(
                "Receipt range exceeds the canonical raw stream."
            )
        raw_range = events[first_sequence - 1 : last_sequence]
        if canonical_json_bytes(raw_range) != canonical_json_bytes(
            normalized_journal["segments"]
        ):
            raise SessionIntegrityError(
                "Receipt journal does not match its canonical raw range."
            )
        return normalized_journal, received_at

    @staticmethod
    def _receipt_response(
        session_id: str,
        segment_id: str,
        accepted_count: int,
        last_contiguous_sequence: int,
    ) -> dict[str, object]:
        return {
            "session_id": session_id,
            "segment_id": segment_id,
            "accepted_count": accepted_count,
            "last_contiguous_sequence": last_contiguous_sequence,
        }

    def _append_missing_events(
        self,
        raw_path: Path,
        existing: list[dict[str, object]],
        segments: Sequence[Mapping[str, object]],
    ) -> list[dict[str, object]]:
        existing_by_sequence = {
            int(event["session_seq"]): event for event in existing
        }
        missing: list[dict[str, object]] = []
        for segment in segments:
            sequence = int(segment["session_seq"])
            prior = existing_by_sequence.get(sequence)
            if prior is not None:
                if canonical_json_bytes(prior) != canonical_json_bytes(segment):
                    raise SessionIntegrityError(
                        "Journal event conflicts with the canonical raw stream."
                    )
            else:
                if sequence != len(existing) + len(missing) + 1:
                    raise SessionIntegrityError(
                        "Journal recovery would create a sequence gap."
                    )
                missing.append(dict(segment))

        if missing:
            descriptor = os.open(raw_path, os.O_WRONLY | os.O_APPEND)
            try:
                for segment in missing:
                    _write_all(
                        descriptor,
                        canonical_json_bytes(segment) + b"\n",
                    )
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return existing + missing

    def append_batch(
        self,
        session_id: str,
        *,
        segment_id: str,
        first_sequence: int,
        last_sequence: int,
        content_hash: str,
        segments: Sequence[Mapping[str, object]],
    ) -> dict[str, object]:
        canonical_session_id = _canonical_uuid(session_id, field="session_id")
        canonical_segment_id = _canonical_uuid(segment_id, field="segment_id")
        copied, expected_hash = self._validate_batch(
            canonical_session_id,
            first_sequence=first_sequence,
            last_sequence=last_sequence,
            content_hash=content_hash,
            segments=segments,
        )
        session_dir = self._session_dir(canonical_session_id)
        batch_path = session_dir / "batches" / f"{canonical_segment_id}.json"
        receipt_path = session_dir / "receipts" / f"{canonical_segment_id}.json"
        journal = {
            "schema_version": 1,
            "session_id": canonical_session_id,
            "segment_id": canonical_segment_id,
            "first_sequence": first_sequence,
            "last_sequence": last_sequence,
            "content_hash": expected_hash,
            "segments": copied,
        }

        with self._lock_for(canonical_session_id):
            session = self.read(canonical_session_id)
            if session.get("status") != "collecting":
                raise SessionStateError(
                    "Segments can only be appended to a collecting session."
                )
            self._assert_safe_path(batch_path)
            self._assert_safe_path(receipt_path)

            if receipt_path.is_file():
                receipt = self._read_json(receipt_path)
                if not batch_path.is_file():
                    raise SessionIntegrityError(
                        "Receipt has no immutable batch journal."
                    )
                stored_journal = self._read_json(batch_path)
                events = self._read_events_locked(
                    canonical_session_id,
                    session_dir,
                )
                validated_journal, _ = self._validate_journal_receipt(
                    canonical_session_id,
                    canonical_segment_id,
                    stored_journal,
                    receipt,
                    events,
                )
                if validated_journal["content_hash"] != expected_hash:
                    raise SegmentConflictError(
                        "segment_id was already accepted with different content."
                    )
                if canonical_json_bytes(validated_journal) != canonical_json_bytes(
                    journal
                ):
                    raise SessionIntegrityError(
                        "Replay does not match its immutable batch journal."
                    )
                receipt_last = int(validated_journal["last_sequence"])
                if session.get("last_contiguous_sequence", 0) < receipt_last:
                    session["last_contiguous_sequence"] = receipt_last
                    session["received_event_count"] = len(events)
                    self._write_json(session_dir / "session.json", session)
                return self._receipt_response(
                    canonical_session_id,
                    canonical_segment_id,
                    len(copied),
                    receipt_last,
                )

            if batch_path.exists():
                stored_journal = self._read_json(batch_path)
                validated_journal = self._validate_stored_journal(
                    canonical_session_id,
                    canonical_segment_id,
                    stored_journal,
                )
                if validated_journal["content_hash"] != expected_hash:
                    raise SegmentConflictError(
                        "segment_id was journaled with different content."
                    )
                if canonical_json_bytes(validated_journal) != canonical_json_bytes(
                    journal
                ):
                    raise SessionIntegrityError(
                        "Immutable batch journal content does not match."
                    )
            else:
                expected_first = int(session["last_contiguous_sequence"]) + 1
                if first_sequence != expected_first:
                    missing_end = first_sequence - 1
                    if missing_end >= expected_first:
                        raise SequenceGapError(
                            [(expected_first, missing_end)]
                        )
                    raise SessionIntegrityError(
                        "New batch overlaps an accepted sequence range."
                    )
                self._write_json(batch_path, journal)

            events = self._read_events_locked(canonical_session_id, session_dir)
            expected_prefix = first_sequence - 1
            if len(events) not in range(expected_prefix, last_sequence + 1):
                raise SessionIntegrityError(
                    "Canonical stream does not match batch journal recovery."
                )
            events = self._append_missing_events(
                self._assert_safe_path(session_dir / "raw_events.jsonl"),
                events,
                copied,
            )
            receipt: dict[str, object] = {
                "schema_version": 1,
                "session_id": canonical_session_id,
                "segment_id": canonical_segment_id,
                "content_hash": expected_hash,
                "accepted_count": len(copied),
                "last_contiguous_sequence": last_sequence,
                "received_at": _now_iso(),
            }
            self._write_json(receipt_path, receipt)
            session["last_contiguous_sequence"] = last_sequence
            session["received_event_count"] = len(events)
            self._write_json(session_dir / "session.json", session)
            return self._receipt_response(
                canonical_session_id,
                canonical_segment_id,
                len(copied),
                last_sequence,
            )

    def finalize(
        self,
        session_id: str,
        *,
        last_sequence: int,
    ) -> dict[str, object]:
        if (
            not isinstance(last_sequence, int)
            or isinstance(last_sequence, bool)
            or last_sequence < 0
        ):
            raise ValueError("last_sequence must be a non-negative integer.")
        session_dir = self._session_dir(session_id)
        with self._lock_for(session_id):
            session = self.read(session_id)
            if session.get("status") == "finalized":
                if session.get("last_contiguous_sequence") != last_sequence:
                    raise SessionStateError(
                        "Finalized session has a different last sequence."
                    )
                return session
            if session.get("status") != "collecting":
                raise SessionStateError(
                    "Only a collecting session can be finalized."
                )

            session["status"] = "finalizing"
            session.pop("finalization_failure_reason", None)
            self._write_json(session_dir / "session.json", session)
            try:
                events = self._read_events_locked(session_id, session_dir)
                contiguous = len(events)
                if last_sequence > contiguous:
                    raise SequenceGapError([(contiguous + 1, last_sequence)])
                if last_sequence < contiguous:
                    raise SessionIntegrityError(
                        "Finalization last_sequence is behind the canonical stream."
                    )
                if (
                    session.get("last_contiguous_sequence") != contiguous
                    or session.get("received_event_count") != len(events)
                ):
                    raise SessionIntegrityError(
                        "Session projection disagrees with canonical events."
                    )
                self._validate_snapshots(session_dir, session)
                if session.get("legacy_projection_path") is None:
                    session["legacy_projection_path"] = write_session_projection(
                        session_id,
                        events,
                        log_root=self.root,
                    )
                session["status"] = "finalized"
                session["ended_at"] = _now_iso()
                self._write_json(session_dir / "session.json", session)
                return session
            except (SequenceGapError, SessionIntegrityError):
                session["status"] = "collecting"
                session["finalization_failure_reason"] = (
                    "sequence_gap"
                    if last_sequence > int(
                        session.get("last_contiguous_sequence", 0)
                    )
                    else "integrity_failure"
                )
                self._write_json(session_dir / "session.json", session)
                raise
            except Exception:
                session["status"] = "collecting"
                session["finalization_failure_reason"] = "projection_failure"
                self._write_json(session_dir / "session.json", session)
                raise

    def attach_job(self, session_id: str, job_id: str) -> dict[str, object]:
        canonical_job_id = _canonical_uuid(job_id, field="job_id")
        session_dir = self._session_dir(session_id)
        with self._lock_for(session_id):
            session = self.read(session_id)
            existing = session.get("analysis_job_id")
            if existing == canonical_job_id:
                return session
            if existing is not None:
                raise SessionStateError(
                    "Session already has a different analysis job."
                )
            session["analysis_job_id"] = canonical_job_id
            self._write_json(session_dir / "session.json", session)
            return session

    def abandon(self, session_id: str, *, reason: str) -> dict[str, object]:
        abandonment_reason = _require_nonempty(reason, field="reason")
        session_dir = self._session_dir(session_id)
        with self._lock_for(session_id):
            session = self.read(session_id)
            if session.get("status") == "abandoned":
                return session
            if session.get("status") != "collecting":
                raise SessionStateError(
                    "Only a collecting session can be abandoned."
                )
            session["status"] = "abandoned"
            session["ended_at"] = _now_iso()
            session["abandonment_reason"] = abandonment_reason
            self._write_json(session_dir / "session.json", session)
            return session

    def recover(
        self,
        session_id: str,
        *,
        actor: str,
        reason: str,
    ) -> dict[str, object]:
        recovery_actor = _require_nonempty(actor, field="actor")
        recovery_reason = _require_nonempty(reason, field="reason")
        session_dir = self._session_dir(session_id)
        with self._lock_for(session_id):
            session = self.read(session_id)
            prior_status = session.get("status")
            if prior_status not in {"abandoned", "finalizing"}:
                raise SessionStateError(
                    "Only an abandoned or finalizing session can be recovered."
                )
            audit_descriptor = self._open_audit_descriptor(
                session_dir / "session_recovery.jsonl"
            )
            try:
                self._write_audit_record(
                    audit_descriptor,
                    {
                        "schema_version": 1,
                        "session_id": session_id,
                        "action": "manual_recovery",
                        "phase": "authorized",
                        "from_status": prior_status,
                        "to_status": "collecting",
                        "actor": recovery_actor,
                        "reason": recovery_reason,
                        "authorized_at": _now_iso(),
                    },
                )
                session["status"] = "collecting"
                session["ended_at"] = None
                session.pop("abandonment_reason", None)
                session.pop("finalization_failure_reason", None)
                self._write_json(session_dir / "session.json", session)
            finally:
                os.close(audit_descriptor)
            return session

    def _last_receipt_time(
        self,
        session_id: str,
        session_dir: Path,
        session: Mapping[str, object],
    ) -> datetime:
        receipts_dir = self._assert_safe_path(session_dir / "receipts")
        latest: datetime | None = None
        if receipts_dir.is_dir():
            receipt_paths = list(receipts_dir.glob("*.json"))
            events = (
                self._read_events_locked(session_id, session_dir)
                if receipt_paths
                else []
            )
            for receipt_path in receipt_paths:
                try:
                    segment_id = _canonical_uuid(
                        receipt_path.stem,
                        field="segment_id",
                    )
                except ValueError as error:
                    raise SessionIntegrityError(
                        "Stored receipt filename is not a canonical segment id."
                    ) from error
                receipt = self._read_json(receipt_path)
                journal_path = (
                    session_dir / "batches" / f"{segment_id}.json"
                )
                try:
                    journal = self._read_json(journal_path)
                except KeyError as error:
                    raise SessionIntegrityError(
                        "Stored receipt has no immutable batch journal."
                    ) from error
                _, received_at = self._validate_journal_receipt(
                    session_id,
                    segment_id,
                    journal,
                    receipt,
                    events,
                )
                latest = (
                    received_at
                    if latest is None or received_at > latest
                    else latest
                )
        if latest is not None:
            return latest
        return _parse_aware_time(session.get("started_at"), field="started_at")

    def abandon_stale(
        self,
        *,
        now: datetime,
        timeout: timedelta = timedelta(minutes=30),
    ) -> list[str]:
        if now.tzinfo is None:
            raise ValueError("now must include a UTC offset.")
        if timeout <= timedelta(0):
            raise ValueError("timeout must be positive.")
        if not self._sessions_root.is_dir():
            return []

        changed: list[str] = []
        for session_dir in sorted(self._sessions_root.iterdir()):
            if not session_dir.is_dir() or session_dir.is_symlink():
                continue
            try:
                session_id = _canonical_uuid(
                    session_dir.name,
                    field="session_id",
                )
            except InvalidSessionIdError:
                continue
            with self._lock_for(session_id):
                session = self.read(session_id)
                if session.get("status") != "collecting":
                    continue
                reference = self._last_receipt_time(
                    session_id,
                    session_dir,
                    session,
                )
                if now - reference >= timeout:
                    self.abandon(session_id, reason="stale_timeout")
                    changed.append(session_id)
        return changed

    def _trusted_delete_targets(
        self,
        session_id: str,
        session: Mapping[str, object],
    ) -> tuple[list[Path], list[str], list[str], list[Path]]:
        from .analysis_job_store import (
            AnalysisJobIntegrityError,
            AnalysisJobStore,
        )
        from .analysis_worker import AnalysisWorker

        directory_targets: list[Path] = []
        job_ids: set[str] = set()
        analysis_ids: set[str] = set()
        file_targets: list[Path] = []

        jobs_root = self._assert_safe_path(self.root / "jobs")
        job_store = AnalysisJobStore(self.root)
        trusted_profile = self.read_profile(session_id)
        trusted_dictionary = self.read_signal_dictionary(session_id)
        if jobs_root.exists():
            if not jobs_root.is_dir():
                raise SessionIntegrityError("Jobs root is not a directory.")
            for job_dir in sorted(jobs_root.iterdir(), key=lambda path: path.name):
                self._assert_safe_path(job_dir)
                if job_dir.is_symlink() or not job_dir.is_dir():
                    raise SessionIntegrityError(
                        "Jobs root contains an unsafe entry."
                    )
                try:
                    job_id = _canonical_uuid(job_dir.name, field="job_id")
                except ValueError as error:
                    raise SessionIntegrityError(
                        "Jobs root contains a malformed identity."
                    ) from error
                if not (job_dir / "job.json").exists():
                    # A pre-publication crash has no trusted session owner.
                    continue
                try:
                    job = job_store.get(job_id)
                except (AnalysisJobIntegrityError, ValueError, KeyError) as error:
                    raise SessionIntegrityError(
                        "Analysis job cannot be trusted for deletion."
                    ) from error
                if job.get("session_id") != session_id:
                    continue
                if job.get("status") in {"queued", "running"}:
                    raise SessionStateError(
                        "Active analysis jobs cannot be deleted."
                    )
                job_ids.add(job_id)
                directory_targets.append(job_dir)
                for attempt_value in job.get("attempt_ids", []):
                    attempt_id = str(attempt_value)
                    try:
                        attempt = job_store.get_attempt(
                            job_id,
                            attempt_id,
                        )
                    except (
                        AnalysisJobIntegrityError,
                        ValueError,
                        KeyError,
                    ) as error:
                        raise SessionIntegrityError(
                            "Analysis attempt cannot be trusted for deletion."
                        ) from error
                    candidate_id = str(
                        uuid5(
                            NAMESPACE_URL,
                            f"{job_id}:{attempt_id}:{session_id}",
                        )
                    )
                    stored_analysis = attempt.get("analysis_id")
                    if stored_analysis is not None:
                        if str(stored_analysis) != candidate_id:
                            raise SessionIntegrityError(
                                "Attempt analysis identity is not deterministic."
                            )
                        analysis_ids.add(str(stored_analysis))
                    analysis_dir = self._assert_safe_path(
                        self.root / "analyses" / candidate_id
                    )
                    if not analysis_dir.exists():
                        continue
                    if analysis_dir.is_symlink() or not analysis_dir.is_dir():
                        raise SessionIntegrityError(
                            "Analysis target is not a safe directory."
                        )
                    result_path = self._assert_safe_path(
                        analysis_dir / "result.json"
                    )
                    try:
                        result_mode = result_path.lstat().st_mode
                    except OSError as error:
                        raise SessionIntegrityError(
                            "Analysis result cannot be safely inspected."
                        ) from error
                    if (
                        not stat.S_ISREG(result_mode)
                        or stat.S_IMODE(result_mode) != 0o600
                    ):
                        raise SessionIntegrityError(
                            "Analysis result is not a private regular file."
                        )
                    result = self._read_json(result_path)
                    try:
                        validated = AnalysisWorker._public_result(
                            result,
                            job=job,
                            attempt=attempt,
                            session=session,
                            profile=trusted_profile,
                            signal_dictionary=trusted_dictionary,
                        )
                    except Exception as error:
                        raise SessionIntegrityError(
                            "Analysis result cannot be trusted for deletion."
                        ) from error
                    if (
                        validated.get("analysis_id") != candidate_id
                        or validated.get("job_id") != job_id
                        or validated.get("attempt_id") != attempt_id
                        or validated.get("session_id") != session_id
                    ):
                        raise SessionIntegrityError(
                            "Analysis result identity does not match its owner."
                        )
                    analysis_ids.add(candidate_id)
                    directory_targets.append(analysis_dir)

        attached_value = session.get("analysis_job_id")
        if attached_value is not None:
            attached_id = _canonical_uuid(
                attached_value,
                field="analysis_job_id",
            )
            if attached_id not in job_ids:
                raise SessionIntegrityError(
                    "Attached analysis job is not a trusted complete job."
                )

        projection_value = session.get("legacy_projection_path")
        if projection_value is not None:
            if (
                not isinstance(projection_value, str)
                or not projection_value
                or Path(projection_value).is_absolute()
                or Path(projection_value).suffix != ".md"
            ):
                raise SessionIntegrityError(
                    "Legacy projection path is not a safe relative Markdown path."
                )
            projection = self._assert_safe_path(self.root / projection_value)
            stem = projection.with_suffix("")
            for candidate in (
                projection,
                projection.with_suffix(".meta.json"),
                stem.with_suffix(".raw_events.jsonl"),
                stem.with_suffix(".timeline.jsonl"),
            ):
                safe_candidate = self._assert_safe_path(candidate)
                if safe_candidate.exists():
                    file_targets.append(safe_candidate)

        session_dir = self._assert_safe_path(self._session_dir(session_id))
        directory_targets.append(session_dir)
        return (
            sorted(set(directory_targets), key=lambda path: str(path)),
            sorted(job_ids),
            sorted(analysis_ids),
            sorted(set(file_targets), key=lambda path: str(path)),
        )

    def _prepare_projection_index_removal(
        self,
        session_id: str,
        projection: object,
    ) -> tuple[Path, dict[str, object]] | None:
        if not isinstance(projection, str):
            return None
        index_path = self.root / Path(projection).parent / ".session_index.json"
        self._assert_safe_path(index_path)
        if not index_path.exists():
            return None
        if not index_path.is_file():
            raise SessionIntegrityError(
                "Legacy projection index is not a regular file."
            )
        index = self._read_json(index_path)
        if session_id in index:
            del index[session_id]
        return index_path, index

    def _validate_delete_target_types(
        self,
        directory_targets: Sequence[Path],
        file_targets: Sequence[Path],
    ) -> None:
        for directory in directory_targets:
            self._assert_safe_path(directory)
            if directory.exists() and not directory.is_dir():
                raise SessionIntegrityError(
                    "Cascade directory target is not a directory."
                )
        for path in file_targets:
            self._assert_safe_path(path)
            if path.exists() and not path.is_file():
                raise SessionIntegrityError(
                    "Cascade file target is not a regular file."
                )

    def delete_cascade(
        self,
        session_id: str,
        *,
        actor: str,
        reason: str,
    ) -> dict[str, object]:
        from .analysis_job_store import (
            AnalysisJobIntegrityError,
            AnalysisJobStore,
        )

        deletion_actor = _require_nonempty(actor, field="actor")
        _require_nonempty(reason, field="reason")
        self._session_dir(session_id)
        with self._lock_for(session_id):
            try:
                with AnalysisJobStore(self.root).deletion_reservation():
                    session = self.read(session_id)
                    (
                        directory_targets,
                        job_ids,
                        analysis_ids,
                        file_targets,
                    ) = self._trusted_delete_targets(session_id, session)
                    self._validate_delete_target_types(
                        directory_targets,
                        file_targets,
                    )
                    projection = session.get("legacy_projection_path")
                    index_context = (
                        projection_index_lock(
                            self.root / Path(projection).parent
                        )
                        if isinstance(projection, str)
                        else nullcontext()
                    )
                    with index_context:
                        index_update = (
                            self._prepare_projection_index_removal(
                                session_id,
                                projection,
                            )
                        )
                        audit_descriptor = self._open_audit_descriptor(
                            self.root
                            / "audit"
                            / "session_deletions.jsonl"
                        )
                        intent: dict[str, object] = {
                            "schema_version": 1,
                            "event": "deletion_intent",
                            "deleted_session_id": session_id,
                            "deleted_job_ids": job_ids,
                            "deleted_analysis_ids": analysis_ids,
                            "recorded_at": _now_iso(),
                            "actor": deletion_actor,
                        }
                        try:
                            self._write_audit_record(
                                audit_descriptor,
                                intent,
                            )
                            if index_update is not None:
                                index_path, updated_index = index_update
                                self._write_json(
                                    index_path,
                                    updated_index,
                                )
                            for path in file_targets:
                                path.unlink()
                            for directory in directory_targets:
                                if directory.exists():
                                    shutil.rmtree(directory)

                            completed: dict[str, object] = {
                                "schema_version": 1,
                                "event": "deletion_completed",
                                "deleted_session_id": session_id,
                                "deleted_job_ids": job_ids,
                                "deleted_analysis_ids": analysis_ids,
                                "deleted_at": _now_iso(),
                                "actor": deletion_actor,
                            }
                            self._write_audit_record(
                                audit_descriptor,
                                completed,
                            )
                            return completed
                        finally:
                            os.close(audit_descriptor)
            except AnalysisJobIntegrityError as error:
                raise SessionIntegrityError(
                    "Analysis jobs cannot be reserved for deletion."
                ) from error
