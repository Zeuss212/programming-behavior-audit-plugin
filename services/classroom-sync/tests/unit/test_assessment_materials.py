"""Assessment material validation and FinColab adapter security boundaries."""

from __future__ import annotations

import base64
import gzip
import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import cast

import httpx
import pytest
from pydantic import ValidationError as PydanticValidationError

from classroom_sync.auth.fincolab import Principal
from classroom_sync.auth.fincolab_materials import FincolabAssessmentMaterialGateway
from classroom_sync.canonical import sha256_json
from classroom_sync.errors import UpstreamContractError, UpstreamUnavailableError
from classroom_sync.services.assessment_materials import (
    AssessmentMaterialBundle,
    AssessmentMaterialService,
)

ROOT = Path(__file__).resolve().parents[4]
MATERIALS = ROOT / "deploy" / "classroom" / "local-demo" / "materials"
MATERIAL_FIXTURES = (
    (
        MATERIALS / "sequence-list" / "bundle.json",
        MATERIALS / "sequence-list" / "顺序表操作练习01.cpp",
    ),
    (
        MATERIALS / "linked-list" / "bundle.json",
        MATERIALS / "linked-list" / "链表操作练习02.cpp",
    ),
)


def private_payload(bundle_path: Path, source_path: Path) -> dict[str, object]:
    payload = cast(
        dict[str, object],
        json.loads(bundle_path.read_text(encoding="utf-8")),
    )
    starter = cast(dict[str, object], payload["starter_source"])
    starter["content_base64"] = base64.b64encode(source_path.read_bytes()).decode("ascii")
    return payload


def importer_private_hash(payload: dict[str, object]) -> str:
    sealed = deepcopy(payload)
    sealed.pop("bundle_hash")
    starter = cast(dict[str, object] | None, sealed.get("starter_source"))
    if starter is not None:
        starter.pop("content_base64", None)
    encoded = json.dumps(
        sealed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def streamed_json_response(value: object, *, status_code: int = 200) -> httpx.Response:
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return httpx.Response(
        status_code,
        headers={"Content-Type": "application/json"},
        stream=httpx.ByteStream(body),
    )


class StaticGateway:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[tuple[Principal, str, str]] = []

    def get_bundle(
        self,
        principal: Principal,
        space_id: str,
        parent_algorithm_id: str,
    ) -> dict[str, object]:
        self.calls.append((principal, space_id, parent_algorithm_id))
        return deepcopy(self.payload)


@pytest.mark.parametrize(("bundle_path", "source_path"), MATERIAL_FIXTURES)
def test_service_validates_real_artifacts_and_recomputes_public_bundle_hash(
    bundle_path: Path,
    source_path: Path,
) -> None:
    payload = private_payload(bundle_path, source_path)
    private_hash = cast(str, payload["bundle_hash"])
    gateway = StaticGateway(payload)
    principal = Principal("teacher-1", "teacher-a", "teacher-token")

    bundle = AssessmentMaterialService(gateway).get_bundle(
        principal,
        cast(str, payload["space_id"]),
        cast(str, payload["parent_algorithm_id"]),
    )

    public_payload = bundle.model_dump(mode="json")
    public_without_hash = {key: value for key, value in public_payload.items() if key != "bundle_hash"}
    assert bundle.bundle_hash == sha256_json(public_without_hash)
    assert bundle.bundle_hash != private_hash
    assert bundle.starter_source is not None
    assert bundle.starter_source.sha256 == sha256(source_path.read_bytes()).hexdigest()
    assert "content_base64" not in json.dumps(public_payload, ensure_ascii=False)
    assert "importer_version" not in public_payload
    assert "toolchain_profile" not in public_payload
    assert gateway.calls == [
        (
            principal,
            cast(str, payload["space_id"]),
            cast(str, payload["parent_algorithm_id"]),
        )
    ]


def test_public_models_are_strict_and_immutable() -> None:
    bundle_path, source_path = MATERIAL_FIXTURES[1]
    bundle = AssessmentMaterialService(StaticGateway(private_payload(bundle_path, source_path))).get_bundle(
        Principal("teacher-1", "teacher-a", "teacher-token"),
        "course-001",
        "linked-list-experiment-002",
    )
    payload = bundle.model_dump(mode="json")

    with pytest.raises(PydanticValidationError):
        AssessmentMaterialBundle.model_validate({**payload, "private_adapter_state": "hidden"})
    with pytest.raises(PydanticValidationError):
        AssessmentMaterialBundle.model_validate({**payload, "schema_version": True})
    with pytest.raises(PydanticValidationError):
        bundle.title = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("corruption", ["artifact", "test"])
def test_service_rejects_artifact_or_test_hash_drift(corruption: str) -> None:
    bundle_path, source_path = MATERIAL_FIXTURES[1]
    payload = private_payload(bundle_path, source_path)
    if corruption == "artifact":
        starter = cast(dict[str, object], payload["starter_source"])
        starter["content_base64"] = base64.b64encode(b"changed source").decode("ascii")
    else:
        tests = cast(list[dict[str, object]], payload["assessment_tests"])
        tests[0]["expected_stdout"] = "tampered"
        payload["bundle_hash"] = importer_private_hash(payload)

    with pytest.raises(UpstreamContractError, match="assessment_materials_contract_invalid"):
        AssessmentMaterialService(StaticGateway(payload)).get_bundle(
            Principal("teacher-1", "teacher-a", "teacher-token"),
            "course-001",
            "linked-list-experiment-002",
        )


def test_service_rejects_private_bundle_seal_drift() -> None:
    bundle_path, source_path = MATERIAL_FIXTURES[1]
    payload = private_payload(bundle_path, source_path)
    payload["title"] = "tampered after importer sealing"

    with pytest.raises(UpstreamContractError, match="assessment_materials_contract_invalid"):
        AssessmentMaterialService(StaticGateway(payload)).get_bundle(
            Principal("teacher-1", "teacher-a", "teacher-token"),
            "course-001",
            "linked-list-experiment-002",
        )


def test_service_bounds_and_sanitizes_public_diagnostic_messages() -> None:
    bundle_path, source_path = MATERIAL_FIXTURES[0]
    payload = private_payload(bundle_path, source_path)
    issues = cast(list[dict[str, object]], payload["issues"])
    issues[0]["message"] = (
        "```cpp /Users/teacher/private.cpp https://private.example/source "
        "/private/var/source.cpp /tmp/source.cpp /workspace/source.cpp "
        "Authorization: Bearer secret sk-private-credential-123 "
        "#include <iostream> int main() { return 0; } "
        + ("诊断" * 500)
    )
    starter = cast(dict[str, object], payload["starter_source"])
    preflight = cast(dict[str, object], starter["preflight"])
    diagnostics = cast(list[dict[str, object]], preflight["diagnostics"])
    diagnostics[0]["message"] = (
        "C:\\private\\source.cpp s3://private-bucket/source ``` "
        "char password[] = \"plain-secret\";"
    )
    payload["bundle_hash"] = importer_private_hash(payload)

    bundle = AssessmentMaterialService(StaticGateway(payload)).get_bundle(
        Principal("teacher-1", "teacher-a", "teacher-token"),
        "course-001",
        "sequence-list-experiment-001",
    )

    messages = [issue.message for issue in bundle.issues]
    assert bundle.starter_source is not None
    messages.extend(item.message for item in bundle.starter_source.preflight.diagnostics)
    assert all(1 <= len(message) <= 500 for message in messages)
    public_json = json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False).lower()
    assert all(
        marker not in public_json
        for marker in (
            "```",
            "http://",
            "https://",
            "s3://",
            "/users/",
            "/private/",
            "/tmp/",
            "/workspace/",
            "authorization:",
            "sk-private-credential",
            "#include",
            "plain-secret",
        )
    )


