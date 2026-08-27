"""Frozen, real-material C++ Profile v3 payloads for schema contracts."""

from __future__ import annotations

import hashlib
import importlib.util
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
_CANONICAL_JSON_SPEC = importlib.util.spec_from_file_location(
    "profile_v3_canonical_json", ROOT / "myextension" / "canonical_json.py"
)
assert _CANONICAL_JSON_SPEC is not None
assert _CANONICAL_JSON_SPEC.loader is not None
_CANONICAL_JSON_MODULE = importlib.util.module_from_spec(_CANONICAL_JSON_SPEC)
_CANONICAL_JSON_SPEC.loader.exec_module(_CANONICAL_JSON_MODULE)
sha256_json = _CANONICAL_JSON_MODULE.sha256_json

LINKED_LIST_SOURCE = (
    ROOT
    / "deploy"
    / "classroom"
    / "local-demo"
    / "materials"
    / "linked-list"
    / "链表操作练习02.cpp"
)


def profile_v3_draft() -> dict[str, object]:
    """Build a complete C++ draft from the approved linked-list material."""

    source_bytes = LINKED_LIST_SOURCE.read_bytes()
    starter_source = {
        "artifact_id": "ART_LINKED_LIST_CPP_01",
        "display_name": "链表操作练习02.cpp",
        "file_name": "链表操作练习02.cpp",
        "sha256": hashlib.sha256(source_bytes).hexdigest(),
        "size_bytes": len(source_bytes),
        "source": "fincolab_experiment",
    }
    knowledge_points = [
        {
            "id": "KP_LINKTAL1",
            "material_requirement_id": "REQ_LINK_TAIL_INSERT",
            "name": "链表尾插",
            "description": "能够把输入元素依次插入链表尾部，并使倒置前输出与输入顺序一致。",
            "source": "teacher",
            "order": 0,
        }
    ]
    tests_without_hashes = [
        {
            "id": "TEST_LINK0001",
            "name": "六元素链表逆置",
            "knowledge_point_ids": ["KP_LINKTAL1"],
            "criterion_ids": ["CRIT_LINKTAL1"],
            "kind": "stdin_stdout",
            "input": "6\n1 2 3 4 5 6\n",
            "expected_stdout": "倒置前为：1 2 3 4 5 6 \n倒置后为：6 5 4 3 2 1\n",
            "comparison": "normalized_text_v1",
            "timeout_ms": 2000,
            "enabled": True,
            "source": "teacher",
            "order": 0,
        },
        {
            "id": "TEST_LINK0002",
            "name": "七元素链表逆置",
            "knowledge_point_ids": ["KP_LINKTAL1"],
            "criterion_ids": ["CRIT_LINKTAL1"],
            "kind": "stdin_stdout",
            "input": "7\n12 3 14 89 12 54 123\n",
            "expected_stdout": "倒置前为：12 3 14 89 12 54 123 \n倒置后为：123 54 12 89 14 3 12\n",
            "comparison": "normalized_text_v1",
            "timeout_ms": 2000,
            "enabled": True,
            "source": "teacher",
            "order": 1,
        },
    ]
    assessment_tests = [
        {**test, "content_hash": sha256_json(test)} for test in tests_without_hashes
    ]
    dimensions = [
        {
            "knowledge_point_id": "KP_LINKTAL1",
            "name": "链表尾插",
            "question": "能够把输入元素依次插入链表尾部，并使倒置前输出与输入顺序一致。",
            "evidence_criteria": [
                {
                    "id": "CRIT_LINKTAL1",
                    "material_requirement_id": "REQ_LINK_TAIL_INSERT",
                    "statement": "倒置前输出与输入顺序一致。",
                    "required": True,
                }
            ],
            "verification_bindings": [
                {
                    "criterion_id": "CRIT_LINKTAL1",
                    "kind": "assessment_test",
                    "assessment_test_id": "TEST_LINK0001",
                }
            ],
            "analysis_config": {"mode": "evidence_binding"},
        }
    ]
    knowledge_points_hash = sha256_json({"knowledge_points": knowledge_points})
    return {
        "schema_version": 3,
        "problem_id": "linked-list-reverse",
        "title": "链表尾插与逆置",
        "problem_context": {
            "statement": "完善带头结点的单链表类定义，完成尾插与链表倒置。",
            "language": "cpp",
            "submission_contract": {"kind": "stdin_stdout"},
            "toolchain_profile": "cpp17_stdio_v1",
            "entry_file": "链表操作练习02.cpp",
            "source_encoding": "utf-8",
        },
        "starter_source": starter_source,
        "knowledge_points": knowledge_points,
        "assessment_tests": assessment_tests,
        "dimensions": dimensions,
        "confirmations": {
            "material_bundle_hash": sha256_json({"material_bundle": "linked-list"}),
            "starter_source_hash": sha256_json(starter_source),
            "knowledge_points_hash": knowledge_points_hash,
            "dimensions_hash": sha256_json(
                {
                    "knowledge_points_hash": knowledge_points_hash,
                    "dimensions": dimensions,
                }
            ),
            "tests_hash": sha256_json({"assessment_tests": tests_without_hashes}),
        },
    }


def profile_v3_version() -> dict[str, object]:
    """Build a publishable version whose hashes derive from the real fixture."""

    draft = profile_v3_draft()
    content = deepcopy(draft)
    return {
        **draft,
        "profile_id": "2b16b5c0-4e58-48f9-9448-9067de005e4a",
        "version": 1,
        "content_hash": sha256_json(content),
        "deployment_status": "pilot",
        "preview_status": "pending_real_samples",
    }
