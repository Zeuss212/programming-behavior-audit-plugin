from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from classroom_sync.auth.fincolab import StudentChildExperiment
from classroom_sync.canonical import sha256_json
from classroom_sync.domain.schemas import ClassroomSchemaRegistry
from classroom_sync.errors import ConflictError, PublicationGateBlockedError
from classroom_sync.models import Base, PlanAuthoringSession, PlanVersion, StudentAssignment
from classroom_sync.services.assignments import AssignmentService
from classroom_sync.services.plans import PlanDraftInput, PlanService
from tests.unit.test_publication_gate import profile_for, real_bundle


def profile_draft(question: str) -> dict[str, object]:
    return {
        "schema_version": 2,
        "problem_id": "dictionary-basics",
        "title": "字典数据结构",
        "problem_context": {
            "statement": "实现一个字典读取函数。",
            "language": "python",
            "submission_contract": {"kind": "function", "entrypoint": "lookup"},
        },
        "knowledge_points": [
            {
                "id": "KP_DICT0001",
                "name": "字典读取",
                "description": "能根据键读取字典中的值。",
                "source": "teacher",
                "order": 0,
            }
        ],
        "assessment_tests": [
            {
                "id": "TEST_DICT0001",
                "name": "读取存在的键",
                "knowledge_point_ids": ["KP_DICT0001"],
                "kind": "function_call",
                "input": "{'data': {'name': 'Ada'}, 'key': 'name'}",
                "expected": "Ada",
                "enabled": True,
                "source": "teacher",
                "order": 0,
            }
        ],
        "confirmations": {"knowledge_points_hash": None, "tests_hash": None},
        "dimensions": [
            {
                "knowledge_point_id": "KP_DICT0001",
                "name": "字典读取",
                "question": question,
                "evidence_criteria": [
                    {
                        "id": "uses_lookup",
                        "direction": "support",
                        "statement": "代码使用键读取字典值。",
                    },
                    {
                        "id": "returns_literal",
                        "direction": "exclude",
                        "statement": "代码直接返回固定值。",
                    },
                ],
                "levels": [
                    {
                        "code": "possible",
                        "name": "可能掌握",
                        "definition": "有一次正确读取。",
                    },
                    {
                        "code": "clear",
                        "name": "明确掌握",
                        "definition": "通过运行验证读取逻辑。",
                    },
                ],
                "teaching_actions": {
                    "possible": "追问边界输入。",
                    "clear": "进入下一题。",
                    "not_observed": "安排补充练习。",
                },
                "analysis_config": {
                    "mode": "llm_evidence",
                    "minimum_observation": {"run_count": 1},
                },
            }
        ],
    }


