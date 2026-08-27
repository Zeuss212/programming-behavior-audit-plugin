"""Deterministic publication decisions against the immutable real C++ bundles."""

from __future__ import annotations

import base64
import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from classroom_sync.auth.fincolab import Principal
from classroom_sync.canonical import sha256_json
from classroom_sync.domain.schemas import ClassroomSchemaRegistry
from classroom_sync.errors import PublicationGateBlockedError
from classroom_sync.services.assessment_materials import (
    AssessmentMaterialBundle,
    AssessmentMaterialService,
)
from classroom_sync.services.publication_gate import PublicationGate

ROOT = Path(__file__).resolve().parents[4]
MATERIALS = ROOT / "deploy" / "classroom" / "local-demo" / "materials"

POINT_IDS = {
    "REQ_LINK_TAIL_INSERT": "KP_LINKTAL1",
    "REQ_LINK_REVERSE": "KP_LINKREV1",
    "REQ_LINK_TRAVERSAL": "KP_LINKTRV1",
    "REQ_LINK_DELETE": "KP_LINKDEL1",
    "REQ_SEQ_INITIALIZATION": "KP_SEQINIT1",
    "REQ_SEQ_SPACE_RELEASE": "KP_SEQSPAC1",
    "REQ_SEQ_SEARCH": "KP_SEQSRCH1",
    "REQ_SEQ_DELETE": "KP_SEQDELT1",
    "REQ_SEQ_MOVE": "KP_SEQMOVE1",
}
CRITERION_IDS = {
    requirement_id: point_id.replace("KP_", "CRIT_")
    for requirement_id, point_id in POINT_IDS.items()
}


def real_bundle(name: str) -> AssessmentMaterialBundle:
    """Validate checked-in importer output and return its real public projection."""

    payload = cast(
        dict[str, object],
        json.loads((MATERIALS / name / "bundle.json").read_text(encoding="utf-8")),
    )
    starter = cast(dict[str, object], payload["starter_source"])
    source_path = MATERIALS / name / cast(str, starter["file_name"])
    starter["content_base64"] = base64.b64encode(source_path.read_bytes()).decode(
        "ascii"
    )

    class StaticGateway:
        def get_bundle(
            self,
            principal: Principal,
            space_id: str,
            parent_algorithm_id: str,
        ) -> dict[str, object]:
            assert principal.user_id == "teacher-1"
            assert space_id == payload["space_id"]
            assert parent_algorithm_id == payload["parent_algorithm_id"]
            return deepcopy(payload)

    return AssessmentMaterialService(StaticGateway()).get_bundle(
        Principal("teacher-1", "teacher-a", "teacher-token"),
        cast(str, payload["space_id"]),
        cast(str, payload["parent_algorithm_id"]),
    )