def test_service_rejects_unapproved_diagnostic_code_before_public_projection() -> None:
    bundle_path, source_path = MATERIAL_FIXTURES[0]
    payload = private_payload(bundle_path, source_path)
    starter = cast(dict[str, object], payload["starter_source"])
    preflight = cast(dict[str, object], starter["preflight"])
    diagnostics = cast(list[dict[str, object]], preflight["diagnostics"])
    diagnostics[0]["code"] = "sk-private-credential-in-code"
    payload["bundle_hash"] = importer_private_hash(payload)

    with pytest.raises(UpstreamContractError, match="assessment_materials_contract_invalid"):
        AssessmentMaterialService(StaticGateway(payload)).get_bundle(
            Principal("teacher-1", "teacher-a", "teacher-token"),
            "course-001",
            "sequence-list-experiment-001",
        )


def test_fincolab_gateway_uses_only_the_approved_encoded_endpoint_and_bearer() -> None:
    bundle_path, source_path = MATERIAL_FIXTURES[1]
    payload = private_payload(bundle_path, source_path)
    requests: list[httpx.Request] = []

    def responder(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return streamed_json_response({"data": payload})

    gateway = FincolabAssessmentMaterialGateway(
        base_url="https://fincolab.example/root?ignored=never",
        client=httpx.Client(transport=httpx.MockTransport(responder), timeout=10.0),
    )
    principal = Principal("teacher-1", "teacher-a", "resolved-token")

    result = gateway.get_bundle(principal, "space ?#1", "parent/one")

    assert result == payload
    assert len(requests) == 1
    assert requests[0].url == (
        "https://fincolab.example/v1/spaces/space%20%3F%231/"
        "algorithm_development/parent%2Fone/assessment_materials"
    )
    assert requests[0].headers["Authorization"] == "Bearer resolved-token"
    assert requests[0].headers["Accept-Encoding"] == "identity"


@pytest.mark.parametrize("unsafe_segment", [".", ".."])
def test_fincolab_gateway_rejects_rfc_dot_segments_before_request(
    unsafe_segment: str,
) -> None:
    requests: list[httpx.Request] = []

    def responder(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return streamed_json_response({"schema_version": 1})

    gateway = FincolabAssessmentMaterialGateway(
        base_url="https://fincolab.example",
        client=httpx.Client(transport=httpx.MockTransport(responder)),
    )

    with pytest.raises(UpstreamContractError, match="assessment_materials_contract_invalid"):
        gateway.get_bundle(
            Principal("teacher-1", "teacher-a", "resolved-token"),
            unsafe_segment,
            unsafe_segment,
        )
    assert requests == []


def test_fincolab_gateway_caps_response_before_json_validation() -> None:
    response_body = b'{"schema_version":1,"padding":"' + (b"x" * 1024) + b'"}'

    class RecordingChunkResponse(httpx.Response):
        requested_chunk_size: int | None = None

        def iter_raw(self, chunk_size: int | None = None):  # type: ignore[no-untyped-def]
            self.requested_chunk_size = chunk_size
            yield response_body

    upstream_response = RecordingChunkResponse(200)
    gateway = FincolabAssessmentMaterialGateway(
        base_url="https://fincolab.example",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _: upstream_response)
        ),
        max_response_bytes=1024,
    )

    with pytest.raises(UpstreamContractError, match="assessment_materials_contract_invalid"):
        gateway.get_bundle(
            Principal("teacher-1", "teacher-a", "resolved-token"),
            "space-1",
            "parent-1",
        )
    assert upstream_response.requested_chunk_size == 1025


