"""Bounded, server-only AI suggestions for teacher-authored classroom plans."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from classroom_sync.config import Settings
from classroom_sync.errors import AiSuggestionUnavailableError, UpstreamUnavailableError


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
        if not 1 <= timeout_seconds <= 30:
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
    ) -> str:
        try:
            response = self._client.post(
                self.completion_url(self._settings.base_url),
                headers={"Authorization": f"Bearer {self._settings.api_key}"},
                json={
                    "model": self._settings.model,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "messages": messages,
                },
                timeout=self._settings.timeout_seconds,
            )
        except httpx.RequestError as error:
            raise UpstreamUnavailableError("ai_provider_upstream_unavailable") from error

        if response.status_code >= 400:
            raise UpstreamUnavailableError("ai_provider_upstream_unavailable")

        try:
            return self._response_content(response.json())
        except (KeyError, TypeError, ValueError) as error:
            raise UpstreamUnavailableError("ai_provider_upstream_unavailable") from error

    @staticmethod
    def completion_url(base_url: str) -> str:
        if base_url.endswith("/chat/completions"):
            return base_url
        return f"{base_url}/chat/completions"

    @staticmethod
    def _response_content(payload: object) -> str:
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
        return content


class PlanSuggestionInput(BaseModel):
    """The bounded teacher text that may be sent to the configured provider."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(default="", max_length=200)
    statement: str = Field(min_length=1, max_length=10_000)


class SuggestedKnowledgePoint(BaseModel):
    """A single editable observation point returned by the provider."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=50)
    description: str = Field(min_length=1, max_length=500)


class _ProviderSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    knowledge_points: list[SuggestedKnowledgePoint] = Field(min_length=1, max_length=10)


@dataclass(frozen=True)
class PlanSuggestion:
    """Transient result that the teacher must explicitly apply before publication."""

    title: str
    knowledge_points: tuple[SuggestedKnowledgePoint, ...]


class OpenAiPlanSuggestionService:
    """A synchronous OpenAI-compatible adapter with a bounded request surface."""

    def __init__(self, settings: AiProviderSettings | None, client: httpx.Client) -> None:
        self._settings = settings
        self._completion_client = (
            OpenAiCompletionClient(settings, client) if settings is not None else None
        )

    def generate(self, suggestion_input: PlanSuggestionInput) -> PlanSuggestion:
        completion_client = self._completion_client
        if completion_client is None:
            raise AiSuggestionUnavailableError("ai_suggestion_not_configured")

        try:
            content = completion_client.complete(
                self._messages(suggestion_input), temperature=0.2, max_tokens=1200
            )
        except UpstreamUnavailableError as error:
            raise UpstreamUnavailableError("ai_suggestion_upstream_unavailable") from error

        try:
            suggestion = _ProviderSuggestion.model_validate_json(self._strip_optional_json_fence(content))
        except (json.JSONDecodeError, KeyError, TypeError, PydanticValidationError, ValueError) as error:
            raise UpstreamUnavailableError("ai_suggestion_upstream_unavailable") from error

        return PlanSuggestion(
            title=suggestion.title,
            knowledge_points=tuple(suggestion.knowledge_points),
        )

    @staticmethod
    def _messages(suggestion_input: PlanSuggestionInput) -> list[dict[str, str]]:
        return [
                {
                    "role": "system",
                    "content": (
                        "你是课堂教学设计助手。只返回一个 JSON 对象，不要 Markdown。"
                        "对象必须含 title 和 knowledge_points；knowledge_points 是 1 到 10 项，"
                        "每项含 name、description。内容必须是教师可继续编辑的中文课堂方案草稿。"
                    ),
                },
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