def profile_for(
    materials: AssessmentMaterialBundle,
    requirement_ids: tuple[str, ...],
) -> dict[str, object]:
    """Build a confirmed profile using stable material IDs and the real tests."""

    requirements_by_id = {item.id: item for item in materials.requirements}
    knowledge_points: list[dict[str, object]] = []
    dimensions: list[dict[str, object]] = []
    criteria_for_test: dict[str, list[tuple[str, str]]] = {
        item.id: [] for item in materials.assessment_tests
    }
    fallback_test_id = materials.assessment_tests[0].id
    for order, requirement_id in enumerate(requirement_ids):
        requirement = requirements_by_id[requirement_id]
        point_id = POINT_IDS[requirement_id]
        criterion_id = CRITERION_IDS[requirement_id]
        knowledge_points.append(
            {
                "id": point_id,
                "material_requirement_id": requirement_id,
                "name": f"stable point {order}",
                "description": f"stable description {order}",
                "source": "teacher",
                "order": order,
            }
        )
        for test_id in requirement.test_ids:
            criteria_for_test[test_id].append((point_id, criterion_id))
        if requirement.detector_profile_ids:
            bindings = [
                {
                    "criterion_id": criterion_id,
                    "kind": "detector_profile",
                    "detector_profile_id": requirement.detector_profile_ids[0],
                }
            ]
        else:
            assessment_test_id = (
                requirement.test_ids[0]
                if requirement.test_ids
                else fallback_test_id
            )
            criteria_for_test[assessment_test_id].append((point_id, criterion_id))
            bindings = [
                {
                    "criterion_id": criterion_id,
                    "kind": "assessment_test",
                    "assessment_test_id": assessment_test_id,
                }
            ]
        dimensions.append(
            {
                "knowledge_point_id": point_id,
                "name": f"stable dimension {order}",
                "question": f"stable question {order}",
                "evidence_criteria": [
                    {
                        "id": criterion_id,
                        "material_requirement_id": requirement_id,
                        "statement": f"stable criterion {order}",
                        "required": True,
                    }
                ],
                "verification_bindings": bindings,
                "analysis_config": {"mode": "evidence_binding"},
            }
        )

    assessment_tests: list[dict[str, object]] = []
    for material_test in materials.assessment_tests:
        linked = criteria_for_test[material_test.id]
        if not linked:
            continue
        without_hash = {
            **material_test.model_dump(mode="json", exclude={"content_hash"}),
            "knowledge_point_ids": list(dict.fromkeys(point for point, _ in linked)),
            "criterion_ids": list(dict.fromkeys(criterion for _, criterion in linked)),
        }
        assessment_tests.append(
            {**without_hash, "content_hash": sha256_json(without_hash)}
        )

    assert materials.starter_source is not None
    starter_source = {
        "artifact_id": materials.starter_source.artifact_id,
        "display_name": materials.starter_source.display_name,
        "file_name": materials.starter_source.file_name,
        "sha256": materials.starter_source.sha256,
        "size_bytes": materials.starter_source.size_bytes,
        "source": materials.starter_source.source,
    }
    knowledge_points_hash = sha256_json({"knowledge_points": knowledge_points})
    tests_without_hash = [
        {key: value for key, value in item.items() if key != "content_hash"}
        for item in assessment_tests
    ]
    return {
        "schema_version": 3,
        "problem_id": "stable-problem-id",
        "title": "teacher title is not a gate key",
        "problem_context": {
            "statement": "teacher prose is not a gate key",
            "language": "cpp",
            "submission_contract": {"kind": "stdin_stdout"},
            "toolchain_profile": "cpp17_stdio_v1",
            "entry_file": starter_source["file_name"],
            "source_encoding": "utf-8",
        },
        "starter_source": starter_source,
        "knowledge_points": knowledge_points,
        "assessment_tests": assessment_tests,
        "dimensions": dimensions,
        "confirmations": {
            "material_bundle_hash": materials.bundle_hash,
            "starter_source_hash": sha256_json(starter_source),
            "knowledge_points_hash": knowledge_points_hash,
            "dimensions_hash": sha256_json(
                {
                    "knowledge_points_hash": knowledge_points_hash,
                    "dimensions": dimensions,
                }
            ),
            "tests_hash": sha256_json(
                {"assessment_tests": tests_without_hash}
            ),
        },
    }


def reconfirm_profile(profile: dict[str, object]) -> None:
    """Recompute teacher confirmations after a schema-valid test mutation."""

    knowledge_points = cast(list[dict[str, object]], profile["knowledge_points"])
    dimensions = cast(list[dict[str, object]], profile["dimensions"])
    assessment_tests = cast(list[dict[str, object]], profile["assessment_tests"])
    knowledge_points_hash = sha256_json({"knowledge_points": knowledge_points})
    confirmations = cast(dict[str, object], profile["confirmations"])
    confirmations["knowledge_points_hash"] = knowledge_points_hash
    confirmations["dimensions_hash"] = sha256_json(
        {
            "knowledge_points_hash": knowledge_points_hash,
            "dimensions": dimensions,
        }
    )
    confirmations["tests_hash"] = sha256_json(
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
    )


def issue_codes(result: object) -> list[str]:
    return [issue.code for issue in result.issues]  # type: ignore[attr-defined]


