"""Safe public projections of local pilot session artifacts."""

from __future__ import annotations

import copy
import json
import math
import re
import unicodedata
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator

from jsonschema import ValidationError

from .analysis_job_store import AnalysisJobIntegrityError, AnalysisJobNotFoundError
from .canonical_json import canonical_json_bytes, sha256_json
from .review_store import ReviewIntegrityError
from .schema_registry import validate_schema
from .session_log_artifacts import (
    MAX_INLINE_LOG_BYTES,
    SESSION_LOG_BY_KIND,
    SESSION_LOG_DEFINITIONS,
    render_session_log_artifacts,
)
from .session_store import SessionIntegrityError


PUBLIC_EVENT_FIELDS = {
    "event_id",
    "session_seq",
    "segment_type",
    "started_at",
    "ended_at",
    "duration_ms",
    "inserted_char_count",
    "deleted_char_count",
    "paste_char_count",
    "cell_source",
    "execution_result",
    "error_type",
    "error_message",
    "deleted_content",
    "deleted_is_full_line",
    "had_paste",
    "document_type",
    "file_name",
    "notebook_id",
    "cell_id",
    "cell_index",
    "cell_type",
}
PUBLIC_PROFILE_FIELDS = {
    "schema_version",
    "profile_id",
    "version",
    "problem_id",
    "title",
    "dimensions",
    "knowledge_points",
    "content_hash",
}
PUBLIC_PROVENANCE_FIELDS = {
    "analysis_pipeline_version",
    "feature_extractor_version",
    "signal_dictionary_version",
    "signal_dictionary_hash",
    "model_name",
    "model_version",
    "model_parameters",
    "prompt_version",
    "prompt_content_hash",
    "raw_response_hash",
    "input_snapshot_hash",
}
_RESULT_PUBLIC_FIELDS = {
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
}
_DIMENSION_RESULT_PUBLIC_FIELDS = {
    "schema_version",
    "dimension_code",
    "decision",
    "data_quality",
    "ai_result",
    "review",
}
_TRAINING_RECORD_PUBLIC_FIELDS = (
    "session",
    "problem_profile",
    "code_snapshots",
    "behavior_events",
    "ai_analysis",
    "teacher_reviews",
    "integrity",
)
_MAX_TRAINING_RECORD_ITEMS = 10_000
_MAX_TRAINING_RECORD_BYTES = 32 * 1024 * 1024
_MAX_TRAINING_RECORD_STRING_CHARACTERS = 100_000
_EXPORT_LIMIT_ERROR = "Training record exceeds the approved export limit."
_EXPORT_STABILITY_ERROR = "Training record source did not stabilize."
DIAGNOSTIC_STRING_FIELDS = {"error_message", "execution_result"}
_ABSOLUTE_PATH_PREFIX = r"(?:[A-Za-z]:[\\/]|\\\\|/(?!/))"
_QUOTED_DIAGNOSTIC_PATH = re.compile(
    rf"(?P<quote>[\"'])(?P<path>{_ABSOLUTE_PATH_PREFIX}[^\"']*)(?P=quote)"
)
_FILE_DIAGNOSTIC_PATH = re.compile(
    rf"(?P<prefix>\bFile\s+)(?P<path>{_ABSOLUTE_PATH_PREFIX}[^,;\r\n]*)",
    re.IGNORECASE,
)
_LOCAL_PATH_LABELS = (
    "path",
    "file",
    "filename",
    "file_path",
    "notebook_path",
    "root",
    "drive",
    "unc",
)
_QUOTED_LABELED_PATH = re.compile(
    rf"(?P<prefix>\b(?:{'|'.join(_LOCAL_PATH_LABELS)})\s*=\s*)"
    rf"(?P<quote>[\"'])(?P<path>{_ABSOLUTE_PATH_PREFIX}[^\"']*)(?P=quote)",
    re.IGNORECASE,
)
_BARE_LABELED_PATH = re.compile(
    rf"(?P<prefix>\b(?:{'|'.join(_LOCAL_PATH_LABELS)})\s*=\s*)"
    rf"(?P<path>{_ABSOLUTE_PATH_PREFIX}"
    r"(?:(?![,;\r\n]|\s+[A-Za-z_][A-Za-z0-9_]*\s*=).)*)",
    re.IGNORECASE,
)
_STRUCTURED_CONTEXT = re.compile(
    r"(?P<prefix>\b(?:route|pointer|json_pointer|url|uri)\s*=\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.IGNORECASE,
)
_RAW_HTTP_URL = re.compile(r"https?://[^\s,;]+", re.IGNORECASE)
_BARE_WINDOWS_OR_UNC_PATH = re.compile(
    rf"(?<![\w:/\\])(?P<path>(?:[A-Za-z]:[\\/]|\\\\)[^\s,;\"']*)"
)
_BARE_POSIX_PATH = re.compile(
    r"(?<![\w:/])(?P<path>/(?!/)"
    r"(?:(?![,;\r\n]|\s+[A-Za-z_][A-Za-z0-9_]*\s*=).)*)"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path_basename(value: str) -> str:
    return value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _scrub_diagnostic_paths(value: str) -> str:
    """Reduce local diagnostic paths while preserving non-file identifiers."""

    def replace_quoted(match: re.Match[str]) -> str:
        return f"{match.group('quote')}{_path_basename(match.group('path'))}{match.group('quote')}"

    def replace_file(match: re.Match[str]) -> str:
        return f"{match.group('prefix')}{_path_basename(match.group('path'))}"

    def replace_labeled(match: re.Match[str]) -> str:
        quote = match.groupdict().get("quote")
        basename = _path_basename(match.group("path").rstrip())
        if quote is None:
            return f"{match.group('prefix')}{basename}"
        return f"{match.group('prefix')}{quote}{basename}{quote}"

    def replace_bare(match: re.Match[str]) -> str:
        return _path_basename(match.group("path").rstrip())

    def scrub_unprotected(segment: str) -> str:
        scrubbed = _QUOTED_LABELED_PATH.sub(replace_labeled, segment)
        scrubbed = _BARE_LABELED_PATH.sub(replace_labeled, scrubbed)
        scrubbed = _FILE_DIAGNOSTIC_PATH.sub(replace_file, scrubbed)
        scrubbed = _QUOTED_DIAGNOSTIC_PATH.sub(replace_quoted, scrubbed)
        scrubbed = _BARE_WINDOWS_OR_UNC_PATH.sub(replace_bare, scrubbed)
        return _BARE_POSIX_PATH.sub(replace_bare, scrubbed)

    candidates = [
        (match.start(), match.end(), 0, match)
        for match in _STRUCTURED_CONTEXT.finditer(value)
    ]
    candidates.extend(
        (match.start(), match.end(), 1, match)
        for match in _RAW_HTTP_URL.finditer(value)
    )
    candidates.sort(
        key=lambda candidate: (
            candidate[0],
            -(candidate[1] - candidate[0]),
            candidate[2],
        )
    )
    protected_spans: list[re.Match[str]] = []
    previous_selected_end = 0
    for start, end, _priority, match in candidates:
        if start < previous_selected_end:
            continue
        protected_spans.append(match)
        previous_selected_end = end

    parts: list[str] = []
    previous_end = 0
    for match in protected_spans:
        parts.append(scrub_unprotected(value[previous_end : match.start()]))
        parts.append(match.group(0))
        previous_end = match.end()
    parts.append(scrub_unprotected(value[previous_end:]))
    return "".join(parts)


class SessionLogIntegrityError(RuntimeError):
    """Raised when persisted session artifacts cannot be safely projected."""


class SessionLogArtifactNotReadyError(RuntimeError):
    """Raised when a requested public log is not in its ready state."""


class SessionLogArtifactTooLargeError(RuntimeError):
    """Raised when a public log exceeds the approved inline-view limit."""


class SessionLogService:
    """Project trusted session storage into a path-safe viewer shape."""

    def __init__(
        self,
        *,
        root: Path,
        session_store,
        job_store,
        review_store,
    ) -> None:
        self.root = Path(root)
        self.session_store = session_store
        self.job_store = job_store
        self.review_store = review_store

    def _summary(self, session_id: str) -> dict[str, object]:
        session = self.session_store.read(session_id)
        profile = self.session_store.read_profile(session_id)
        job_id = session.get("analysis_job_id")
        job_status: object = None
        review_count: int | None = None
        event_count = session.get("received_event_count")
        if (
            not isinstance(event_count, int)
            or isinstance(event_count, bool)
            or event_count < 0
        ):
            raise SessionIntegrityError("Session event count is invalid.")
        if job_id is not None:
            if not isinstance(job_id, str):
                raise SessionIntegrityError(
                    "Session analysis job linkage is invalid."
                )
            job = self.job_store.get(job_id)
            if job.get("session_id") != session.get("session_id"):
                raise AnalysisJobIntegrityError(
                    "Analysis job does not belong to the session."
                )
            job_status = job.get("status")
            if job_status in {"ready", "partial"}:
                try:
                    result = self.job_store.load_public_result(
                        job_id,
                        session_store=self.session_store,
                    )
                except AnalysisJobIntegrityError:
                    result = None
                if result is None:
                    return {
                        "session_id": session["session_id"],
                        "problem_id": session["problem_id"],
                        "problem_title": profile["title"],
                        "profile_id": session["profile_id"],
                        "profile_version": session["profile_version"],
                        "status": session["status"],
                        "started_at": session["started_at"],
                        "ended_at": session["ended_at"],
                        "analysis_job_id": job_id,
                        "analysis_status": job_status,
                        "review_count": None,
                        "event_count": event_count,
                    }
                analysis_id = result.get("analysis_id")
                dimensions = result.get("dimension_results")
                if not isinstance(analysis_id, str) or not isinstance(
                    dimensions, list
                ):
                    raise AnalysisJobIntegrityError(
                        "Validated analysis result is structurally invalid."
                    )
                expected_dimensions: set[str] = set()
                for dimension in dimensions:
                    if not isinstance(dimension, Mapping) or not isinstance(
                        dimension.get("dimension_code"), str
                    ):
                        raise AnalysisJobIntegrityError(
                            "Validated analysis dimension is structurally invalid."
                        )
                    expected_dimensions.add(str(dimension["dimension_code"]))
                reviews = self.review_store.list_all(analysis_id)
                if any(
                    review.get("dimension_code") not in expected_dimensions
                    for review in reviews
                ):
                    raise ReviewIntegrityError(
                        "Review history references an unknown dimension."
                    )
                review_count = len(reviews)
        return {
            "session_id": session["session_id"],
            "problem_id": session["problem_id"],
            "problem_title": profile["title"],
            "profile_id": session["profile_id"],
            "profile_version": session["profile_version"],
            "status": session["status"],
            "started_at": session["started_at"],
            "ended_at": session["ended_at"],
            "analysis_job_id": job_id,
            "analysis_status": job_status,
            "review_count": review_count,
            "event_count": event_count,
        }

    def get_detail(self, session_id: str) -> dict[str, object]:
        """Return a safe, standalone projection for one persisted session."""

        try:
            session = self.session_store.read(session_id)
            profile = self.session_store.read_profile(session_id)
            missing: list[str] = []
            warnings: list[str] = []
            events = self.session_store.read_events_if_present(session_id)
            if events is None:
                events = []
                missing.append("raw_events")
            public_events = [self._public_event(event) for event in events]
            detail: dict[str, object] = {
                "schema_version": 1,
                "session": self._summary(session_id),
                "problem_profile": self._public_profile(profile),
                "code_snapshots": self._code_snapshots(public_events),
                "behavior_events": public_events,
                "ai_analysis": None,
                "teacher_reviews": [],
                "integrity": {
                    "complete": not missing,
                    "missing_artifacts": missing,
                    "warnings": warnings,
                },
                "training_record": {
                    "exists": False,
                    "stale": False,
                    "generated_at": None,
                    "content_hash": None,
                },
            }
            detail = self._attach_analysis(detail, session, profile)
            detail["training_record"] = self._training_record_state(
                session_id,
                detail,
            )
            return detail
        except (
            SessionIntegrityError,
            AnalysisJobIntegrityError,
            AnalysisJobNotFoundError,
            ReviewIntegrityError,
        ) as error:
            raise SessionLogIntegrityError(
                "Stored session log is incomplete or unsafe."
            ) from error

    def export_training_record(self, session_id: str) -> dict[str, object]:
        """Persist a private-safe, reproducible training projection."""

        for _attempt in range(3):
            detail = self.get_detail(session_id)
            self._assert_export_bounds(detail)
            record = self._build_training_record(session_id, detail)
            export = record["export"]
            if not isinstance(export, Mapping):
                raise SessionIntegrityError(
                    "Training record export metadata is invalid."
                )
            source_state_hash = export["source_state_hash"]
            content_hash = export["content_hash"]
            try:
                source_accepted = (
                    self.session_store.write_training_record_if_source_current(
                        session_id,
                        record,
                        lambda: self._source_state_hash(
                            self.get_detail(session_id)
                        )
                        == source_state_hash,
                    )
                )
            except SessionIntegrityError as error:
                raise SessionLogIntegrityError(
                    "Training source state is incomplete."
                ) from error
            if not source_accepted:
                continue
            stored = self.session_store.read_training_record(session_id)
            if stored is None:
                raise SessionIntegrityError("Training record disappeared after write.")
            self._validate_training_record(stored)
            stored_export = stored.get("export")
            if not isinstance(stored_export, Mapping):
                raise SessionIntegrityError(
                    "Training record export metadata is invalid."
                )
            current_detail = self.get_detail(session_id)
            if (
                stored_export.get("content_hash") == content_hash
                and stored_export.get("source_state_hash") == source_state_hash
                and self._source_state_hash(current_detail) == source_state_hash
            ):
                generated_at = stored_export.get("generated_at")
                stored_content_hash = stored_export.get("content_hash")
                if not isinstance(generated_at, str) or not isinstance(
                    stored_content_hash, str
                ):
                    raise SessionIntegrityError(
                        "Training record export metadata is invalid."
                    )
                self._refresh_log_artifacts(session_id, stored)
                return {
                    "schema_version": 1,
                    "session_id": session_id,
                    "relative_path": f"sessions/{session_id}/training_record.json",
                    "generated_at": generated_at,
                    "content_hash": stored_content_hash,
                    "stale": False,
                }
        raise SessionLogIntegrityError(_EXPORT_STABILITY_ERROR)

    def list_log_artifacts(self, session_id: str) -> list[dict[str, object]]:
        """Return the fixed public artifact order and durable status."""

        session = self.session_store.read(session_id)
        session_status = session.get("status")
        job_status: object = None
        job_error: object = None
        job_id = session.get("analysis_job_id")
        if job_id is not None:
            if not isinstance(job_id, str):
                raise SessionLogIntegrityError(
                    "Stored session log is incomplete or unsafe."
                )
            job = self.job_store.get(job_id)
            if job.get("session_id") != session_id:
                raise SessionLogIntegrityError(
                    "Stored session log is incomplete or unsafe."
                )
            job_status = job.get("status")
            job_error = job.get("error_code")

        rows: list[dict[str, object]] = []
        for definition in SESSION_LOG_DEFINITIONS:
            filename = definition["filename"]
            metadata = self.session_store.stat_log_artifact(
                session_id,
                filename,
            )
            status = "pending"
            error_code: object = None
            if definition["kind"] == "analysis":
                if job_status in {"queued", "running"}:
                    status = "generating"
                elif job_status == "ready":
                    if metadata is not None:
                        status = "ready"
                    else:
                        # The worker commits the job before its failure-isolated
                        # export callback writes the public artifact. Treat that
                        # narrow handoff as transient so clients keep polling.
                        status = "generating"
                elif job_status in {"partial", "error"}:
                    status = "error"
                    error_code = job_error or f"analysis_{job_status}"
            elif metadata is not None:
                status = "ready"
            elif session_status == "finalizing":
                status = "generating"
            elif session_status == "finalized":
                status = "error"
                error_code = "local_log_missing"

            rows.append(
                {
                    **definition,
                    "status": status,
                    "size_bytes": metadata.st_size if metadata is not None else None,
                    "generated_at": (
                        datetime.fromtimestamp(
                            metadata.st_mtime,
                            timezone.utc,
                        ).isoformat()
                        if metadata is not None
                        else None
                    ),
                    "error_code": error_code,
                }
            )
        return rows

    def read_log_artifact(
        self,
        session_id: str,
        kind: str,
        *,
        inline: bool,
    ) -> tuple[dict[str, object], bytes]:
        """Read one ready allowlisted log for inline viewing or download."""

        with self.open_log_artifact(
            session_id,
            kind,
            inline=inline,
        ) as (metadata, stream):
            try:
                return metadata, stream.read()
            except OSError as error:
                raise SessionLogIntegrityError(
                    "Requested session log is unreadable."
                ) from error

    @contextmanager
    def open_log_artifact(
        self,
        session_id: str,
        kind: str,
        *,
        inline: bool,
    ) -> Iterator[tuple[dict[str, object], BinaryIO]]:
        """Securely open one ready artifact for bounded view or streaming."""

        definition = SESSION_LOG_BY_KIND.get(kind)
        if definition is None:
            raise ValueError("Session log kind is not allowed.")
        metadata = next(
            row
            for row in self.list_log_artifacts(session_id)
            if row["kind"] == kind
        )
        if metadata["status"] != "ready":
            raise SessionLogArtifactNotReadyError(
                "Requested session log is not ready."
            )
        if inline and isinstance(metadata.get("size_bytes"), int) and (
            int(metadata["size_bytes"]) > MAX_INLINE_LOG_BYTES
        ):
            raise SessionLogArtifactTooLargeError(
                "Requested session log is too large for inline viewing."
            )
        try:
            with self.session_store.open_log_artifact(
                session_id,
                definition["filename"],
                max_bytes=MAX_INLINE_LOG_BYTES if inline else None,
            ) as (stream, actual):
                actual_metadata = {
                    **metadata,
                    "size_bytes": actual.st_size,
                    "generated_at": datetime.fromtimestamp(
                        actual.st_mtime,
                        timezone.utc,
                    ).isoformat(),
                }
                yield actual_metadata, stream
        except KeyError as error:
            raise SessionLogArtifactNotReadyError(
                "Requested session log is not ready."
            ) from error

    def _refresh_log_artifacts(
        self,
        session_id: str,
        record: Mapping[str, object],
    ) -> None:
        artifacts = render_session_log_artifacts(record)
        for filename, content in artifacts.items():
            if filename in {"operation_log.json", "process_log.md"} and (
                self.session_store.stat_log_artifact(session_id, filename)
                is not None
            ):
                continue
            self.session_store.write_log_artifact(
                session_id,
                filename,
                content,
            )
        if "analysis_log.json" not in artifacts:
            self.session_store.remove_log_artifact(
                session_id,
                "analysis_log.json",
            )

    def _public_event(
        self,
        event: Mapping[str, object],
    ) -> dict[str, object]:
        projected: dict[str, object] = {}
        for key in PUBLIC_EVENT_FIELDS:
            if key not in event:
                continue
            value = event[key]
            if key == "notebook_id":
                value = self._safe_notebook_id(value)
            elif key in DIAGNOSTIC_STRING_FIELDS and isinstance(value, str):
                value = _scrub_diagnostic_paths(value)
            projected[key] = value
        document_name = self._document_name(event)
        if document_name is not None:
            projected["document_name"] = document_name
        if "file_name" in projected:
            projected["file_name"] = self._basename(projected["file_name"])
        return projected

    def _public_profile(
        self,
        profile: Mapping[str, object],
    ) -> dict[str, object]:
        return {
            key: profile[key]
            for key in PUBLIC_PROFILE_FIELDS
            if key in profile
        }

    @staticmethod
    def _basename(value: object) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        basename = value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        return basename or None

    def _document_name(self, event: Mapping[str, object]) -> str | None:
        for field in ("file_name", "file_path", "notebook_path"):
            basename = self._basename(event.get(field))
            if basename is not None:
                return basename
        return None

    @staticmethod
    def _safe_notebook_id(value: object) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        if (
            "/" in value
            or "\\" in value
            or re.match(r"^[A-Za-z]:", value) is not None
        ):
            return SessionLogService._basename(value)
        return value

    def _code_snapshots(
        self,
        events: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        snapshots: list[dict[str, object]] = []
        for event in events:
            source = event.get("cell_source")
            if not isinstance(source, str):
                continue
            event_id = event.get("event_id")
            sequence = event.get("session_seq")
            started_at = event.get("started_at")
            ended_at = event.get("ended_at")
            if (
                not isinstance(event_id, str)
                or not event_id
                or not isinstance(sequence, int)
                or isinstance(sequence, bool)
                or not isinstance(started_at, str)
                or not started_at
                or not isinstance(ended_at, str)
                or not ended_at
            ):
                raise SessionIntegrityError(
                    "Source event is missing required snapshot fields."
                )
            snapshot = {
                "snapshot_id": sha256_json(
                    {
                        "first_event_id": event_id,
                        "source": source,
                    }
                ),
                "event_ids": [event_id],
                "first_session_seq": sequence,
                "last_session_seq": sequence,
                "started_at": started_at,
                "ended_at": ended_at,
                "source": source,
                "document_type": event.get("document_type"),
                "document_name": event.get("document_name"),
                "cell_id": event.get("cell_id"),
                "cell_index": event.get("cell_index"),
                "execution_result": event.get("execution_result"),
                "error_type": event.get("error_type"),
                "error_message": event.get("error_message"),
            }
            if self._can_merge_snapshot(snapshots, snapshot):
                current = snapshots[-1]
                current["event_ids"].append(event_id)
                current["last_session_seq"] = sequence
                current["ended_at"] = ended_at
                for field in (
                    "execution_result",
                    "error_type",
                    "error_message",
                ):
                    if event.get(field) is not None:
                        current[field] = event[field]
            else:
                snapshots.append(snapshot)
        return snapshots

    @staticmethod
    def _can_merge_snapshot(
        snapshots: list[dict[str, object]],
        candidate: dict[str, object],
    ) -> bool:
        if not snapshots:
            return False
        previous = snapshots[-1]
        return all(
            previous[field] == candidate[field]
            for field in (
                "source",
                "document_type",
                "document_name",
                "cell_id",
                "cell_index",
            )
        )

    def _attach_analysis(
        self,
        detail: dict[str, object],
        session: Mapping[str, object],
        profile: Mapping[str, object],
    ) -> dict[str, object]:
        """Attach only a validated terminal result and append-only reviews."""

        del profile
        job_id = session.get("analysis_job_id")
        if job_id is None:
            return detail
        if not isinstance(job_id, str):
            raise SessionIntegrityError("Session analysis job linkage is invalid.")
        job = self.job_store.get(job_id)
        if job.get("session_id") != session.get("session_id"):
            raise AnalysisJobIntegrityError(
                "Analysis job does not belong to the session."
            )
        status = job.get("status")
        session_detail = detail.get("session")
        if not isinstance(session_detail, dict):
            raise SessionIntegrityError("Session detail is structurally invalid.")
        session_detail["analysis_status"] = status
        if status not in {"ready", "partial"}:
            return detail
        if status == "partial" and job.get("error_code") == "ai_not_configured":
            return detail

        result = self.job_store.load_public_result(
            job_id,
            session_store=self.session_store,
        )
        if (
            result.get("session_id") != session.get("session_id")
            or result.get("job_id") != job_id
            or result.get("analysis_id") != job.get("analysis_id")
        ):
            raise AnalysisJobIntegrityError(
                "Analysis result does not belong to the attached job."
            )
        dimensions = result.get("dimension_results")
        analysis_id = result.get("analysis_id")
        if not isinstance(dimensions, list) or not isinstance(analysis_id, str):
            raise AnalysisJobIntegrityError(
                "Validated analysis result is structurally invalid."
            )
        public_result = {
            key: copy.deepcopy(result[key])
            for key in _RESULT_PUBLIC_FIELDS
            if key in result
        }
        provenance = result.get("provenance")
        if isinstance(provenance, Mapping):
            public_result["provenance"] = {
                key: copy.deepcopy(provenance[key])
                for key in PUBLIC_PROVENANCE_FIELDS
                if key in provenance
            }
        public_dimensions: list[dict[str, object]] = []
        for row in dimensions:
            if not isinstance(row, Mapping):
                raise AnalysisJobIntegrityError("Analysis dimension is invalid.")
            public_dimensions.append({
                key: copy.deepcopy(row[key])
                for key in _DIMENSION_RESULT_PUBLIC_FIELDS
                if key in row
            })
        public_result["dimension_results"] = public_dimensions
        detail["ai_analysis"] = public_result

        public_events = detail.get("behavior_events")
        integrity = detail.get("integrity")
        if not isinstance(public_events, list) or not isinstance(integrity, dict):
            raise SessionIntegrityError("Session detail is structurally invalid.")
        events_by_id: dict[str, dict[str, object]] = {}
        for event in public_events:
            if not isinstance(event, dict) or not isinstance(
                event.get("event_id"), str
            ):
                raise SessionIntegrityError("Public event identity is invalid.")
            events_by_id[str(event["event_id"])] = event

        unknown_event_ids: set[str] = set()
        dimension_codes: set[str] = set()
        for row in public_dimensions:
            dimension_code = row.get("dimension_code")
            if not isinstance(dimension_code, str):
                raise AnalysisJobIntegrityError("Analysis dimension code is invalid.")
            dimension_codes.add(dimension_code)
            ai_result = row.get("ai_result")
            if ai_result is not None:
                if not isinstance(ai_result, Mapping):
                    raise AnalysisJobIntegrityError("Analysis AI result is invalid.")
                claims = ai_result.get("evidence_claims")
                if not isinstance(claims, list):
                    raise AnalysisJobIntegrityError("Analysis evidence claims are invalid.")
                for claim in claims:
                    if not isinstance(claim, Mapping) or not isinstance(
                        claim.get("event_id"), str
                    ):
                        raise AnalysisJobIntegrityError("Analysis evidence claim is invalid.")
                    event_id = str(claim["event_id"])
                    event = events_by_id.get(event_id)
                    if event is None:
                        unknown_event_ids.add(event_id)
                    else:
                        referenced = event.setdefault(
                            "referenced_by_dimensions", []
                        )
                        if not isinstance(referenced, list):
                            raise SessionIntegrityError(
                                "Public event evidence linkage is invalid."
                            )
                        referenced.append(dimension_code)
        reviews = self.review_store.list_all(analysis_id)
        if any(
            review.get("dimension_code") not in dimension_codes
            for review in reviews
        ):
            raise ReviewIntegrityError(
                "Review history references an unknown dimension."
            )
        for event in public_events:
            referenced = event.get("referenced_by_dimensions")
            if isinstance(referenced, list):
                event["referenced_by_dimensions"] = sorted(set(referenced))
        if unknown_event_ids:
            warnings = integrity.get("warnings")
            if not isinstance(warnings, list):
                raise SessionIntegrityError("Session integrity warnings are invalid.")
            warnings.append(
                f"AI 证据引用了 {len(unknown_event_ids)} 个不存在的事件。"
            )
            integrity["complete"] = False
        detail["teacher_reviews"] = reviews
        return detail

    def _training_record_state(
        self,
        session_id: str,
        detail: Mapping[str, object],
    ) -> dict[str, object]:
        """Describe whether the persisted export matches attached source state."""

        stored = self.session_store.read_training_record(session_id)
        if stored is None:
            return {
                "exists": False,
                "stale": False,
                "generated_at": None,
                "content_hash": None,
            }
        self._validate_training_record(stored)
        export = stored.get("export")
        if not isinstance(export, Mapping) or export.get("source_session_id") != session_id:
            raise SessionIntegrityError("Training record belongs to another session.")
        source_state_hash = export.get("source_state_hash")
        generated_at = export.get("generated_at")
        content_hash = export.get("content_hash")
        if not all(isinstance(value, str) for value in (
            source_state_hash, generated_at, content_hash,
        )):
            raise SessionIntegrityError("Training record export metadata is invalid.")
        return {
            "exists": True,
            "stale": source_state_hash != self._source_state_hash(detail),
            "generated_at": generated_at,
            "content_hash": content_hash,
        }

    @staticmethod
    def _canonical_hash(value: object) -> str:
        return sha256_json(value)

    def _source_state_hash(self, detail: Mapping[str, object]) -> str:
        return self._canonical_hash(self._public_record_fields(detail))

    @staticmethod
    def _public_record_fields(
        detail: Mapping[str, object],
    ) -> dict[str, object]:
        try:
            return {
                key: copy.deepcopy(detail[key])
                for key in _TRAINING_RECORD_PUBLIC_FIELDS
            }
        except KeyError as error:
            raise SessionIntegrityError("Training source state is invalid.") from error

    def _build_training_record(
        self,
        session_id: str,
        detail: Mapping[str, object],
    ) -> dict[str, object]:
        record = {
            "schema_version": 1,
            **self._public_record_fields(detail),
            "export": {
                "schema_version": 1,
                "generated_at": _now_iso(),
                "source_session_id": session_id,
                "source_state_hash": self._source_state_hash(detail),
            },
        }
        export = record["export"]
        if not isinstance(export, dict):
            raise SessionIntegrityError("Training record export metadata is invalid.")
        export["content_hash"] = self._record_content_hash(record)
        try:
            validate_schema("training-record-v1", record)
        except ValidationError as error:
            raise SessionLogIntegrityError(
                "Training record cannot be safely projected."
            ) from error
        return record

    def _validate_training_record(self, record: Mapping[str, object]) -> None:
        try:
            validate_schema("training-record-v1", record)
        except ValidationError as error:
            raise SessionIntegrityError("Training record is structurally invalid.") from error
        export = record.get("export")
        if not isinstance(export, Mapping) or not isinstance(
            export.get("content_hash"), str
        ):
            raise SessionIntegrityError("Training record export metadata is invalid.")
        if export["content_hash"] != self._record_content_hash(record):
            raise SessionIntegrityError("Training record content hash is invalid.")
        source_state_hash = export.get("source_state_hash")
        if not isinstance(source_state_hash, str):
            raise SessionIntegrityError("Training record export metadata is invalid.")
        if source_state_hash != self._source_state_hash(record):
            raise SessionIntegrityError("Training record source state hash is invalid.")

    def _record_content_hash(self, record: Mapping[str, object]) -> str:
        payload = copy.deepcopy(dict(record))
        export = payload.get("export")
        if not isinstance(export, dict):
            raise SessionIntegrityError("Training record export metadata is invalid.")
        export.pop("generated_at", None)
        export.pop("content_hash", None)
        return self._canonical_hash(payload)

    def _assert_export_bounds(self, detail: Mapping[str, object]) -> None:
        integrity = detail.get("integrity")
        if not isinstance(integrity, Mapping):
            raise SessionIntegrityError("Training source state is invalid.")
        missing_artifacts = integrity.get("missing_artifacts")
        if not isinstance(missing_artifacts, list):
            raise SessionIntegrityError("Training source state is invalid.")
        if missing_artifacts:
            raise SessionLogIntegrityError(
                "Training source state is incomplete."
            )
        for field in (
            "behavior_events",
            "code_snapshots",
            "teacher_reviews",
        ):
            value = detail.get(field)
            if not isinstance(value, list):
                raise SessionIntegrityError("Training source state is invalid.")
            if len(value) > _MAX_TRAINING_RECORD_ITEMS:
                raise SessionLogIntegrityError(_EXPORT_LIMIT_ERROR)
        if self._public_fields_byte_size(detail) > _MAX_TRAINING_RECORD_BYTES:
            raise SessionLogIntegrityError(_EXPORT_LIMIT_ERROR)

    @staticmethod
    def _public_fields_byte_size(detail: Mapping[str, object]) -> int:
        """Measure public fields without serializing a whole nested value."""

        total = 1  # opening object brace
        for index, field in enumerate(_TRAINING_RECORD_PUBLIC_FIELDS):
            try:
                value = detail[field]
            except KeyError as error:
                raise SessionIntegrityError(
                    "Training source state is invalid."
                ) from error
            if index:
                total += 1
            total = SessionLogService._json_string_size(field, total)
            if total > _MAX_TRAINING_RECORD_BYTES:
                return total
            total += 1  # key/value colon
            total = SessionLogService._json_value_size(value, total)
            if total > _MAX_TRAINING_RECORD_BYTES:
                return total
        return total + 1  # closing object brace

    @staticmethod
    def _json_value_size(value: object, total: int) -> int:
        """Count JSON bytes with explicit container frames and early exits."""

        stack: list[tuple[str, object, bool | None]] = [
            ("value", value, None)
        ]
        while stack:
            kind, current, first = stack.pop()
            if kind == "value":
                if isinstance(current, str):
                    total = SessionLogService._json_string_size(current, total)
                elif current is None:
                    total += 4
                elif isinstance(current, bool):
                    total += 4 if current else 5
                elif isinstance(current, int):
                    total += len(str(current))
                elif isinstance(current, float):
                    if not math.isfinite(current):
                        raise ValueError(
                            "Out of range float values are not JSON compliant"
                        )
                    total += len(json.dumps(
                        current,
                        allow_nan=False,
                        separators=(",", ":"),
                    ).encode("utf-8"))
                elif isinstance(current, Mapping):
                    total += 1  # opening object brace
                    stack.append(("mapping", iter(current.items()), True))
                elif isinstance(current, (list, tuple)):
                    total += 1  # opening array bracket
                    stack.append(("sequence", iter(current), True))
                else:
                    raise TypeError(
                        f"Object of type {type(current).__name__} is not JSON serializable"
                    )
            elif kind == "mapping":
                iterator = current
                if not hasattr(iterator, "__next__"):
                    raise TypeError("JSON object iterator is invalid")
                try:
                    key, child = next(iterator)  # type: ignore[misc]
                except StopIteration:
                    total += 1  # closing object brace
                else:
                    if not isinstance(key, str):
                        raise TypeError("keys must be str")
                    if first is False:
                        total += 1
                    total = SessionLogService._json_string_size(key, total)
                    total += 1  # key/value colon
                    stack.append(("mapping", iterator, False))
                    stack.append(("value", child, None))
            elif kind == "sequence":
                iterator = current
                if not hasattr(iterator, "__next__"):
                    raise TypeError("JSON array iterator is invalid")
                try:
                    child = next(iterator)  # type: ignore[misc]
                except StopIteration:
                    total += 1  # closing array bracket
                else:
                    if first is False:
                        total += 1
                    stack.append(("sequence", iterator, False))
                    stack.append(("value", child, None))
            else:
                raise AssertionError("Unknown JSON size frame.")
            if total > _MAX_TRAINING_RECORD_BYTES:
                return total
        return total

    @staticmethod
    def _json_string_size(value: str, total: int) -> int:
        """Count one bounded, NFC-normalized JSON string without escaping it."""

        if len(value) > _MAX_TRAINING_RECORD_STRING_CHARACTERS:
            return _MAX_TRAINING_RECORD_BYTES + 1
        value = unicodedata.normalize("NFC", value)
        if len(value) > _MAX_TRAINING_RECORD_STRING_CHARACTERS:
            return _MAX_TRAINING_RECORD_BYTES + 1

        remaining = _MAX_TRAINING_RECORD_BYTES - total
        if len(value) + 2 > remaining:
            return _MAX_TRAINING_RECORD_BYTES + 1
        total += 2  # quotes
        for character in value:
            codepoint = ord(character)
            if character in {'"', "\\"} or character in {
                "\b", "\f", "\n", "\r", "\t",
            }:
                total += 2
            elif codepoint < 0x20:
                total += 6
            elif 0xD800 <= codepoint <= 0xDFFF:
                raise UnicodeEncodeError(
                    "utf-8",
                    value,
                    0,
                    1,
                    "surrogates not allowed",
                )
            elif codepoint <= 0x7F:
                total += 1
            elif codepoint <= 0x7FF:
                total += 2
            elif codepoint <= 0xFFFF:
                total += 3
            else:
                total += 4
            if total > _MAX_TRAINING_RECORD_BYTES:
                return total
        return total
