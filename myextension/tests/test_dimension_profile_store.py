import json
import math
import os
import re
import stat
import threading
import unicodedata
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import ValidationError

from myextension.canonical_json import (
    atomic_write_json,
    canonical_json_bytes,
    sha256_json,
)
from myextension.dimension_profile_store import (
    DimensionProfileStore,
    InvalidProfileIdError,
    ProfileConflictError,
)
from myextension.dimension_template_store import get_template, list_templates
from myextension.profile_validator import ProfileValidationError, validate_profile_draft


LEVELS = [
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


def make_dimension(**overrides):
    dimension = {
        "code": "CUSTOM_A1B2C3D4",
        "name": "失败后的验证",
        "question": "学生运行失败后，是否修改相关代码并再次验证？",
        "evidence_criteria": [
            {
                "id": "support-1",
                "direction": "support",
                "statement": "失败运行后修改相关代码并再次运行",
            },
            {
                "id": "exclude-1",
                "direction": "exclude",
                "statement": "只修改注释或运行无关 Cell 不计入",
            },
        ],
        "levels": deepcopy(LEVELS),
        "teaching_actions": {
            "possible": "结合证据询问调试思路",
            "clear": "安排修改后立即验证的短练习",
        },
        "analysis_config": {
            "mode": "llm_evidence",
            "minimum_observation": {
                "valid_observation_duration_ms": 30000,
                "edit_event_count": 1,
            },
        },
    }
    dimension.update(overrides)
    return dimension


def make_profile_payload(*, question=None, dimensions=None, **overrides):
    dimension = make_dimension()
    if question is not None:
        dimension["question"] = question
    payload = {
        "schema_version": 1,
        "problem_id": "synthetic-debug-problem",
        "title": "合成调试题观察方案",
        "dimensions": dimensions if dimensions is not None else [dimension],
    }
    payload.update(overrides)
    return payload


EXPECTED_TEMPLATE_MEANINGS = [
    (
        "repeated-editing",
        "学生是否在同一任务阶段反复改写相近代码？",
        "同一 Cell 或文件区域多次删除、恢复或小范围改写",
        "正常的一次性重构或格式化不计入",
        {"valid_observation_duration_ms": 60000, "edit_event_count": 3},
    ),
    (
        "debug-chain",
        "学生运行失败后，是否修改相关代码并再次验证？",
        "失败运行后修改相关代码并再次运行",
        "只修改注释或运行无关 Cell 不计入",
        {"edit_event_count": 1, "run_count": 1},
    ),
    (
        "repeated-run-failures",
        "学生是否连续运行失败且没有形成有效修复？",
        "多次运行失败，错误持续或在未解决时反复出现",
        "单次失败后及时修复不计入",
        {"run_count": 2},
    ),
    (
        "pause-without-validation",
        "学生主动停顿后是否缺少及时的运行验证？",
        "有效观察期间停顿，之后继续编辑但没有及时运行",
        "页面离开、程序运行等待或停顿后及时运行不计入",
        {"valid_observation_duration_ms": 60000, "edit_event_count": 2},
    ),
]


def test_template_catalog_has_four_teacher_facing_templates_in_fixed_order():
    templates = list_templates()

    assert [item["template_id"] for item in templates] == [
        "repeated-editing",
        "debug-chain",
        "repeated-run-failures",
        "pause-without-validation",
    ]


@pytest.mark.parametrize(
    ("template_id", "question", "support", "exclusion", "minimum"),
    EXPECTED_TEMPLATE_MEANINGS,
)
def test_template_preserves_exact_teacher_meaning_and_hidden_defaults(
    template_id, question, support, exclusion, minimum
):
    template = get_template(template_id)

    assert template["schema_version"] == 1
    assert template["version"] == 1
    assert template["deployment_status"] == "pilot"
    assert template["question"] == question
    assert template["evidence_criteria"] == [
        {"id": "support-1", "direction": "support", "statement": support},
        {"id": "exclude-1", "direction": "exclude", "statement": exclusion},
    ]
    assert template["levels"] == LEVELS
    assert template["analysis_config"] == {
        "mode": "llm_evidence",
        "minimum_observation": minimum,
    }
    assert [example["kind"] for example in template["examples"]] == [
        "positive",
        "negative",
    ]
    assert all(set(example) == {"kind", "summary"} for example in template["examples"])
    assert all(example["summary"].strip() for example in template["examples"])
    assert set(template["teaching_actions"]) >= {"possible", "clear"}


def test_get_template_rejects_unknown_id_and_version():
    with pytest.raises(KeyError):
        get_template("../debug-chain")
    with pytest.raises(KeyError):
        get_template("debug-chain", version=2)


def test_canonical_json_recursively_normalizes_nfc_and_sorts_keys():
    decomposed = "e\u0301"
    value = {"z": [decomposed, {"n\u0303": decomposed}], "a": "先"}

    assert canonical_json_bytes(value) == (
        '{"a":"先","z":["é",{"ñ":"é"}]}'.encode("utf-8")
    )


def test_canonical_json_rejects_keys_that_collide_after_nfc():
    with pytest.raises(ValueError, match="collid"):
        canonical_json_bytes({"é": 1, "e\u0301": 2})


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_canonical_json_rejects_non_finite_floats(value):
    with pytest.raises(ValueError, match="finite"):
        canonical_json_bytes({"value": value})


def test_sha256_json_is_stable_across_equivalent_unicode_and_key_order():
    assert sha256_json({"é": "n\u0303", "a": 1}) == sha256_json(
        {"a": 1, "e\u0301": "ñ"}
    )


def test_atomic_write_uses_same_directory_fsync_replace_and_private_mode(
    tmp_path, monkeypatch
):
    destination = tmp_path / "nested" / "profile.json"
    expected_bytes = '{"a":1,"é":"é"}'.encode("utf-8")
    events = []
    real_fsync = os.fsync
    real_replace = os.replace

    def recording_fsync(fd):
        events.append(("fsync", os.fstat(fd).st_size))
        return real_fsync(fd)

    def recording_replace(source, target):
        events.append(("replace", Path(source).parent, Path(target)))
        return real_replace(source, target)

    monkeypatch.setattr("myextension.canonical_json.os.fsync", recording_fsync)
    monkeypatch.setattr("myextension.canonical_json.os.replace", recording_replace)

    atomic_write_json(destination, {"é": "e\u0301", "a": 1})

    assert destination.read_bytes() == expected_bytes
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert events[0] == ("fsync", len(expected_bytes))
    assert events[1][0] == "replace"
    assert events[1][1] == destination.parent
    assert events[1][2] == destination
    assert list(destination.parent.iterdir()) == [destination]


def test_input_shape_is_rejected_before_custom_code_generation(monkeypatch):
    payload = make_profile_payload()
    del payload["dimensions"][0]["code"]
    payload["dimensions"][0]["unknown_teacher_field"] = True

    def must_not_generate():
        raise AssertionError("shape validation must run first")

    monkeypatch.setattr("myextension.profile_validator.uuid4", must_not_generate)

    with pytest.raises(ValidationError):
        validate_profile_draft(payload)


def test_validation_trims_teacher_text_without_rewriting_meaning():
    payload = make_profile_payload()
    payload["problem_id"] = "  synthetic-debug-problem  "
    payload["title"] = "  合成 调试题  "
    payload["dimensions"][0]["name"] = "  失败后的 验证  "
    payload["dimensions"][0]["question"] = "  学生是否再次验证？  "
    payload["dimensions"][0]["evidence_criteria"][0]["statement"] = "  修改后 再次运行  "

    normalized = validate_profile_draft(payload)

    assert normalized["problem_id"] == "synthetic-debug-problem"
    assert normalized["title"] == "合成 调试题"
    assert normalized["dimensions"][0]["name"] == "失败后的 验证"
    assert normalized["dimensions"][0]["question"] == "学生是否再次验证？"
    assert (
        normalized["dimensions"][0]["evidence_criteria"][0]["statement"]
        == "修改后 再次运行"
    )


def test_missing_custom_code_is_generated_once_and_can_be_reused():
    payload = make_profile_payload()
    del payload["dimensions"][0]["code"]

    first = validate_profile_draft(payload)
    generated_code = first["dimensions"][0]["code"]
    second = validate_profile_draft(first)

    assert re.fullmatch(r"CUSTOM_[0-9A-F]{8}", generated_code)
    assert second["dimensions"][0]["code"] == generated_code


def test_custom_defaults_force_llm_evidence_and_hidden_minimum():
    dimension = make_dimension(analysis_config={"mode": "rule"})

    normalized = validate_profile_draft(make_profile_payload(dimensions=[dimension]))

    assert normalized["dimensions"][0]["analysis_config"] == {
        "mode": "llm_evidence",
        "minimum_observation": {
            "valid_observation_duration_ms": 30000,
            "edit_event_count": 1,
        },
    }


@pytest.mark.parametrize(
    ("code", "expected_minimum"),
    [
        (
            "REPEATED_EDITING",
            {"valid_observation_duration_ms": 60000, "edit_event_count": 3},
        ),
        ("DEBUG_CHAIN", {"edit_event_count": 1, "run_count": 1}),
        ("REPEATED_RUN_FAILURES", {"run_count": 2}),
        (
            "PAUSE_WITHOUT_VALIDATION",
            {"valid_observation_duration_ms": 60000, "edit_event_count": 2},
        ),
        (
            "CUSTOM_Z9Y8X7W6",
            {"valid_observation_duration_ms": 30000, "edit_event_count": 1},
        ),
    ],
)
def test_server_overrides_tampered_hidden_minimum_for_every_guided_code(
    code, expected_minimum
):
    dimension = make_dimension(
        code=code,
        analysis_config={
            "mode": "llm_evidence",
            "minimum_observation": {
                "valid_observation_duration_ms": 1,
                "edit_event_count": 999,
                "run_count": 999,
            },
        },
    )

    normalized = validate_profile_draft(make_profile_payload(dimensions=[dimension]))

    assert normalized["dimensions"][0]["analysis_config"] == {
        "mode": "llm_evidence",
        "minimum_observation": expected_minimum,
    }


def test_guided_validation_rejects_unknown_non_custom_code():
    dimension = make_dimension(code="TEACHER_INVENTED_CODE")

    with pytest.raises(ProfileValidationError) as exc_info:
        validate_profile_draft(make_profile_payload(dimensions=[dimension]))

    assert exc_info.value.code == "unknown_dimension_code"


def test_server_forces_exact_guided_levels_without_mutating_caller_payload():
    teacher_levels = [
        {"code": "clear", "name": "  教师清晰级  ", "definition": "  教师定义二  "},
        {"code": "possible", "name": "  教师可能级  ", "definition": "  教师定义一  "},
    ]
    payload = make_profile_payload(
        dimensions=[make_dimension(levels=teacher_levels)]
    )
    original_payload = deepcopy(payload)

    normalized = validate_profile_draft(payload)

    assert normalized["dimensions"][0]["levels"] == LEVELS
    assert payload == original_payload


@pytest.mark.parametrize("dimension_count", [0, 11])
def test_validation_rejects_zero_or_more_than_ten_dimensions(dimension_count):
    dimensions = [
        make_dimension(code=f"CUSTOM_{index:08X}") for index in range(dimension_count)
    ]

    with pytest.raises(ValidationError):
        validate_profile_draft(make_profile_payload(dimensions=dimensions))


def test_validation_rejects_duplicate_dimension_codes():
    dimensions = [make_dimension(), make_dimension(name="另一维度")]

    with pytest.raises(ProfileValidationError) as exc_info:
        validate_profile_draft(make_profile_payload(dimensions=dimensions))

    assert exc_info.value.code == "duplicate_dimension_code"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda dimension: dimension.update({"question": "  "}),
        lambda dimension: dimension["evidence_criteria"][0].update({"statement": " "}),
        lambda dimension: dimension.update(
            {
                "evidence_criteria": [
                    criterion
                    for criterion in dimension["evidence_criteria"]
                    if criterion["direction"] != "support"
                ]
            }
        ),
    ],
)
def test_validation_rejects_empty_question_or_missing_support(mutate):
    dimension = make_dimension()
    mutate(dimension)

    with pytest.raises((ValidationError, ProfileValidationError)):
        validate_profile_draft(make_profile_payload(dimensions=[dimension]))