def test_v2_remains_ready_even_when_cpp_materials_are_blocked() -> None:
    result = PublicationGate().evaluate(
        {"schema_version": 2, "title": "legacy"},
        real_bundle("sequence-list"),
    )

    assert result.model_dump(mode="json") == {
        "status": "ready",
        "blocking_count": 0,
        "warning_count": 0,
        "issues": [],
    }


def test_sequence_bundle_retains_its_three_real_publication_blockers() -> None:
    materials = real_bundle("sequence-list")
    profile = profile_for(
        materials,
        tuple(requirement.id for requirement in materials.requirements),
    )

    result = PublicationGate().evaluate(profile, materials)

    assert result.status == "blocked"
    assert result.blocking_count == 3
    assert result.warning_count == 1
    assert issue_codes(result) == [
        "starter_source_non_utf8_confirmation_required",
        "starter_source_protected_compile_error",
        "detector_binding_unavailable",
        "boundary_coverage_incomplete",
    ]


def test_raw_linked_list_dimensions_report_all_three_truthful_mismatches() -> None:
    materials = real_bundle("linked-list")
    profile = profile_for(
        materials,
        (
            "REQ_LINK_TAIL_INSERT",
            "REQ_LINK_TRAVERSAL",
            "REQ_LINK_DELETE",
        ),
    )

    result = PublicationGate().evaluate(profile, materials)

    assert result.status == "blocked"
    assert result.blocking_count == 3
    assert [
        (issue.code, issue.requirement_id)
        for issue in result.issues
        if issue.severity == "blocking"
    ] == [
        ("requirement_not_student_responsibility", "REQ_LINK_DELETE"),
        ("requirement_not_student_responsibility", "REQ_LINK_TRAVERSAL"),
        ("missing_required_requirement", "REQ_LINK_REVERSE"),
    ]


def test_corrected_linked_list_tail_reverse_and_both_real_tests_is_ready() -> None:
    materials = real_bundle("linked-list")
    profile = profile_for(
        materials,
        ("REQ_LINK_TAIL_INSERT", "REQ_LINK_REVERSE"),
    )

    result = PublicationGate().evaluate(profile, materials)

    assert result.status == "ready"
    assert result.blocking_count == 0
    assert result.warning_count == 1
    assert issue_codes(result) == ["boundary_coverage_incomplete"]


@pytest.mark.parametrize(
    ("drift", "expected_code"),
    (
        ("bundle", "material_bundle_changed"),
        ("material_source", "starter_source_mismatch"),
        ("material_test", "unknown_test_reference"),
        ("profile_source", "stale_profile_confirmation"),
        ("profile_test", "stale_profile_confirmation"),
        ("profile_test_hash", "stale_profile_confirmation"),
    ),
)
def test_confirmation_or_material_drift_blocks(
    drift: str,
    expected_code: str,
) -> None:
    materials = real_bundle("linked-list")
    profile = profile_for(
        materials,
        ("REQ_LINK_TAIL_INSERT", "REQ_LINK_REVERSE"),
    )
    if drift == "bundle":
        materials = materials.model_copy(update={"bundle_hash": "0" * 64})
    elif drift == "material_source":
        assert materials.starter_source is not None
        materials = materials.model_copy(
            update={
                "starter_source": materials.starter_source.model_copy(
                    update={"sha256": "0" * 64}
                )
            }
        )
    elif drift == "material_test":
        changed_test = materials.assessment_tests[0].model_copy(
            update={"content_hash": "0" * 64}
        )
        materials = materials.model_copy(
            update={
                "assessment_tests": (changed_test, *materials.assessment_tests[1:])
            }
        )
    elif drift == "profile_source":
        starter = cast(dict[str, object], profile["starter_source"])
        starter["file_name"] = "renamed.cpp"
    elif drift == "profile_test":
        tests = cast(list[dict[str, object]], profile["assessment_tests"])
        tests[0]["input"] = "1\n9\n"
        tests[0]["content_hash"] = sha256_json(
            {key: value for key, value in tests[0].items() if key != "content_hash"}
        )
    else:
        tests = cast(list[dict[str, object]], profile["assessment_tests"])
        tests[0]["content_hash"] = "0" * 64

    result = PublicationGate().evaluate(profile, materials)

    assert result.status == "blocked"
    assert expected_code in issue_codes(result)


