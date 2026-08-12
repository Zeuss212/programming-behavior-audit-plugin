import asyncio
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import UUID

from jsonschema import ValidationError
from jupyter_core.utils import ensure_async
from jupyter_server.base.handlers import APIHandler
from jupyter_server.utils import url_path_join
import tornado

from .api_base import ApiRequestError, JsonAPIHandler
from .analysis_job_store import (
    AnalysisJobConflictError,
    AnalysisJobIntegrityError,
    AnalysisJobNotFoundError,
    AnalysisJobStateError,
)
from .analysis_worker import (
    AnalysisQueueFullError,
    AnalysisWorkerStateError,
    compute_input_snapshot_hash,
)
from .assessment_assistant import (
    AssessmentAssistantOutputError,
    generate_assessment_tests,
    recommend_knowledge_points,
)
from .behavior_log_store import append_segments, resolve_log_root, validate_session_id
from .dimension_profile_store import (
    DimensionProfileStore,
    InvalidProfileIdError,
    ProfileConflictError,
    ProfileIntegrityError,
)
from .dimension_template_store import list_templates
from .llm_labeler import ai_config_status, save_ai_config
from .llm_transport import (
    AiConfigValidationError,
    AiNotConfiguredError,
    LlmTransportError,
)
from .platform_client import PlatformClientError, PlatformSyncClient
from .platform_config import PlatformConfig
from .platform_context_store import PlatformContextStore, RegisteredPlatformContext
from .profile_validator import ProfileValidationError
from .review_store import (
    ReviewConflictError,
    ReviewIntegrityError,
    ReviewStore,
)
from .schema_registry import validate_schema
from .session_log_service import (
    SessionLogArtifactNotReadyError,
    SessionLogArtifactTooLargeError,
    SessionLogIntegrityError,
    SessionLogService,
)
from .training_record_automation import TrainingRecordRefresher
from .log_folder_opener import (
    LogFolderOpenError,
    LogFolderOpenUnsupportedError,
    LogFolderOpener,
)
from .session_store import (
    InvalidSessionIdError,
    SegmentConflictError,
    SequenceGapError,
    SessionIntegrityError,
    SessionNotFoundError,
    SessionStateError,
)

MAX_SEGMENTS_PER_BATCH = 100
MAX_SEGMENT_BYTES = 250_000
MAX_REQUEST_BYTES = 2_000_000
MAX_RUN_OUTPUT_CHARS = 20_000
MAX_ANALYSIS_CHARS = 80_000
PYTHON_RUN_TIMEOUT_SEC = 30
ALLOWED_SEGMENT_TYPES = {
    "code_writing",
    "code_deletion",
    "code_paste",
    "code_execution",
    "idle",
    "page_away",
    "cell_switch",
    "notebook_switch",
    "kernel_restart",
}
_PROFILE_STORE_CACHE: dict[Path, DimensionProfileStore] = {}
_PROFILE_STORE_CACHE_LOCK = threading.Lock()
_VALIDATION_REASONS = {
    "additionalProperties": "unknown_field",
    "const": "unsupported_value",
    "contains": "required_item_missing",
    "enum": "unsupported_value",
    "maxItems": "too_many_items",
    "maxLength": "too_long",
    "minItems": "too_few_items",
    "minimum": "below_minimum",
    "minLength": "too_short",
    "pattern": "invalid_format",
    "required": "required",
    "type": "invalid_type",
    "uniqueItems": "duplicate_items",
}


def _platform_config() -> PlatformConfig:
    """Load platform settings at request time and fail closed on bad student config."""

    try:
        return PlatformConfig.from_env()
    except RuntimeError as error:
        raise ApiRequestError(
            503,
            "platform_configuration_invalid",
            "课堂学生模式尚未正确配置。",
        ) from error


def _require_platform_capability(capability: str) -> PlatformConfig:
    """Prevent a browser or direct request from enabling teacher-only actions."""

    config = _platform_config()
    if not config.capabilities().get(capability, False):
        raise ApiRequestError(
            403,
            "student_capability_forbidden",
            "课堂学生模式不允许此操作。",
        )
    return config


class HelloRouteHandler(APIHandler):
    # The following decorator should be present on all verb methods (head, get, post,
    # patch, put, delete, options) to ensure only authorized user can request the
    # Jupyter server
    @tornado.web.authenticated
    def get(self):
        self.finish(json.dumps({
            "data": (
                "Hello, world!"
                " This is the '/myextension/hello' endpoint."
                " Try visiting me in your browser!"
            ),
        }))