def test_validation_requires_exclusion_or_explicit_acknowledgement():
    dimension = make_dimension(
        evidence_criteria=[
            {"id": "support-1", "direction": "support", "statement": "存在合成证据"}
        ]
    )

    with pytest.raises(ProfileValidationError) as exc_info:
        validate_profile_draft(make_profile_payload(dimensions=[dimension]))
    assert exc_info.value.code == "missing_exclusion"

    dimension["no_known_exclusion"] = True
    normalized = validate_profile_draft(make_profile_payload(dimensions=[dimension]))
    assert normalized["dimensions"][0]["no_known_exclusion"] is True


def test_validation_rejects_empty_exclusion_even_with_nonempty_support():
    dimension = make_dimension()
    dimension["evidence_criteria"][1]["statement"] = " "

    with pytest.raises((ValidationError, ProfileValidationError)):
        validate_profile_draft(make_profile_payload(dimensions=[dimension]))


def test_guided_validation_rejects_knowledge_inference():
    dimension = make_dimension(dimension_type="knowledge_inference")

    with pytest.raises(ProfileValidationError) as exc_info:
        validate_profile_draft(make_profile_payload(dimensions=[dimension]))

    assert exc_info.value.code == "unsupported_guided_dimension_type"


@pytest.mark.parametrize("term", ["懒惰", "能力差", "笨", "焦虑症", "心理疾病"])
@pytest.mark.parametrize("field", ["name", "definition"])
def test_validation_rejects_stigmatizing_terms_with_stable_error_code(term, field):
    dimension = make_dimension()
    if field == "name":
        dimension["name"] = f"学生{term}表现"
    else:
        dimension["levels"][0]["definition"] = f"说明学生{term}"

    with pytest.raises(ProfileValidationError) as exc_info:
        validate_profile_draft(make_profile_payload(dimensions=[dimension]))

    assert exc_info.value.code == "stigmatizing_language"