def test_republish_moves_only_unaccepted_assignments_to_the_new_plan_version():
    """A student accepting v1 keeps it even when the teacher publishes v2."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository_root = Path(__file__).resolve().parents[4]
    schema_registry = ClassroomSchemaRegistry(repository_root / "contracts" / "classroom" / "v1")
    now = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)
    plan_service = PlanService(session_factory, schema_registry, clock=lambda: now)
    assignment_service = AssignmentService(session_factory, clock=lambda: now)
    draft = plan_service.create_draft(
        PlanDraftInput(
            space_id="space-1",
            parent_algorithm_id="parent-1",
            title="字典课堂练习",
            profile=profile_draft("学生是否正确读取字典中的值？"),
            scheduled_start_at=now,
            scheduled_end_at=now + timedelta(minutes=30),
            ai_policy="prohibited",
        ),
        teacher_id="teacher-1",
    )
    version_one = plan_service.publish_draft(draft.id, teacher_id="teacher-1")
    roster = (
        StudentChildExperiment("student-1", "student-a", "child-1", "workbench-1"),
        StudentChildExperiment("student-2", "student-b", "child-2", "workbench-2"),
    )
    initial_assignments = assignment_service.sync_assignments(version_one, roster)
    accepted_assignment = assignment_service.accept_assignment(
        initial_assignments[0].id, student_id="student-1"
    )

    plan_service.update_draft(
        draft.id,
        profile=profile_draft("学生能否在运行结果中验证字典读取逻辑？"),
        teacher_id="teacher-1",
    )
    version_two = plan_service.publish_draft(draft.id, teacher_id="teacher-1")
    resynchronized_assignments = assignment_service.sync_assignments(version_two, roster)
    repeated_assignments = assignment_service.sync_assignments(version_two, roster)

    assignments_by_student = {
        assignment.student_id: assignment for assignment in resynchronized_assignments
    }
    repeated_by_student = {assignment.student_id: assignment for assignment in repeated_assignments}
    assert version_one.version == 1
    assert version_two.version == 2
    assert version_one.content_hash != version_two.content_hash
    assert accepted_assignment.status == "ready"
    assert assignments_by_student["student-1"].plan_version == 1
    assert assignments_by_student["student-2"].plan_version == 2
    assert assignments_by_student["student-1"].id == initial_assignments[0].id
    assert assignments_by_student["student-2"].id == initial_assignments[1].id
    assert repeated_by_student["student-1"].id == initial_assignments[0].id
    assert repeated_by_student["student-2"].id == initial_assignments[1].id


def test_new_plan_creates_a_fresh_assignment_after_prior_plan_is_submitted():
    """A new classroom plan must not reuse a student's submitted prior assignment."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository_root = Path(__file__).resolve().parents[4]
    schema_registry = ClassroomSchemaRegistry(repository_root / "contracts" / "classroom" / "v1")
    now = datetime(2026, 8, 16, 8, 0, tzinfo=UTC)
    plan_service = PlanService(session_factory, schema_registry, clock=lambda: now)
    assignment_service = AssignmentService(session_factory, clock=lambda: now)
    roster = (StudentChildExperiment("student-1", "student-a", "child-1", "workbench-1"),)

    first_draft = plan_service.create_draft(
        PlanDraftInput(
            space_id="space-1",
            parent_algorithm_id="parent-1",
            title="第一节字典课堂",
            profile=profile_draft("学生是否能读取字典中的值？"),
            scheduled_start_at=now,
            scheduled_end_at=now + timedelta(minutes=30),
            ai_policy="prohibited",
        ),
        teacher_id="teacher-1",
    )
    first_plan = plan_service.publish_draft(first_draft.id, teacher_id="teacher-1")
    first_assignment = assignment_service.sync_assignments(first_plan, roster)[0]
    assignment_service.accept_assignment(first_assignment.id, student_id="student-1")
    with session_factory.begin() as session:
        persisted = session.get(StudentAssignment, first_assignment.id)
        assert persisted is not None
        persisted.status = "submitted"

    second_draft = plan_service.create_draft(
        PlanDraftInput(
            space_id="space-1",
            parent_algorithm_id="parent-1",
            title="第二节字典课堂",
            profile=profile_draft("学生是否能通过运行验证字典读取？"),
            scheduled_start_at=now,
            scheduled_end_at=now + timedelta(minutes=30),
            ai_policy="prohibited",
        ),
        teacher_id="teacher-1",
    )
    second_plan = plan_service.publish_draft(second_draft.id, teacher_id="teacher-1")
    second_assignment = assignment_service.sync_assignments(second_plan, roster)[0]

    assert first_plan.plan_id != second_plan.plan_id
    assert second_assignment.id != first_assignment.id
    assert second_assignment.plan_id == second_plan.plan_id
    assert second_assignment.plan_version == 1
    assert second_assignment.status == "pending_acceptance"
    assert assignment_service.accept_assignment(
        second_assignment.id, student_id="student-1"
    ).status == "ready"
    with session_factory() as session:
        preserved = session.get(StudentAssignment, first_assignment.id)
        assert preserved is not None
        assert preserved.status == "submitted"


