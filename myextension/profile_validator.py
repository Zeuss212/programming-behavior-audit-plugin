"""Teacher-authored dimension profile validation for the guided Pilot flow."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from jsonschema import Draft202012Validator

from .canonical_json import sha256_json
from .schema_registry import schema_path, validate_schema


DEFAULT_CUSTOM_MINIMUM = {
    "valid_observation_duration_ms": 30000,
    "edit_event_count": 1,
}
BUILTIN_MINIMUMS = {
    "REPEATED_EDITING": {
        "valid_observation_duration_ms": 60000,
        "edit_event_count": 3,
    },
    "DEBUG_CHAIN": {"edit_event_count": 1, "run_count": 1},
    "REPEATED_RUN_FAILURES": {"run_count": 2},
    "PAUSE_WITHOUT_VALIDATION": {
        "valid_observation_duration_ms": 60000,
        "edit_event_count": 2,
    },
}
GUIDED_LEVELS = [
    {
        "code": "possible",
        "name": "可能出现",
        "definition": "存在相关行为证据，但范围或持续性有限",
    },
    {
        "code": "clear",
        "name": "明显出现",
        "definition": "在多个有效阶段持续出现相关行为",
    },
]
STIGMATIZING_TERMS = ("懒惰", "能力差", "笨", "焦虑症", "心理疾病")


class ProfileValidationError(ValueError):
    """A semantic profile error with a stable API-facing code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        field: str = "dimensions",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


def _input_shape_schema(schema_name: str) -> dict[str, Any]:
    schema = json.loads(schema_path(schema_name).read_text(encoding="utf-8"))
    dimension = schema["$defs"]["dimension"]
    if schema_name == "profile-draft-v1":
        dimension["properties"]["dimension_type"] = {
            "type": "string",
            "enum": ["behavioral_inference", "knowledge_inference"],
        }
    dimension["allOf"] = []
    mode = schema["$defs"]["analysis_config"]["properties"]["mode"]
    mode.pop("const", None)
    mode["type"] = "string"
    return schema