class BehaviorEventsRouteHandler(APIHandler):
    """Persist aggregated behavior timeline segments to local text logs."""

    @tornado.web.authenticated
    def post(self):
        """Validate and append a batch of behavior segments."""
        if len(self.request.body or b"") > MAX_REQUEST_BYTES:
            self._finish_error(413, "Request body is too large.")
            return

        try:
            body = self.get_json_body()
        except Exception:
            self._finish_error(400, "Request body must be valid JSON.")
            return

        if not isinstance(body, dict):
            self._finish_error(400, "Request body must be a JSON object.")
            return

        try:
            session_id = validate_session_id(body.get("session_id"))
            segments = self._validate_segments(body.get("segments"))
        except ValueError as exc:
            self._finish_error(400, str(exc))
            return

        try:
            accepted_count, log_file = append_segments(session_id, segments)
        except ValueError:
            self._finish_error(400, "Invalid segment data.")
            return
        except OSError:
            self._finish_error(500, "Failed to write behavior segments.")
            return

        self.finish({
            "status": "success",
            "accepted_count": accepted_count,
            "log_file": log_file,
            "llm_labeling": "disabled",
            "deprecation": (
                "Use /sessions/start, /segments and /finalize."
            ),
        })

    def _validate_segments(self, value):
        if not isinstance(value, list):
            raise ValueError("segments must be an array.")

        if not value:
            raise ValueError("segments must contain at least one segment.")

        if len(value) > MAX_SEGMENTS_PER_BATCH:
            raise ValueError(
                f"segments must contain no more than {MAX_SEGMENTS_PER_BATCH} items."
            )

        segments = []
        total_size = 0
        for segment in value:
            if not isinstance(segment, dict):
                raise ValueError("Each segment must be a JSON object.")

            segment_type = segment.get("segment_type")
            if segment_type not in ALLOWED_SEGMENT_TYPES:
                raise ValueError("Each segment must include a valid segment_type.")

            for field in ["started_at", "ended_at"]:
                if not isinstance(segment.get(field), str) or not segment[field].strip():
                    raise ValueError(f"Each segment must include a non-empty {field}.")

            duration_ms = segment.get("duration_ms")
            if not isinstance(duration_ms, int) or duration_ms < 0:
                raise ValueError("Each segment must include a non-negative duration_ms.")

            segment_size = len(
                json.dumps(segment, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            if segment_size > MAX_SEGMENT_BYTES:
                raise ValueError("A segment is too large.")

            total_size += segment_size
            if total_size > MAX_REQUEST_BYTES:
                raise ValueError("The segment batch is too large.")

            segments.append(segment)

        return segments

    def _finish_error(self, status_code, message):
        self.set_status(status_code)
        self.finish({
            "status": "error",
            "message": message,
        })


class RunPythonFileRouteHandler(APIHandler):
    """Run a Jupyter contents-managed Python file with this server's Python."""

    @tornado.web.authenticated
    async def post(self):
        try:
            body = self.get_json_body()
        except Exception:
            self._finish_error(400, "Request body must be valid JSON.")
            return

        if not isinstance(body, dict):
            self._finish_error(400, "Request body must be a JSON object.")
            return

        try:
            path = _validate_python_path(body.get("path"))
            os_path = await _contents_os_path(self, path)
        except ValueError as exc:
            self._finish_error(400, str(exc))
            return
        except OSError:
            self._finish_error(404, "Python file was not found.")
            return

        started = time.monotonic()
        timed_out = False
        try:
            completed = subprocess.run(
                [sys.executable, str(os_path)],
                cwd=str(os_path.parent),
                capture_output=True,
                text=True,
                timeout=PYTHON_RUN_TIMEOUT_SEC,
            )
            exit_code = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = -1
            stdout = _decode_process_output(exc.stdout)
            stderr = _decode_process_output(exc.stderr)

        duration_ms = int((time.monotonic() - started) * 1000)
        self.finish({
            "status": "success",
            "path": path,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "stdout": _truncate_output(stdout),
            "stderr": _truncate_output(stderr),
            "timed_out": timed_out,
        })

    def _finish_error(self, status_code, message):
        self.set_status(status_code)
        self.finish({
            "status": "error",
            "message": message,
            "path": "",
            "exit_code": -1,
            "duration_ms": 0,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
        })


class LatestAnalysisRouteHandler(APIHandler):
    """Return the newest generated behavior analysis file."""

    @tornado.web.authenticated
    def get(self):
        try:
            path = _latest_analysis_path()
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self.finish({
                "status": "empty",
                "log_groups": [],
                "content": "",
                "truncated": False,
            })
            return
        except OSError:
            self.set_status(500)
            self.finish({
                "status": "error",
                "message": "Failed to read latest analysis result.",
            })
            return

        root = resolve_log_root()
        raw_path = _raw_event_path(path)
        source_path = _source_log_path(path)
        self.finish({
            "status": "success",
            "path": str(path.relative_to(root)).replace("\\", "/"),
            "contents_path": _contents_path(self, path),
            "source_path": str(source_path.relative_to(root)).replace("\\", "/")
            if source_path.exists()
            else None,
            "source_contents_path": _contents_path(self, source_path)
            if source_path.exists()
            else None,
            "raw_path": str(raw_path.relative_to(root)).replace("\\", "/")
            if raw_path.exists()
            else None,
            "raw_contents_path": _contents_path(self, raw_path)
            if raw_path.exists()
            else None,
            "log_groups": _related_log_groups(self, path),
            "content": _truncate_analysis(content),
            "truncated": len(content) > MAX_ANALYSIS_CHARS,
        })


class AiConfigRouteHandler(JsonAPIHandler):
    """Read and update Ark AI config for the current server process."""

    @tornado.web.authenticated
    def get(self):
        try:
            _require_platform_capability("canConfigureAi")
            self.finish_json(ai_config_status())
        except ApiRequestError as error:
            self.finish_error(
                error.status,
                error.code,
                error.message,
                details=error.details,
            )

    @tornado.web.authenticated
    def post(self):
        try:
            _require_platform_capability("canConfigureAi")
            body = self.read_json_object()
        except ApiRequestError as error:
            self.finish_error(
                error.status,
                error.code,
                error.message,
                details=error.details,
            )
            return
        try:
            save_ai_config(body)
        except AiConfigValidationError as error:
            self.finish_error(
                400,
                "ai_config_validation_failed",
                "AI 配置格式不正确。",
                details={
                    "field": error.field,
                    "reason": error.reason,
                },
            )
            return
        except ValueError:
            self.finish_error(
                400,
                "ai_config_validation_failed",
                "AI 配置格式不正确。",
                details={
                    "field": "$",
                    "reason": "invalid_config",
                },
            )
            return
        except OSError:
            self.finish_error(
                500,
                "ai_config_save_failed",
                "AI 配置保存失败。",
                retryable=True,
            )
            return
        self.finish_json(ai_config_status())


class PlatformRegistrationRouteHandler(JsonAPIHandler):
    """Exchange a one-time browser ticket without exposing plugin credentials."""

    @tornado.web.authenticated
    def post(self):
        try:
            config = PlatformConfig.from_env()
            if not config.student_mode or config.sync_base_url is None:
                self.finish_error(
                    404,
                    "platform_registration_disabled",
                    "当前运行环境未启用课堂学生模式。",
                )
                return
            ticket, plugin_instance_id = self._registration_input()
            context = PlatformSyncClient(config.sync_base_url).register(
                ticket,
                plugin_instance_id=plugin_instance_id,
            )
            PlatformContextStore(config.log_root).save_registered_context(context)
            self.finish_json(self._public_context(context), status=201)
        except ApiRequestError as error:
            self.finish_error(
                error.status,
                error.code,
                error.message,
                details=error.details,
            )
        except PlatformClientError as error:
            self._finish_platform_error(error)
        except RuntimeError:
            self.finish_error(
                503,
                "platform_configuration_invalid",
                "课堂学生模式尚未正确配置。",
            )
        except OSError:
            self.finish_error(
                503,
                "platform_context_unavailable",
                "课堂会话暂时无法保存，请稍后重试。",
                retryable=True,
            )
        except ValueError:
            self.finish_error(
                502,
                "platform_registration_invalid_response",
                "课堂服务返回的数据无法使用。",
            )
        except Exception:
            self._finish_internal_error()

    def _registration_input(self) -> tuple[str, str]:
        body = self.read_json_object(max_bytes=8_192)
        if set(body) != {"schema_version", "ticket", "plugin_instance_id"}:
            raise ApiRequestError(
                422,
                "platform_registration_validation_failed",
                "课堂启动信息未通过校验。",
            )
        schema_version = body["schema_version"]
        ticket = body["ticket"]
        plugin_instance_id = body["plugin_instance_id"]
        if (
            not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
            or schema_version != 1
            or not isinstance(ticket, str)
            or not 1 <= len(ticket.strip()) <= 1_024
            or not isinstance(plugin_instance_id, str)
            or not 1 <= len(plugin_instance_id.strip()) <= 200
        ):
            raise ApiRequestError(
                422,
                "platform_registration_validation_failed",
                "课堂启动信息未通过校验。",
            )
        return ticket.strip(), plugin_instance_id.strip()

    def _finish_platform_error(self, error: PlatformClientError) -> None:
        mappings = {
            "platform_registration_unauthorized": (401, False),
            "platform_registration_conflict": (409, False),
            "platform_registration_unavailable": (503, True),
            "platform_registration_failed": (502, True),
            "platform_registration_invalid_response": (502, False),
            "platform_registration_invalid": (422, False),
        }
        status, retryable = mappings.get(error.args[0], (502, True))
        self.finish_error(
            status,
            error.args[0],
            "课堂会话注册未能完成。",
            retryable=retryable,
        )

    @staticmethod
    def _public_context(context: RegisteredPlatformContext) -> dict[str, object]:
        return {
            "assignment_id": context.assignment_id,
            "plan_id": context.plan_id,
            "plan_version": context.plan_version,
            "session_id": context.session_id,
            "profile": context.profile,
            "scheduled_end_at": context.scheduled_end_at,
            "evidence_cutoff_at": context.evidence_cutoff_at,
            "last_sync_at": context.last_sync_at,
        }


class PlatformContextRouteHandler(JsonAPIHandler):
    """Serve only server-authoritative local or classroom-student UI state."""

    @tornado.web.authenticated
    def get(self):
        try:
            config = _platform_config()
            context = self._registered_context(config)
            self._finish_context(config, context)
        except ApiRequestError as error:
            self.finish_error(
                error.status,
                error.code,
                error.message,
                details=error.details,
            )
        except OSError:
            self.finish_error(
                503,
                "platform_context_unavailable",
                "课堂会话暂时无法读取，请稍后重试。",
                retryable=True,
            )
        except ValueError:
            self.finish_error(
                503,
                "platform_context_invalid",
                "课堂会话数据无效，请重新进入课堂。",
            )
        except Exception:
            self._finish_internal_error()

    @tornado.web.authenticated
    def post(self):
        try:
            config = _platform_config()
            if not config.student_mode or config.sync_base_url is None:
                raise ApiRequestError(
                    404,
                    "platform_context_refresh_disabled",
                    "当前运行环境未启用课堂学生模式。",
                )
            context = self._registered_context(config)
            refreshed = PlatformSyncClient(config.sync_base_url).refresh(context)
            PlatformContextStore(config.log_root).save_registered_context(refreshed)
            self._finish_context(config, refreshed)
        except ApiRequestError as error:
            self.finish_error(
                error.status,
                error.code,
                error.message,
                details=error.details,
            )
        except PlatformClientError as error:
            self._finish_platform_error(error)
        except OSError:
            self.finish_error(
                503,
                "platform_context_unavailable",
                "课堂会话暂时无法保存，请稍后重试。",
                retryable=True,
            )
        except ValueError:
            self.finish_error(
                502,
                "platform_context_invalid_response",
                "课堂服务返回的数据无法使用。",
            )
        except Exception:
            self._finish_internal_error()

    def _registered_context(self, config: PlatformConfig) -> RegisteredPlatformContext | None:
        if not config.student_mode:
            return None
        context = PlatformContextStore(config.log_root).read_registered_context()
        if context is None:
            raise ApiRequestError(
                409,
                "platform_context_not_registered",
                "尚未注册课堂会话，请从课堂平台重新进入。",
            )
        return context

    def _finish_context(
        self,
        config: PlatformConfig,
        context: RegisteredPlatformContext | None,
    ) -> None:
        payload = {
            "mode": config.mode,
            "capabilities": config.capabilities(),
            "classroom_session": (
                PlatformRegistrationRouteHandler._public_context(context)
                if context is not None
                else None
            ),
        }
        validate_schema(
            "platform-context-response-v1",
            {**payload, "schema_version": 1, "request_id": self.request_id()},
        )
        self.finish_json(payload)

    def _finish_platform_error(self, error: PlatformClientError) -> None:
        mappings = {
            "platform_context_refresh_unauthorized": (401, False),
            "platform_context_refresh_conflict": (409, False),
            "platform_context_refresh_unavailable": (503, True),
            "platform_context_refresh_failed": (502, True),
            "platform_context_refresh_invalid_response": (502, False),
            "platform_context_refresh_invalid": (422, False),
        }
        status, retryable = mappings.get(error.args[0], (502, True))
        self.finish_error(
            status,
            error.args[0],
            "课堂会话刷新未能完成。",
            retryable=retryable,
        )

    def _finish_internal_error(self) -> None:
        self.finish_error(500, "internal_error", "服务器暂时无法处理请求。")


def _profile_store_at(root: Path) -> DimensionProfileStore:
    root = Path(root).expanduser().resolve()
    with _PROFILE_STORE_CACHE_LOCK:
        store = _PROFILE_STORE_CACHE.get(root)
        if store is None:
            store = DimensionProfileStore(root)
            _PROFILE_STORE_CACHE[root] = store
        return store


def _profile_store() -> DimensionProfileStore:
    return _profile_store_at(resolve_log_root())


def _canonical_profile_id(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidProfileIdError("profile_id must be a canonical UUID.")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as error:
        raise InvalidProfileIdError(
            "profile_id must be a canonical UUID."
        ) from error
    if str(parsed) != value:
        raise InvalidProfileIdError("profile_id must be a canonical UUID.")
    return value


def _positive_version(value: str) -> int:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[1-9][0-9]{0,8}", value) is None
    ):
        raise ApiRequestError(
            400,
            "invalid_version",
            "版本号必须是正整数。",
        )
    return int(value)


def _safe_validation_field(error: ValidationError) -> str:
    field = ""
    for part in error.absolute_path:
        if isinstance(part, int):
            field += f"[{part}]"
        elif isinstance(part, str) and re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*",
            part,
        ):
            field += ("." if field else "") + part
        else:
            return "$"
    return field or "$"


def _validation_details(error: ValidationError) -> dict[str, str]:
    return {
        "field": _safe_validation_field(error),
        "reason": _VALIDATION_REASONS.get(error.validator, "invalid_value"),
    }


def _semantic_validation_details(
    error: ProfileValidationError,
) -> dict[str, str]:
    field = error.field
    if not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*",
        field,
    ):
        field = "$"
    return {
        "field": field,
        "reason": error.code,
    }