def test_v3_publish_closes_its_locked_authoring_session_in_the_same_transaction():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository_root = Path(__file__).resolve().parents[4]
    schema_registry = ClassroomSchemaRegistry(repository_root / "contracts" / "classroom" / "v1")
    now = datetime(2026, 8, 28, 8, 0, tzinfo=UTC)
    materials = real_bundle("linked-list")
    profile = profile_for(
        materials,
        ("REQ_LINK_TAIL_INSERT", "REQ_LINK_REVERSE"),
    )
    with session_factory.begin() as session:
        session.add(
            PlanAuthoringSession(
                id="authoring-1",
                teacher_id="teacher-1",
                space_id=materials.space_id,
                parent_algorithm_id=materials.parent_algorithm_id,
                status="open",
                active_slot=1,
                suggestion_job_id=None,
                published_plan_id=None,
                created_at=now,
                updated_at=now,
                closed_at=None,
            )
        )
    service = PlanService(session_factory, schema_registry, clock=lambda: now)
    draft = service.create_draft(
        PlanDraftInput(
            authoring_session_id="authoring-1",
            space_id=materials.space_id,
            parent_algorithm_id=materials.parent_algorithm_id,
            title="linked list lesson",
            profile=profile,
            scheduled_start_at=now,
            scheduled_end_at=now + timedelta(minutes=30),
            ai_policy="prohibited",
        ),
        teacher_id="teacher-1",
    )

    published = service.publish_draft(
        draft.id,
        teacher_id="teacher-1",
        materials=materials,
    )

    with session_factory() as session:
        authoring = session.get(PlanAuthoringSession, "authoring-1")
        assert authoring is not None
        assert authoring.status == "published"
        assert authoring.active_slot is None
        assert authoring.published_plan_id == draft.id
        assert authoring.closed_at is not None
        assert authoring.closed_at.replace(tzinfo=UTC) == now
        assert session.get(PlanVersion, published.id) is not None


def test_v3_publish_requires_the_draft_to_be_linked_to_an_authoring_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository_root = Path(__file__).resolve().parents[4]
    schema_registry = ClassroomSchemaRegistry(repository_root / "contracts" / "classroom" / "v1")
    now = datetime(2026, 8, 28, 8, 0, tzinfo=UTC)
    materials = real_bundle("linked-list")
    service = PlanService(session_factory, schema_registry, clock=lambda: now)
    draft = service.create_draft(
        PlanDraftInput(
            space_id=materials.space_id,
            parent_algorithm_id=materials.parent_algorithm_id,
            title="unlinked v3 draft",
            profile=profile_for(
                materials,
                ("REQ_LINK_TAIL_INSERT", "REQ_LINK_REVERSE"),
            ),
            scheduled_start_at=now,
            scheduled_end_at=now + timedelta(minutes=30),
            ai_policy="prohibited",
        ),
        teacher_id="teacher-1",
    )

    with pytest.raises(ConflictError, match="plan_authoring_session_required"):
        service.publish_draft(
            draft.id,
            teacher_id="teacher-1",
            materials=materials,
        )

    with session_factory() as session:
        assert session.query(PlanVersion).filter_by(plan_id=draft.id).count() == 0