def test_gate_uses_stable_ids_and_hashes_not_names_or_teacher_prose() -> None:
    materials = real_bundle("linked-list")
    profile = profile_for(
        materials,
        ("REQ_LINK_TAIL_INSERT", "REQ_LINK_REVERSE"),
    )
    renamed_requirements = tuple(
        item.model_copy(
            update={"name": "完全不同的显示名", "source_statement": "任意教师文本"}
        )
        for item in materials.requirements
    )
    renamed_materials = materials.model_copy(
        update={"title": "重命名", "statement": "改写说明", "requirements": renamed_requirements}
    )

    result = PublicationGate().evaluate(profile, renamed_materials)

    assert result.status == "ready"


def test_unknown_assessment_binding_is_not_counted_as_criterion_evidence() -> None:
    materials = real_bundle("linked-list")
    profile = profile_for(
        materials,
        ("REQ_LINK_TAIL_INSERT", "REQ_LINK_REVERSE"),
    )
    dimensions = cast(list[dict[str, object]], profile["dimensions"])
    bindings = cast(
        list[dict[str, object]],
        dimensions[0]["verification_bindings"],
    )
    bindings[0]["assessment_test_id"] = "TEST_BAD00001"
    confirmations = cast(dict[str, object], profile["confirmations"])
    confirmations["dimensions_hash"] = sha256_json(
        {
            "knowledge_points_hash": confirmations["knowledge_points_hash"],
            "dimensions": dimensions,
        }
    )

    result = PublicationGate().evaluate(profile, materials)

    assert result.status == "blocked"
    assert "unknown_test_reference" in issue_codes(result)
    assert "criterion_binding_missing" in issue_codes(result)


def test_disabled_assessment_binding_is_not_counted_as_criterion_evidence() -> None:
    materials = real_bundle("linked-list")
    disabled_test = materials.assessment_tests[0].model_copy(
        update={"enabled": False}
    )
    disabled_test = disabled_test.model_copy(
        update={
            "content_hash": sha256_json(
                disabled_test.model_dump(mode="json", exclude={"content_hash"})
            )
        }
    )
    materials = materials.model_copy(
        update={
            "assessment_tests": (disabled_test, *materials.assessment_tests[1:]),
            "bundle_hash": "0" * 64,
        }
    )
    profile = profile_for(
        materials,
        ("REQ_LINK_TAIL_INSERT", "REQ_LINK_REVERSE"),
    )

    result = PublicationGate().evaluate(profile, materials)

    assert result.status == "blocked"
    assert "unknown_test_reference" in issue_codes(result)
    assert "criterion_binding_missing" in issue_codes(result)


def test_missing_dimension_cannot_leave_a_selected_knowledge_point_unchecked() -> None:
    materials = real_bundle("linked-list")
    profile = profile_for(
        materials,
        ("REQ_LINK_TAIL_INSERT", "REQ_LINK_REVERSE"),
    )
    dimensions = cast(list[dict[str, object]], profile["dimensions"])
    dimensions.pop()
    confirmations = cast(dict[str, object], profile["confirmations"])
    confirmations["dimensions_hash"] = sha256_json(
        {
            "knowledge_points_hash": confirmations["knowledge_points_hash"],
            "dimensions": dimensions,
        }
    )

    result = PublicationGate().evaluate(profile, materials)

    assert result.status == "blocked"
    assert "criterion_binding_missing" in issue_codes(result)


def test_duplicate_dimensions_do_not_count_as_one_deterministic_binding() -> None:
    materials = real_bundle("linked-list")
    profile = profile_for(
        materials,
        ("REQ_LINK_TAIL_INSERT", "REQ_LINK_REVERSE"),
    )
    dimensions = cast(list[dict[str, object]], profile["dimensions"])
    dimensions.append(deepcopy(dimensions[0]))
    confirmations = cast(dict[str, object], profile["confirmations"])
    confirmations["dimensions_hash"] = sha256_json(
        {
            "knowledge_points_hash": confirmations["knowledge_points_hash"],
            "dimensions": dimensions,
        }
    )

    result = PublicationGate().evaluate(profile, materials)

    assert result.status == "blocked"
    assert "criterion_binding_missing" in issue_codes(result)


