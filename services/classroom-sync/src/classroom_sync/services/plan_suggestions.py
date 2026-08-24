"""Bounded, server-only AI suggestions for teacher-authored classroom plans."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic import ValidationError as PydanticValidationError

from classroom_sync.config import Settings
from classroom_sync.errors import AiSuggestionUnavailableError, UpstreamUnavailableError

logger = logging.getLogger(__name__)


def _validate_safe_plan_display_text(value: str) -> str:
    """Prevent provider output from becoming a transport for secrets or raw artifacts."""

    lowered = value.lower()
    if any(marker in lowered for marker in ("```", "http://", "https://", "s3://")):
        raise ValueError("plan suggestion contains a forbidden address or code fence")
    if re.search(r"(?:/Users/|/home/|[A-Za-z]:[\\/])", value):
        raise ValueError("plan suggestion contains an absolute path")
    if re.search(
        r"(?:api[_ -]?key|authorization\s*:|原始输出|模型原始响应|"
        r"\braw\s+(?:model\s+)?(?:output|response)\b|\bprovider\b)",
        value,
        re.IGNORECASE,
    ):
        raise ValueError("plan suggestion contains provider-sensitive text")
    return value


@dataclass(frozen=True)
class AiProviderSettings:
    """Complete provider settings, derived only from server runtime configuration."""

    base_url: str
    model: str
    api_key: str = field(repr=False)
    timeout_seconds: int = 15

    @classmethod
    def from_settings(cls, settings: Settings) -> AiProviderSettings | None:
        values = (settings.ai_base_url, settings.ai_model, settings.ai_api_key)
        if all(value is None for value in values):
            return None
        if any(value is None for value in values):
            raise AiSuggestionUnavailableError("ai_suggestion_not_configured")

        base_url = settings.ai_base_url
        model = settings.ai_model
        api_key = settings.ai_api_key
        timeout_seconds = settings.ai_timeout_seconds
        if base_url is None or model is None or api_key is None:
            raise AiSuggestionUnavailableError("ai_suggestion_not_configured")
        if not cls._is_safe_base_url(base_url):
            raise AiSuggestionUnavailableError("ai_suggestion_not_configured")
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool):
            raise AiSuggestionUnavailableError("ai_suggestion_not_configured")
        # Calls run only in the durable background workers, never in the
        # teacher's browser request.  Allow a bounded longer window for a
        # slower provider while still preventing unbounded work.
        if not 1 <= timeout_seconds <= 180:
            raise AiSuggestionUnavailableError("ai_suggestion_not_configured")

        return cls(
            base_url=base_url.rstrip("/"),
            model=model,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )

    @staticmethod
    def _is_safe_base_url(value: str) -> bool:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https"
            and parsed.hostname is not None
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
        )


AiSuggestionSettings = AiProviderSettings


@dataclass(frozen=True)
class OpenAiCompletionResult:
    """The text and safe completion metadata returned by one provider call."""

    content: str
    finish_reason: str | None


class OpenAiCompletionClient:
    """Small OpenAI-compatible boundary that never logs provider credentials."""

    def __init__(self, settings: AiProviderSettings, client: httpx.Client) -> None:
        self._settings = settings
        self._client = client

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        thinking_mode: Literal["disabled"] | None = None,
        json_mode: bool = False,
    ) -> str:
        return self.complete_with_metadata(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking_mode=thinking_mode,
            json_mode=json_mode,
        ).content

    def complete_with_metadata(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        thinking_mode: Literal["disabled"] | None = None,
        json_mode: bool = False,
    ) -> OpenAiCompletionResult:
        request_body: dict[str, object] = {
            "model": self._settings.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if thinking_mode is not None:
            request_body["thinking"] = {"type": thinking_mode}
        if json_mode:
            request_body["response_format"] = {"type": "json_object"}

        try:
            response = self._client.post(
                self.completion_url(self._settings.base_url),
                headers={"Authorization": f"Bearer {self._settings.api_key}"},
                json=request_body,
                timeout=self._settings.timeout_seconds,
            )
        except httpx.TimeoutException as error:
            raise UpstreamUnavailableError("ai_provider_timeout") from error
        except httpx.RequestError as error:
            raise UpstreamUnavailableError("ai_provider_network_unavailable") from error

        if response.status_code in {401, 403}:
            raise UpstreamUnavailableError(
                "ai_provider_authorization_or_policy_rejected", retryable=False
            )
        if response.status_code == 429:
            raise UpstreamUnavailableError("ai_provider_rate_limited")
        if response.status_code >= 500:
            raise UpstreamUnavailableError("ai_provider_server_unavailable")
        if not response.is_success:
            raise UpstreamUnavailableError("ai_provider_request_rejected", retryable=False)

        try:
            return self._response_result(response.json())
        except (KeyError, TypeError, ValueError) as error:
            raise UpstreamUnavailableError("ai_provider_response_invalid", retryable=False) from error

    @staticmethod
    def completion_url(base_url: str) -> str:
        if base_url.endswith("/chat/completions"):
            return base_url
        return f"{base_url}/chat/completions"

    @staticmethod
    def _response_content(payload: object) -> str:
        return OpenAiCompletionClient._response_result(payload).content

    @staticmethod
    def _response_result(payload: object) -> OpenAiCompletionResult:
        if not isinstance(payload, dict):
            raise TypeError("provider response is not an object")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("provider response has no choices")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise TypeError("provider choice is invalid")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise TypeError("provider message is invalid")
        content = message.get("content")
        if not isinstance(content, str):
            raise TypeError("provider content is invalid")
        finish_reason = choice.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            raise TypeError("provider finish reason is invalid")
        return OpenAiCompletionResult(content=content, finish_reason=finish_reason)


class PlanSuggestionInput(BaseModel):
    """The bounded teacher text that may be sent to the configured provider."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(default="", max_length=200)
    statement: str = Field(min_length=1, max_length=10_000)