def _validate_draft_update(
    value: dict[str, object],
) -> tuple[int, dict[str, object]]:
    if set(value) != {"revision", "draft"}:
        raise ApiRequestError(
            422,
            "profile_validation_failed",
            "方案内容未通过校验。",
            details={"field": "$", "reason": "unknown_or_missing_field"},
        )
    revision = value["revision"]
    if (
        not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 1
    ):
        raise ApiRequestError(
            422,
            "profile_validation_failed",
            "方案内容未通过校验。",
            details={"field": "revision", "reason": "invalid_positive_integer"},
        )
    draft = value["draft"]
    if not isinstance(draft, dict):
        raise ApiRequestError(
            422,
            "profile_validation_failed",
            "方案内容未通过校验。",
            details={"field": "draft", "reason": "invalid_type"},
        )
    return revision, draft


class ProfileAPIHandler(JsonAPIHandler):
    """Safe exception mapping shared by the Pilot profile handlers."""

    def _finish_request_error(self, error: ApiRequestError) -> None:
        self.finish_error(
            error.status,
            error.code,
            error.message,
            details=error.details,
        )

    def _finish_validation_error(
        self,
        error: ValidationError | ProfileValidationError,
        *,
        field_prefix: str | None = None,
    ) -> None:
        details = (
            _validation_details(error)
            if isinstance(error, ValidationError)
            else _semantic_validation_details(error)
        )
        if field_prefix is not None:
            details["field"] = (
                field_prefix
                if details["field"] == "$"
                else f"{field_prefix}.{details['field']}"
            )
        self.finish_error(
            422,
            "profile_validation_failed",
            "方案内容未通过校验。",
            details=details,
        )

    def _finish_internal_error(self) -> None:
        self.finish_error(
            500,
            "internal_error",
            "服务器暂时无法处理请求。",
        )


class DimensionTemplatesRouteHandler(ProfileAPIHandler):
    @tornado.web.authenticated
    def get(self):
        try:
            templates = [
                {
                    key: value
                    for key, value in template.items()
                    if key != "schema_version"
                }
                for template in list_templates()
            ]
            self.finish_json({"templates": templates})
        except Exception:
            self._finish_internal_error()


class DimensionProfilesRouteHandler(ProfileAPIHandler):
    @tornado.web.authenticated
    def get(self):
        try:
            raw_problem_id = self.get_query_argument(
                "problem_id",
                default=None,
                strip=False,
            )
            problem_id = (
                raw_problem_id.strip()
                if raw_problem_id is not None
                else None
            )
            if problem_id is not None and not 1 <= len(problem_id) <= 200:
                raise ApiRequestError(
                    400,
                    "invalid_problem_id",
                    "题目标识长度必须在 1 到 200 个字符之间。",
                )
            self.finish_json(
                {"profiles": _profile_store().list_profiles(problem_id)}
            )
        except ApiRequestError as error:
            self._finish_request_error(error)
        except Exception:
            self._finish_internal_error()

    @tornado.web.authenticated
    def post(self):
        try:
            _require_platform_capability("canAuthorPlan")
            payload = self.read_json_object()
            created = _profile_store().create_draft(payload)
            self.finish_json(created, status=201)
        except ApiRequestError as error:
            self._finish_request_error(error)
        except (ValidationError, ProfileValidationError) as error:
            self._finish_validation_error(error)
        except Exception:
            self._finish_internal_error()


class DimensionProfileDraftRouteHandler(ProfileAPIHandler):
    @tornado.web.authenticated
    def put(self, profile_id):
        try:
            _require_platform_capability("canAuthorPlan")
            request_body = self.read_json_object()
            canonical_id = _canonical_profile_id(profile_id)
            revision, draft = _validate_draft_update(request_body)
            updated = _profile_store().update_draft(
                canonical_id,
                draft,
                expected_revision=revision,
            )
            self.finish_json(updated)
        except ApiRequestError as error:
            self._finish_request_error(error)
        except InvalidProfileIdError:
            self.finish_error(
                400,
                "invalid_profile_id",
                "方案标识不是规范 UUID。",
            )
        except (ValidationError, ProfileValidationError) as error:
            self._finish_validation_error(error, field_prefix="draft")
        except KeyError:
            self.finish_error(
                404,
                "profile_not_found",
                "未找到指定方案。",
            )
        except ProfileConflictError:
            self.finish_error(
                409,
                "draft_revision_conflict",
                "草稿已被其他请求更新。",
            )
        except Exception:
            self._finish_internal_error()


class DimensionProfilePublishRouteHandler(ProfileAPIHandler):
    @tornado.web.authenticated
    def post(self, profile_id):
        try:
            _require_platform_capability("canPublishPlan")
            if self.request.body:
                publish_request = self.read_json_object()
                if publish_request:
                    raise ApiRequestError(
                        422,
                        "profile_validation_failed",
                        "方案内容未通过校验。",
                        details={"field": "$", "reason": "unknown_field"},
                    )
            canonical_id = _canonical_profile_id(profile_id)
            published = _profile_store().publish(canonical_id)
            self.finish_json(published)
        except ApiRequestError as error:
            self._finish_request_error(error)
        except InvalidProfileIdError:
            self.finish_error(
                400,
                "invalid_profile_id",
                "方案标识不是规范 UUID。",
            )
        except KeyError:
            self.finish_error(
                404,
                "profile_not_found",
                "未找到指定方案。",
            )
        except (ValidationError, ProfileValidationError) as error:
            self._finish_validation_error(error)
        except ProfileConflictError:
            self.finish_error(
                409,
                "profile_publish_conflict",
                "方案版本发布冲突。",
            )
        except Exception:
            self._finish_internal_error()


class DimensionProfileVersionRouteHandler(ProfileAPIHandler):
    @tornado.web.authenticated
    def get(self, profile_id, version):
        try:
            canonical_id = _canonical_profile_id(profile_id)
            parsed_version = _positive_version(version)
            published = _profile_store().get_version(
                canonical_id,
                parsed_version,
            )
            self.finish_json(published)
        except ApiRequestError as error:
            self._finish_request_error(error)
        except InvalidProfileIdError:
            self.finish_error(
                400,
                "invalid_profile_id",
                "方案标识不是规范 UUID。",
            )
        except KeyError:
            self.finish_error(
                404,
                "profile_version_not_found",
                "未找到指定方案版本。",
            )
        except Exception:
            self._finish_internal_error()


def _assessment_assist_transport_code(
    error: LlmTransportError,
    fallback: str,
) -> str:
    if error.error_code == "provider_timeout":
        return "ai_provider_timeout"
    if error.error_code == "provider_network_error":
        return "ai_provider_network_error"
    if error.error_code == "provider_response_truncated":
        return "ai_response_truncated"
    if error.error_code == "provider_response_invalid":
        return "ai_response_invalid"
    if error.error_code != "provider_http_error":
        return fallback
    if error.http_status in {401, 403}:
        return "ai_provider_auth_failed"
    if error.http_status == 429:
        return "ai_provider_rate_limited"
    if error.http_status is not None and 400 <= error.http_status < 500:
        return "ai_provider_request_rejected"
    if error.http_status is not None and 500 <= error.http_status < 600:
        return "ai_provider_unavailable"
    return fallback