@pytest.mark.parametrize("alias_kind", ("knowledge_point", "requirement"))
def test_duplicate_stable_id_aliases_cannot_share_one_evidence_path(
    alias_kind: str,
) -> None:
    materials = real_bundle("linked-list")
    profile = profile_for(
        materials,
        ("REQ_LINK_TAIL_INSERT", "REQ_LINK_REVERSE"),
    )
    knowledge_points = cast(list[dict[str, object]], profile["knowledge_points"])
    dimensions = cast(list[dict[str, object]], profile["dimensions"])
    tests = cast(list[dict[str, object]], profile["assessment_tests"])
    if alias_kind == "knowledge_point":
        aliased_point_id = knowledge_points[0]["id"]
        knowledge_points[1]["id"] = aliased_point_id
        dimensions.pop(0)
        dimensions[0]["knowledge_point_id"] = aliased_point_id
        for assessment_test in tests:
            assessment_test["knowledge_point_ids"] = [aliased_point_id]
            assessment_test["content_hash"] = sha256_json(
                {
                    key: value
                    for key, value in assessment_test.items()
                    if key != "content_hash"
                }
            )
    else:
        knowledge_points[1]["material_requirement_id"] = knowledge_points[0][
            "material_requirement_id"
        ]
    knowledge_points_hash = sha256_json(
        {"knowledge_points": knowledge_points}
    )
    confirmations = cast(dict[str, object], profile["confirmations"])
    confirmations["knowledge_points_hash"] = knowledge_points_hash
    confirmations["dimensions_hash"] = sha256_json(
        {
            "knowledge_points_hash": knowledge_points_hash,
            "dimensions": dimensions,
        }
    )
    confirmations["tests_hash"] = sha256_json(
        {
            "assessment_tests": [
                {
                    key: value
                    for key, value in assessment_test.items()
                    if key != "content_hash"
                }
                for assessment_test in tests
            ]
        }
    )

    result = PublicationGate().evaluate(profile, materials)

    assert result.status == "blocked"
    assert "criterion_binding_missing" in issue_codes(result)


@pytest.mark.parametrize(
    "invalid_relation",
    (
        "criterion_material_owner",
        "duplicate_test_id",
        "duplicate_criterion_id",
        "unknown_test_criterion",
        "misowned_test_criterion",
    ),
)
def test_criterion_and_test_relations_cannot_bypass_stable_ownership(
    invalid_relation: str,
) -> None:
    materials = real_bundle("linked-list")
    profile = profile_for(
        materials,
        ("REQ_LINK_TAIL_INSERT", "REQ_LINK_REVERSE"),
    )
    dimensions = cast(list[dict[str, object]], profile["dimensions"])
    assessment_tests = cast(list[dict[str, object]], profile["assessment_tests"])

    if invalid_relation == "criterion_material_owner":
        criteria = cast(
            list[dict[str, object]],
            dimensions[0]["evidence_criteria"],
        )
        criteria[0]["material_requirement_id"] = "REQ_LINK_REVERSE"
    elif invalid_relation == "duplicate_test_id":
        assessment_tests.append(deepcopy(assessment_tests[0]))
    elif invalid_relation == "duplicate_criterion_id":
        first_criterion = cast(
            list[dict[str, object]], dimensions[0]["evidence_criteria"]
        )[0]["id"]
        second_criteria = cast(
            list[dict[str, object]], dimensions[1]["evidence_criteria"]
        )
        second_criteria[0]["id"] = first_criterion
        second_bindings = cast(
            list[dict[str, object]], dimensions[1]["verification_bindings"]
        )
        second_bindings[0]["criterion_id"] = first_criterion
        second_test = next(
            assessment_test
            for assessment_test in assessment_tests
            if dimensions[1]["knowledge_point_id"]
            in cast(list[object], assessment_test["knowledge_point_ids"])
        )
        second_test["criterion_ids"] = [first_criterion]
        second_test["content_hash"] = sha256_json(
            {
                key: value
                for key, value in second_test.items()
                if key != "content_hash"
            }
        )
    elif invalid_relation == "unknown_test_criterion":
        assessment_tests[0]["criterion_ids"] = [
            *cast(list[object], assessment_tests[0]["criterion_ids"]),
            "CRIT_UNKNOWN1",
        ]
        assessment_tests[0]["content_hash"] = sha256_json(
            {
                key: value
                for key, value in assessment_tests[0].items()
                if key != "content_hash"
            }
        )
    else:
        assessment_tests[-1]["knowledge_point_ids"] = [
            dimensions[0]["knowledge_point_id"]
        ]
        assessment_tests[-1]["content_hash"] = sha256_json(
            {
                key: value
                for key, value in assessment_tests[-1].items()
                if key != "content_hash"
            }
        )
    reconfirm_profile(profile)

    result = PublicationGate().evaluate(profile, materials)

    assert result.status == "blocked"
    assert set(issue_codes(result)) & {
        "criterion_binding_missing",
        "unknown_test_reference",
    }