def test_fincolab_gateway_rejects_encoded_response_before_decoding() -> None:
    requests: list[httpx.Request] = []
    encoded = gzip.compress(b'{"schema_version":1}')

    def responder(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            stream=httpx.ByteStream(encoded),
        )

    gateway = FincolabAssessmentMaterialGateway(
        base_url="https://fincolab.example",
        client=httpx.Client(transport=httpx.MockTransport(responder)),
        max_response_bytes=1024,
    )

    with pytest.raises(UpstreamContractError, match="assessment_materials_contract_invalid"):
        gateway.get_bundle(
            Principal("teacher-1", "teacher-a", "resolved-token"),
            "space-1",
            "parent-1",
        )
    assert requests[0].headers["Accept-Encoding"] == "identity"


@pytest.mark.parametrize(
    "body",
    (
        b'{"schema_version":1,"value":' + (b"9" * 5_000) + b"}",
        (b"[" * 1_500) + b"0" + (b"]" * 1_500),
    ),
    ids=("huge_integer", "excessive_depth"),
)
def test_fincolab_gateway_maps_bounded_json_parser_failures(body: bytes) -> None:
    gateway = FincolabAssessmentMaterialGateway(
        base_url="https://fincolab.example",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, stream=httpx.ByteStream(body))
            )
        ),
        max_response_bytes=10_000,
    )

    with pytest.raises(UpstreamContractError, match="assessment_materials_contract_invalid"):
        gateway.get_bundle(
            Principal("teacher-1", "teacher-a", "resolved-token"),
            "space-1",
            "parent-1",
        )


def test_fincolab_gateway_maps_json_recursion_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_at_parser_boundary(_body: object) -> object:
        raise RecursionError("private parser detail")

    monkeypatch.setattr(
        "classroom_sync.auth.fincolab_materials.json.loads",
        fail_at_parser_boundary,
    )
    gateway = FincolabAssessmentMaterialGateway(
        base_url="https://fincolab.example",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    stream=httpx.ByteStream(b'{"schema_version":1}'),
                )
            )
        ),
    )

    with pytest.raises(UpstreamContractError, match="assessment_materials_contract_invalid"):
        gateway.get_bundle(
            Principal("teacher-1", "teacher-a", "resolved-token"),
            "space-1",
            "parent-1",
        )


@pytest.mark.parametrize(
    ("responder", "error_type", "code"),
    (
        (
            lambda _request: (_ for _ in ()).throw(httpx.ConnectError("private detail")),
            UpstreamUnavailableError,
            "assessment_materials_upstream_unavailable",
        ),
        (
            lambda _request: streamed_json_response({"schema_version": 2}),
            UpstreamContractError,
            "assessment_materials_contract_invalid",
        ),
    ),
)
def test_fincolab_gateway_maps_transport_and_schema_failures_to_stable_codes(
    responder: object,
    error_type: type[Exception],
    code: str,
) -> None:
    transport = httpx.MockTransport(cast(httpx.MockTransport, responder))
    gateway = FincolabAssessmentMaterialGateway(
        base_url="https://fincolab.example",
        client=httpx.Client(transport=transport),
    )

    with pytest.raises(error_type, match=code):
        gateway.get_bundle(
            Principal("teacher-1", "teacher-a", "resolved-token"),
            "space-1",
            "parent-1",
        )
