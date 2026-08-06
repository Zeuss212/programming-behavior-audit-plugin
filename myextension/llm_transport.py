"""Shared, privacy-conscious JSON chat transport and local AI configuration."""

from __future__ import annotations

import json
import os
import socket
import stat
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from .behavior_log_store import LOG_DIR_ENV_VAR, resolve_log_root
from .canonical_json import atomic_write_json, sha256_json


ARK_API_KEY_ENV_VAR = "ARK_API_KEY"
ARK_BASE_URL_ENV_VAR = "ARK_BASE_URL"
ARK_MODEL_ENV_VAR = "ARK_MODEL"
DEFAULT_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/coding/v3"
DEFAULT_ARK_MODEL = "glm-5-2-260617"
REQUEST_TIMEOUT_SEC = 60
ANALYSIS_TIMEOUT_ENV_VAR = (
    "JUPYTERLAB_BEHAVIOR_AUDIT_ANALYSIS_TIMEOUT_SEC"
)
DEFAULT_ANALYSIS_TIMEOUT_SEC = 120
MIN_ANALYSIS_TIMEOUT_SEC = 60
MAX_ANALYSIS_TIMEOUT_SEC = 180
PROVIDER_CALL_TIMEOUT_SEC = 60
STRUCTURED_OUTPUT_TOKEN_BUDGETS: tuple[int, int] = (8192, 16384)
AI_CONFIG_FILENAME = ".ark_ai_config.json"
AI_CONFIG_PATH_ENV_VAR = "JUPYTERLAB_BEHAVIOR_AUDIT_AI_CONFIG_PATH"
BLUEDOT_WORKSPACE_CODE_DIR = Path("/workspace/code")
BLUEDOT_AI_CONFIG_DIRNAME = ".behavior-audit"


class AiNotConfiguredError(RuntimeError):
    """Raised when a provider call is requested without an API key."""


class AiConfigValidationError(ValueError):
    """Closed, field-addressable validation failure for local AI config."""

    def __init__(
        self,
        field: str,
        reason: str,
        message: str | None = None,
    ) -> None:
        self.field = field
        self.reason = reason
        super().__init__(message or reason)


class LlmTransportError(RuntimeError):
    """Stable provider failure that never carries response content."""

    def __init__(
        self,
        error_code: str,
        *,
        http_status: int | None = None,
    ) -> None:
        self.error_code = error_code
        self.http_status = http_status
        self.safe_code = (
            f"{error_code}_{http_status}"
            if http_status is not None
            else error_code
        )
        super().__init__(self.safe_code)


@dataclass(frozen=True)
class LlmTransportResult:
    payload: Mapping[str, object]
    model_name: str
    model_version: str | None
    provider_request_id: str | None
    raw_response_hash: str


JsonClient = Callable[[Mapping[str, object]], Mapping[str, object]]
ThinkingMode = Literal["enabled", "disabled", "auto"]


def analysis_timeout_sec() -> int:
    """Return the validated whole-analysis timeout budget in seconds."""

    raw = os.environ.get(ANALYSIS_TIMEOUT_ENV_VAR)
    if raw is None or not raw.isdecimal():
        return DEFAULT_ANALYSIS_TIMEOUT_SEC
    value = int(raw)
    if not MIN_ANALYSIS_TIMEOUT_SEC <= value <= MAX_ANALYSIS_TIMEOUT_SEC:
        return DEFAULT_ANALYSIS_TIMEOUT_SEC
    return value