def test_require_ready_returns_only_the_bounded_safe_gate_projection() -> None:
    materials = real_bundle("sequence-list")
    profile = profile_for(
        materials,
        tuple(requirement.id for requirement in materials.requirements),
    )

    with pytest.raises(PublicationGateBlockedError) as captured:
        PublicationGate().require_ready(profile, materials)

    details = captured.value.details
    assert details is not None
    assert details["status"] == "blocked"
    serialized = json.dumps(details, ensure_ascii=False)
    assert len(serialized.encode("utf-8")) <= 32_768
    assert "teacher prose is not a gate key" not in serialized
    assert "expected_stdout" not in serialized
    assert "input" not in serialized


def test_blocked_error_projection_stays_bounded_at_the_material_contract_limit() -> None:
    materials = real_bundle("linked-list")
    template = materials.requirements[0]
    materials = materials.model_copy(
        update={
            "requirements": tuple(
                template.model_copy(
                    update={
                        "id": f"REQ_BOUND_{index:03d}",
                        "student_responsibility": True,
                        "test_ids": (),
                        "detector_profile_ids": (),
                    }
                )
                for index in range(128)
            )
        }
    )
    profile = profile_for(real_bundle("linked-list"), ())
    confirmations = cast(dict[str, object], profile["confirmations"])
    confirmations["material_bundle_hash"] = materials.bundle_hash

    with pytest.raises(PublicationGateBlockedError) as captured:
        PublicationGate().require_ready(profile, materials)

    assert captured.value.details is not None
    encoded = json.dumps(
        captured.value.details,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) <= 32_768
    assert captured.value.details["blocking_count"] == 128