@pytest.mark.parametrize(
    "invalid_binding",
    (
        "unknown",
        "disabled",
        "missing_dimension",
        "duplicate_dimension",
        "duplicate_point_id",
        "duplicate_requirement",
    ),
)
def test_v3_publish_blocks_invalid_evidence_bindings_and_keeps_session_open(
    invalid_binding: str,
):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository_root = Path(__file__).resolve().parents[4]
    schema_registry = ClassroomSchemaRegistry(repository_root / "contracts" / "classroom" / "v1")
    now = datetime(2026, 8, 28, 8, 0, tzinfo=UTC)
    materials = real_bundle("linked-list")
    if invalid_binding == "disabled":
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
                "assessment_tests": (
                    disabled_test,
                    *materials.assessment_tests[1:],
                ),
                "bundle_hash": "0" * 64,
            }
        )
    profile = profile_for(
        materials,
        ("REQ_LINK_TAIL_INSERT", "REQ_LINK_REVERSE"),
    )
    if invalid_binding in {"duplicate_point_id", "duplicate_requirement"}:
        knowledge_points = profile["knowledge_points"]
        dimensions = profile["dimensions"]
        assessment_tests = profile["assessment_tests"]
        if invalid_binding == "duplicate_point_id":
            aliased_point_id = knowledge_points[0]["id"]
            knowledge_points[1]["id"] = aliased_point_id
            dimensions.pop(0)
            dimensions[0]["knowledge_point_id"] = aliased_point_id
            for assessment_test in assessment_tests:
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
        confirmations = profile["confirmations"]
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
    if invalid_binding in {"unknown", "missing_dimension", "duplicate_dimension"}:
        dimensions = profile["dimensions"]
        if invalid_binding == "unknown":
            dimensions[0]["verification_bindings"][0][
                "assessment_test_id"
            ] = "TEST_BAD00001"
        elif invalid_binding == "missing_dimension":
            dimensions.pop()
        else:
            dimensions.append(deepcopy(dimensions[0]))
        confirmations = profile["confirmations"]
        confirmations["dimensions_hash"] = sha256_json(
            {
                "knowledge_points_hash": confirmations["knowledge_points_hash"],
                "dimensions": dimensions,
            }
        )
    with session_factory.begin() as session:
        session.add(
            PlanAuthoringSession(
                id="authoring-invalid-binding",
                teacher_id="teacher-1",
                space_id=materials.space_id,
                parent_algorithm_id=materials.parent_algorithm_id,
                status="open",
                active_slot=1,
                suggestion_job_id=None,
                published_plan_id=None,
                created_at=now,
                updated_at=now,
                closed_at=None,
            )
        )
    service = PlanService(session_factory, schema_registry, clock=lambda: now)
    draft = service.create_draft(
        PlanDraftInput(
            authoring_session_id="authoring-invalid-binding",
            space_id=materials.space_id,
            parent_algorithm_id=materials.parent_algorithm_id,
            title="invalid binding lesson",
            profile=profile,
            scheduled_start_at=now,
            scheduled_end_at=now + timedelta(minutes=30),
            ai_policy="prohibited",
        ),
        teacher_id="teacher-1",
    )

    with pytest.raises(PublicationGateBlockedError) as captured:
        service.publish_draft(
            draft.id,
            teacher_id="teacher-1",
            materials=materials,
        )

    assert captured.value.details is not None
    issue_codes = {
        issue["code"] for issue in captured.value.details["issues"]
    }
    assert "criterion_binding_missing" in issue_codes
    if invalid_binding in {"unknown", "disabled"}:
        assert "unknown_test_reference" in issue_codes
    with session_factory() as session:
        authoring = session.get(
            PlanAuthoringSession,
            "authoring-invalid-binding",
        )
        assert authoring is not None
        assert authoring.status == "open"
        assert authoring.active_slot == 1
        assert authoring.published_plan_id is None
        assert session.query(PlanVersion).filter_by(plan_id=draft.id).count() == 0


