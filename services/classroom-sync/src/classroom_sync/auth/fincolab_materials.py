"""Bounded FinColab adapter for the one approved assessment-material endpoint."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from classroom_sync.auth.fincolab import Principal
from classroom_sync.errors import (
    AuthenticationError,
    AuthorizationError,
    UpstreamContractError,
    UpstreamUnavailableError,
)


class FincolabAssessmentMaterialGateway:
    """Fetch a private material bundle without accepting a caller URL or path."""

    def __init__(
        self,
        *,
        base_url: str,
        client: httpx.Client,
        max_response_bytes: int = 1_048_576,
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("FinColab base URL must be an HTTP origin.")
        if max_response_bytes <= 0:
            raise ValueError("Material response limit must be positive.")
        self._origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        self._client = client
        self._max_response_bytes = max_response_bytes

    def get_bundle(
        self,
        principal: Principal,
        space_id: str,
        parent_algorithm_id: str,
    ) -> Mapping[str, object]:
        if space_id in {".", ".."} or parent_algorithm_id in {".", ".."}:
            raise UpstreamContractError("assessment_materials_contract_invalid")
        path = (
            f"/v1/spaces/{quote(space_id, safe='')}/algorithm_development/"
            f"{quote(parent_algorithm_id, safe='')}/assessment_materials"
        )
        try:
            with self._client.stream(
                "GET",
                f"{self._origin}{path}",
                headers={
                    "Accept-Encoding": "identity",
                    "Authorization": f"Bearer {principal.bearer_token}",
                },
            ) as response:
                self._raise_for_status(response)
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared_length = int(content_length)
                    except ValueError as error:
                        raise UpstreamContractError(
                            "assessment_materials_contract_invalid"
                        ) from error
                    if declared_length < 0 or declared_length > self._max_response_bytes:
                        raise UpstreamContractError("assessment_materials_contract_invalid")

                body = bytearray()
                chunk_size = min(64 * 1024, self._max_response_bytes + 1)
                for chunk in response.iter_bytes(chunk_size=chunk_size):
                    if len(chunk) > self._max_response_bytes - len(body):
                        raise UpstreamContractError("assessment_materials_contract_invalid")
                    body.extend(chunk)
        except httpx.RequestError as error:
            raise UpstreamUnavailableError(
                "assessment_materials_upstream_unavailable"
            ) from error

        try:
            decoded: object = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise UpstreamContractError("assessment_materials_contract_invalid") from error
        if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
            raise UpstreamContractError("assessment_materials_contract_invalid")
        payload = cast(dict[str, object], decoded)
        nested = payload.get("data")
        if nested is not None:
            if not isinstance(nested, dict) or not all(isinstance(key, str) for key in nested):
                raise UpstreamContractError("assessment_materials_contract_invalid")
            payload = cast(dict[str, object], nested)
        schema_version = payload.get("schema_version")
        if not isinstance(schema_version, int) or isinstance(schema_version, bool):
            raise UpstreamContractError("assessment_materials_contract_invalid")
        if schema_version != 1:
            raise UpstreamContractError("assessment_materials_contract_invalid")
        return payload

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code == 401:
            raise AuthenticationError("upstream_unauthorized")
        if response.status_code == 403:
            raise AuthorizationError("upstream_forbidden")
        if response.status_code >= 500:
            raise UpstreamUnavailableError("assessment_materials_upstream_unavailable")
        if not response.is_success:
            raise UpstreamContractError("assessment_materials_upstream_rejected")