def test_contract_valid_combined_issue_space_exceeds_512_but_projects_safely() -> None:
    raw = cast(
        dict[str, object],
        json.loads((MATERIALS / "linked-list" / "bundle.json").read_text("utf-8")),
    )
    starter = cast(dict[str, object], raw["starter_source"])
    source_path = MATERIALS / "linked-list" / cast(str, starter["file_name"])
    starter["content_base64"] = base64.b64encode(source_path.read_bytes()).decode()
    requirement_template = cast(list[dict[str, object]], raw["requirements"])[0]
    raw["requirements"] = [
        {
            **requirement_template,
            "id": f"REQ_BOUND_{index:03d}",
            "student_responsibility": True,
            "test_ids": [],
            "detector_profile_ids": [],
        }
        for index in range(128)
    ]
    sealed = deepcopy(raw)
    sealed.pop("bundle_hash")
    cast(dict[str, object], sealed["starter_source"]).pop("content_base64")
    raw["bundle_hash"] = sha256(
        json.dumps(
            sealed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

    class MaxContractGateway:
        def get_bundle(
            self,
            _principal: Principal,
            _space_id: str,
            _parent_algorithm_id: str,
        ) -> dict[str, object]:
            return deepcopy(raw)

    materials = AssessmentMaterialService(MaxContractGateway()).get_bundle(
        Principal("teacher-1", "teacher-a", "teacher-token"),
        cast(str, raw["space_id"]),
        cast(str, raw["parent_algorithm_id"]),
    )
    profile = profile_for(real_bundle("linked-list"), ())
    profile_starter = cast(dict[str, object], profile["starter_source"])
    profile_starter["artifact_id"] = "ART_WRONG"
    profile["knowledge_points"] = [
        {
            "id": f"KP_OWN{index:05d}",
            "material_requirement_id": f"REQ_UNKNOWN_{index}",
            "name": f"point {index}",
            "description": f"description {index}",
            "source": "teacher",
            "order": index,
        }
        for index in range(10)
    ]
    profile["assessment_tests"] = [
        {
            "id": f"TEST_MAX{test_index:05d}",
            "name": f"test {test_index}",
            "knowledge_point_ids": [
                f"KP_{test_index * 10 + point_index:08X}"
                for point_index in range(10)
            ],
            "criterion_ids": [
                f"CRIT_{test_index * 20 + criterion_index:08X}"
                for criterion_index in range(20)
            ],
            "kind": "stdin_stdout",
            "input": "",
            "expected_stdout": "",
            "comparison": "normalized_text_v1",
            "timeout_ms": 100,
            "enabled": True,
            "source": "teacher",
            "order": test_index,
            "content_hash": "0" * 64,
        }
        for test_index in range(30)
    ]
    profile["dimensions"] = [
        {
            "knowledge_point_id": f"KP_{index:08X}",
            "name": f"dimension {index}",
            "question": f"question {index}",
            "evidence_criteria": [
                {
                    "id": f"CRIT_DIM{index:05d}",
                    "material_requirement_id": f"REQ_DIM_{index}",
                    "statement": f"criterion {index}",
                    "required": True,
                }
            ],
            "verification_bindings": [
                {
                    "criterion_id": f"CRIT_DIM{index:05d}",
                    "kind": "assessment_test",
                    "assessment_test_id": f"TEST_MAX{index:05d}",
                }
            ],
            "analysis_config": {"mode": "evidence_binding"},
        }
        for index in range(10)
    ]
    profile["confirmations"] = {
        "material_bundle_hash": "0" * 64,
        "starter_source_hash": "0" * 64,
        "knowledge_points_hash": "0" * 64,
        "dimensions_hash": "0" * 64,
        "tests_hash": "0" * 64,
    }
    ClassroomSchemaRegistry(ROOT / "contracts" / "classroom" / "v1").validate(
        "plan-draft",
        {
            "schema_version": 1,
            "draft_id": "09e4e1cc-9155-42dd-a951-632148040bd8",
            "space_id": materials.space_id,
            "parent_algorithm_id": materials.parent_algorithm_id,
            "title": "maximum issue profile",
            "profile": profile,
            "scheduled_start_at": "2026-08-28T08:00:00Z",
            "scheduled_end_at": "2026-08-28T08:30:00Z",
            "ai_policy": "prohibited",
            "revision": 0,
            "updated_at": "2026-08-28T07:00:00Z",
        },
    )

    result = PublicationGate().evaluate(profile, materials)

    assert result.status == "blocked"
    assert result.blocking_count > 512
    assert len(result.issues) > 512
    with pytest.raises(PublicationGateBlockedError) as captured:
        PublicationGate().require_ready(profile, materials)
    assert captured.value.status_code == 409
    assert captured.value.details is not None
    assert captured.value.details["blocking_count"] == result.blocking_count
    ClassroomSchemaRegistry(ROOT / "contracts" / "classroom" / "v1").validate(
        "error",
        {
            "schema_version": 1,
            "error": {
                "code": "publication_gate_blocked",
                "message": "课堂服务请求未能完成。",
                "retryable": False,
                "request_id": "req-max-issues",
                "details": captured.value.details,
            },
        },
    )
    encoded = json.dumps(
        captured.value.details,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    assert len(encoded) <= 32_768