class AutomaticEvaluationRequirement(BaseModel):
    """One non-executable local evidence requirement selected by the provider."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal[
        "successful_execution",
        "dict_literal_assignment",
        "dict_key_value_pairs",
        "dict_subscript_access",
        "dict_get_with_default",
        "print_call",
        "input_call",
    ]


class AutomaticEvaluation(BaseModel):
    """A bounded all-of rule that the local plugin can evaluate without AI."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mode: Literal["all"]
    summary: str = Field(min_length=1, max_length=500)
    requirements: list[AutomaticEvaluationRequirement] = Field(min_length=1, max_length=7)

    @field_validator("summary")
    @classmethod
    def validate_safe_summary(cls, value: str) -> str:
        return _validate_safe_plan_display_text(value)


class SuggestedKnowledgePoint(BaseModel):
    """A single editable observation point returned by the provider."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=50)
    description: str = Field(min_length=1, max_length=500)
    automatic_evaluation: AutomaticEvaluation | None = None

    @field_validator("name", "description")
    @classmethod
    def validate_safe_display_text(cls, value: str) -> str:
        return _validate_safe_plan_display_text(value)


class PlanSuggestionPayload(BaseModel):
    """Strict persisted and API-safe representation of a plan suggestion."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    knowledge_points: list[SuggestedKnowledgePoint] = Field(min_length=1, max_length=10)

    @field_validator("title")
    @classmethod
    def validate_safe_title(cls, value: str) -> str:
        return _validate_safe_plan_display_text(value)


@dataclass(frozen=True)
class PlanSuggestion:
    """Transient result that the teacher must explicitly apply before publication."""

    title: str
    knowledge_points: tuple[SuggestedKnowledgePoint, ...]


