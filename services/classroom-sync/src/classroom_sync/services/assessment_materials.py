"""Verified, source-free projection of private teacher assessment materials."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from hashlib import sha256
from typing import Annotated, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import PydanticCustomError

from classroom_sync.auth.fincolab import Principal
from classroom_sync.canonical import sha256_json
from classroom_sync.errors import UpstreamContractError

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", strict=True)]
Identifier = Annotated[str, Field(min_length=1, max_length=200, strict=True)]
DisplayName = Annotated[str, Field(min_length=1, max_length=500, strict=True)]
PublicMessage = Annotated[str, Field(min_length=1, max_length=500, strict=True)]
MaterialDiagnosticCode = Literal["undeclared_identifier", "protected_source_compile_error"]

_MAX_PRIVATE_MESSAGE_LENGTH = 10_000
_ISSUE_PUBLIC_MESSAGES = {
    "starter_source_non_utf8_confirmation_required": "源框架需要教师确认 UTF-8 候选副本后才能发布。",
    "starter_source_protected_compile_error": "受保护的源框架未通过编译预检。",
    "detector_profile_unavailable": "所需的专用检测器当前不可用。",
    "teacher_dimension_not_student_responsibility": "教师维度包含非学生编辑责任。",
    "teacher_dimension_outside_task": "教师维度包含题目任务范围外的内容。",
    "required_student_dimension_missing": "教师维度缺少学生必须完成的任务。",
    "boundary_coverage_incomplete": "教师测试的边界场景覆盖尚不完整。",
}


class _StrictImmutableModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("schema_version", mode="before", check_fields=False)
    @classmethod
    def _require_integer_schema_version(cls, value: object) -> object:
        if not isinstance(value, int) or isinstance(value, bool):
            raise PydanticCustomError(
                "assessment_material_schema_version_type",
                "schema_version must be an integer",
            )
        return value


class MaterialDiagnostic(_StrictImmutableModel):
    code: MaterialDiagnosticCode
    line: Annotated[int, Field(ge=0, le=1_000_000, strict=True)]
    column: Annotated[int, Field(ge=0, le=1_000_000, strict=True)]
    message: PublicMessage


class MaterialPreflight(_StrictImmutableModel):
    status: Literal["ready", "blocked", "unavailable"]
    accepted_editable_symbols: Annotated[tuple[DisplayName, ...], Field(max_length=128)]
    diagnostics: Annotated[tuple[MaterialDiagnostic, ...], Field(max_length=128)]


class StarterSourceCandidate(_StrictImmutableModel):
    artifact_id: Identifier
    display_name: DisplayName
    file_name: Annotated[
        str,
        Field(min_length=1, max_length=255, pattern=r"^[^/\\\x00]+$", strict=True),
    ]
    sha256: Hash
    size_bytes: Annotated[int, Field(ge=0, le=1_048_576, strict=True)]
    source: Literal["fincolab_experiment"]
    detected_encoding: Literal["utf-8", "gb18030"]
    utf8_candidate_sha256: Hash
    utf8_confirmed: Annotated[bool, Field(strict=True)]
    preflight: MaterialPreflight


class MaterialRequirement(_StrictImmutableModel):
    id: Identifier
    name: DisplayName
    source_statement: Annotated[str, Field(min_length=1, max_length=5_000, strict=True)]
    student_responsibility: Annotated[bool, Field(strict=True)]
    test_ids: Annotated[tuple[Identifier, ...], Field(max_length=128)]
    detector_profile_ids: Annotated[tuple[Identifier, ...], Field(max_length=128)]


class MaterialAssessmentTest(_StrictImmutableModel):
    id: Identifier
    name: DisplayName
    kind: Literal["stdin_stdout"]
    input: Annotated[str, Field(max_length=100_000, strict=True)]
    expected_stdout: Annotated[str, Field(max_length=100_000, strict=True)]
    comparison: Literal["normalized_text_v1"]
    timeout_ms: Annotated[int, Field(ge=1, le=60_000, strict=True)]
    enabled: Annotated[bool, Field(strict=True)]
    source: Literal["teacher"]
    order: Annotated[int, Field(ge=0, le=10_000, strict=True)]
    content_hash: Hash


class DetectorProfileAvailability(_StrictImmutableModel):
    id: Identifier
    available: Annotated[bool, Field(strict=True)]


MaterialIssueCode = Literal[
    "starter_source_non_utf8_confirmation_required",
    "starter_source_protected_compile_error",
    "detector_profile_unavailable",
    "teacher_dimension_not_student_responsibility",
    "teacher_dimension_outside_task",
    "required_student_dimension_missing",
    "boundary_coverage_incomplete",
]


class MaterialIssue(_StrictImmutableModel):
    code: MaterialIssueCode
    severity: Literal["blocking", "warning"]
    scope: Literal["classroom", "source", "requirement", "test"]
    requirement_id: Identifier | None
    message: PublicMessage


class AssessmentMaterialBundle(_StrictImmutableModel):
    schema_version: Literal[1]
    space_id: Identifier
    parent_algorithm_id: Identifier
    title: Annotated[str, Field(min_length=1, max_length=200, strict=True)]
    statement: Annotated[str, Field(min_length=1, max_length=10_000, strict=True)]
    starter_source: StarterSourceCandidate | None
    requirements: Annotated[tuple[MaterialRequirement, ...], Field(max_length=128)]
    assessment_tests: Annotated[tuple[MaterialAssessmentTest, ...], Field(max_length=128)]
    detector_profiles: Annotated[
        tuple[DetectorProfileAvailability, ...], Field(max_length=128)
    ]
    issues: Annotated[tuple[MaterialIssue, ...], Field(max_length=256)]
    bundle_hash: Hash


class _PrivateMaterialDiagnostic(_StrictImmutableModel):
    code: MaterialDiagnosticCode
    line: Annotated[int, Field(ge=0, le=1_000_000, strict=True)]
    column: Annotated[int, Field(ge=0, le=1_000_000, strict=True)]
    message: Annotated[
        str,
        Field(min_length=1, max_length=_MAX_PRIVATE_MESSAGE_LENGTH, strict=True),
    ]


class _PrivateMaterialPreflight(_StrictImmutableModel):
    status: Literal["ready", "blocked", "unavailable"]
    accepted_editable_symbols: Annotated[tuple[DisplayName, ...], Field(max_length=128)]
    diagnostics: Annotated[tuple[_PrivateMaterialDiagnostic, ...], Field(max_length=128)]


class _PrivateStarterSourceCandidate(_StrictImmutableModel):
    artifact_id: Identifier
    display_name: DisplayName
    file_name: Annotated[
        str,
        Field(min_length=1, max_length=255, pattern=r"^[^/\\\x00]+$", strict=True),
    ]
    sha256: Hash
    size_bytes: Annotated[int, Field(ge=0, le=1_048_576, strict=True)]
    source: Literal["fincolab_experiment"]
    detected_encoding: Literal["utf-8", "gb18030"]
    utf8_candidate_sha256: Hash
    utf8_confirmed: Annotated[bool, Field(strict=True)]
    preflight: _PrivateMaterialPreflight
    content_base64: Annotated[str, Field(min_length=1, max_length=1_400_000, strict=True)]


class _PrivateMaterialIssue(_StrictImmutableModel):
    code: MaterialIssueCode
    severity: Literal["blocking", "warning"]
    scope: Literal["classroom", "source", "requirement", "test"]
    requirement_id: Identifier | None
    message: Annotated[
        str,
        Field(min_length=1, max_length=_MAX_PRIVATE_MESSAGE_LENGTH, strict=True),
    ]


class _PrivateAssessmentMaterialBundle(_StrictImmutableModel):
    schema_version: Literal[1]
    importer_version: Literal["cpp_assessment_material_importer_v1"]
    space_id: Identifier
    parent_algorithm_id: Identifier
    title: Annotated[str, Field(min_length=1, max_length=200, strict=True)]
    statement: Annotated[str, Field(min_length=1, max_length=10_000, strict=True)]
    toolchain_profile: Literal["cpp17_stdio_v1"]
    starter_source: _PrivateStarterSourceCandidate | None
    requirements: Annotated[tuple[MaterialRequirement, ...], Field(max_length=128)]
    assessment_tests: Annotated[tuple[MaterialAssessmentTest, ...], Field(max_length=128)]
    detector_profiles: Annotated[
        tuple[DetectorProfileAvailability, ...], Field(max_length=128)
    ]
    issues: Annotated[tuple[_PrivateMaterialIssue, ...], Field(max_length=256)]
    bundle_hash: Hash


class AssessmentMaterialGateway(Protocol):
    def get_bundle(
        self,
        principal: Principal,
        space_id: str,
        parent_algorithm_id: str,
    ) -> Mapping[str, object]: ...


class AssessmentMaterialService:
    """Validate private bytes and expose only a closed, immutable public bundle."""

    def __init__(self, gateway: AssessmentMaterialGateway) -> None:
        self._gateway = gateway

    def get_bundle(
        self,
        principal: Principal,
        space_id: str,
        parent_algorithm_id: str,
    ) -> AssessmentMaterialBundle:
        raw_payload = self._gateway.get_bundle(principal, space_id, parent_algorithm_id)
        try:
            private = _PrivateAssessmentMaterialBundle.model_validate(raw_payload)
            if (
                private.space_id != space_id
                or private.parent_algorithm_id != parent_algorithm_id
            ):
                raise ValueError("material identity mismatch")
            self._validate_hashes(private)
            public_without_hash = self._public_payload(private)
            return AssessmentMaterialBundle.model_validate(
                {
                    **public_without_hash,
                    "bundle_hash": sha256_json(public_without_hash),
                }
            )
        except (
            PydanticValidationError,
            ValueError,
            TypeError,
            UnicodeError,
            binascii.Error,
        ) as error:
            raise UpstreamContractError("assessment_materials_contract_invalid") from error

    @staticmethod
    def _validate_hashes(bundle: _PrivateAssessmentMaterialBundle) -> None:
        sealed_payload = bundle.model_dump(mode="json", exclude={"bundle_hash"})
        sealed_starter = cast(dict[str, object] | None, sealed_payload.get("starter_source"))
        if sealed_starter is not None:
            sealed_starter.pop("content_base64")
        sealed_bytes = json.dumps(
            sealed_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if sha256(sealed_bytes).hexdigest() != bundle.bundle_hash:
            raise ValueError("private material bundle hash mismatch")

        starter = bundle.starter_source
        if starter is not None:
            source_bytes = base64.b64decode(starter.content_base64, validate=True)
            if len(source_bytes) != starter.size_bytes:
                raise ValueError("starter source size mismatch")
            if sha256(source_bytes).hexdigest() != starter.sha256:
                raise ValueError("starter source hash mismatch")
            source_text = source_bytes.decode(starter.detected_encoding)
            if sha256(source_text.encode("utf-8")).hexdigest() != starter.utf8_candidate_sha256:
                raise ValueError("starter UTF-8 candidate hash mismatch")

        for assessment_test in bundle.assessment_tests:
            test_payload = assessment_test.model_dump(mode="json", exclude={"content_hash"})
            if sha256_json(test_payload) != assessment_test.content_hash:
                raise ValueError("assessment test hash mismatch")

    @classmethod
    def _public_payload(cls, bundle: _PrivateAssessmentMaterialBundle) -> dict[str, object]:
        starter_source: dict[str, object] | None = None
        if bundle.starter_source is not None:
            private_starter = bundle.starter_source
            starter_source = {
                "artifact_id": private_starter.artifact_id,
                "display_name": private_starter.display_name,
                "file_name": private_starter.file_name,
                "sha256": private_starter.sha256,
                "size_bytes": private_starter.size_bytes,
                "source": private_starter.source,
                "detected_encoding": private_starter.detected_encoding,
                "utf8_candidate_sha256": private_starter.utf8_candidate_sha256,
                "utf8_confirmed": private_starter.utf8_confirmed,
                "preflight": {
                    "status": private_starter.preflight.status,
                    "accepted_editable_symbols": (
                        private_starter.preflight.accepted_editable_symbols
                    ),
                    "diagnostics": tuple(
                        {
                            "code": diagnostic.code,
                            "line": diagnostic.line,
                            "column": diagnostic.column,
                            "message": cls._diagnostic_message(
                                diagnostic.line, diagnostic.column
                            ),
                        }
                        for diagnostic in private_starter.preflight.diagnostics
                    ),
                },
            }
        return {
            "schema_version": 1,
            "space_id": bundle.space_id,
            "parent_algorithm_id": bundle.parent_algorithm_id,
            "title": bundle.title,
            "statement": bundle.statement,
            "starter_source": starter_source,
            "requirements": tuple(item.model_dump(mode="json") for item in bundle.requirements),
            "assessment_tests": tuple(
                item.model_dump(mode="json") for item in bundle.assessment_tests
            ),
            "detector_profiles": tuple(
                item.model_dump(mode="json") for item in bundle.detector_profiles
            ),
            "issues": tuple(
                {
                    **item.model_dump(mode="json", exclude={"message"}),
                    "message": _ISSUE_PUBLIC_MESSAGES[item.code],
                }
                for item in bundle.issues
            ),
        }

    @staticmethod
    def _diagnostic_message(line: int, column: int) -> str:
        return f"材料预检发现受保护源框架问题（第 {line} 行，第 {column} 列）。"