class AssessmentAssistRouteHandler(JsonAPIHandler):
    """Shared safe validation and error mapping for stateless AI assistance."""

    request_schema = ""
    failure_code = ""
    failure_message = ""

    def _request_body(self) -> dict[str, object]:
        body = self.read_json_object()
        try:
            validate_schema(self.request_schema, body)
        except ValidationError as error:
            raise ApiRequestError(
                422,
                "assessment_assist_validation_failed",
                "辅助配置请求未通过校验。",
                details=_validation_details(error),
            ) from error
        return body

    def _finish_assist_error(self, error: Exception) -> None:
        if isinstance(error, ApiRequestError):
            self.finish_error(
                error.status,
                error.code,
                error.message,
                details=error.details,
            )
            return
        if isinstance(error, AiNotConfiguredError):
            self.finish_error(
                409,
                "ai_not_configured",
                "尚未配置 AI 服务，请手工填写或先完成 AI 配置。",
            )
            return
        if isinstance(error, AssessmentAssistantOutputError):
            self.finish_error(
                502,
                "invalid_ai_output",
                "AI 返回内容无法用于当前方案，请重试或手工填写。",
                retryable=True,
            )
            return
        if isinstance(error, LlmTransportError):
            self.finish_error(
                502,
                _assessment_assist_transport_code(
                    error,
                    self.failure_code,
                ),
                self.failure_message,
                retryable=True,
            )
            return
        if isinstance(error, (ValidationError, ValueError)):
            self.finish_error(
                422,
                "assessment_assist_validation_failed",
                "辅助配置请求未通过校验。",
                details={"field": "$", "reason": "invalid_value"},
            )
            return
        self.finish_error(
            500,
            self.failure_code,
            self.failure_message,
            retryable=True,
        )


class AssessmentKnowledgeAssistRouteHandler(AssessmentAssistRouteHandler):
    request_schema = "assessment-knowledge-request-v1"
    failure_code = "knowledge_recommendation_failed"
    failure_message = "暂时无法生成知识点建议，请重试或手工填写。"

    @tornado.web.authenticated
    async def post(self):
        try:
            _require_platform_capability("canUseAssessmentAssist")
            body = self._request_body()
            context = body["problem_context"]
            result = await asyncio.to_thread(
                recommend_knowledge_points,
                context["statement"],
                submission_contract=context["submission_contract"],
                teacher_focus=body["teacher_focus"],
            )
            self.finish_json(result)
        except Exception as error:
            self._finish_assist_error(error)


class AssessmentTestsAssistRouteHandler(AssessmentAssistRouteHandler):
    request_schema = "assessment-tests-request-v1"
    failure_code = "test_generation_failed"
    failure_message = "暂时无法生成测试建议，请重试或手工填写。"

    @tornado.web.authenticated
    async def post(self):
        try:
            _require_platform_capability("canUseAssessmentAssist")
            body = self._request_body()
            context = body["problem_context"]
            result = await asyncio.to_thread(
                generate_assessment_tests,
                context["statement"],
                submission_contract=context["submission_contract"],
                knowledge_points=body["knowledge_points"],
            )
            self.finish_json(result)
        except Exception as error:
            self._finish_assist_error(error)