def test_template_examples_are_display_only_not_accepted_in_drafts():
    dimension = make_dimension(examples=get_template("debug-chain")["examples"])

    with pytest.raises(ValidationError):
        validate_profile_draft(make_profile_payload(dimensions=[dimension]))


def test_create_update_publish_list_and_get_version(tmp_path):
    store = DimensionProfileStore(tmp_path)
    created = store.create_draft(make_profile_payload())
    updated_payload = make_profile_payload(question="学生是否在修改后重新验证？")
    updated_payload["dimensions"][0]["code"] = created["dimensions"][0]["code"]
    updated = store.update_draft(created["profile_id"], updated_payload)
    published = store.publish(created["profile_id"])

    assert created["revision"] == 1
    assert updated["revision"] == 2
    assert updated["dimensions"][0]["question"] == "学生是否在修改后重新验证？"
    assert store.list_profiles() == [published]
    assert store.list_profiles(problem_id="synthetic-debug-problem") == [published]
    assert store.list_profiles(problem_id="other-problem") == []
    assert store.get_version(created["profile_id"], 1) == published


def test_update_draft_atomically_rejects_one_of_two_stale_revisions(tmp_path):
    store = DimensionProfileStore(tmp_path)
    created = store.create_draft(make_profile_payload())
    profile_id = created["profile_id"]
    start = threading.Barrier(3)
    outcomes = []

    def update(question):
        payload = make_profile_payload(question=question)
        start.wait()
        try:
            outcomes.append(
                store.update_draft(
                    profile_id,
                    payload,
                    expected_revision=1,
                )
            )
        except ProfileConflictError as error:
            outcomes.append(error)

    first = threading.Thread(target=update, args=("并发修改一",))
    second = threading.Thread(target=update, args=("并发修改二",))
    first.start()
    second.start()
    start.wait()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len([item for item in outcomes if isinstance(item, dict)]) == 1
    assert len(
        [item for item in outcomes if isinstance(item, ProfileConflictError)]
    ) == 1
    stored = json.loads(
        (
            tmp_path
            / "config"
            / "dimension_profiles"
            / profile_id
            / "draft.json"
        ).read_text(encoding="utf-8")
    )
    assert stored["revision"] == 2
    assert stored["dimensions"][0]["question"] in {"并发修改一", "并发修改二"}


