"""Bounded local background worker for persisted whole-session analysis."""

from __future__ import annotations

import json
import os
import queue
import re
import stat
import threading
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from jsonschema import ValidationError

from .analysis_job_store import (
    AnalysisJobIntegrityError,
    AnalysisJobStateError,
    AnalysisJobStore,
)
from .canonical_json import (
    canonical_json_bytes,
    normalize_json_value,
    sha256_json,
)
from .dimension_analyzer import (
    analysis_session_snapshot,
    analyze_session,
)
from .llm_transport import (
    LlmTransportError,
    WORKER_REQUEST_TIMEOUT_SEC,
    provider_json_client,
)
from .schema_registry import validate_schema
from .session_store import SessionIntegrityError, SessionStore


class AnalysisQueueFullError(RuntimeError):
    """Raised when the bounded local queue cannot accept another job."""


class AnalysisWorkerStateError(RuntimeError):
    """Raised when enqueue is requested for an ineligible job."""


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
_ANALYZER_ERROR_CODES = {
    "ai_not_configured",
    "ai_analysis_failed",
    "invalid_profile",
}
_PROVENANCE_KEYS = {
    "analysis_pipeline_version",
    "feature_extractor_version",
    "signal_dictionary_version",
    "signal_dictionary_hash",
    "model_name",
    "model_version",
    "model_parameters",
    "prompt_version",
    "prompt_content_hash",
    "provider_request_id",
    "raw_response_hash",
    "input_snapshot_hash",
}
_HEX_HASH = re.compile(r"^[0-9a-f]{64}$")
_SENTINEL = object()


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


def _read_inputs(
    session_store: SessionStore,
    session_id: str,
) -> tuple[
    dict[str, object],
    dict[str, object],
    list[dict[str, object]],
    dict[str, object],
]:
    # The shared Task 6 RLock makes the four canonical snapshots one atomic
    # read transaction relative to append/finalize/attach/delete operations.
    with session_store._lock_for(session_id):  # noqa: SLF001
        session = session_store.read(session_id)
        # Task 6 owns snapshot path validation and content-hash verification.
        # Until its API exposes read_profile(), use those validated helpers
        # rather than independently opening an untrusted path.
        profile = session_store._read_json(  # noqa: SLF001
            session_store._session_dir(session_id)  # noqa: SLF001
            / "profile.json"
        )
        events = session_store.read_events(session_id)
        signal_dictionary = session_store.read_signal_dictionary(
            session_id
        )
        return session, profile, events, signal_dictionary


def compute_input_snapshot_hash(
    session_store: SessionStore,
    session_id: str,
) -> str:
    """Compute the exact Task 8 analyzer input hash from canonical snapshots."""

    canonical_session_id = _canonical_uuid(session_id, field="session_id")
    session, profile, events, signal_dictionary = _read_inputs(
        session_store,
        canonical_session_id,
    )
    return sha256_json(
        {
            "session": analysis_session_snapshot(session),
            "profile": profile,
            "events": events,
            "signal_dictionary": signal_dictionary,
        }
    )


class _RecordingRetryingClient:
    def __init__(
        self,
        provider: Callable[..., Mapping[str, object]],
        wait: Callable[[float], None],
    ) -> None:
        self._provider = provider
        self._wait = wait
        self.responses: list[dict[str, object]] = []

    @staticmethod
    def _retryable(error: LlmTransportError) -> bool:
        if error.error_code in {
            "provider_network_error",
            "provider_timeout",
        }:
            return True
        return (
            error.error_code == "provider_http_error"
            and error.http_status is not None
            and (
                error.http_status == 429
                or 500 <= error.http_status <= 599
            )
        )

    def __call__(
        self,
        request_body: Mapping[str, object],
    ) -> Mapping[str, object]:
        delays = (2.0, 8.0)
        for call_index in range(3):
            try:
                raw = self._provider(
                    request_body,
                    timeout_sec=WORKER_REQUEST_TIMEOUT_SEC,
                )
                normalized = normalize_json_value(raw)
                if not isinstance(normalized, dict):
                    raise ValueError(
                        "Provider response must be a JSON object."
                    )
                self.responses.append(normalized)
                return normalized
            except LlmTransportError as error:
                if call_index >= 2 or not self._retryable(error):
                    raise
                self._wait(delays[call_index])
        raise AssertionError("unreachable")


