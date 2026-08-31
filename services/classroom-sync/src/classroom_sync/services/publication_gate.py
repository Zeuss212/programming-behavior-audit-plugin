"""Pure, deterministic publication checks for classroom plan profiles."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from classroom_sync.canonical import sha256_json
from classroom_sync.errors import PublicationGateBlockedError
from classroom_sync.services.assessment_materials import AssessmentMaterialBundle

PublicationGateIssueCode = Literal[
    "material_bundle_changed",
    "starter_source_non_utf8_confirmation_required",
    "starter_source_protected_compile_error",
    "starter_source_mismatch",
    "stale_profile_confirmation",
    "unknown_material_requirement",
    "requirement_not_student_responsibility",
    "missing_required_requirement",
    "unknown_test_reference",
    "criterion_binding_missing",
    "detector_binding_unavailable",
    "boundary_coverage_incomplete",
]

_CODE_ORDER: tuple[PublicationGateIssueCode, ...] = (
    "material_bundle_changed",
    "starter_source_non_utf8_confirmation_required",
    "starter_source_protected_compile_error",
    "starter_source_mismatch",
    "stale_profile_confirmation",
    "unknown_material_requirement",
    "requirement_not_student_responsibility",
    "missing_required_requirement",
    "unknown_test_reference",
    "criterion_binding_missing",
    "detector_binding_unavailable",
    "boundary_coverage_incomplete",
)
_CODE_RANK = {code: rank for rank, code in enumerate(_CODE_ORDER)}
_SCOPE_RANK = {"classroom": 0, "source": 1, "requirement": 2, "test": 3}
_SOURCE_BLOCKERS = {
    "starter_source_non_utf8_confirmation_required",
    "starter_source_protected_compile_error",
}
_MESSAGES: dict[PublicationGateIssueCode, str] = {
    "material_bundle_changed": "课堂材料已在确认后变更，请重新检查。",
    "starter_source_non_utf8_confirmation_required": "源框架需要确认 UTF-8 候选副本。",
    "starter_source_protected_compile_error": "受保护的源框架未通过编译预检。",
    "starter_source_mismatch": "已选源框架与当前材料不匹配。",
    "stale_profile_confirmation": "草稿在确认后已变更，请重新确认。",
    "unknown_material_requirement": "草稿引用了当前材料中不存在的要求。",
    "requirement_not_student_responsibility": "已选要求不是学生编辑责任。",
    "missing_required_requirement": "草稿缺少学生必须完成的材料要求。",
    "unknown_test_reference": "草稿测试与当前真实材料不匹配。",
    "criterion_binding_missing": "必需证据标准缺少可验证绑定。",
    "detector_binding_unavailable": "已选要求依赖的检测器当前不可用。",
    "boundary_coverage_incomplete": "教师测试的边界场景覆盖尚不完整。",
}


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PublicationGateIssue(_FrozenModel):
    code: PublicationGateIssueCode
    severity: Literal["blocking", "warning"]
    scope: Literal["classroom", "source", "requirement", "test"]
    knowledge_point_id: Annotated[str | None, Field(max_length=200)]
    requirement_id: Annotated[str | None, Field(max_length=200)]
    message: Annotated[str, Field(min_length=1, max_length=500)]


class PublicationGateResult(_FrozenModel):
    status: Literal["ready", "blocked"]
    blocking_count: Annotated[int, Field(ge=0)]
    warning_count: Annotated[int, Field(ge=0)]
    issues: tuple[PublicationGateIssue, ...]

    def safe_projection(self, *, max_bytes: int = 30_000) -> dict[str, object]:
        """Project a deterministic issue prefix within the error-envelope bound."""

        projection: dict[str, object] = {
            "status": self.status,
            "blocking_count": self.blocking_count,
            "warning_count": self.warning_count,
            "issues": [],
        }
        projected_issues = cast(list[dict[str, object]], projection["issues"])
        for issue in self.issues:
            candidate = issue.model_dump(mode="json")
            projected_issues.append(candidate)
            encoded = json.dumps(
                projection,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(encoded) > max_bytes:
                projected_issues.pop()
                break
        return projection


class PublicationGate:
    """Evaluate only stable material identifiers and canonical hashes."""

    def evaluate(
        self,
        profile: Mapping[str, object],
        materials: AssessmentMaterialBundle,
    ) -> PublicationGateResult:
        if profile.get("schema_version") != 3:
            return PublicationGateResult(
                status="ready",
                blocking_count=0,
                warning_count=0,
                issues=(),
            )

        issues: list[PublicationGateIssue] = []
        material_issue_messages: dict[tuple[str, str | None], str] = {
            (issue.code, issue.requirement_id): issue.message
            for issue in materials.issues
        }
        confirmations = self._mapping(profile.get("confirmations"))

        if confirmations.get("material_bundle_hash") != materials.bundle_hash:
            self._add(issues, "material_bundle_changed", "blocking", "classroom")

        for material_issue in materials.issues:
            if material_issue.code in _SOURCE_BLOCKERS:
                self._add(
                    issues,
                    cast(PublicationGateIssueCode, material_issue.code),
                    "blocking",
                    "source",
                    message=material_issue.message,
                )

        profile_starter = self._mapping(profile.get("starter_source"))
        material_starter = materials.starter_source
        if (
            material_starter is None
            or profile_starter.get("artifact_id") != material_starter.artifact_id
            or profile_starter.get("sha256") != material_starter.sha256
        ):
            self._add(issues, "starter_source_mismatch", "blocking", "source")

        expected_confirmations = self._profile_confirmation_hashes(profile)
        confirmation_scopes = {
            "starter_source_hash": "source",
            "knowledge_points_hash": "requirement",
            "dimensions_hash": "requirement",
            "tests_hash": "test",
        }
        for field, expected in expected_confirmations.items():
            if confirmations.get(field) != expected:
                self._add(
                    issues,
                    "stale_profile_confirmation",
                    "blocking",
                    cast(
                        Literal["classroom", "source", "requirement", "test"],
                        confirmation_scopes[field],
                    ),
                )

        requirements_by_id = {item.id: item for item in materials.requirements}
        available_detectors = {
            item.id: item.available for item in materials.detector_profiles
        }
        knowledge_points = self._mapping_sequence(profile.get("knowledge_points"))
        selected_requirement_ids: set[str] = set()
        point_requirement_ids: dict[str, str] = {}
        requirement_point_ids: dict[str, str] = {}
        for point in knowledge_points:
            point_id = self._string(point.get("id"))
            requirement_id = self._string(point.get("material_requirement_id"))
            if point_id is not None and requirement_id is not None:
                previous_requirement_id = point_requirement_ids.get(point_id)
                if previous_requirement_id is not None:
                    self._add(
                        issues,
                        "criterion_binding_missing",
                        "blocking",
                        "requirement",
                        knowledge_point_id=point_id,
                        requirement_id=previous_requirement_id,
                    )
                    self._add(
                        issues,
                        "criterion_binding_missing",
                        "blocking",
                        "requirement",
                        knowledge_point_id=point_id,
                        requirement_id=requirement_id,
                    )
                else:
                    point_requirement_ids[point_id] = requirement_id
                previous_point_id = requirement_point_ids.get(requirement_id)
                if previous_point_id is not None:
                    self._add(
                        issues,
                        "criterion_binding_missing",
                        "blocking",
                        "requirement",
                        knowledge_point_id=previous_point_id,
                        requirement_id=requirement_id,
                    )
                    self._add(
                        issues,
                        "criterion_binding_missing",
                        "blocking",
                        "requirement",
                        knowledge_point_id=point_id,
                        requirement_id=requirement_id,
                    )
                else:
                    requirement_point_ids[requirement_id] = point_id
            if requirement_id is None:
                continue
            selected_requirement_ids.add(requirement_id)
            requirement = requirements_by_id.get(requirement_id)
            if requirement is None:
                self._add(
                    issues,
                    "unknown_material_requirement",
                    "blocking",
                    "requirement",
                    knowledge_point_id=point_id,
                    requirement_id=requirement_id,
                )
            elif not requirement.student_responsibility:
                message = self._material_message_for_requirement(
                    material_issue_messages,
                    requirement_id,
                    (
                        "teacher_dimension_not_student_responsibility",
                        "teacher_dimension_outside_task",
                    ),
                    fallback=_MESSAGES["requirement_not_student_responsibility"],
                )
                self._add(
                    issues,
                    "requirement_not_student_responsibility",
                    "blocking",
                    "requirement",
                    knowledge_point_id=point_id,
                    requirement_id=requirement_id,
                    message=message,
                )
            if requirement is not None:
                for detector_id in requirement.detector_profile_ids:
                    if available_detectors.get(detector_id, False):
                        continue
                    message = self._material_message_for_requirement(
                        material_issue_messages,
                        requirement_id,
                        ("detector_profile_unavailable",),
                        fallback=_MESSAGES["detector_binding_unavailable"],
                    )
                    self._add(
                        issues,
                        "detector_binding_unavailable",
                        "blocking",
                        "requirement",
                        knowledge_point_id=point_id,
                        requirement_id=requirement_id,
                        message=message,
                    )

        for requirement in materials.requirements:
            if (
                requirement.student_responsibility
                and requirement.id not in selected_requirement_ids
            ):
                message = material_issue_messages.get(
                    ("required_student_dimension_missing", requirement.id),
                    _MESSAGES["missing_required_requirement"],
                )
                self._add(
                    issues,
                    "missing_required_requirement",
                    "blocking",
                    "requirement",
                    requirement_id=requirement.id,
                    message=message,
                )

        dimensions = self._mapping_sequence(profile.get("dimensions"))
        dimension_counts: dict[str, int] = {}
        criterion_owners: dict[str, tuple[str, str]] = {}
        for dimension in dimensions:
            point_id = self._string(dimension.get("knowledge_point_id"))
            if point_id is not None:
                dimension_counts[point_id] = dimension_counts.get(point_id, 0) + 1
            requirement_id = (
                point_requirement_ids.get(point_id) if point_id is not None else None
            )
            for criterion in self._mapping_sequence(
                dimension.get("evidence_criteria")
            ):
                criterion_id = self._string(criterion.get("id"))
                if (
                    point_id is None
                    or requirement_id is None
                    or criterion_id is None
                ):
                    continue
                if criterion.get("material_requirement_id") != requirement_id:
                    self._add(
                        issues,
                        "criterion_binding_missing",
                        "blocking",
                        "requirement",
                        knowledge_point_id=point_id,
                        requirement_id=requirement_id,
                    )
                previous_owner = criterion_owners.get(criterion_id)
                if previous_owner is not None:
                    self._add(
                        issues,
                        "criterion_binding_missing",
                        "blocking",
                        "requirement",
                        knowledge_point_id=previous_owner[0],
                        requirement_id=previous_owner[1],
                    )
                    self._add(
                        issues,
                        "criterion_binding_missing",
                        "blocking",
                        "requirement",
                        knowledge_point_id=point_id,
                        requirement_id=requirement_id,
                    )
                else:
                    criterion_owners[criterion_id] = (point_id, requirement_id)

        material_tests = {item.id: item for item in materials.assessment_tests}
        profile_tests = self._mapping_sequence(profile.get("assessment_tests"))
        profile_tests_by_id: dict[str, Mapping[str, object]] = {}
        valid_profile_test_ids: set[str] = set()
        for assessment_test in profile_tests:
            test_id = self._string(assessment_test.get("id"))
            linked_points = self._string_sequence(
                assessment_test.get("knowledge_point_ids")
            )
            knowledge_point_id = linked_points[0] if linked_points else None
            requirement_id = (
                point_requirement_ids.get(knowledge_point_id)
                if knowledge_point_id is not None
                else None
            )
            if test_id is not None:
                if test_id in profile_tests_by_id:
                    self._add(
                        issues,
                        "unknown_test_reference",
                        "blocking",
                        "test",
                        knowledge_point_id=knowledge_point_id,
                        requirement_id=requirement_id,
                    )
                else:
                    profile_tests_by_id[test_id] = assessment_test
            for linked_point_id in linked_points:
                if linked_point_id not in point_requirement_ids:
                    self._add(
                        issues,
                        "unknown_test_reference",
                        "blocking",
                        "test",
                        knowledge_point_id=linked_point_id,
                    )
            for criterion_id in self._string_sequence(
                assessment_test.get("criterion_ids")
            ):
                criterion_owner = criterion_owners.get(criterion_id)
                if criterion_owner is None:
                    self._add(
                        issues,
                        "criterion_binding_missing",
                        "blocking",
                        "test",
                        knowledge_point_id=knowledge_point_id,
                        requirement_id=requirement_id,
                    )
                elif criterion_owner[0] not in linked_points:
                    self._add(
                        issues,
                        "criterion_binding_missing",
                        "blocking",
                        "test",
                        knowledge_point_id=criterion_owner[0],
                        requirement_id=criterion_owner[1],
                    )
            material_test = material_tests.get(test_id) if test_id is not None else None
            profile_hash_valid = assessment_test.get("content_hash") == sha256_json(
                {
                    key: value
                    for key, value in assessment_test.items()
                    if key != "content_hash"
                }
            )
            if not profile_hash_valid:
                self._add(
                    issues,
                    "stale_profile_confirmation",
                    "blocking",
                    "test",
                    knowledge_point_id=knowledge_point_id,
                    requirement_id=requirement_id,
                )
            material_test_valid = (
                material_test is not None
                and sha256_json(self._material_test_projection(assessment_test))
                == material_test.content_hash
            )
            if not material_test_valid:
                self._add(
                    issues,
                    "unknown_test_reference",
                    "blocking",
                    "test",
                    knowledge_point_id=knowledge_point_id,
                    requirement_id=requirement_id,
                )
            if (
                test_id is not None
                and profile_hash_valid
                and material_test_valid
                and assessment_test.get("enabled") is True
                and material_test is not None
                and material_test.enabled
            ):
                valid_profile_test_ids.add(test_id)

        for point_id, requirement_id in point_requirement_ids.items():
            requirement = requirements_by_id.get(requirement_id)
            if (
                requirement is not None
                and requirement.student_responsibility
                and dimension_counts.get(point_id, 0) != 1
            ):
                self._add(
                    issues,
                    "criterion_binding_missing",
                    "blocking",
                    "requirement",
                    knowledge_point_id=point_id,
                    requirement_id=requirement_id,
                )
        for dimension in dimensions:
            point_id = self._string(dimension.get("knowledge_point_id"))
            requirement_id = (
                point_requirement_ids.get(point_id) if point_id is not None else None
            )
            requirement = (
                requirements_by_id.get(requirement_id)
                if requirement_id is not None
                else None
            )
            if requirement is None:
                self._add(
                    issues,
                    "criterion_binding_missing",
                    "blocking",
                    "requirement",
                    knowledge_point_id=point_id,
                )
                continue
            if not requirement.student_responsibility:
                continue
            bindings = self._mapping_sequence(
                dimension.get("verification_bindings")
            )
            bound_criteria: set[str] = set()
            for binding in bindings:
                criterion_id = self._string(binding.get("criterion_id"))
                if criterion_id is None:
                    continue
                if binding.get("kind") == "assessment_test":
                    assessment_test_id = self._string(
                        binding.get("assessment_test_id")
                    )
                    bound_assessment_test = (
                        profile_tests_by_id.get(assessment_test_id)
                        if assessment_test_id is not None
                        else None
                    )
                    binding_valid = (
                        assessment_test_id in valid_profile_test_ids
                        and bound_assessment_test is not None
                        and criterion_owners.get(criterion_id)
                        == (point_id, requirement_id)
                        and point_id
                        in self._string_sequence(
                            bound_assessment_test.get("knowledge_point_ids")
                        )
                        and criterion_id
                        in self._string_sequence(
                            bound_assessment_test.get("criterion_ids")
                        )
                        and assessment_test_id in requirement.test_ids
                    )
                    if binding_valid:
                        bound_criteria.add(criterion_id)
                    else:
                        self._add(
                            issues,
                            "unknown_test_reference",
                            "blocking",
                            "test",
                            knowledge_point_id=point_id,
                            requirement_id=requirement_id,
                        )
                elif binding.get("kind") == "detector_profile":
                    binding_detector_id = self._string(
                        binding.get("detector_profile_id")
                    )
                    if (
                        criterion_owners.get(criterion_id)
                        == (point_id, requirement_id)
                        and binding_detector_id in requirement.detector_profile_ids
                    ):
                        bound_criteria.add(criterion_id)
            for criterion in self._mapping_sequence(
                dimension.get("evidence_criteria")
            ):
                criterion_id = self._string(criterion.get("id"))
                if criterion.get("required") is True and criterion_id not in bound_criteria:
                    self._add(
                        issues,
                        "criterion_binding_missing",
                        "blocking",
                        "requirement",
                        knowledge_point_id=point_id,
                        requirement_id=requirement_id,
                    )
            for binding in bindings:
                if binding.get("kind") != "detector_profile":
                    continue
                binding_detector_id = self._string(
                    binding.get("detector_profile_id")
                )
                if binding_detector_id is None or not available_detectors.get(
                    binding_detector_id, False
                ):
                    message = self._material_message_for_requirement(
                        material_issue_messages,
                        requirement_id,
                        ("detector_profile_unavailable",),
                        fallback=_MESSAGES["detector_binding_unavailable"],
                    )
                    self._add(
                        issues,
                        "detector_binding_unavailable",
                        "blocking",
                        "requirement",
                        knowledge_point_id=point_id,
                        requirement_id=requirement_id,
                        message=message,
                    )

        for material_issue in materials.issues:
            if material_issue.code == "boundary_coverage_incomplete":
                self._add(
                    issues,
                    "boundary_coverage_incomplete",
                    "warning",
                    material_issue.scope,
                    requirement_id=material_issue.requirement_id,
                    message=material_issue.message,
                )

        ordered = self._deduplicate_and_order(issues)
        blocking_count = sum(item.severity == "blocking" for item in ordered)
        warning_count = len(ordered) - blocking_count
        return PublicationGateResult(
            status="blocked" if blocking_count else "ready",
            blocking_count=blocking_count,
            warning_count=warning_count,
            issues=ordered,
        )

    def require_ready(
        self,
        profile: Mapping[str, object],
        materials: AssessmentMaterialBundle,
    ) -> None:
        result = self.evaluate(profile, materials)
        if result.status != "ready":
            raise PublicationGateBlockedError(result.safe_projection())

    @staticmethod
    def _profile_confirmation_hashes(
        profile: Mapping[str, object],
    ) -> dict[str, str]:
        starter_source = profile.get("starter_source")
        knowledge_points = profile.get("knowledge_points")
        dimensions = profile.get("dimensions")
        assessment_tests = PublicationGate._mapping_sequence(
            profile.get("assessment_tests")
        )
        knowledge_points_hash = sha256_json(
            {"knowledge_points": knowledge_points}
        )
        return {
            "starter_source_hash": sha256_json(starter_source),
            "knowledge_points_hash": knowledge_points_hash,
            "dimensions_hash": sha256_json(
                {
                    "knowledge_points_hash": knowledge_points_hash,
                    "dimensions": dimensions,
                }
            ),
            "tests_hash": sha256_json(
                {
                    "assessment_tests": [
                        {
                            key: value
                            for key, value in assessment_test.items()
                            if key != "content_hash"
                        }
                        for assessment_test in assessment_tests
                    ]
                }
            ),
        }

    @staticmethod
    def _material_test_projection(
        assessment_test: Mapping[str, object],
    ) -> dict[str, object]:
        return {
            key: value
            for key, value in assessment_test.items()
            if key not in {"content_hash", "knowledge_point_ids", "criterion_ids"}
        }

    @staticmethod
    def _material_message_for_requirement(
        messages: Mapping[tuple[str, str | None], str],
        requirement_id: str | None,
        codes: Sequence[str],
        *,
        fallback: str,
    ) -> str:
        for code in codes:
            message = messages.get((code, requirement_id))
            if message is not None:
                return message
        return fallback

    @staticmethod
    def _deduplicate_and_order(
        issues: Sequence[PublicationGateIssue],
    ) -> tuple[PublicationGateIssue, ...]:
        unique: dict[tuple[str, str, str | None, str | None], PublicationGateIssue] = {}
        for issue in issues:
            key = (
                issue.code,
                issue.scope,
                issue.knowledge_point_id,
                issue.requirement_id,
            )
            unique.setdefault(key, issue)
        return tuple(
            sorted(
                unique.values(),
                key=lambda issue: (
                    0 if issue.severity == "blocking" else 1,
                    _CODE_RANK[issue.code],
                    _SCOPE_RANK[issue.scope],
                    issue.knowledge_point_id or "",
                    issue.requirement_id or "",
                ),
            )
        )

    @staticmethod
    def _add(
        issues: list[PublicationGateIssue],
        code: PublicationGateIssueCode,
        severity: Literal["blocking", "warning"],
        scope: Literal["classroom", "source", "requirement", "test"],
        *,
        knowledge_point_id: str | None = None,
        requirement_id: str | None = None,
        message: str | None = None,
    ) -> None:
        issues.append(
            PublicationGateIssue(
                code=code,
                severity=severity,
                scope=scope,
                knowledge_point_id=knowledge_point_id,
                requirement_id=requirement_id,
                message=message or _MESSAGES[code],
            )
        )

    @staticmethod
    def _mapping(value: object) -> Mapping[str, object]:
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _mapping_sequence(value: object) -> tuple[Mapping[str, object], ...]:
        if not isinstance(value, (list, tuple)):
            return ()
        return tuple(item for item in value if isinstance(item, Mapping))

    @staticmethod
    def _string_sequence(value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            return ()
        return tuple(item for item in value if isinstance(item, str))

    @staticmethod
    def _string(value: object) -> str | None:
        return value if isinstance(value, str) else None