def test_publish_creates_immutable_version_and_separate_projection(tmp_path):
    store = DimensionProfileStore(tmp_path)
    draft = store.create_draft(make_profile_payload())
    published = store.publish(draft["profile_id"])
    version_file = (
        tmp_path / "config" / "dimension_profiles" / draft["profile_id"] / "v1.json"
    )
    stored = json.loads(version_file.read_text(encoding="utf-8"))

    assert published["version"] == 1
    assert published["deployment_status"] == "pilot"
    assert published["preview_status"] == "pending_real_samples"
    assert "deployment_status" not in stored
    assert "preview_status" not in stored
    assert len(published["content_hash"]) == 64
    hash_input = dict(stored)
    del hash_input["content_hash"]
    assert published["content_hash"] == sha256_json(hash_input)
    audit_lines = (
        tmp_path / "audit" / "profile_deployment.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in audit_lines] == [
        {
            "profile_id": draft["profile_id"],
            "version": 1,
            "content_hash": published["content_hash"],
            "deployment_status": "pilot",
            "preview_status": "pending_real_samples",
        }
    ]


def test_updating_published_content_creates_version_two_without_mutating_v1(tmp_path):
    store = DimensionProfileStore(tmp_path)
    draft = store.create_draft(make_profile_payload())
    first = store.publish(draft["profile_id"])
    v1_path = (
        tmp_path / "config" / "dimension_profiles" / draft["profile_id"] / "v1.json"
    )
    first_bytes = v1_path.read_bytes()
    changed = make_profile_payload(question="学生是否在失败后重新验证？")
    store.update_draft(draft["profile_id"], changed)
    second = store.publish(draft["profile_id"])

    assert first["version"] == 1
    assert second["version"] == 2
    assert first["content_hash"] != second["content_hash"]
    assert v1_path.read_bytes() == first_bytes


