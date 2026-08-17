from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from classroom_sync.auth.fincolab import StudentChildExperiment
from classroom_sync.domain.schemas import ClassroomSchemaRegistry
from classroom_sync.models import Base, StudentAssignment
from classroom_sync.services.assignments import AssignmentService
from classroom_sync.services.plans import PlanDraftInput, PlanService


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