def test_v3_publish_validation_failure_rolls_back_and_keeps_session_open():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository_root = Path(__file__).resolve().parents[4]
    real_registry = ClassroomSchemaRegistry(repository_root / "contracts" / "classroom" / "v1")
    now = datetime(2026, 8, 28, 8, 0, tzinfo=UTC)
    materials = real_bundle("linked-list")
    profile = profile_for(
        materials,
        ("REQ_LINK_TAIL_INSERT", "REQ_LINK_REVERSE"),
    )

    class FailingPlanVersionRegistry:
        def validate(self, schema_name: str, payload: object) -> None:
            if schema_name == "plan-version":
                raise ValueError("forced plan-version validation failure")
            real_registry.validate(schema_name, payload)

    with session_factory.begin() as session:
        session.add(
            PlanAuthoringSession(
                id="authoring-rollback",
                teacher_id="teacher-1",
                space_id=materials.space_id,
                parent_algorithm_id=materials.parent_algorithm_id,
                status="open",
                active_slot=1,
                suggestion_job_id=None,
                published_plan_id=None,
                created_at=now,
                updated_at=now,
                closed_at=None,
            )
        )
    create_service = PlanService(session_factory, real_registry, clock=lambda: now)
    draft = create_service.create_draft(
        PlanDraftInput(
            authoring_session_id="authoring-rollback",
            space_id=materials.space_id,
            parent_algorithm_id=materials.parent_algorithm_id,
            title="linked list lesson",
            profile=profile,
            scheduled_start_at=now,
            scheduled_end_at=now + timedelta(minutes=30),
            ai_policy="prohibited",
        ),
        teacher_id="teacher-1",
    )
    failing_service = PlanService(
        session_factory,
        FailingPlanVersionRegistry(),  # type: ignore[arg-type]
        clock=lambda: now,
    )

    try:
        failing_service.publish_draft(
            draft.id,
            teacher_id="teacher-1",
            materials=materials,
        )
    except ValueError as error:
        assert str(error) == "forced plan-version validation failure"
    else:
        raise AssertionError("publish should have failed validation")

    with session_factory() as session:
        authoring = session.get(PlanAuthoringSession, "authoring-rollback")
        assert authoring is not None
        assert authoring.status == "open"
        assert authoring.active_slot == 1
        assert authoring.published_plan_id is None
        assert authoring.closed_at is None
        assert session.query(PlanVersion).filter_by(plan_id=draft.id).count() == 0


def test_v3_publish_insert_failure_rolls_back_and_keeps_session_open():
    class RejectPlanVersionSession(Session):
        def flush(self, objects: object = None) -> None:
            if any(isinstance(item, PlanVersion) for item in self.new):
                raise IntegrityError(
                    "INSERT INTO plan_versions",
                    {},
                    RuntimeError("forced insert failure"),
                )
            super().flush(objects)  # type: ignore[arg-type]

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(
        bind=engine,
        class_=RejectPlanVersionSession,
        expire_on_commit=False,
    )
    repository_root = Path(__file__).resolve().parents[4]
    schema_registry = ClassroomSchemaRegistry(repository_root / "contracts" / "classroom" / "v1")
    now = datetime(2026, 8, 28, 8, 0, tzinfo=UTC)
    materials = real_bundle("linked-list")
    profile = profile_for(
        materials,
        ("REQ_LINK_TAIL_INSERT", "REQ_LINK_REVERSE"),
    )
    with session_factory.begin() as session:
        session.add(
            PlanAuthoringSession(
                id="authoring-insert-rollback",
                teacher_id="teacher-1",
                space_id=materials.space_id,
                parent_algorithm_id=materials.parent_algorithm_id,
                status="open",
                active_slot=1,
                suggestion_job_id=None,
                published_plan_id=None,
                created_at=now,
                updated_at=now,
                closed_at=None,
            )
        )
    service = PlanService(session_factory, schema_registry, clock=lambda: now)
    draft = service.create_draft(
        PlanDraftInput(
            authoring_session_id="authoring-insert-rollback",
            space_id=materials.space_id,
            parent_algorithm_id=materials.parent_algorithm_id,
            title="linked list lesson",
            profile=profile,
            scheduled_start_at=now,
            scheduled_end_at=now + timedelta(minutes=30),
            ai_policy="prohibited",
        ),
        teacher_id="teacher-1",
    )

    with pytest.raises(IntegrityError, match="forced insert failure"):
        service.publish_draft(
            draft.id,
            teacher_id="teacher-1",
            materials=materials,
        )

    with session_factory() as session:
        authoring = session.get(
            PlanAuthoringSession,
            "authoring-insert-rollback",
        )
        assert authoring is not None
        assert authoring.status == "open"
        assert authoring.active_slot == 1
        assert authoring.published_plan_id is None
        assert authoring.closed_at is None
        assert session.query(PlanVersion).filter_by(plan_id=draft.id).count() == 0
