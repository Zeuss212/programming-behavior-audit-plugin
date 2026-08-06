"""Shared closed-envelope behavior for authenticated JSON APIs."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping

from jupyter_server.base.handlers import APIHandler, JupyterHandler
from tornado import web


class ApiRequestError(ValueError):
    """A request failure containing only API-safe response fields."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        details: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.details = dict(details) if details is not None else None


class JsonAPIHandler(APIHandler):
    """API handler with stable request IDs and frozen JSON envelopes."""

    async def prepare(self) -> None:
        try:
            await JupyterHandler.prepare(self, _redirect_to_login=False)
            if not self.check_origin():
                raise web.HTTPError(404)
        except web.HTTPError as error:
            if error.status_code in {401, 403}:
                self.finish_error(
                    403,
                    "forbidden",
                    "没有权限访问此接口。",
                )
                return
            raise

    def request_id(self) -> str:
        value = getattr(self, "_request_id", None)
        if value is None:
            value = str(uuid.uuid4())
            self._request_id = value
        return value

    def read_json_object(
        self,
        *,
        max_bytes: int = 1_048_576,
    ) -> dict[str, object]:
        if len(self.request.body or b"") > max_bytes:
            raise ApiRequestError(
                413,
                "request_too_large",
                "请求内容过大。",
            )
        try:
            body = (self.request.body or b"").decode("utf-8", errors="strict")
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ApiRequestError(
                400,
                "invalid_json",
                "请求内容不是有效的 JSON。",
            ) from error
        if not isinstance(value, dict):
            raise ApiRequestError(
                400,
                "invalid_json_object",
                "请求必须是 JSON 对象。",
            )
        return value

    def finish_json(
        self,
        payload: Mapping[str, object],
        status: int = 200,
    ) -> None:
        body = dict(payload)
        body.setdefault("schema_version", 1)
        body["request_id"] = self.request_id()
        self.set_status(status)
        self.finish(body)

    def finish_error(
        self,
        status: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: Mapping[str, str] | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "schema_version": 1,
            "request_id": self.request_id(),
            "code": code,
            "message": message,
            "retryable": retryable,
        }
        if details is not None:
            payload["details"] = dict(details)
        self.set_status(status)
        self.finish(payload)

    def write_error(self, status_code: int, **kwargs: object) -> None:
        if status_code in {401, 403}:
            self.finish_error(
                403,
                "forbidden",
                "没有权限访问此接口。",
            )
        elif status_code == 404:
            self.finish_error(
                404,
                "not_found",
                "未找到请求的资源。",
            )
        elif status_code == 409:
            self.finish_error(
                409,
                "conflict",
                "请求与当前资源状态冲突。",
            )
        elif status_code == 413:
            self.finish_error(
                413,
                "request_too_large",
                "请求内容过大。",
            )
        elif status_code == 422:
            self.finish_error(
                422,
                "invalid_request",
                "请求内容未通过校验。",
            )
        elif status_code == 429:
            self.finish_error(
                429,
                "rate_limited",
                "请求过于频繁。",
                retryable=True,
            )
        elif status_code >= 500:
            self.finish_error(
                status_code,
                "internal_error",
                "服务器暂时无法处理请求。",
            )
        else:
            self.finish_error(
                status_code,
                "http_error",
                "请求无法处理。",
            )