def test_publish_rejects_an_existing_target_version(tmp_path, monkeypatch):
    store = DimensionProfileStore(tmp_path)
    draft = store.create_draft(make_profile_payload())
    store.publish(draft["profile_id"])
    monkeypatch.setattr(store, "_next_version", lambda _profile_dir: 1)

    with pytest.raises(ProfileConflictError):
        store.publish(draft["profile_id"])


class _ObservedRLock:
    def __init__(self, real_lock):
        self._real_lock = real_lock
        self._watched_thread = None
        self.reader_acquisition_attempted = threading.Event()

    def watch_current_thread(self):
        self._watched_thread = threading.get_ident()

    def __enter__(self):
        if threading.get_ident() == self._watched_thread:
            self.reader_acquisition_attempted.set()
        self._real_lock.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._real_lock.release()


@pytest.mark.parametrize("read_operation", ["get", "list"])
def test_profile_reader_attempts_real_lock_and_cannot_observe_publish_half_state(
    tmp_path, monkeypatch, read_operation
):
    store = DimensionProfileStore(tmp_path)
    draft = store.create_draft(make_profile_payload())
    store.publish(draft["profile_id"])
    changed = make_profile_payload(question="学生是否在第二版中重新验证？")
    store.update_draft(draft["profile_id"], changed)

    observed_lock = _ObservedRLock(store._lock)
    store._lock = observed_lock
    append_entered = threading.Event()
    allow_append = threading.Event()
    reader_finished = threading.Event()
    publisher_errors = []
    reader_outcomes = []
    real_append = store._append_projection

    def paused_append(projection):
        append_entered.set()
        assert allow_append.wait(timeout=2)
        real_append(projection)

    def publish_second_version():
        try:
            store.publish(draft["profile_id"])
        except BaseException as error:
            publisher_errors.append(error)

    def read_second_version():
        observed_lock.watch_current_thread()
        try:
            if read_operation == "get":
                reader_outcomes.append(store.get_version(draft["profile_id"], 2))
            else:
                reader_outcomes.append(store.list_profiles())
        except BaseException as error:
            reader_outcomes.append(error)
        finally:
            reader_finished.set()

    monkeypatch.setattr(store, "_append_projection", paused_append)
    publisher = threading.Thread(target=publish_second_version)
    publisher.start()
    assert append_entered.wait(timeout=2)

    reader = threading.Thread(target=read_second_version)
    reader.start()
    acquisition_was_observed = observed_lock.reader_acquisition_attempted.wait(
        timeout=2
    )
    reader_finished_before_publish_release = reader_finished.is_set()
    allow_append.set()
    publisher.join(timeout=2)
    reader.join(timeout=2)

    assert acquisition_was_observed
    assert not reader_finished_before_publish_release
    assert not publisher.is_alive()
    assert not reader.is_alive()
    assert publisher_errors == []
    if read_operation == "get":
        assert reader_outcomes[0]["version"] == 2
    else:
        assert reader_outcomes[0][0]["version"] == 2