class AnalysisWorker:
    """Run jobs synchronously in tests or on one bounded daemon worker."""

    def __init__(
        self,
        root: Path,
        *,
        job_store: AnalysisJobStore | None = None,
        session_store: SessionStore | None = None,
        provider_client: Callable[..., Mapping[str, object]] | None = None,
        wait: Callable[[float], None] | None = None,
        terminal_callback: Callable[[str], object] | None = None,
        synchronous: bool = False,
        autostart: bool = True,
    ) -> None:
        self.root = Path(root)
        self.job_store = job_store or AnalysisJobStore(self.root)
        self.session_store = session_store or SessionStore(self.root)
        self._provider_client = provider_client or provider_json_client
        self._wait = wait or time.sleep
        self._terminal_callback = terminal_callback
        self._synchronous = synchronous
        self._queue: queue.Queue[object] = queue.Queue(maxsize=100)
        self._enqueued: set[str] = set()
        self._state_lock = threading.RLock()
        self._stop = threading.Event()
        self._shutdown = False
        self._thread: threading.Thread | None = None
        if not synchronous and autostart:
            self.start()

    def start(self) -> None:
        """Start the daemon exactly once after deferred startup preparation."""

        with self._state_lock:
            if self._synchronous:
                return
            if self._shutdown:
                raise AnalysisWorkerStateError(
                    "Analysis worker is shut down."
                )
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            thread = threading.Thread(
                target=self._run,
                name="myextension-analysis-worker",
                daemon=True,
            )
            try:
                thread.start()
            except Exception:
                self._thread = None
                raise
            self._thread = thread

    def enqueue(self, job_id: str) -> None:
        canonical_job_id = _canonical_uuid(job_id, field="job_id")
        with self._state_lock:
            if self._shutdown:
                raise AnalysisWorkerStateError(
                    "Analysis worker is shut down."
                )
            if canonical_job_id in self._enqueued:
                return
            job = self.job_store.get(canonical_job_id)
            if job["status"] != "queued":
                raise AnalysisWorkerStateError(
                    "Only a queued job can be enqueued."
                )
            self._enqueued.add(canonical_job_id)
            if self._synchronous:
                execute_now = True
            else:
                execute_now = False
                try:
                    self._queue.put_nowait(canonical_job_id)
                except queue.Full as error:
                    self._enqueued.remove(canonical_job_id)
                    raise AnalysisQueueFullError(
                        "Analysis queue is full."
                    ) from error
        if execute_now:
            try:
                self._execute(canonical_job_id)
            finally:
                with self._state_lock:
                    self._enqueued.discard(canonical_job_id)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _SENTINEL:
                    return
                if not isinstance(item, str):
                    continue
                try:
                    self._execute(item)
                finally:
                    with self._state_lock:
                        self._enqueued.discard(item)
                if self._stop.is_set():
                    return
            finally:
                self._queue.task_done()

    def _assert_safe_path(self, path: Path) -> Path:
        root_absolute = self.root.absolute()
        candidate = Path(path).absolute()
        try:
            relative = candidate.relative_to(root_absolute)
        except ValueError as error:
            raise AnalysisJobIntegrityError(
                "Analysis path escapes the configured root."
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
                    "Analysis path cannot be safely inspected."
                ) from error
            if stat.S_ISLNK(mode):
                raise AnalysisJobIntegrityError(
                    "Analysis path traverses a symbolic link."
                )
        try:
            candidate.resolve(strict=False).relative_to(self.root.resolve())
        except ValueError as error:
            raise AnalysisJobIntegrityError(
                "Analysis path escapes the configured root."
            ) from error
        return candidate

    def _write_private_json(
        self,
        path: Path,
        value: Mapping[str, object],
        *,
        validator: Callable[[Mapping[str, object]], object] | None = None,
    ) -> str:
        safe_parent = self._assert_safe_path(path.parent)
        safe_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        safe_path = self._assert_safe_path(path)
        expected_bytes = canonical_json_bytes(value)
        if validator is not None:
            validator(value)

        def validate_existing() -> str:
            try:
                mode = safe_path.lstat().st_mode
            except FileNotFoundError as error:
                raise AnalysisJobIntegrityError(
                    "Immutable artifact disappeared during validation."
                ) from error
            except OSError as error:
                raise AnalysisJobIntegrityError(
                    "Private artifact cannot be safely inspected."
                ) from error
            if (
                not stat.S_ISREG(mode)
                or stat.S_IMODE(mode) != 0o600
            ):
                raise AnalysisJobIntegrityError(
                    "Immutable artifact is not a private regular file."
                )
            try:
                stored_bytes = safe_path.read_bytes()
                stored = json.loads(stored_bytes)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise AnalysisJobIntegrityError(
                    "Immutable artifact is not valid JSON."
                ) from error
            if not isinstance(stored, Mapping):
                raise AnalysisJobIntegrityError(
                    "Immutable artifact is not a JSON object."
                )
            try:
                canonical_stored = canonical_json_bytes(stored)
            except (TypeError, ValueError) as error:
                raise AnalysisJobIntegrityError(
                    "Immutable artifact is not canonical JSON."
                ) from error
            if stored_bytes != canonical_stored:
                raise AnalysisJobIntegrityError(
                    "Immutable artifact is not canonically encoded."
                )
            if validator is not None:
                validator(stored)
            if stored_bytes != expected_bytes:
                raise AnalysisJobIntegrityError(
                    "Immutable artifact conflicts with existing content."
                )
            return sha256_json(stored)

        try:
            existing_mode = safe_path.lstat().st_mode
        except FileNotFoundError:
            existing_mode = None
        except OSError as error:
            raise AnalysisJobIntegrityError(
                "Private artifact cannot be safely inspected."
            ) from error
        if existing_mode is not None:
            return validate_existing()

        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=safe_parent,
                prefix=f".{safe_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                os.fchmod(temporary.fileno(), 0o600)
                temporary.write(expected_bytes)
                temporary.flush()
                os.fsync(temporary.fileno())
            try:
                os.link(temporary_name, safe_path)
            except FileExistsError:
                return validate_existing()
            os.chmod(safe_path, 0o600)
        finally:
            if temporary_name is not None:
                try:
                    Path(temporary_name).unlink()
                except FileNotFoundError:
                    pass
        descriptor = os.open(safe_parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return sha256_json(value)

    @staticmethod
    def _validate_prompt_snapshot(
        value: Mapping[str, object],
    ) -> None:
        if set(value) != {
            "candidate_selector_version",
            "system_prompt",
            "requests",
            "selected_event_ids_by_dimension",
            "request_content_hashes",
        }:
            raise AnalysisJobIntegrityError(
                "Prompt snapshot is not closed."
            )
        if not isinstance(value.get("candidate_selector_version"), str):
            raise AnalysisJobIntegrityError(
                "Prompt candidate selector version is invalid."
            )
        if not isinstance(value.get("system_prompt"), str):
            raise AnalysisJobIntegrityError(
                "Prompt system text is invalid."
            )
        requests = value.get("requests")
        hashes = value.get("request_content_hashes")
        selections = value.get("selected_event_ids_by_dimension")
        if (
            not isinstance(requests, list)
            or not all(isinstance(item, Mapping) for item in requests)
            or not isinstance(hashes, list)
            or len(hashes) != len(requests)
            or hashes != [sha256_json(item) for item in requests]
            or not isinstance(selections, Mapping)
            or not all(
                isinstance(code, str)
                and isinstance(event_ids, list)
                and all(
                    isinstance(event_id, str) and event_id
                    for event_id in event_ids
                )
                for code, event_ids in selections.items()
            )
        ):
            raise AnalysisJobIntegrityError(
                "Prompt snapshot content is invalid."
            )

    @staticmethod
    def _validate_raw_wrapper(
        value: Mapping[str, object],
    ) -> None:
        if (
            set(value) != {"schema_version", "responses"}
            or value.get("schema_version") != 1
            or not isinstance(value.get("responses"), list)
            or not all(
                isinstance(response, Mapping)
                for response in value["responses"]  # type: ignore[index]
            )
        ):
            raise AnalysisJobIntegrityError(
                "Raw response wrapper is invalid."
            )

    @staticmethod
    def _public_result(
        analysis: Mapping[str, object],
        *,
        job: Mapping[str, object],
        attempt: Mapping[str, object],
        session: Mapping[str, object],
        profile: Mapping[str, object],
        signal_dictionary: Mapping[str, object],
    ) -> dict[str, object]:
        if not _PUBLIC_RESULT_KEYS.issubset(analysis):
            raise AnalysisJobIntegrityError(
                "Analyzer result is missing public fields."
            )
        projected = {
            key: analysis[key]
            for key in _PUBLIC_RESULT_KEYS
        }
        if set(projected) != _PUBLIC_RESULT_KEYS:
            raise AnalysisJobIntegrityError(
                "Public analysis projection is not closed."
            )
        if (
            projected["schema_version"] != 1
            or projected["job_id"] != job["job_id"]
            or projected["attempt_id"] != attempt["attempt_id"]
            or projected["session_id"] != job["session_id"]
            or projected["status"] not in {"ready", "partial"}
        ):
            raise AnalysisJobIntegrityError(
                "Public analysis identity or state is invalid."
            )
        analysis_id = _canonical_uuid(
            projected["analysis_id"], field="analysis_id"
        )
        expected_analysis_id = str(
            uuid5(
                NAMESPACE_URL,
                f"{job['job_id']}:{attempt['attempt_id']}:{session.get('session_id')}",
            )
        )
        if analysis_id != expected_analysis_id:
            raise AnalysisJobIntegrityError(
                "Public analysis id is not deterministic."
            )
        profile_id = _canonical_uuid(
            projected["profile_id"], field="profile_id"
        )
        version = projected["profile_version"]
        if (
            not isinstance(version, int)
            or isinstance(version, bool)
            or version < 1
        ):
            raise AnalysisJobIntegrityError(
                "Public profile version is invalid."
            )
        content_hash = projected["profile_content_hash"]
        if (
            not isinstance(content_hash, str)
            or _HEX_HASH.fullmatch(content_hash) is None
        ):
            raise AnalysisJobIntegrityError(
                "Public profile hash is invalid."
            )
        if (
            profile_id != session.get("profile_id")
            or profile_id != profile.get("profile_id")
            or version != session.get("profile_version")
            or version != profile.get("version")
            or content_hash != session.get("profile_content_hash")
            or content_hash != profile.get("content_hash")
        ):
            raise AnalysisJobIntegrityError(
                "Public profile identity does not match trusted snapshots."
            )
        rows = projected["dimension_results"]
        if (
            not isinstance(rows, list)
            or not 1 <= len(rows) <= 10
        ):
            raise AnalysisJobIntegrityError(
                "Public dimension results are invalid."
            )
        seen: set[str] = set()
        for row in rows:
            try:
                validate_schema("dimension-result-v1", row)
            except ValidationError as error:
                raise AnalysisJobIntegrityError(
                    "Public dimension result is invalid."
                ) from error
            if not isinstance(row, Mapping):
                raise AnalysisJobIntegrityError(
                    "Public dimension result is invalid."
                )
            code = row.get("dimension_code")
            if not isinstance(code, str) or code in seen:
                raise AnalysisJobIntegrityError(
                    "Public dimension results contain duplicate codes."
                )
            seen.add(code)
        dimensions = profile.get("dimensions")
        if not isinstance(dimensions, list):
            raise AnalysisJobIntegrityError(
                "Trusted profile dimensions are invalid."
            )
        expected_codes = {
            dimension.get("code")
            for dimension in dimensions
            if isinstance(dimension, Mapping)
            and isinstance(dimension.get("code"), str)
        }
        if len(expected_codes) != len(dimensions) or seen != expected_codes:
            raise AnalysisJobIntegrityError(
                "Public dimensions do not exactly match the profile."
            )
        provenance = projected["provenance"]
        if (
            not isinstance(provenance, Mapping)
            or set(provenance) != _PROVENANCE_KEYS
        ):
            raise AnalysisJobIntegrityError(
                "Public provenance is not closed."
            )
        for field in (
            "analysis_pipeline_version",
            "feature_extractor_version",
            "signal_dictionary_version",
            "model_name",
            "prompt_version",
        ):
            value = provenance.get(field)
            if not isinstance(value, str) or not value:
                raise AnalysisJobIntegrityError(
                    "Public provenance string field is invalid."
                )
        for field in (
            "model_version",
            "provider_request_id",
        ):
            value = provenance.get(field)
            if value is not None and (
                not isinstance(value, str) or not value
            ):
                raise AnalysisJobIntegrityError(
                    "Public provenance optional field is invalid."
                )
        for field in (
            "signal_dictionary_hash",
            "prompt_content_hash",
            "raw_response_hash",
            "input_snapshot_hash",
        ):
            value = provenance.get(field)
            if not isinstance(value, str) or _HEX_HASH.fullmatch(value) is None:
                raise AnalysisJobIntegrityError(
                    "Public provenance hash is invalid."
                )
        model_parameters = provenance.get("model_parameters")
        if (
            not isinstance(model_parameters, Mapping)
            or set(model_parameters) != {"temperature"}
            or isinstance(model_parameters.get("temperature"), bool)
            or not isinstance(
                model_parameters.get("temperature"), (int, float)
            )
            or model_parameters.get("temperature") != 0
        ):
            raise AnalysisJobIntegrityError(
                "Public model parameters are invalid."
            )
        dictionary_version = signal_dictionary.get("version")
        if (
            provenance["input_snapshot_hash"] != job["input_snapshot_hash"]
            or provenance["signal_dictionary_version"]
            != dictionary_version
            or provenance["signal_dictionary_version"]
            != session.get("signal_dictionary_version")
            or provenance["signal_dictionary_hash"]
            != sha256_json(signal_dictionary)
            or provenance["signal_dictionary_hash"]
            != session.get("signal_dictionary_hash")
        ):
            raise AnalysisJobIntegrityError(
                "Public provenance does not match trusted snapshots."
            )
        projected["analysis_id"] = analysis_id
        return projected

    def _execute(self, job_id: str) -> None:
        attempt: dict[str, object] | None = None
        prompt_hash: str | None = None
        raw_hash: str | None = None
        recorder = _RecordingRetryingClient(
            self._provider_client,
            self._wait,
        )
        error_code = "analysis_worker_failed"
        try:
            attempt = self.job_store.begin_attempt(job_id)
            job = self.job_store.get(job_id)
            session_id = str(job["session_id"])
            session, profile, events, dictionary = _read_inputs(
                self.session_store,
                session_id,
            )
            if session.get("status") != "finalized":
                error_code = "session_not_finalized"
                raise AnalysisWorkerStateError(error_code)
            current_hash = sha256_json(
                {
                    "session": analysis_session_snapshot(session),
                    "profile": profile,
                    "events": events,
                    "signal_dictionary": dictionary,
                }
            )
            if current_hash != job["input_snapshot_hash"]:
                error_code = "input_snapshot_mismatch"
                raise AnalysisJobIntegrityError(error_code)

            analysis = analyze_session(
                job_id=job_id,
                attempt_id=str(attempt["attempt_id"]),
                session=analysis_session_snapshot(session),
                profile=profile,
                events=events,
                signal_dictionary=dictionary,
                client=recorder,
            )
            prompt_snapshot = analysis.get("prompt_snapshot")
            if not isinstance(prompt_snapshot, Mapping):
                error_code = "analysis_output_invalid"
                raise AnalysisJobIntegrityError(error_code)
            attempt_root = (
                self.root
                / "jobs"
                / job_id
                / "attempts"
            )
            error_code = "analysis_artifact_write_failed"
            prompt_hash = self._write_private_json(
                attempt_root
                / f"{attempt['attempt_id']}.prompt.json",
                dict(prompt_snapshot),
                validator=self._validate_prompt_snapshot,
            )
            raw_wrapper: dict[str, object] = {
                "schema_version": 1,
                "responses": list(recorder.responses),
            }
            raw_hash = self._write_private_json(
                attempt_root
                / f"{attempt['attempt_id']}.raw_response.json",
                raw_wrapper,
                validator=self._validate_raw_wrapper,
            )
            error_code = "analysis_output_invalid"
            public_result = self._public_result(
                analysis,
                job=job,
                attempt=attempt,
                session=session,
                profile=profile,
                signal_dictionary=dictionary,
            )
            analysis_id = str(public_result["analysis_id"])
            analyzer_error = analysis.get("error_code")
            terminal_error = (
                str(analyzer_error)
                if isinstance(analyzer_error, str)
                and analyzer_error in _ANALYZER_ERROR_CODES
                else None
            )
            if analyzer_error is not None and terminal_error is None:
                error_code = "analysis_output_invalid"
                raise AnalysisJobIntegrityError(error_code)
            error_code = "analysis_artifact_write_failed"
            self._write_private_json(
                self.root
                / "analyses"
                / analysis_id
                / "result.json",
                public_result,
                validator=lambda stored: self._public_result(
                    stored,
                    job=job,
                    attempt=attempt,
                    session=session,
                    profile=profile,
                    signal_dictionary=dictionary,
                ),
            )
            error_code = "analysis_commit_failed"
            self.job_store.finish_attempt(
                job_id,
                str(attempt["attempt_id"]),
                status=str(public_result["status"]),  # type: ignore[arg-type]
                analysis_id=analysis_id,
                error_code=terminal_error,
                prompt_snapshot_hash=prompt_hash,
                raw_response_snapshot_hash=raw_hash,
            )
            self._notify_terminal(session_id)
            return
        except (
            AnalysisJobIntegrityError,
            AnalysisWorkerStateError,
            SessionIntegrityError,
        ):
            if error_code == "analysis_worker_failed":
                error_code = "analysis_input_invalid"
        except Exception:
            error_code = "analysis_worker_failed"

        if attempt is None:
            return
        if raw_hash is None and recorder.responses:
            try:
                raw_hash = self._write_private_json(
                    self.root
                    / "jobs"
                    / job_id
                    / "attempts"
                    / f"{attempt['attempt_id']}.raw_response.json",
                    {
                        "schema_version": 1,
                        "responses": list(recorder.responses),
                    },
                    validator=self._validate_raw_wrapper,
                )
            except Exception:
                raw_hash = None
        try:
            self.job_store.finish_attempt(
                job_id,
                str(attempt["attempt_id"]),
                status="error",
                analysis_id=None,
                error_code=error_code,
                prompt_snapshot_hash=prompt_hash,
                raw_response_snapshot_hash=raw_hash,
            )
        except (AnalysisJobIntegrityError, AnalysisJobStateError, ValueError):
            # Fail closed: never overwrite a conflicting durable terminal state.
            return

    def _notify_terminal(self, session_id: str) -> None:
        callback = self._terminal_callback
        if callback is None:
            return
        try:
            callback(session_id)
        except Exception:
            return

    def shutdown(self) -> None:
        with self._state_lock:
            if self._shutdown:
                return
            self._shutdown = True
            self._stop.set()
            thread = self._thread
            if thread is not None:
                try:
                    self._queue.put_nowait(_SENTINEL)
                except queue.Full:
                    pass
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=1.0)