class OpenAiPlanSuggestionService:
    """A synchronous OpenAI-compatible adapter with a bounded request surface."""

    def __init__(self, settings: AiProviderSettings | None, client: httpx.Client) -> None:
        self._settings = settings
        self._uses_coding_plan_profile = (
            settings is not None and "coding" in urlsplit(settings.base_url).path.split("/")
        )
        self._completion_client = (
            OpenAiCompletionClient(settings, client) if settings is not None else None
        )

    @property
    def retry_provider_errors(self) -> bool:
        """Only Coding Plan may spend the durable worker retry budget."""

        return self._uses_coding_plan_profile

    def generate(self, suggestion_input: PlanSuggestionInput) -> PlanSuggestion:
        completion_client = self._completion_client
        if completion_client is None:
            raise AiSuggestionUnavailableError("ai_suggestion_not_configured")
        thinking_mode: Literal["disabled"] | None = (
            "disabled" if self._uses_coding_plan_profile else None
        )
        max_tokens = 2048 if self._uses_coding_plan_profile else 1200
        messages = self._messages(
            suggestion_input,
            include_automatic_evaluation=self._uses_coding_plan_profile,
        )

        try:
            completion = completion_client.complete_with_metadata(
                messages,
                temperature=0.2,
                max_tokens=max_tokens,
                thinking_mode=thinking_mode,
                json_mode=self._uses_coding_plan_profile,
            )
            if self._uses_coding_plan_profile and completion.finish_reason == "length":
                completion = completion_client.complete_with_metadata(
                    messages,
                    temperature=0.2,
                    max_tokens=4096,
                    thinking_mode=thinking_mode,
                    json_mode=self._uses_coding_plan_profile,
                )
        except UpstreamUnavailableError as error:
            logger.warning(
                "AI plan suggestion provider failure: code=%s retryable=%s",
                error.code,
                error.retryable,
            )
            raise UpstreamUnavailableError(
                "ai_suggestion_upstream_unavailable", retryable=error.retryable
            ) from error

        try:
            suggestion = self._parse_provider_suggestion(completion.content)
        except (json.JSONDecodeError, KeyError, TypeError, PydanticValidationError, ValueError) as error:
            logger.warning(
                "AI plan suggestion response rejected: error_type=%s",
                type(error).__name__,
            )
            raise UpstreamUnavailableError(
                "ai_suggestion_response_invalid", retryable=False
            ) from error

        return PlanSuggestion(
            title=suggestion.title,
            knowledge_points=tuple(suggestion.knowledge_points),
        )

    @classmethod
    def _parse_provider_suggestion(cls, content: str) -> PlanSuggestionPayload:
        """Keep a usable plan when only an optional local rule is unsupported.

        Automatic evaluation is never executed and must pass the strict local
        allowlist.  A malformed optional rule therefore cannot weaken the
        boundary or discard the independently valid teaching suggestions.
        """
        payload = json.loads(cls._strip_optional_json_fence(content))
        if not isinstance(payload, dict):
            raise TypeError("provider suggestion is not an object")

        knowledge_points = payload.get("knowledge_points")
        if not isinstance(knowledge_points, list):
            return PlanSuggestionPayload.model_validate(
                {
                    "title": payload.get("title"),
                    "knowledge_points": knowledge_points,
                }
            )

        sanitized_points: list[object] = []
        for knowledge_point in knowledge_points:
            if not isinstance(knowledge_point, dict):
                sanitized_points.append(knowledge_point)
                continue

            sanitized_point: dict[str, object] = {
                "name": knowledge_point.get("name"),
                "description": knowledge_point.get("description"),
            }
            automatic_evaluation = knowledge_point.get("automatic_evaluation")
            if automatic_evaluation is not None:
                try:
                    AutomaticEvaluation.model_validate(automatic_evaluation)
                except PydanticValidationError:
                    pass
                else:
                    sanitized_point["automatic_evaluation"] = automatic_evaluation
            sanitized_points.append(sanitized_point)

        return PlanSuggestionPayload.model_validate(
            {
                "title": payload.get("title"),
                "knowledge_points": sanitized_points,
            }
        )

    @staticmethod
    def _messages(
        suggestion_input: PlanSuggestionInput,
        *,
        include_automatic_evaluation: bool,
    ) -> list[dict[str, str]]:
        system_content = (
            "你是课堂教学设计助手。只返回一个 JSON 对象，不要 Markdown。"
            "对象必须含 title 和 knowledge_points；knowledge_points 是 1 到 10 项，"
            "每项含 name、description。"
        )
        if include_automatic_evaluation:
            system_content += (
                "每项可以含 automatic_evaluation。"
                "automatic_evaluation 只能含 mode=all、summary 和 requirements；"
                "requirements 每项只能含 kind，kind 只能是 successful_execution、"
                "dict_literal_assignment、dict_key_value_pairs、dict_subscript_access、"
                "dict_get_with_default、print_call 或 input_call。"
                "仅在能用这些本地、非执行性证据可靠判定时提供 automatic_evaluation，"
                "否则省略该字段。"
            )
        system_content += "内容必须是教师可继续编辑的简洁中文课堂方案草稿。"
        return [
            {"role": "system", "content": system_content},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "title": suggestion_input.title,
                        "statement": suggestion_input.statement,
                    },
                    ensure_ascii=False,
                ),
            },
        ]

    @staticmethod
    def _strip_optional_json_fence(content: str) -> str:
        value = content.strip()
        if not value.startswith("```"):
            return value
        lines = value.splitlines()
        if len(lines) < 3 or not lines[0].lower().startswith("```json") or lines[-1] != "```":
            raise ValueError("provider fenced response is invalid")
        return "\n".join(lines[1:-1]).strip()