def _assert_profile_integrity_error(operation):
    with pytest.raises(Exception) as exc_info:
        operation()
    assert type(exc_info.value).__name__ == "ProfileIntegrityError"


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda stored: stored.update({"title": "篡改后的标题"}), "old hash"),
        (lambda stored: stored.update({"version": 2}), "path/version mismatch"),
        (
            lambda stored: stored.update(
                {"profile_id": "00000000-0000-0000-0000-000000000000"}
            ),
            "path/profile mismatch",
        ),
        (
            lambda stored: stored.update({"deployment_status": "pilot"}),
            "projection field leaked into immutable content",
        ),
        (lambda stored: stored.update({"unexpected": True}), "unknown stored field"),
    ],
)
def test_get_version_rejects_tampered_immutable_content_with_integrity_error(
    tmp_path, mutation, reason
):
    store = DimensionProfileStore(tmp_path)
    draft = store.create_draft(make_profile_payload())
    store.publish(draft["profile_id"])
    version_path = (
        tmp_path / "config" / "dimension_profiles" / draft["profile_id"] / "v1.json"
    )
    stored = json.loads(version_path.read_text(encoding="utf-8"))
    mutation(stored)
    version_path.write_text(
        json.dumps(stored, ensure_ascii=False), encoding="utf-8"
    )

    _assert_profile_integrity_error(
        lambda: store.get_version(draft["profile_id"], 1)
    )


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            lambda projection: projection.update({"content_hash": "0" * 64}),
            "projection/content hash mismatch",
        ),
        (
            lambda projection: projection.update({"unexpected": True}),
            "unknown projection field",
        ),
        (
            lambda projection: projection.update(
                {"profile_id": "00000000-0000-0000-0000-000000000000"}
            ),
            "projection profile mismatch",
        ),
        (
            lambda projection: projection.update({"version": 99}),
            "projection version mismatch",
        ),
    ],
)
def test_get_version_rejects_tampered_projection_with_integrity_error(
    tmp_path, mutation, reason
):
    store = DimensionProfileStore(tmp_path)
    draft = store.create_draft(make_profile_payload())
    store.publish(draft["profile_id"])
    audit_path = tmp_path / "audit" / "profile_deployment.jsonl"
    projection = json.loads(audit_path.read_text(encoding="utf-8"))
    mutation(projection)
    audit_path.write_text(
        json.dumps(projection, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    _assert_profile_integrity_error(
        lambda: store.get_version(draft["profile_id"], 1)
    )


def test_frozen_schema_failure_is_wrapped_after_hash_integrity_checks_pass(
    tmp_path,
):
    store = DimensionProfileStore(tmp_path)
    draft = store.create_draft(make_profile_payload())
    store.publish(draft["profile_id"])
    version_path = (
        tmp_path / "config" / "dimension_profiles" / draft["profile_id"] / "v1.json"
    )
    audit_path = tmp_path / "audit" / "profile_deployment.jsonl"
    stored = json.loads(version_path.read_text(encoding="utf-8"))
    stored["title"] = ""
    hash_input = {
        key: value for key, value in stored.items() if key != "content_hash"
    }
    stored["content_hash"] = sha256_json(hash_input)
    projection = json.loads(audit_path.read_text(encoding="utf-8"))
    projection["content_hash"] = stored["content_hash"]
    version_path.write_bytes(canonical_json_bytes(stored))
    audit_path.write_bytes(canonical_json_bytes(projection) + b"\n")

    with pytest.raises(Exception) as exc_info:
        store.get_version(draft["profile_id"], 1)

    assert type(exc_info.value).__name__ == "ProfileIntegrityError"
    assert isinstance(exc_info.value.__cause__, ValidationError)


def test_audit_append_retries_short_os_writes(tmp_path, monkeypatch):
    store = DimensionProfileStore(tmp_path)
    draft = store.create_draft(make_profile_payload())
    real_write = os.write
    write_sizes = []

    def short_write(fd, data):
        chunk_size = min(7, len(data))
        written = real_write(fd, data[:chunk_size])
        write_sizes.append(written)
        return written

    monkeypatch.setattr("myextension.dimension_profile_store.os.write", short_write)

    published = store.publish(draft["profile_id"])

    assert len(write_sizes) > 1
    assert store.get_version(draft["profile_id"], 1) == published


def test_partial_audit_tail_is_ignored_then_truncated_before_next_append(tmp_path):
    store = DimensionProfileStore(tmp_path)
    draft = store.create_draft(make_profile_payload())
    first = store.publish(draft["profile_id"])
    audit_path = tmp_path / "audit" / "profile_deployment.jsonl"
    with audit_path.open("ab") as audit:
        audit.write(b'{"profile_id":"unfinished')

    assert store.get_version(draft["profile_id"], 1) == first

    changed = make_profile_payload(question="第二版使用合成问题？")
    store.update_draft(draft["profile_id"], changed)
    second = store.publish(draft["profile_id"])

    assert store.get_version(draft["profile_id"], 1) == first
    assert store.get_version(draft["profile_id"], 2) == second
    assert audit_path.read_bytes().endswith(b"\n")
    assert b"unfinished" not in audit_path.read_bytes()


def test_complete_audit_projection_without_final_lf_remains_readable(tmp_path):
    store = DimensionProfileStore(tmp_path)
    draft = store.create_draft(make_profile_payload())
    published = store.publish(draft["profile_id"])
    audit_path = tmp_path / "audit" / "profile_deployment.jsonl"
    audit_bytes = audit_path.read_bytes()
    assert audit_bytes.endswith(b"\n")
    audit_path.write_bytes(audit_bytes[:-1])

    assert store.get_version(draft["profile_id"], 1) == published


def test_append_delimits_and_preserves_complete_no_lf_projection(tmp_path):
    store = DimensionProfileStore(tmp_path)
    draft = store.create_draft(make_profile_payload())
    first = store.publish(draft["profile_id"])
    audit_path = tmp_path / "audit" / "profile_deployment.jsonl"
    audit_path.write_bytes(audit_path.read_bytes()[:-1])
    changed = make_profile_payload(question="第二版保留第一版投影？")
    store.update_draft(draft["profile_id"], changed)

    second = store.publish(draft["profile_id"])

    assert store.get_version(draft["profile_id"], 1) == first
    assert store.get_version(draft["profile_id"], 2) == second
    audit_records = [
        json.loads(line) for line in audit_path.read_bytes().splitlines()
    ]
    assert [record["version"] for record in audit_records] == [1, 2]


def test_unrelated_malformed_complete_audit_line_does_not_hide_valid_target(
    tmp_path,
):
    store = DimensionProfileStore(tmp_path)
    draft = store.create_draft(make_profile_payload())
    published = store.publish(draft["profile_id"])
    audit_path = tmp_path / "audit" / "profile_deployment.jsonl"
    valid_projection = json.loads(audit_path.read_text(encoding="utf-8"))
    audit_path.write_bytes(
        b'{"unrelated":broken}\n' + canonical_json_bytes(valid_projection) + b"\n"
    )

    assert store.get_version(draft["profile_id"], 1) == published


def test_missing_target_projection_among_bad_lines_is_integrity_error(tmp_path):
    store = DimensionProfileStore(tmp_path)
    draft = store.create_draft(make_profile_payload())
    store.publish(draft["profile_id"])
    audit_path = tmp_path / "audit" / "profile_deployment.jsonl"
    audit_path.write_bytes(b'{"unrelated":broken}\n')

    _assert_profile_integrity_error(
        lambda: store.get_version(draft["profile_id"], 1)
    )


@pytest.mark.parametrize(
    "profile_id",
    [
        "../outside",
        "123e4567-e89b-12d3-a456-426614174000/../../outside",
        "123E4567-E89B-12D3-A456-426614174000",
        "123e4567e89b12d3a456426614174000",
        "not-a-uuid",
    ],
)
@pytest.mark.parametrize("operation", ["update", "publish", "get"])
def test_profile_operations_reject_noncanonical_or_traversal_ids(
    tmp_path, profile_id, operation
):
    store = DimensionProfileStore(tmp_path)

    with pytest.raises(InvalidProfileIdError):
        if operation == "update":
            store.update_draft(profile_id, make_profile_payload())
        elif operation == "publish":
            store.publish(profile_id)
        else:
            store.get_version(profile_id, 1)