def _strip_strings(value: object) -> Any:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return [_strip_strings(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _strip_strings(item) for key, item in value.items()}
    return copy.deepcopy(value)


def _restore_assessment_test_io(
    normalized: dict[str, Any],
    payload: Mapping[str, object],
    *,
    expected_field: str,
) -> None:
    """Preserve exact teacher-authored test bytes after shape validation."""

    original_tests = payload["assessment_tests"]
    for normalized_test, original_test in zip(
        normalized["assessment_tests"],
        original_tests,
        strict=True,
    ):
        normalized_test["input"] = original_test["input"]
        normalized_test[expected_field] = original_test[expected_field]


def _reject_stigmatizing_language(dimension: Mapping[str, object]) -> None:
    texts = [dimension.get("name", "")]
    levels = dimension.get("levels", [])
    if isinstance(levels, list):
        texts.extend(
            level.get("definition", "")
            for level in levels
            if isinstance(level, Mapping)
        )
    if any(term in text for text in texts if isinstance(text, str) for term in STIGMATIZING_TERMS):
        raise ProfileValidationError(
            "stigmatizing_language",
            "Dimension names and definitions must describe observable behavior.",
        )


def _normalize_dimensions(
    dimensions: list[dict[str, Any]],
    *,
    knowledge_point_ids: set[str] | None = None,
) -> None:
    codes: set[str] = set()
    linked_points: set[str] = set()
    for dimension in dimensions:
        dimension_type = dimension.pop("dimension_type", "behavioral_inference")
        if dimension_type == "knowledge_inference":
            raise ProfileValidationError(
                "unsupported_guided_dimension_type",
                "knowledge_inference is unavailable in guided mode.",
            )

        linked_point = dimension.get("knowledge_point_id")
        if knowledge_point_ids is not None:
            if linked_point not in knowledge_point_ids:
                raise ProfileValidationError(
                    "unknown_knowledge_point_reference",
                    "A behavior dimension references an unknown knowledge point.",
                )
            if linked_point in linked_points:
                raise ProfileValidationError(
                    "duplicate_knowledge_point_dimension",
                    "Each knowledge point requires exactly one behavior dimension.",
                )
            linked_points.add(linked_point)

        code = dimension.get("code")
        if not code:
            code = f"CUSTOM_{uuid4().hex[:8].upper()}"
            dimension["code"] = code
        if code in codes:
            raise ProfileValidationError(
                "duplicate_dimension_code", f"Duplicate dimension code: {code}"
            )
        codes.add(code)

        criteria = dimension.get("evidence_criteria", [])
        support = [
            criterion
            for criterion in criteria
            if criterion.get("direction") == "support"
            and criterion.get("statement", "")
        ]
        exclusions = [
            criterion
            for criterion in criteria
            if criterion.get("direction") == "exclude"
            and criterion.get("statement", "")
        ]
        if not support:
            raise ProfileValidationError(
                "missing_support_criterion",
                "Each dimension requires a non-empty support criterion.",
            )
        if not exclusions and dimension.get("no_known_exclusion") is not True:
            raise ProfileValidationError(
                "missing_exclusion",
                "Provide an exclusion or explicitly acknowledge none is known.",
            )

        if code in BUILTIN_MINIMUMS:
            minimum = BUILTIN_MINIMUMS[code]
        elif re.fullmatch(r"CUSTOM_[A-Z0-9]{8}", code):
            minimum = DEFAULT_CUSTOM_MINIMUM
        else:
            raise ProfileValidationError(
                "unknown_dimension_code",
                f"Unknown guided dimension code: {code}",
            )
        dimension["analysis_config"] = {
            "mode": "llm_evidence",
            "minimum_observation": copy.deepcopy(minimum),
        }
        _reject_stigmatizing_language(dimension)
        dimension["levels"] = copy.deepcopy(GUIDED_LEVELS)

    if knowledge_point_ids is not None and linked_points != knowledge_point_ids:
        raise ProfileValidationError(
            "missing_knowledge_point_dimension",
            "Each knowledge point requires exactly one behavior dimension.",
        )


def _validate_v1_profile_draft(
    payload: Mapping[str, object],
) -> dict[str, object]:
    shape_schema = _input_shape_schema("profile-draft-v1")
    Draft202012Validator(shape_schema).validate(payload)

    normalized = _strip_strings(payload)
    _normalize_dimensions(normalized["dimensions"])
    validate_schema("profile-draft-v1", normalized)
    return normalized


def _require_continuous_order(
    rows: list[dict[str, Any]],
    *,
    code: str,
    field: str,
) -> None:
    if [row.get("order") for row in rows] != list(range(len(rows))):
        raise ProfileValidationError(
            code,
            "Items must use a continuous zero-based order.",
            field=field,
        )


def _validate_confirmation_hashes(normalized: dict[str, Any]) -> None:
    confirmations = normalized["confirmations"]
    knowledge_hash = sha256_json(
        {
            "problem_context": normalized["problem_context"],
            "knowledge_points": normalized["knowledge_points"],
        }
    )
    provided_knowledge_hash = confirmations["knowledge_points_hash"]
    if (
        provided_knowledge_hash is not None
        and provided_knowledge_hash != knowledge_hash
    ):
        raise ProfileValidationError(
            "stale_knowledge_confirmation",
            "The knowledge-point confirmation no longer matches the draft.",
            field="confirmations.knowledge_points_hash",
        )
    tests_hash = sha256_json(
        {
            "problem_context": normalized["problem_context"],
            "knowledge_points_hash": knowledge_hash,
            "assessment_tests": normalized["assessment_tests"],
        }
    )
    provided_tests_hash = confirmations["tests_hash"]
    if provided_tests_hash is not None and provided_tests_hash != tests_hash:
        raise ProfileValidationError(
            "stale_test_confirmation",
            "The test confirmation no longer matches the draft.",
            field="confirmations.tests_hash",
        )
    if provided_tests_hash is not None and provided_knowledge_hash is None:
        raise ProfileValidationError(
            "tests_confirmed_without_knowledge",
            "Tests cannot be confirmed before knowledge points.",
            field="confirmations.tests_hash",
        )


def _validate_v2_profile_draft(
    payload: Mapping[str, object],
) -> dict[str, object]:
    shape_schema = _input_shape_schema("profile-draft-v2")
    Draft202012Validator(shape_schema).validate(payload)
    normalized = _strip_strings(payload)
    _restore_assessment_test_io(normalized, payload, expected_field="expected")

    knowledge_points = normalized["knowledge_points"]
    assessment_tests = normalized["assessment_tests"]
    _require_continuous_order(
        knowledge_points,
        code="invalid_knowledge_point_order",
        field="knowledge_points",
    )
    _require_continuous_order(
        assessment_tests,
        code="invalid_test_order",
        field="assessment_tests",
    )
    knowledge_point_ids = [item["id"] for item in knowledge_points]
    if len(set(knowledge_point_ids)) != len(knowledge_point_ids):
        raise ProfileValidationError(
            "duplicate_knowledge_point_id",
            "Knowledge-point IDs must be unique.",
            field="knowledge_points",
        )
    test_ids = [item["id"] for item in assessment_tests]
    if len(set(test_ids)) != len(test_ids):
        raise ProfileValidationError(
            "duplicate_test_id",
            "Assessment-test IDs must be unique.",
            field="assessment_tests",
        )

    known_points = set(knowledge_point_ids)
    contract_kind = normalized["problem_context"]["submission_contract"]["kind"]
    expected_test_kind = (
        "function_call" if contract_kind == "function" else "stdin_stdout"
    )
    for assessment_test in assessment_tests:
        if not set(assessment_test["knowledge_point_ids"]).issubset(known_points):
            raise ProfileValidationError(
                "unknown_knowledge_point_reference",
                "An assessment test references an unknown knowledge point.",
                field="assessment_tests",
            )
        if assessment_test["kind"] != expected_test_kind:
            raise ProfileValidationError(
                "test_kind_mismatch",
                "Assessment-test kind does not match the submission contract.",
                field="assessment_tests",
            )

    _normalize_dimensions(
        normalized["dimensions"],
        knowledge_point_ids=known_points,
    )
    _validate_confirmation_hashes(normalized)
    validate_schema("profile-draft-v2", normalized)
    return normalized


def assessment_test_content_hash(assessment_test: Mapping[str, object]) -> str:
    """Hash one v3 test without its self-referential content hash."""
    return sha256_json(
        {
            key: copy.deepcopy(value)
            for key, value in assessment_test.items()
            if key != "content_hash"
        }
    )


def profile_v3_confirmation_hashes(
    profile: Mapping[str, object],
) -> dict[str, str]:
    """Return the v3 confirmations derived entirely from the profile draft."""
    knowledge_points = profile["knowledge_points"]
    knowledge_points_hash = sha256_json({"knowledge_points": knowledge_points})
    assessment_tests = profile["assessment_tests"]
    tests_without_content_hash = [
        {
            key: copy.deepcopy(value)
            for key, value in assessment_test.items()
            if key != "content_hash"
        }
        for assessment_test in assessment_tests
    ]
    return {
        "starter_source_hash": sha256_json(profile["starter_source"]),
        "knowledge_points_hash": knowledge_points_hash,
        "dimensions_hash": sha256_json(
            {
                "knowledge_points_hash": knowledge_points_hash,
                "dimensions": profile["dimensions"],
            }
        ),
        "tests_hash": sha256_json(
            {"assessment_tests": tests_without_content_hash}
        ),
    }


def validate_profile_v3_confirmations(normalized: Mapping[str, object]) -> None:
    """Reject non-null local v3 confirmations that no longer match the draft.

    The material-bundle hash is an external attestation supplied by the
    material gateway, so it is required at publication but cannot be derived
    from a profile draft alone.
    """
    confirmations = normalized["confirmations"]
    expected_hashes = profile_v3_confirmation_hashes(normalized)
    for field, expected_hash in expected_hashes.items():
        provided_hash = confirmations[field]
        if provided_hash is not None and provided_hash != expected_hash:
            confirmation_name = field.removesuffix("_hash")
            raise ProfileValidationError(
                f"stale_{confirmation_name}_confirmation",
                f"The {confirmation_name.replace('_', ' ')} confirmation no longer matches the draft.",
                field=f"confirmations.{field}",
            )


def _validate_v3_profile_relationships(normalized: dict[str, Any]) -> None:
    knowledge_points = normalized["knowledge_points"]
    assessment_tests = normalized["assessment_tests"]
    dimensions = normalized["dimensions"]
    _require_continuous_order(
        knowledge_points,
        code="invalid_knowledge_point_order",
        field="knowledge_points",
    )
    _require_continuous_order(
        assessment_tests,
        code="invalid_assessment_test_order",
        field="assessment_tests",
    )

    knowledge_point_ids = [item["id"] for item in knowledge_points]
    if len(set(knowledge_point_ids)) != len(knowledge_point_ids):
        raise ProfileValidationError(
            "duplicate_knowledge_point_id",
            "Knowledge-point IDs must be unique.",
            field="knowledge_points",
        )
    test_ids = [item["id"] for item in assessment_tests]
    if len(set(test_ids)) != len(test_ids):
        raise ProfileValidationError(
            "duplicate_assessment_test_id",
            "Assessment-test IDs must be unique.",
            field="assessment_tests",
        )

    material_requirement_ids = [
        item["material_requirement_id"] for item in knowledge_points
    ]
    if len(set(material_requirement_ids)) != len(material_requirement_ids):
        raise ProfileValidationError(
            "duplicate_material_requirement_mapping",
            "Each knowledge point must map to a unique material requirement.",
            field="knowledge_points",
        )

    known_points = set(knowledge_point_ids)
    dimensions_by_point: dict[str, dict[str, Any]] = {}
    criteria_by_id: dict[str, tuple[str, dict[str, Any]]] = {}
    for dimension in dimensions:
        point_id = dimension["knowledge_point_id"]
        if point_id not in known_points:
            raise ProfileValidationError(
                "unknown_knowledge_point_reference",
                "A dimension references an unknown knowledge point.",
                field="dimensions",
            )
        if point_id in dimensions_by_point:
            raise ProfileValidationError(
                "duplicate_knowledge_point_dimension",
                "Each knowledge point requires exactly one dimension.",
                field="dimensions",
            )
        dimensions_by_point[point_id] = dimension
        for criterion in dimension["evidence_criteria"]:
            criterion_id = criterion["id"]
            if criterion_id in criteria_by_id:
                raise ProfileValidationError(
                    "duplicate_criterion_id",
                    "Evidence-criterion IDs must be unique.",
                    field="dimensions",
                )
            criteria_by_id[criterion_id] = (point_id, criterion)

    if set(dimensions_by_point) != known_points:
        raise ProfileValidationError(
            "missing_knowledge_point_dimension",
            "Each knowledge point requires exactly one dimension.",
            field="dimensions",
        )

    tests_by_id = {assessment_test["id"]: assessment_test for assessment_test in assessment_tests}
    for assessment_test in assessment_tests:
        if not set(assessment_test["knowledge_point_ids"]).issubset(known_points):
            raise ProfileValidationError(
                "unknown_knowledge_point_reference",
                "An assessment test references an unknown knowledge point.",
                field="assessment_tests",
            )

    for point_id, dimension in dimensions_by_point.items():
        binding_criterion_ids: set[str] = set()
        for binding in dimension["verification_bindings"]:
            criterion_id = binding["criterion_id"]
            criterion_record = criteria_by_id.get(criterion_id)
            if criterion_record is None or criterion_record[0] != point_id:
                raise ProfileValidationError(
                    "unknown_criterion_reference",
                    "A verification binding references an unknown criterion.",
                    field="dimensions",
                )
            binding_criterion_ids.add(criterion_id)
            if binding["kind"] == "assessment_test":
                assessment_test = tests_by_id.get(binding["assessment_test_id"])
                if assessment_test is None:
                    raise ProfileValidationError(
                        "unknown_assessment_test_reference",
                        "A verification binding references an unknown assessment test.",
                        field="dimensions",
                    )
                if not assessment_test["enabled"]:
                    raise ProfileValidationError(
                        "disabled_assessment_test_binding",
                        "A verification binding must reference an enabled assessment test.",
                        field="dimensions",
                    )
                if point_id not in assessment_test["knowledge_point_ids"]:
                    raise ProfileValidationError(
                        "assessment_test_knowledge_point_mismatch",
                        "An assessment test binding must cover the dimension knowledge point.",
                        field="dimensions",
                    )
                if criterion_id not in assessment_test["criterion_ids"]:
                    raise ProfileValidationError(
                        "assessment_test_criterion_mismatch",
                        "An assessment test binding must cover the bound criterion.",
                        field="dimensions",
                    )
        missing_required_bindings = [
            criterion["id"]
            for criterion in dimension["evidence_criteria"]
            if criterion["required"] and criterion["id"] not in binding_criterion_ids
        ]
        if missing_required_bindings:
            raise ProfileValidationError(
                "missing_verification_binding",
                "Each required evidence criterion needs a verification binding.",
                field="dimensions",
            )

    for assessment_test in assessment_tests:
        for criterion_id in assessment_test["criterion_ids"]:
            criterion_record = criteria_by_id.get(criterion_id)
            if criterion_record is None:
                raise ProfileValidationError(
                    "unknown_assessment_test_criterion_reference",
                    "An assessment test references an unknown criterion.",
                    field="assessment_tests",
                )
            if criterion_record[0] not in assessment_test["knowledge_point_ids"]:
                raise ProfileValidationError(
                    "assessment_test_criterion_knowledge_point_mismatch",
                    "An assessment test criterion must belong to one of its knowledge points.",
                    field="assessment_tests",
                )

    for assessment_test in assessment_tests:
        if assessment_test["content_hash"] != assessment_test_content_hash(assessment_test):
            raise ProfileValidationError(
                "stale_assessment_test_content_hash",
                "Assessment-test content_hash no longer matches its content.",
                field="assessment_tests",
            )


def _validate_v3_profile_draft(
    payload: Mapping[str, object],
) -> dict[str, object]:
    shape_schema = _input_shape_schema("profile-draft-v3")
    Draft202012Validator(shape_schema).validate(payload)
    normalized = _strip_strings(payload)
    _restore_assessment_test_io(
        normalized,
        payload,
        expected_field="expected_stdout",
    )
    _validate_v3_profile_relationships(normalized)
    validate_profile_v3_confirmations(normalized)
    validate_schema("profile-draft-v3", normalized)
    return normalized


def validate_profile_draft(payload: Mapping[str, object]) -> dict[str, object]:
    """Validate, minimally normalize, and complete a guided profile draft."""
    schema_version = payload.get("schema_version")
    if schema_version == 1:
        return _validate_v1_profile_draft(payload)
    if schema_version == 2:
        return _validate_v2_profile_draft(payload)
    if schema_version == 3:
        return _validate_v3_profile_draft(payload)
    raise ProfileValidationError(
        "unsupported_schema_version",
        "Unsupported profile schema version.",
        field="schema_version",
    )