_SESSION_START_FIELDS = {
    "schema_version",
    "problem_id",
    "profile_id",
    "profile_version",
    "profile_content_hash",
}
_SESSION_STATE_FIELDS = {
    "session_id",
    "problem_id",
    "profile_id",
    "profile_version",
    "profile_content_hash",
    "status",
    "last_contiguous_sequence",
    "received_event_count",
    "analysis_job_id",
}
_JOB_PUBLIC_FIELDS = {
    "job_id",
    "session_id",
    "status",
    "active_attempt_id",
    "attempt_ids",
    "analysis_id",
    "error_code",
}
_RESULT_PUBLIC_FIELDS = {
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
_RESULT_ERROR_CODES = {
    "ai_not_configured",
    "ai_analysis_failed",
    "invalid_profile",
}
_REVIEW_FIELDS = {
    "revision",
    "decision_status",
    "evidence_status",
    "level_code",
    "evidence_event_ids",
    "reason_code",
    "comment",
}
_DIMENSION_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class PilotResourceNotFoundError(KeyError):
    """Raised for a genuinely absent attached Pilot resource."""


def _canonical_resource_uuid(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ApiRequestError(
            400,
            f"invalid_{field}",
            "资源标识不是规范 UUID。",
        )
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as error:
        raise ApiRequestError(
            400,
            f"invalid_{field}",
            "资源标识不是规范 UUID。",
        ) from error
    if str(parsed) != value:
        raise ApiRequestError(
            400,
            f"invalid_{field}",
            "资源标识不是规范 UUID。",
        )
    return value


def _closed_body(
    value: dict[str, object],
    fields: set[str],
    *,
    code: str,
) -> dict[str, object]:
    if set(value) != fields:
        raise ApiRequestError(
            422,
            code,
            "请求内容未通过校验。",
            details={
                "field": "$",
                "reason": "unknown_or_missing_field",
            },
        )
    return value


def _nonempty_text(value: object, *, field: str, code: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > 1_000
        or "\x00" in value
    ):
        raise ApiRequestError(
            422,
            code,
            "请求内容未通过校验。",
            details={"field": field, "reason": "invalid_nonempty_string"},
        )
    return value.strip()


def _session_projection(session: dict[str, object]) -> dict[str, object]:
    return {key: session.get(key) for key in _SESSION_STATE_FIELDS}


def _job_projection(job: dict[str, object]) -> dict[str, object]:
    return {key: job.get(key) for key in _JOB_PUBLIC_FIELDS}


class PilotAPIHandler(JsonAPIHandler):
    """Shared service lookup and safe errors for the Pilot runtime APIs."""

    def _services(self):
        worker = self.settings.get("myextension_analysis_worker")
        configured_job_store = self.settings.get(
            "myextension_analysis_job_store"
        )
        session_store = getattr(worker, "session_store", None)
        job_store = getattr(worker, "job_store", None)
        if (
            worker is None
            or session_store is None
            or job_store is None
            or configured_job_store is None
            or configured_job_store is not job_store
        ):
            raise ApiRequestError(
                503,
                "service_unavailable",
                "分析服务暂时不可用。",
            )
        return worker, session_store, job_store

    def _session_log_service(self) -> SessionLogService:
        _, session_store, job_store = self._services()
        root = Path(session_store.root)
        return SessionLogService(
            root=root,
            session_store=session_store,
            job_store=job_store,
            review_store=ReviewStore(root),
        )

    def _refresh_training_record(self, session_id: str) -> bool:
        try:
            return TrainingRecordRefresher(
                self._session_log_service(),
                logger=self.log,
            ).refresh(session_id)
        except Exception:
            try:
                self.log.warning("training_record_refresh_failed")
            except Exception:
                pass
            return False

    def _refresh_classroom_brief(self, session_id: str) -> bool:
        try:
            self._session_log_service().export_classroom_brief(session_id)
            return True
        except Exception:
            try:
                self.log.warning("classroom_brief_refresh_failed")
            except Exception:
                pass
            return False

    def _finish_request_error(self, error: ApiRequestError) -> None:
        self.finish_error(
            error.status,
            error.code,
            error.message,
            retryable=error.status in {429, 503},
            details=error.details,
        )

    def _validate_schema_body(
        self,
        schema_name: str,
        *,
        code: str,
    ) -> dict[str, object]:
        body = self.read_json_object()
        try:
            validate_schema(schema_name, body)
        except ValidationError as error:
            raise ApiRequestError(
                422,
                code,
                "请求内容未通过校验。",
                details=_validation_details(error),
            ) from error
        return body

    def _finish_not_found(self, code: str) -> None:
        self.finish_error(404, code, "未找到指定资源。")

    def _finish_conflict(self, code: str) -> None:
        self.finish_error(409, code, "请求与当前资源状态冲突。")

    def _finish_internal_error(self) -> None:
        self.finish_error(
            500,
            "internal_error",
            "服务器暂时无法处理请求。",
        )

    def _load_result(
        self,
        session_id: str,
    ) -> tuple[
        dict[str, object],
        dict[str, object],
        dict[str, object],
        dict[str, object],
    ]:
        _, session_store, job_store = self._services()
        session = session_store.read(session_id)
        job_value = session.get("analysis_job_id")
        if job_value is None:
            raise PilotResourceNotFoundError("analysis_job_id")
        job_id = _canonical_resource_uuid(job_value, field="job_id")
        job = job_store.get(job_id)
        if job.get("session_id") != session_id:
            raise SessionIntegrityError(
                "Attached job does not belong to the session."
            )
        if job.get("status") not in {"ready", "partial"}:
            raise AnalysisJobStateError(
                "Analysis result is not available."
            )
        result = job_store.load_public_result(
            job_id,
            session_store=session_store,
        )
        profile = session_store.read_profile(session_id)
        if (
            result.get("job_id") != job_id
            or result.get("session_id") != session_id
        ):
            raise AnalysisJobIntegrityError(
                "Result identity does not match its session job."
            )
        return session, job, result, profile

    @staticmethod
    def _effective_dimension(
        row: dict[str, object],
        *,
        profile_dimension: dict[str, object],
        review: dict[str, object] | None,
    ) -> dict[str, object]:
        effective = json.loads(json.dumps(row, ensure_ascii=False))
        if review is None:
            marker = effective.get("review")
            if marker != {"revision": 0, "status": "unreviewed"}:
                effective["review"] = {
                    "revision": 0,
                    "status": "unreviewed",
                }
            return effective

        decision = dict(effective["decision"])
        decision["status"] = review["decision_status"]
        decision["final_evidence_status"] = review["evidence_status"]
        decision["final_level_code"] = review["level_code"]
        decision["display_label"] = self_display_label(
            review["decision_status"],
            review["evidence_status"],
            review["level_code"],
            profile_dimension,
        )
        effective["decision"] = decision
        effective["review"] = {
            "revision": review["revision"],
            "status": "reviewed",
        }
        return effective

    def _effective_result(
        self,
        result: dict[str, object],
        profile: dict[str, object],
    ) -> dict[str, object]:
        root = Path(self._services()[1].root)
        reviews = ReviewStore(root)
        dimensions = {
            item["code"]: item
            for item in profile.get("dimensions", [])
            if isinstance(item, dict) and isinstance(item.get("code"), str)
        }
        projected = {
            key: json.loads(json.dumps(result[key], ensure_ascii=False))
            for key in _RESULT_PUBLIC_FIELDS
        }
        effective_rows = []
        for raw_row in result["dimension_results"]:
            code = raw_row["dimension_code"]
            history = reviews.list(str(result["analysis_id"]), code)
            effective_rows.append(
                self._effective_dimension(
                    raw_row,
                    profile_dimension=dimensions[code],
                    review=history[-1] if history else None,
                )
            )
        projected["dimension_results"] = effective_rows
        return projected


def self_display_label(
    decision_status: object,
    evidence_status: object,
    level_code: object,
    profile_dimension: dict[str, object],
) -> str:
    if decision_status == "needs_review":
        return "需要教师复核"
    if evidence_status == "observed":
        for level in profile_dimension.get("levels", []):
            if isinstance(level, dict) and level.get("code") == level_code:
                name = level.get("name", level.get("label"))
                if isinstance(name, str) and name:
                    return name
        raise SessionIntegrityError("Published level label is missing.")
    if evidence_status == "not_observed":
        return "未发现明显证据"
    if evidence_status == "insufficient_evidence":
        return "数据不足"
    if evidence_status == "not_computable":
        return "当前记录无法分析"
    return "需要教师复核"


class LogFolderOpenRouteHandler(PilotAPIHandler):
    @tornado.web.authenticated
    def post(self):
        try:
            try:
                body = self.read_json_object()
            except ApiRequestError as error:
                if error.code not in {"invalid_json", "invalid_json_object"}:
                    raise
                raise ApiRequestError(
                    422,
                    "log_folder_open_validation_failed",
                    "请求内容未通过校验。",
                ) from error
            if body:
                raise ApiRequestError(
                    422,
                    "log_folder_open_validation_failed",
                    "请求内容未通过校验。",
                )
            platform = LogFolderOpener(
                resolve_log_root()
            ).open_sessions_folder()
            result = {"opened": True, "platform": platform}
            validate_schema(
                "log-folder-open-response-v1",
                {**result, "schema_version": 1, "request_id": self.request_id()},
            )
            self.finish_json(result)
        except ApiRequestError as error:
            self._finish_request_error(error)
        except LogFolderOpenUnsupportedError:
            self.finish_error(
                409,
                "log_folder_open_unsupported",
                "当前环境不支持打开本机日志文件夹。",
            )
        except LogFolderOpenError:
            self.finish_error(
                500,
                "log_folder_open_failed",
                "日志文件夹暂时无法打开。",
            )
        except Exception:
            self.finish_error(
                500,
                "log_folder_open_failed",
                "日志文件夹暂时无法打开。",
            )


class SessionStartRouteHandler(PilotAPIHandler):
    @tornado.web.authenticated
    def post(self):
        try:
            body = self._validate_schema_body(
                "session-start-v1",
                code="session_validation_failed",
            )
            _, session_store, _ = self._services()
            profile_id = _canonical_resource_uuid(
                body["profile_id"],
                field="profile_id",
            )
            profile = _profile_store_at(session_store.root).get_version(
                profile_id,
                body["profile_version"],
            )
            if (
                profile.get("deployment_status") != "pilot"
                or profile.get("problem_id") != body["problem_id"]
                or profile.get("profile_id") != profile_id
                or profile.get("version") != body["profile_version"]
                or profile.get("content_hash")
                != body["profile_content_hash"]
            ):
                raise SessionStateError("Published profile selection mismatch.")
            session = session_store.start(
                problem_id=str(body["problem_id"]),
                profile=profile,
            )
            self.finish_json(
                {
                    key: session[key]
                    for key in (
                        "session_id",
                        "problem_id",
                        "profile_id",
                        "profile_version",
                        "profile_content_hash",
                        "signal_dictionary_version",
                        "signal_dictionary_hash",
                        "status",
                        "last_contiguous_sequence",
                    )
                },
                status=201,
            )
        except ApiRequestError as error:
            self._finish_request_error(error)
        except KeyError:
            self._finish_not_found("profile_version_not_found")
        except (SessionStateError, ProfileConflictError):
            self._finish_conflict("profile_mismatch")
        except (
            InvalidProfileIdError,
            ProfileIntegrityError,
            SessionIntegrityError,
            OSError,
        ):
            self._finish_internal_error()
        except Exception:
            self._finish_internal_error()


class SessionSegmentsRouteHandler(PilotAPIHandler):
    @tornado.web.authenticated
    def post(self, session_id):
        try:
            body = self._validate_schema_body(
                "segment-batch-v1",
                code="session_validation_failed",
            )
            canonical_id = _canonical_resource_uuid(
                session_id,
                field="session_id",
            )
            _, session_store, _ = self._services()
            receipt = session_store.append_batch(
                canonical_id,
                **{
                    key: value
                    for key, value in body.items()
                    if key != "schema_version"
                },
            )
            self.finish_json(receipt, status=202)
        except ApiRequestError as error:
            self._finish_request_error(error)
        except (InvalidSessionIdError, ValueError):
            self.finish_error(
                422,
                "session_validation_failed",
                "请求内容未通过校验。",
                details={"field": "$", "reason": "invalid_value"},
            )
        except SessionNotFoundError:
            self._finish_not_found("session_not_found")
        except SegmentConflictError:
            self._finish_conflict("segment_conflict")
        except SequenceGapError as error:
            details = {
                "field": "first_sequence",
                "reason": "missing_ranges:"
                + ",".join(
                    f"{start}-{end}"
                    for start, end in error.missing_ranges
                ),
            }
            self.finish_error(
                409,
                "sequence_gap",
                "行为序列存在缺口。",
                details=details,
            )
        except SessionStateError:
            self._finish_conflict("session_state_conflict")
        except (SessionIntegrityError, OSError):
            self._finish_internal_error()
        except Exception:
            self._finish_internal_error()


class SessionFinalizeRouteHandler(PilotAPIHandler):
    @tornado.web.authenticated
    def post(self, session_id):
        try:
            body = _closed_body(
                self.read_json_object(),
                {"schema_version", "last_sequence"},
                code="session_validation_failed",
            )
            if (
                body["schema_version"] != 1
                or not isinstance(body["last_sequence"], int)
                or isinstance(body["last_sequence"], bool)
                or body["last_sequence"] < 0
            ):
                raise ApiRequestError(
                    422,
                    "session_validation_failed",
                    "请求内容未通过校验。",
                    details={
                        "field": "last_sequence",
                        "reason": "invalid_nonnegative_integer",
                    },
                )
            canonical_id = _canonical_resource_uuid(
                session_id,
                field="session_id",
            )
            worker, session_store, job_store = self._services()
            finalized = session_store.finalize(
                canonical_id,
                last_sequence=body["last_sequence"],
            )
            input_hash = compute_input_snapshot_hash(
                session_store,
                canonical_id,
            )
            job = job_store.create(
                session=finalized,
                input_snapshot_hash=input_hash,
            )
            attached = session_store.attach_job(
                canonical_id,
                str(job["job_id"]),
            )
            self._refresh_training_record(canonical_id)
            self._refresh_classroom_brief(canonical_id)
            if job.get("status") == "queued":
                worker.enqueue(str(job["job_id"]))
            self.finish_json(
                {
                    "session_id": attached["session_id"],
                    "status": attached["status"],
                    "last_contiguous_sequence": attached[
                        "last_contiguous_sequence"
                    ],
                    "analysis_job_id": attached["analysis_job_id"],
                },
                status=202,
            )
        except ApiRequestError as error:
            self._finish_request_error(error)
        except SessionNotFoundError:
            self._finish_not_found("session_not_found")
        except SequenceGapError as error:
            self.finish_error(
                409,
                "sequence_gap",
                "行为序列存在缺口。",
                details={
                    "field": "last_sequence",
                    "reason": "missing_ranges:"
                    + ",".join(
                        f"{start}-{end}"
                        for start, end in error.missing_ranges
                    ),
                },
            )
        except AnalysisQueueFullError:
            self.finish_error(
                429,
                "analysis_queue_full",
                "分析队列暂时已满。",
                retryable=True,
            )
        except (
            SessionStateError,
            AnalysisJobStateError,
            AnalysisJobConflictError,
            AnalysisWorkerStateError,
        ):
            self._finish_conflict("session_finalize_conflict")
        except (
            SessionIntegrityError,
            AnalysisJobIntegrityError,
            OSError,
        ):
            self._finish_internal_error()
        except Exception:
            self._finish_internal_error()


class SessionLogsRouteHandler(PilotAPIHandler):
    """List the three fixed public logs for one canonical session."""

    @tornado.web.authenticated
    def get(self, session_id):
        try:
            canonical_id = _canonical_resource_uuid(
                session_id,
                field="session_id",
            )
            logs = self._session_log_service().list_log_artifacts(canonical_id)
            self.set_header("Cache-Control", "no-store")
            self.finish_json({"session_id": canonical_id, "logs": logs})
        except ApiRequestError as error:
            self._finish_request_error(error)
        except SessionNotFoundError:
            self._finish_not_found("session_not_found")
        except (
            SessionIntegrityError,
            SessionLogIntegrityError,
            AnalysisJobIntegrityError,
            AnalysisJobNotFoundError,
        ):
            self.finish_error(
                500,
                "session_log_unavailable",
                "本次日志暂时无法读取。",
                retryable=True,
            )


class SessionBriefRouteHandler(PilotAPIHandler):
    """Return only the private-safe deterministic classroom brief."""

    @tornado.web.authenticated
    def get(self, session_id):
        try:
            canonical_id = _canonical_resource_uuid(
                session_id,
                field="session_id",
            )
            brief = self._session_log_service().get_classroom_brief(
                canonical_id
            )
            if brief is None:
                self.finish_error(
                    409,
                    "classroom_brief_not_ready",
                    "本地课堂简报尚未生成。",
                    retryable=True,
                )
                return
            self.set_header("Cache-Control", "no-store")
            self.finish_json(brief)
        except ApiRequestError as error:
            self._finish_request_error(error)
        except SessionNotFoundError:
            self._finish_not_found("session_not_found")
        except (SessionIntegrityError, SessionLogIntegrityError):
            self.finish_error(
                500,
                "classroom_brief_unavailable",
                "本地课堂简报暂时无法读取。",
                retryable=True,
            )


class SessionLogContentRouteHandler(PilotAPIHandler):
    """Return one bounded, allowlisted UTF-8 artifact for inline viewing."""

    @tornado.web.authenticated
    async def get(self, session_id, kind):
        await self._finish_log(session_id, kind, download=False)

    async def _finish_log(
        self,
        session_id: object,
        kind: object,
        *,
        download: bool,
    ):
        try:
            canonical_id = _canonical_resource_uuid(
                session_id,
                field="session_id",
            )
            if not isinstance(kind, str):
                raise ApiRequestError(
                    400,
                    "invalid_session_log_kind",
                    "日志类型无效。",
                )
            try:
                service = self._session_log_service()
                if download:
                    with service.open_log_artifact(
                        canonical_id,
                        kind,
                        inline=False,
                    ) as (metadata, stream):
                        self._set_log_headers(metadata, download=True)
                        self.set_header("Content-Length", metadata["size_bytes"])
                        while chunk := stream.read(64 * 1024):
                            self.write(chunk)
                            await self.flush()
                        self.finish()
                        return
                metadata, content = service.read_log_artifact(
                    canonical_id,
                    kind,
                    inline=True,
                )
            except ValueError as error:
                raise ApiRequestError(
                    400,
                    "invalid_session_log_kind",
                    "日志类型无效。",
                ) from error
            self._set_log_headers(metadata, download=False)
            self.finish(content)
        except ApiRequestError as error:
            self._finish_request_error(error)
        except SessionLogArtifactTooLargeError:
            self.finish_error(
                413,
                "session_log_too_large",
                "日志内容过大，请下载后查看。",
            )
        except SessionLogArtifactNotReadyError:
            self.finish_error(
                409,
                "session_log_not_ready",
                "本次日志尚未生成完成。",
                retryable=True,
            )
        except SessionNotFoundError:
            self._finish_not_found("session_not_found")
        except (
            SessionIntegrityError,
            SessionLogIntegrityError,
            AnalysisJobIntegrityError,
            AnalysisJobNotFoundError,
            UnicodeError,
        ):
            self.finish_error(
                500,
                "session_log_unavailable",
                "本次日志暂时无法读取。",
                retryable=True,
            )

    def _set_log_headers(
        self,
        metadata: dict[str, object],
        *,
        download: bool,
    ) -> None:
        self.set_header("Content-Type", metadata["media_type"])
        self.set_header("Cache-Control", "no-store")
        self.set_header("X-Content-Type-Options", "nosniff")
        self.set_header("Content-Security-Policy", "default-src 'none'")
        if download:
            self.set_header(
                "Content-Disposition",
                f'attachment; filename="{metadata["filename"]}"',
            )


class SessionLogDownloadRouteHandler(SessionLogContentRouteHandler):
    """Download the complete allowlisted artifact without inline truncation."""

    @tornado.web.authenticated
    async def get(self, session_id, kind):
        await self._finish_log(session_id, kind, download=True)


class SessionLifecycleRouteHandler(PilotAPIHandler):
    action = ""

    @tornado.web.authenticated
    def post(self, session_id):
        try:
            canonical_id = _canonical_resource_uuid(
                session_id,
                field="session_id",
            )
            body = self.read_json_object()
            _, session_store, _ = self._services()
            if self.action == "abandon":
                _closed_body(
                    body,
                    {"reason"},
                    code="session_validation_failed",
                )
                session = session_store.abandon(
                    canonical_id,
                    reason=_nonempty_text(
                        body["reason"],
                        field="reason",
                        code="session_validation_failed",
                    ),
                )
                self._refresh_classroom_brief(canonical_id)
            else:
                _closed_body(
                    body,
                    {"actor", "reason"},
                    code="session_validation_failed",
                )
                session = session_store.recover(
                    canonical_id,
                    actor=_nonempty_text(
                        body["actor"],
                        field="actor",
                        code="session_validation_failed",
                    ),
                    reason=_nonempty_text(
                        body["reason"],
                        field="reason",
                        code="session_validation_failed",
                    ),
                )
            self.finish_json(_session_projection(session))
        except ApiRequestError as error:
            self._finish_request_error(error)
        except SessionNotFoundError:
            self._finish_not_found("session_not_found")
        except (SessionStateError, ValueError):
            self._finish_conflict("session_state_conflict")
        except (SessionIntegrityError, OSError):
            self._finish_internal_error()
        except Exception:
            self._finish_internal_error()


class SessionAbandonRouteHandler(SessionLifecycleRouteHandler):
    action = "abandon"


class SessionRecoverRouteHandler(SessionLifecycleRouteHandler):
    action = "recover"


class SessionRouteHandler(PilotAPIHandler):
    @tornado.web.authenticated
    def get(self, session_id):
        try:
            canonical_id = _canonical_resource_uuid(
                session_id,
                field="session_id",
            )
            _, session_store, _ = self._services()
            self.finish_json(_session_projection(session_store.read(canonical_id)))
        except ApiRequestError as error:
            self._finish_request_error(error)
        except SessionNotFoundError:
            self._finish_not_found("session_not_found")
        except SessionIntegrityError:
            self._finish_internal_error()
        except Exception:
            self._finish_internal_error()

    @tornado.web.authenticated
    def delete(self, session_id):
        try:
            canonical_id = _canonical_resource_uuid(
                session_id,
                field="session_id",
            )
            body = _closed_body(
                self.read_json_object(),
                {"actor", "reason", "confirm_session_id"},
                code="session_validation_failed",
            )
            confirmation = _canonical_resource_uuid(
                body["confirm_session_id"],
                field="session_id",
            )
            if confirmation != canonical_id:
                raise ApiRequestError(
                    409,
                    "session_confirmation_mismatch",
                    "删除确认与会话标识不一致。",
                )
            actor = _nonempty_text(
                body["actor"],
                field="actor",
                code="session_validation_failed",
            )
            reason = _nonempty_text(
                body["reason"],
                field="reason",
                code="session_validation_failed",
            )
            _, session_store, _ = self._services()
            session_store.delete_cascade(
                canonical_id,
                actor=actor,
                reason=reason,
            )
            self.finish_json({"deleted_session_id": canonical_id})
        except ApiRequestError as error:
            self._finish_request_error(error)
        except SessionNotFoundError:
            self._finish_not_found("session_not_found")
        except SessionStateError:
            self._finish_conflict("active_analysis_job")
        except (SessionIntegrityError, AnalysisJobIntegrityError, OSError):
            self._finish_internal_error()
        except Exception:
            self._finish_internal_error()


class AnalysisJobRouteHandler(PilotAPIHandler):
    @tornado.web.authenticated
    def get(self, job_id):
        try:
            canonical_id = _canonical_resource_uuid(
                job_id,
                field="job_id",
            )
            _, _, job_store = self._services()
            self.finish_json(_job_projection(job_store.get(canonical_id)))
        except ApiRequestError as error:
            self._finish_request_error(error)
        except AnalysisJobNotFoundError:
            self._finish_not_found("analysis_job_not_found")
        except AnalysisJobIntegrityError:
            self._finish_internal_error()
        except Exception:
            self._finish_internal_error()


class AnalysisRetryRouteHandler(PilotAPIHandler):
    @tornado.web.authenticated
    def post(self, job_id):
        try:
            canonical_id = _canonical_resource_uuid(
                job_id,
                field="job_id",
            )
            body = _closed_body(
                self.read_json_object(),
                {"reason"},
                code="analysis_retry_validation_failed",
            )
            reason = _nonempty_text(
                body["reason"],
                field="reason",
                code="analysis_retry_validation_failed",
            )
            worker, _, job_store = self._services()
            job = job_store.retry(canonical_id, reason=reason)
            worker.enqueue(canonical_id)
            self.finish_json(_job_projection(job))
        except ApiRequestError as error:
            self._finish_request_error(error)
        except AnalysisJobNotFoundError:
            self._finish_not_found("analysis_job_not_found")
        except AnalysisQueueFullError:
            self.finish_error(
                429,
                "analysis_queue_full",
                "分析队列暂时已满。",
                retryable=True,
            )
        except (AnalysisJobStateError, AnalysisWorkerStateError):
            self._finish_conflict("analysis_retry_conflict")
        except ValueError:
            self.finish_error(
                422,
                "analysis_retry_validation_failed",
                "请求内容未通过校验。",
                details={"field": "reason", "reason": "invalid_value"},
            )
        except (AnalysisJobIntegrityError, OSError):
            self._finish_internal_error()
        except Exception:
            self._finish_internal_error()


class SessionAnalysisRouteHandler(PilotAPIHandler):
    @tornado.web.authenticated
    def get(self, session_id):
        try:
            canonical_id = _canonical_resource_uuid(
                session_id,
                field="session_id",
            )
            _, session_store, job_store = self._services()
            session = session_store.read(canonical_id)
            job_value = session.get("analysis_job_id")
            if job_value is None:
                raise PilotResourceNotFoundError("analysis_job_id")
            job = job_store.get(str(job_value))
            if job.get("session_id") != canonical_id:
                raise SessionIntegrityError(
                    "Attached job does not belong to the session."
                )
            if job.get("status") in {"queued", "running"}:
                self.finish_json(_job_projection(job), status=202)
                return
            if job.get("status") == "error":
                self.finish_error(
                    409,
                    str(job.get("error_code") or "analysis_failed"),
                    "分析任务失败，可以重试。",
                    retryable=True,
                )
                return
            _, _, result, profile = self._load_result(canonical_id)
            projected = self._effective_result(result, profile)
            job_error = job.get("error_code")
            projected["error_code"] = (
                str(job_error)
                if isinstance(job_error, str)
                and job_error in _RESULT_ERROR_CODES
                else None
            )
            self.finish_json(projected)
        except ApiRequestError as error:
            self._finish_request_error(error)
        except (SessionNotFoundError, PilotResourceNotFoundError):
            self._finish_not_found("analysis_not_found")
        except AnalysisJobStateError:
            self._finish_conflict("analysis_not_ready")
        except (
            SessionIntegrityError,
            AnalysisJobIntegrityError,
            ReviewIntegrityError,
            OSError,
        ):
            self._finish_internal_error()
        except Exception:
            self._finish_internal_error()


class DimensionReviewRouteHandler(PilotAPIHandler):
    @tornado.web.authenticated
    def patch(self, session_id, dimension_code):
        try:
            canonical_id = _canonical_resource_uuid(
                session_id,
                field="session_id",
            )
            if (
                not isinstance(dimension_code, str)
                or _DIMENSION_CODE.fullmatch(dimension_code) is None
            ):
                raise ApiRequestError(
                    400,
                    "invalid_dimension_code",
                    "维度标识格式无效。",
                )
            body = _closed_body(
                self.read_json_object(),
                _REVIEW_FIELDS,
                code="review_validation_failed",
            )
            if (
                not isinstance(body["revision"], int)
                or isinstance(body["revision"], bool)
                or body["revision"] < 0
                or not isinstance(body["evidence_event_ids"], list)
            ):
                raise ApiRequestError(
                    422,
                    "review_validation_failed",
                    "复核内容未通过校验。",
                    details={"field": "$", "reason": "invalid_value"},
                )
            session, _, result, profile = self._load_result(canonical_id)
            rows = {
                row["dimension_code"]: row
                for row in result["dimension_results"]
            }
            profile_dimensions = {
                item["code"]: item
                for item in profile.get("dimensions", [])
                if isinstance(item, dict)
                and isinstance(item.get("code"), str)
            }
            if (
                dimension_code not in rows
                or dimension_code not in profile_dimensions
            ):
                raise PilotResourceNotFoundError("dimension_code")
            profile_dimension = profile_dimensions[dimension_code]
            allowed_levels = {
                level.get("code")
                for level in profile_dimension.get("levels", [])
                if isinstance(level, dict)
                and isinstance(level.get("code"), str)
            }
            if (
                body["level_code"] is not None
                and body["level_code"] not in allowed_levels
            ):
                raise ApiRequestError(
                    422,
                    "review_validation_failed",
                    "复核内容未通过校验。",
                    details={
                        "field": "level_code",
                        "reason": "unknown_level_code",
                    },
                )
            _, session_store, _ = self._services()
            canonical_event_ids = {
                str(event["event_id"])
                for event in session_store.read_events(canonical_id)
            }
            requested_event_ids = body["evidence_event_ids"]
            if (
                not all(
                    isinstance(item, str)
                    and item in canonical_event_ids
                    for item in requested_event_ids
                )
                or (
                    body["evidence_status"] == "observed"
                    and not requested_event_ids
                )
            ):
                raise ApiRequestError(
                    422,
                    "review_validation_failed",
                    "复核内容未通过校验。",
                    details={
                        "field": "evidence_event_ids",
                        "reason": "unknown_or_missing_evidence",
                    },
                )
            reviews = ReviewStore(Path(session_store.root))
            record = reviews.append(
                str(result["analysis_id"]),
                dimension_code,
                expected_revision=body["revision"],
                correction=body,
            )
            self._refresh_training_record(canonical_id)
            effective = self._effective_dimension(
                rows[dimension_code],
                profile_dimension=profile_dimension,
                review=record,
            )
            self.finish_json(effective)
        except ApiRequestError as error:
            self._finish_request_error(error)
        except (SessionNotFoundError, PilotResourceNotFoundError):
            self._finish_not_found("analysis_dimension_not_found")
        except ReviewConflictError:
            self._finish_conflict("review_revision_conflict")
        except ValueError:
            self.finish_error(
                422,
                "review_validation_failed",
                "复核内容未通过校验。",
                details={"field": "$", "reason": "invalid_combination"},
            )
        except AnalysisJobStateError:
            self._finish_conflict("analysis_not_ready")
        except (
            SessionIntegrityError,
            AnalysisJobIntegrityError,
            ReviewIntegrityError,
            OSError,
        ):
            self._finish_internal_error()
        except Exception:
            self._finish_internal_error()


def setup_route_handlers(web_app):
    host_pattern = ".*$"
    base_url = web_app.settings["base_url"]

    hello_route_pattern = url_path_join(base_url, "myextension", "hello")
    behavior_events_route_pattern = url_path_join(
        base_url, "myextension", "behavior-events"
    )
    run_python_file_route_pattern = url_path_join(
        base_url, "myextension", "run-python-file"
    )
    latest_analysis_route_pattern = url_path_join(
        base_url, "myextension", "latest-analysis"
    )
    ai_config_route_pattern = url_path_join(base_url, "myextension", "ai-config")
    platform_registration_route_pattern = url_path_join(
        base_url,
        "myextension",
        "platform",
        "register",
    )
    platform_context_route_pattern = url_path_join(
        base_url,
        "myextension",
        "platform",
        "context",
    )
    dimension_templates_route_pattern = url_path_join(
        base_url,
        "myextension",
        "dimension-templates",
    )
    dimension_profiles_route_pattern = url_path_join(
        base_url,
        "myextension",
        "dimension-profiles",
    )
    dimension_profile_draft_route_pattern = url_path_join(
        base_url,
        "myextension",
        "dimension-profiles",
        r"([^/]+)",
        "draft",
    )
    dimension_profile_publish_route_pattern = url_path_join(
        base_url,
        "myextension",
        "dimension-profiles",
        r"([^/]+)",
        "publish",
    )
    dimension_profile_version_route_pattern = url_path_join(
        base_url,
        "myextension",
        "dimension-profiles",
        r"([^/]+)",
        "versions",
        r"([^/]+)",
    )
    assessment_knowledge_route_pattern = url_path_join(
        base_url,
        "myextension",
        "assessment-assist",
        "knowledge-points",
    )
    assessment_tests_route_pattern = url_path_join(
        base_url,
        "myextension",
        "assessment-assist",
        "tests",
    )
    session_start_route_pattern = url_path_join(
        base_url, "myextension", "sessions", "start"
    )
    log_folder_open_route_pattern = url_path_join(
        base_url,
        "myextension",
        "log-folder",
        "open",
    )
    session_segments_route_pattern = url_path_join(
        base_url,
        "myextension",
        "sessions",
        r"([^/]+)",
        "segments",
    )
    session_finalize_route_pattern = url_path_join(
        base_url,
        "myextension",
        "sessions",
        r"([^/]+)",
        "finalize",
    )
    session_logs_route_pattern = url_path_join(
        base_url,
        "myextension",
        "sessions",
        r"([^/]+)",
        "logs",
    )
    session_brief_route_pattern = url_path_join(
        base_url,
        "myextension",
        "sessions",
        r"([^/]+)",
        "brief",
    )
    session_log_content_route_pattern = url_path_join(
        base_url,
        "myextension",
        "sessions",
        r"([^/]+)",
        "logs",
        r"([^/]+)",
    )
    session_log_download_route_pattern = url_path_join(
        base_url,
        "myextension",
        "sessions",
        r"([^/]+)",
        "logs",
        r"([^/]+)",
        "download",
    )
    session_abandon_route_pattern = url_path_join(
        base_url,
        "myextension",
        "sessions",
        r"([^/]+)",
        "abandon",
    )
    session_recover_route_pattern = url_path_join(
        base_url,
        "myextension",
        "sessions",
        r"([^/]+)",
        "recover",
    )
    session_route_pattern = url_path_join(
        base_url,
        "myextension",
        "sessions",
        r"([^/]+)",
    )
    analysis_job_route_pattern = url_path_join(
        base_url,
        "myextension",
        "analysis-jobs",
        r"([^/]+)",
    )
    analysis_retry_route_pattern = url_path_join(
        base_url,
        "myextension",
        "analysis-jobs",
        r"([^/]+)",
        "retry",
    )
    session_analysis_route_pattern = url_path_join(
        base_url,
        "myextension",
        "sessions",
        r"([^/]+)",
        "analysis",
    )
    dimension_review_route_pattern = url_path_join(
        base_url,
        "myextension",
        "sessions",
        r"([^/]+)",
        "analysis",
        r"([^/]+)",
        "review",
    )
    handlers = [
        (hello_route_pattern, HelloRouteHandler),
        (behavior_events_route_pattern, BehaviorEventsRouteHandler),
        (run_python_file_route_pattern, RunPythonFileRouteHandler),
        (latest_analysis_route_pattern, LatestAnalysisRouteHandler),
        (ai_config_route_pattern, AiConfigRouteHandler),
        (platform_registration_route_pattern, PlatformRegistrationRouteHandler),
        (platform_context_route_pattern, PlatformContextRouteHandler),
        (dimension_templates_route_pattern, DimensionTemplatesRouteHandler),
        (dimension_profiles_route_pattern, DimensionProfilesRouteHandler),
        (
            dimension_profile_draft_route_pattern,
            DimensionProfileDraftRouteHandler,
        ),
        (
            dimension_profile_publish_route_pattern,
            DimensionProfilePublishRouteHandler,
        ),
        (
            dimension_profile_version_route_pattern,
            DimensionProfileVersionRouteHandler,
        ),
        (
            assessment_knowledge_route_pattern,
            AssessmentKnowledgeAssistRouteHandler,
        ),
        (
            assessment_tests_route_pattern,
            AssessmentTestsAssistRouteHandler,
        ),
        (log_folder_open_route_pattern, LogFolderOpenRouteHandler),
        (session_start_route_pattern, SessionStartRouteHandler),
        (session_segments_route_pattern, SessionSegmentsRouteHandler),
        (session_finalize_route_pattern, SessionFinalizeRouteHandler),
        (session_brief_route_pattern, SessionBriefRouteHandler),
        (session_logs_route_pattern, SessionLogsRouteHandler),
        (session_log_download_route_pattern, SessionLogDownloadRouteHandler),
        (session_log_content_route_pattern, SessionLogContentRouteHandler),
        (session_abandon_route_pattern, SessionAbandonRouteHandler),
        (session_recover_route_pattern, SessionRecoverRouteHandler),
        (dimension_review_route_pattern, DimensionReviewRouteHandler),
        (session_analysis_route_pattern, SessionAnalysisRouteHandler),
        (session_route_pattern, SessionRouteHandler),
        (analysis_retry_route_pattern, AnalysisRetryRouteHandler),
        (analysis_job_route_pattern, AnalysisJobRouteHandler),
    ]

    web_app.add_handlers(host_pattern, handlers)


def _validate_python_path(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("path must be a non-empty string.")
    path = value.strip()
    if path.startswith("/") or "\\" in path or ".." in Path(path).parts:
        raise ValueError("path must be a safe Jupyter contents path.")
    if not path.lower().endswith(".py"):
        raise ValueError("Only .py files can be run.")
    return path


async def _contents_os_path(handler, path):
    contents_manager = (
        getattr(handler, "contents_manager", None)
        or handler.settings.get("contents_manager")
    )
    if contents_manager is None:
        raise ValueError(
            "当前 Jupyter Contents Manager 不支持本地运行。"
        )

    get_os_path = getattr(contents_manager, "_get_os_path", None)
    root_dir = getattr(contents_manager, "root_dir", None)
    if (
        not callable(get_os_path)
        or not isinstance(root_dir, str)
        or not root_dir
    ):
        raise ValueError(
            "当前 Jupyter Contents Manager 不支持本地运行。"
        )

    try:
        model = await ensure_async(
            contents_manager.get(path, content=False)
        )
    except tornado.web.HTTPError as error:
        if error.status_code == 404:
            raise OSError("Python file was not found.") from error
        raise ValueError("Jupyter Contents 校验未通过。") from error

    if not isinstance(model, dict) or model.get("type") != "file":
        raise ValueError("只能运行普通 Python 文件。")

    root_path = Path(root_dir).resolve()
    try:
        os_path = Path(get_os_path(path)).resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise OSError("Python file was not found.") from error

    try:
        os_path.relative_to(root_path)
    except ValueError as error:
        raise ValueError(
            "Python 文件必须位于 Jupyter 根目录内。"
        ) from error

    if not os_path.is_file():
        raise OSError("Python file was not found.")
    return os_path


def _decode_process_output(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _truncate_output(value):
    if len(value) <= MAX_RUN_OUTPUT_CHARS:
        return value
    return value[:MAX_RUN_OUTPUT_CHARS] + "\n... output truncated ..."


def _latest_analysis_path():
    root = resolve_log_root()
    candidates = list(root.glob("**/*.stage_samples.pretty.json"))
    candidates.extend(root.glob("**/*.stage_samples.jsonl"))
    candidates = [path for path in candidates if path.stat().st_size > 0]
    if not candidates:
        raise FileNotFoundError("No stage sample file found.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _contents_path(handler, path):
    contents_manager = (
        getattr(handler, "contents_manager", None)
        or handler.settings.get("contents_manager")
    )
    root_dir = getattr(contents_manager, "root_dir", None)
    if not root_dir:
        return None
    try:
        return str(path.resolve().relative_to(Path(root_dir).resolve())).replace("\\", "/")
    except ValueError:
        return None


def _raw_event_path(path):
    stem = path.name.split(".stage_samples", 1)[0]
    return path.with_name(f"{stem}.raw_events.jsonl")


def _source_log_path(path):
    stem = path.name.split(".stage_samples", 1)[0]
    return path.with_name(f"{stem}.md")


def _related_log_groups(handler, path):
    root = resolve_log_root()
    stem = path.name.split(".stage_samples", 1)[0]
    specs = [
        ("可读记录", [("行为记录", ".md"), ("行为时间线", ".timeline.jsonl")]),
        ("训练数据", [
            ("聚合样本（可读）", ".stage_samples.pretty.json"),
            ("聚合样本（JSONL）", ".stage_samples.jsonl"),
            ("事件样本", ".samples.jsonl"),
        ]),
        ("原始与状态", [
            ("原始事件", ".raw_events.jsonl"),
            ("AI标签", ".llm_labels.jsonl"),
            ("分析状态", ".analysis_status.json"),
            ("结构化元数据", ".meta.json"),
        ]),
    ]
    groups = []
    for category, files in specs:
        rows = []
        for label, suffix in files:
            candidate = path.with_name(f"{stem}{suffix}")
            if not candidate.exists():
                continue
            rows.append({
                "label": label,
                "path": str(candidate.relative_to(root)).replace("\\", "/"),
                "contents_path": _contents_path(handler, candidate),
            })
        if rows:
            groups.append({"category": category, "files": rows})
    return groups


def _truncate_analysis(value):
    if len(value) <= MAX_ANALYSIS_CHARS:
        return value
    return value[:MAX_ANALYSIS_CHARS] + "\n... analysis truncated ..."