def _validated_base_url(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AiConfigValidationError("base_url", "missing_url")
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.username is not None or parsed.password is not None:
        raise AiConfigValidationError(
            "base_url",
            "credentials_not_allowed",
        )
    is_loopback_http = (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    )
    if parsed.scheme != "https" and not is_loopback_http:
        raise AiConfigValidationError("base_url", "insecure_url")
    if not parsed.hostname or parsed.query or parsed.fragment:
        raise AiConfigValidationError("base_url", "invalid_url")
    return normalized


def _chat_url() -> str:
    base_url = _validated_base_url(
        os.environ.get(ARK_BASE_URL_ENV_VAR, DEFAULT_ARK_BASE_URL)
    )
    if base_url.endswith("/chat/completions"):
        return base_url
    return f"{base_url}/chat/completions"


def _parse_json_content(content: str) -> Mapping[str, object]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("LLM response must be a JSON object.")
    return parsed


def _response_was_truncated(response: Mapping[str, object]) -> bool:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return False
    first = choices[0]
    return (
        isinstance(first, Mapping)
        and first.get("finish_reason") == "length"
    )


def _provider_payload(response: Mapping[str, object]) -> Mapping[str, object]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return response
    first = choices[0]
    if not isinstance(first, Mapping):
        raise ValueError("LLM response choice must be an object.")
    message = first.get("message")
    if not isinstance(message, Mapping):
        raise ValueError("LLM response message must be an object.")
    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError("LLM response content must be text.")
    return _parse_json_content(content)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def provider_json_client(
    request_body: Mapping[str, object],
    *,
    timeout_sec: int = REQUEST_TIMEOUT_SEC,
) -> Mapping[str, object]:
    """Perform one provider request without exposing credentials or bodies."""

    if (
        not isinstance(timeout_sec, int)
        or isinstance(timeout_sec, bool)
        or timeout_sec <= 0
    ):
        raise ValueError("timeout_sec must be a positive integer.")
    load_ai_config()
    api_key = os.environ.get(ARK_API_KEY_ENV_VAR)
    if not api_key:
        raise AiNotConfiguredError("AI provider is not configured.")
    request = urllib.request.Request(
        _chat_url(),
        data=json.dumps(
            request_body,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    decoded: object = None
    provider_failure: LlmTransportError | None = None
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout_sec,
        ) as response:
            decoded = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        provider_failure = LlmTransportError(
            "provider_http_error",
            http_status=status,
        )
    except urllib.error.URLError as error:
        if isinstance(error.reason, (TimeoutError, socket.timeout)):
            provider_failure = LlmTransportError("provider_timeout")
        else:
            provider_failure = LlmTransportError(
                "provider_network_error"
            )
    except (TimeoutError, socket.timeout):
        provider_failure = LlmTransportError("provider_timeout")
    except (UnicodeDecodeError, json.JSONDecodeError):
        provider_failure = LlmTransportError(
            "provider_response_invalid"
        )
    if provider_failure is not None:
        raise provider_failure
    if not isinstance(decoded, Mapping):
        raise LlmTransportError("provider_response_invalid")
    return decoded


def chat_json(
    *,
    system_prompt: str,
    user_payload: Mapping[str, object],
    client: JsonClient | None = None,
    token_budgets: Sequence[int] = STRUCTURED_OUTPUT_TOKEN_BUDGETS,
    thinking_mode: ThinkingMode | None = None,
    json_mode: bool = False,
) -> LlmTransportResult:
    """Send a bounded deterministic JSON chat request and parse JSON."""

    budgets = tuple(token_budgets)
    if not budgets or any(
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
        for value in budgets
    ):
        raise ValueError("token_budgets must contain positive integers")
    if thinking_mode not in {None, "enabled", "disabled", "auto"}:
        raise ValueError("thinking_mode is invalid")
    if not isinstance(json_mode, bool):
        raise ValueError("json_mode must be boolean")
    if client is None:
        load_ai_config()
    model_name = os.environ.get(ARK_MODEL_ENV_VAR, DEFAULT_ARK_MODEL)
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": json.dumps(
                user_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        },
    ]
    raw: Mapping[str, object] | None = None
    for max_tokens in budgets:
        request_body: dict[str, object] = {
            "model": model_name,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        if thinking_mode is not None:
            request_body["thinking"] = {"type": thinking_mode}
        if json_mode:
            request_body["response_format"] = {"type": "json_object"}
        if client is not None:
            candidate = client(request_body)
            if not isinstance(candidate, Mapping):
                raise ValueError("LLM client response must be an object.")
            raw = candidate
        else:
            raw = provider_json_client(
                request_body,
                timeout_sec=REQUEST_TIMEOUT_SEC,
            )
        if not _response_was_truncated(raw):
            break
    else:
        raise LlmTransportError("provider_response_truncated")

    if raw is None:
        raise AssertionError("structured provider call produced no response")

    response_failure: LlmTransportError | None = None
    payload: Mapping[str, object] = {}
    raw_response_hash = ""
    try:
        payload = _provider_payload(raw)
        raw_response_hash = sha256_json(raw)
    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        response_failure = LlmTransportError(
            "provider_response_invalid"
        )
    if response_failure is not None:
        raise response_failure
    response_model = _optional_string(raw.get("model"))
    provider_request_id = _optional_string(
        raw.get("id") or raw.get("request_id")
    )
    model_version = _optional_string(raw.get("model_version"))
    return LlmTransportResult(
        payload=payload,
        model_name=response_model or model_name,
        model_version=model_version,
        provider_request_id=provider_request_id,
        raw_response_hash=raw_response_hash,
    )


def ai_config_status() -> dict[str, object]:
    load_ai_config()
    key = os.environ.get(ARK_API_KEY_ENV_VAR, "")
    return {
        "status": "success",
        "base_url": os.environ.get(
            ARK_BASE_URL_ENV_VAR,
            DEFAULT_ARK_BASE_URL,
        ),
        "model": os.environ.get(ARK_MODEL_ENV_VAR, DEFAULT_ARK_MODEL),
        "api_key_configured": bool(key),
        "api_key_preview": (
            f"...{key[-6:]}"
            if len(key) > 6
            else "configured"
            if key
            else ""
        ),
    }


def save_ai_config(config: Mapping[str, object]) -> None:
    path = _ai_config_path()
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        mode = None
    if mode is not None and not stat.S_ISREG(mode):
        raise AiConfigValidationError(
            "$",
            "invalid_destination",
            "AI config destination must be a regular file.",
        )

    current = _read_ai_config()
    environment_updates: dict[str, str | None] = {}
    base_url = config.get("base_url")
    if isinstance(base_url, str) and base_url.strip():
        validated = _validated_base_url(base_url)
        current[ARK_BASE_URL_ENV_VAR] = validated
        environment_updates[ARK_BASE_URL_ENV_VAR] = validated

    model = config.get("model")
    if isinstance(model, str) and model.strip():
        current[ARK_MODEL_ENV_VAR] = model.strip()
        environment_updates[ARK_MODEL_ENV_VAR] = model.strip()

    if config.get("clear_api_key") is True:
        current.pop(ARK_API_KEY_ENV_VAR, None)
        environment_updates[ARK_API_KEY_ENV_VAR] = None
    else:
        api_key = config.get("api_key")
        if isinstance(api_key, str) and api_key.strip():
            current[ARK_API_KEY_ENV_VAR] = api_key.strip()
            environment_updates[ARK_API_KEY_ENV_VAR] = api_key.strip()

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic_write_json(path, current)
    for env_var, value in environment_updates.items():
        if value is None:
            os.environ.pop(env_var, None)
        else:
            os.environ[env_var] = value


def load_ai_config() -> None:
    for env_var, value in _read_ai_config().items():
        if value and not os.environ.get(env_var):
            os.environ[env_var] = value


def _read_ai_config() -> dict[str, str]:
    path = _ai_config_path()
    try:
        mode = path.lstat().st_mode
    except (FileNotFoundError, OSError):
        return {}
    if not stat.S_ISREG(mode):
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, str] = {}
    for key, value in raw.items():
        if (
            key
            not in {
                ARK_BASE_URL_ENV_VAR,
                ARK_MODEL_ENV_VAR,
                ARK_API_KEY_ENV_VAR,
            }
            or not isinstance(value, str)
            or not value.strip()
        ):
            continue
        if key == ARK_BASE_URL_ENV_VAR:
            try:
                value = _validated_base_url(value)
            except ValueError:
                continue
        result[key] = value.strip()
    return result


def _ai_config_path() -> Path:
    configured_path = os.environ.get(AI_CONFIG_PATH_ENV_VAR)
    if configured_path:
        return Path(configured_path).expanduser()
    if os.environ.get(LOG_DIR_ENV_VAR):
        return resolve_log_root() / AI_CONFIG_FILENAME
    if BLUEDOT_WORKSPACE_CODE_DIR.is_dir():
        return (
            BLUEDOT_WORKSPACE_CODE_DIR
            / BLUEDOT_AI_CONFIG_DIRNAME
            / AI_CONFIG_FILENAME
        )
    return resolve_log_root() / AI_CONFIG_FILENAME
