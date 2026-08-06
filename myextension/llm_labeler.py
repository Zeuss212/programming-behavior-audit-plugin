"""Async Ark labeling for persisted behavior segments."""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .behavior_log_store import LOCAL_TIMEZONE, resolve_log_root
from .llm_transport import (
    ARK_API_KEY_ENV_VAR,
    ARK_MODEL_ENV_VAR,
    AiNotConfiguredError,
    DEFAULT_ARK_MODEL,
    LlmTransportError,
    ai_config_status,
    chat_json,
    load_ai_config,
    save_ai_config,
)

PROMPT_VERSION = "behavior-label-v1"
MASTERY_NAMES = {
    "not_mastered": "未掌握",
    "partial": "部分掌握",
    "proficient": "熟练",
}
MASTERY_RANK = {"proficient": 0, "partial": 1, "not_mastered": 2}
KNOWLEDGE_ALIASES = {
    "KP.DP.BASIC": ("KP.ALGO.DYNAMIC_PROGRAMMING", "动态规划"),
    "KP.DP.TABLE": ("KP.ALGO.DP_TABLE", "DP表构建"),
    "KP.DP.STATE": ("KP.ALGO.DP_STATE", "状态定义"),
    "KP.DP.TRANSITION": ("KP.ALGO.DP_TRANSITION", "状态转移方程"),
    "KP.ALGO.DP": ("KP.ALGO.DYNAMIC_PROGRAMMING", "动态规划"),
    "KP.ALGO.DYNAMIC_PROGRAMMING": ("KP.ALGO.DYNAMIC_PROGRAMMING", "动态规划"),
    "KP.PYTHON.RANGE": ("KP.PYTHON.RANGE_BOUNDARY", "range循环边界"),
    "KP.RANGE": ("KP.PYTHON.RANGE_BOUNDARY", "range循环边界"),
    "KP.LOOP.RANGE": ("KP.PYTHON.RANGE_BOUNDARY", "range循环边界"),
    "KP.PYTHON.LOOP_BOUNDARY": ("KP.PYTHON.RANGE_BOUNDARY", "range循环边界"),
    "KP.BOUNDARY": ("KP.ALGORITHM.BOUNDARY_CASE", "边界条件"),
    "KP.BOUNDARY_CASE": ("KP.ALGORITHM.BOUNDARY_CASE", "边界条件"),
    "KP.PYTHON.BOUNDARY_CASE": ("KP.ALGORITHM.BOUNDARY_CASE", "边界条件"),
    "KP.ALGORITHM.BOUNDARY_CASE": ("KP.ALGORITHM.BOUNDARY_CASE", "边界条件"),
    "KP.PYTHON.FUNCTION": ("KP.PYTHON.FUNCTION_DEF", "函数定义"),
    "KP.FUNCTION.DEFINE": ("KP.PYTHON.FUNCTION_DEF", "函数定义"),
    "KP.FUNCTION_DEF": ("KP.PYTHON.FUNCTION_DEF", "函数定义"),
    "KP.PYTHON.FUNCTION_DEF": ("KP.PYTHON.FUNCTION_DEF", "函数定义"),
    "KP.PYTHON.INDENT": ("KP.PYTHON.INDENTATION", "Python缩进"),
    "KP.PYTHON.INDENTATION": ("KP.PYTHON.INDENTATION", "Python缩进"),
    "KP.SYNTAX.INDENTATION": ("KP.PYTHON.INDENTATION", "Python缩进"),
    "KP.FOR_LOOP": ("KP.PYTHON.FOR_LOOP", "for循环"),
    "KP.LOOP.FOR": ("KP.PYTHON.FOR_LOOP", "for循环"),
    "KP.PYTHON.FOR_LOOP": ("KP.PYTHON.FOR_LOOP", "for循环"),
    "KP.LIST.COMPREHENSION": ("KP.PYTHON.LIST_COMPREHENSION", "列表推导式"),
    "KP.LIST_COMPREHENSION": ("KP.PYTHON.LIST_COMPREHENSION", "列表推导式"),
    "KP.PY.LIST_COMPREHENSION": ("KP.PYTHON.LIST_COMPREHENSION", "列表推导式"),
    "KP.PYTHON.LIST_COMPREHENSION": ("KP.PYTHON.LIST_COMPREHENSION", "列表推导式"),
    "KP.STRING.SPLIT": ("KP.PYTHON.STRING_SPLIT", "字符串分割"),
    "KP.STRING_SPLIT": ("KP.PYTHON.STRING_SPLIT", "字符串分割"),
    "KP.PY.STR_SPLIT": ("KP.PYTHON.STRING_SPLIT", "字符串分割"),
    "KP.PYTHON.STR_SPLIT": ("KP.PYTHON.STRING_SPLIT", "字符串分割"),
    "KP.PYTHON.STRING_SPLIT": ("KP.PYTHON.STRING_SPLIT", "字符串分割"),
    "KP.PY.TYPE_CONVERSION": ("KP.PYTHON.TYPE_CONVERSION", "类型转换"),
    "KP.PYTHON.INT_CONVERSION": ("KP.PYTHON.TYPE_CONVERSION", "类型转换"),
    "KP.PYTHON.TYPE_CONVERSION": ("KP.PYTHON.TYPE_CONVERSION", "类型转换"),
    "KP.ASSERT": ("KP.PYTHON.ASSERT", "assert断言"),
    "KP.PYTHON.ASSERT": ("KP.PYTHON.ASSERT", "assert断言"),
}

LlmClient = Callable[[Sequence[Mapping[str, object]]], Mapping[str, object]]

TAXONOMY = {
    "behavior": {
        "BEHAVIOR.CODE.WRITE_CODE": "编写代码",
        "BEHAVIOR.CODE.DELETE_CODE": "删除代码",
        "BEHAVIOR.CODE.PASTE_CODE": "粘贴代码",
        "BEHAVIOR.CODE.REFACTOR_CODE": "重构代码",
        "BEHAVIOR.RUN.RUN_CODE": "运行程序",
        "BEHAVIOR.RUN.INSPECT_ERROR": "查看错误",
        "BEHAVIOR.DEBUG.DEBUG_CODE": "调试代码",
        "BEHAVIOR.WORKFLOW.IDLE": "思考/空闲",
        "BEHAVIOR.WORKFLOW.PAGE_AWAY": "离开页面",
        "BEHAVIOR.WORKFLOW.SWITCH_CONTEXT": "切换上下文",
    },
    "target": {
        "TARGET.EDITOR.NOTEBOOK_CELL": "Notebook Cell",
        "TARGET.EDITOR.PYTHON_FILE": "Python 文件编辑区",
        "TARGET.RUNTIME.RUN_MENU": "Run 菜单",
        "TARGET.RUNTIME.CODE_CONSOLE": "Code Console",
        "TARGET.RUNTIME.DEBUGGER": "Debugger",
        "TARGET.OUTPUT.ERROR_TRACE": "错误信息",
        "TARGET.OUTPUT.CONSOLE_OUTPUT": "控制台输出",
    },
    "ability": {
        "ABILITY.PROBLEM.UNDERSTAND_REQUIREMENT": "题意理解",
        "ABILITY.CODE.SYNTAX": "基础语法",
        "ABILITY.CODE.DATA_TYPE": "数据类型",
        "ABILITY.CODE.CONTROL_FLOW": "分支与循环",
        "ABILITY.CODE.IO": "输入输出",
        "ABILITY.DEBUG.ERROR_IDENTIFICATION": "错误识别",
        "ABILITY.DEBUG.ERROR_LOCALIZATION": "错误定位",
        "ABILITY.DEBUG.HYPOTHESIS_FIX": "假设与修正",
        "ABILITY.DEBUG.VERIFICATION": "测试验证",
    },
    "error": {
        "ERR.SYNTAX": "语法错误",
        "ERR.RUNTIME": "运行时错误",
        "ERR.LOGIC": "逻辑错误",
        "ERR.DATA_TYPE": "数据类型错误",
        "ERR.BOUNDARY": "边界条件错误",
        "ERR.INDEX": "越界错误",
        "ERR.IO": "输入输出格式错误",
        "CAUSE.CONCEPT.MISUNDERSTANDING": "概念不准",
        "CAUSE.CONCEPT.DATA_TYPE_MISUNDERSTANDING": "数据类型理解不足",
        "CAUSE.BOUNDARY.MISSING_CASE": "漏边界情况",
        "CAUSE.CARELESS.TYPO": "粗心拼写",
        "CAUSE.LOGIC.WRONG_CONDITION": "条件表达式错误",
    },
}


def schedule_label_segments(
    session_id: str,
    relative_log_path: str,
    segments: Sequence[Mapping[str, object]],
) -> bool:
    """Start a background label job if Ark credentials are configured."""
    load_ai_config()
    if not os.environ.get(ARK_API_KEY_ENV_VAR):
        return False

    thread = threading.Thread(
        target=label_segments,
        args=(session_id, relative_log_path, list(segments)),
        daemon=True,
    )
    thread.start()
    return True


def label_segments(
    session_id: str,
    relative_log_path: str,
    segments: Sequence[Mapping[str, object]],
    client: LlmClient | None = None,
) -> None:
    """Label a batch and append JSONL results beside the Markdown log."""
    paths = _analysis_paths(relative_log_path)
    paths["labels"].parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    event_ids = [_event_id(segment) for segment in segments]
    _write_status(paths["status"], session_id, event_ids, "running")

    try:
        labels = client(segments) if client else _call_ark(segments)
    except Exception as exc:
        error_code = _safe_error_code(exc)
        _write_status(
            paths["status"],
            session_id,
            event_ids,
            "error",
            error_code,
        )
        _append_error(
            paths["labels"],
            session_id,
            event_ids,
            error_code,
        )
        return

    labels_by_id = _labels_by_event_id(labels)
    with paths["labels"].open("a", encoding="utf-8") as label_file, paths[
        "samples"
    ].open("a", encoding="utf-8") as sample_file:
        for segment in segments:
            event_id = _event_id(segment)
            label = labels_by_id.get(event_id, {})
            record = {
                "schema_version": 1,
                "session_id": session_id,
                "event_id": event_id,
                "created_at": _now_iso(),
                "status": "success",
                "model": os.environ.get(ARK_MODEL_ENV_VAR, DEFAULT_ARK_MODEL),
                "prompt_version": PROMPT_VERSION,
                "label": label,
            }
            label_file.write(json.dumps(record, ensure_ascii=False) + "\n")
            sample = _dataset_sample(segment, label)
            sample_file.write(json.dumps(sample, ensure_ascii=False) + "\n")

    _write_stage_samples(paths["stage_samples"], paths["samples"])
    _write_status(paths["status"], session_id, event_ids, "ready")


def _call_ark(segments: Sequence[Mapping[str, object]]) -> Mapping[str, object]:
    prompt = json.loads(_build_prompt(segments))
    return chat_json(
        system_prompt=(
            "你是编程学习行为标注器。只输出 JSON，不输出解释。"
            "必须从给定目录中选择编码；无法判断时返回空数组或 null。"
        ),
        user_payload=prompt,
    ).payload


def _build_prompt(segments: Sequence[Mapping[str, object]]) -> str:
    compact_segments = [_compact_segment(segment) for segment in segments]
    return json.dumps(
        {
            "task": "为每个行为片段补充教师训练数据标签。",
            "output_schema": {
                "labels": [
                    {
                        "event_id": "string",
                        "behavior_code": "string",
                        "target_code": "string",
                        "ability_tags": [
                            {
                                "ability_code": "string",
                                "mastery": "not_mastered|partial|proficient",
                            }
                        ],
                        "knowledge_points": [
                            {
                                "kp_code": "string",
                                "kp_name": "string",
                                "mastery": "not_mastered|partial|proficient",
                            }
                        ],
                        "code_errors": [
                            {
                                "line_start": "number|null",
                                "line_end": "number|null",
                                "error_type_code": "string|null",
                                "error_reason_code": "string|null",
                                "description": "string",
                            }
                        ],
                        "teacher_summary": "string",
                        "confidence": "number",
                    }
                ]
            },
            "taxonomy": TAXONOMY,
            "segments": compact_segments,
        },
        ensure_ascii=False,
    )


def _compact_segment(segment: Mapping[str, object]) -> dict[str, object]:
    fields = [
        "event_id",
        "segment_type",
        "started_at",
        "ended_at",
        "duration_ms",
        "document_type",
        "file_path",
        "file_name",
        "notebook_path",
        "cell_index",
        "cell_type",
        "inserted_char_count",
        "deleted_char_count",
        "paste_char_count",
        "execution_result",
        "error_type",
        "error_message",
        "cell_source",
    ]
    compact = {key: segment[key] for key in fields if key in segment}
    source = compact.get("cell_source")
    if isinstance(source, str) and len(source) > 6000:
        compact["cell_source"] = source[:6000]
        compact["cell_source_truncated"] = True
    return compact


def _labels_by_event_id(labels: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    raw_labels = labels.get("labels")
    if not isinstance(raw_labels, list):
        return {}
    by_id: dict[str, Mapping[str, object]] = {}
    for label in raw_labels:
        if not isinstance(label, dict):
            continue
        event_id = label.get("event_id")
        if isinstance(event_id, str) and event_id:
            by_id[event_id] = label
    return by_id


def _analysis_paths(relative_log_path: str) -> dict[str, Path]:
    md_path = resolve_log_root() / relative_log_path
    return {
        "labels": md_path.with_suffix(".llm_labels.jsonl"),
        "samples": md_path.with_suffix(".samples.jsonl"),
        "stage_samples": md_path.with_suffix(".stage_samples.jsonl"),
        "status": md_path.with_suffix(".analysis_status.json"),
    }


def _dataset_sample(
    segment: Mapping[str, object],
    label: Mapping[str, object],
) -> dict[str, object]:
    source = segment.get("cell_source")
    behavior_code = label.get("behavior_code")
    target_code = _normalized_target_code(segment, label)
    return _drop_empty(
        {
            "seq": segment.get("session_seq"),
            "event_type": segment.get("segment_type"),
            "start_time": segment.get("started_at"),
            "duration_ms": segment.get("duration_ms"),
            "file_path": segment.get("file_path") or segment.get("notebook_path"),
            "cell_index": segment.get("cell_index"),
            "edit_chars": segment.get("inserted_char_count"),
            "delete_chars": segment.get("deleted_char_count"),
            "paste_chars": segment.get("paste_char_count"),
            "execution_result": segment.get("execution_result"),
            "runtime_error": segment.get("error_type"),
            "code": source if isinstance(source, str) else None,
            "function_names": _function_names(source) if isinstance(source, str) else None,
            "behavior_code": behavior_code,
            "behavior_name": _taxonomy_name("behavior", behavior_code),
            "target_code": target_code,
            "target_name": _taxonomy_name("target", target_code),
            "knowledge_points": _short_tags(label.get("knowledge_points"), "kp_code"),
            "ability_tags": _short_tags(label.get("ability_tags"), "ability_code"),
            "errors": _short_errors(label.get("code_errors")),
            "confidence": label.get("confidence"),
        }
    )


def _write_stage_samples(stage_path: Path, sample_path: Path) -> None:
    samples = sorted(_read_jsonl(sample_path), key=lambda item: int(item.get("seq") or 0))
    stages = _build_stage_samples(samples)
    with stage_path.open("w", encoding="utf-8") as handle:
        for stage in stages:
            handle.write(json.dumps(stage, ensure_ascii=False) + "\n")
    pretty_path = stage_path.with_name(
        stage_path.name.replace(".stage_samples.jsonl", ".stage_samples.pretty.json")
    )
    pretty_path.write_text(
        json.dumps(stages, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _build_stage_samples(samples: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    stages: list[dict[str, object]] = []
    current: dict[str, object] | None = None

    for sample in samples:
        event_type = sample.get("event_type")
        if event_type in {"code_writing", "code_deletion", "code_paste"}:
            if current is None or current.get("file_path") != sample.get("file_path"):
                if current:
                    stages.append(_finish_stage(current, len(stages) + 1))
                current = _new_stage(sample)
            _merge_stage_event(current, sample)
            continue

        if event_type == "code_execution":
            if current is None:
                current = _new_stage(sample)
            _merge_stage_event(current, sample)
            if sample.get("execution_result") == "success":
                stages.append(_finish_stage(current, len(stages) + 1))
                current = None

    if current:
        stages.append(_finish_stage(current, len(stages) + 1))
    return stages


def _new_stage(sample: Mapping[str, object]) -> dict[str, object]:
    return {
        "stage_seq": 0,
        "event_type": "coding_stage",
        "start_seq": sample.get("seq"),
        "end_seq": sample.get("seq"),
        "start_time": sample.get("start_time"),
        "duration_ms": 0,
        "file_path": sample.get("file_path"),
        "edit_chars": 0,
        "delete_chars": 0,
        "paste_chars": 0,
        "edit_event_count": 0,
        "delete_event_count": 0,
        "paste_event_count": 0,
        "run_count": 0,
        "failed_run_count": 0,
        "function_names": [],
        "knowledge_points": [],
        "ability_tags": [],
        "errors": [],
    }


def _merge_stage_event(stage: dict[str, object], sample: Mapping[str, object]) -> None:
    stage["end_seq"] = sample.get("seq")
    stage["duration_ms"] = int(stage.get("duration_ms") or 0) + int(sample.get("duration_ms") or 0)
    stage["edit_chars"] = int(stage.get("edit_chars") or 0) + int(sample.get("edit_chars") or 0)
    stage["delete_chars"] = int(stage.get("delete_chars") or 0) + int(sample.get("delete_chars") or 0)
    stage["paste_chars"] = int(stage.get("paste_chars") or 0) + int(sample.get("paste_chars") or 0)

    if sample.get("event_type") == "code_writing":
        stage["edit_event_count"] = int(stage.get("edit_event_count") or 0) + 1
    if sample.get("event_type") == "code_deletion":
        stage["delete_event_count"] = int(stage.get("delete_event_count") or 0) + 1
    if sample.get("event_type") == "code_paste":
        stage["paste_event_count"] = int(stage.get("paste_event_count") or 0) + 1

    if sample.get("code"):
        stage["code"] = sample.get("code")
    if sample.get("target_code"):
        stage["target_code"] = sample.get("target_code")
    if sample.get("target_name"):
        stage["target_name"] = sample.get("target_name")
    if sample.get("confidence"):
        stage["confidence"] = sample.get("confidence")

    if sample.get("event_type") == "code_execution":
        stage["run_count"] = int(stage.get("run_count") or 0) + 1
        stage["execution_result"] = sample.get("execution_result")
        stage["runtime_error"] = sample.get("runtime_error")
        if sample.get("execution_result") != "success":
            stage["failed_run_count"] = int(stage.get("failed_run_count") or 0) + 1

    _extend_unique(stage, "knowledge_points", sample.get("knowledge_points"))
    _extend_unique(stage, "ability_tags", sample.get("ability_tags"))
    _extend_unique(stage, "errors", sample.get("errors"))
    _extend_unique(stage, "function_names", sample.get("function_names"))


def _finish_stage(stage: dict[str, object], stage_seq: int) -> dict[str, object]:
    stage["stage_seq"] = stage_seq
    stage["revision_count"] = (
        int(stage.get("edit_event_count") or 0)
        + int(stage.get("delete_event_count") or 0)
        + int(stage.get("paste_event_count") or 0)
    )
    stage["delete_edit_ratio"] = _delete_edit_ratio(stage)
    if stage.get("execution_result") == "success":
        stage["time_to_success_ms"] = stage.get("duration_ms")
    stage["mastery_hint"] = _mastery_hint(stage)
    _set_mastery_target(stage)
    stage["behavior_code"] = (
        "BEHAVIOR.STAGE.CODE_RUN"
        if int(stage.get("run_count") or 0) > 0
        else "BEHAVIOR.STAGE.CODE_EDIT"
    )
    stage["behavior_name"] = (
        "编码运行阶段"
        if stage["behavior_code"] == "BEHAVIOR.STAGE.CODE_RUN"
        else "编码编辑阶段"
    )
    return _drop_empty(_stage_dataset_fields(stage))


def _set_mastery_target(stage: dict[str, object]) -> None:
    function_names = stage.get("function_names")
    if not isinstance(function_names, list) or not function_names:
        return
    if len(function_names) == 1:
        stage["mastery_scope"] = "function"
        stage["function_name"] = function_names[0]
        return
    stage["mastery_scope"] = "functions"


def _stage_dataset_fields(stage: Mapping[str, object]) -> dict[str, object]:
    return {
        "stage_seq": stage.get("stage_seq"),
        "start_seq": stage.get("start_seq"),
        "end_seq": stage.get("end_seq"),
        "start_time": stage.get("start_time"),
        "duration_ms": stage.get("duration_ms"),
        "file_path": stage.get("file_path"),
        "function_name": stage.get("function_name"),
        "function_names": stage.get("function_names"),
        "mastery_scope": stage.get("mastery_scope"),
        "behavior_code": stage.get("behavior_code"),
        "behavior_name": stage.get("behavior_name"),
        "target_code": stage.get("target_code"),
        "target_name": stage.get("target_name"),
        "edit_chars": stage.get("edit_chars"),
        "delete_chars": stage.get("delete_chars"),
        "paste_chars": stage.get("paste_chars"),
        "edit_event_count": stage.get("edit_event_count"),
        "delete_event_count": stage.get("delete_event_count"),
        "run_count": stage.get("run_count"),
        "failed_run_count": stage.get("failed_run_count"),
        "execution_result": stage.get("execution_result"),
        "runtime_error": stage.get("runtime_error"),
        "knowledge_points": stage.get("knowledge_points"),
        "ability_tags": stage.get("ability_tags"),
        "errors": stage.get("errors"),
        "mastery_hint": stage.get("mastery_hint"),
        "mastery_name": _mastery_name(stage.get("mastery_hint")),
        "confidence": stage.get("confidence"),
    }


def _delete_edit_ratio(stage: Mapping[str, object]) -> float:
    edit_chars = int(stage.get("edit_chars") or 0)
    delete_chars = int(stage.get("delete_chars") or 0)
    if edit_chars <= 0:
        return 1.0 if delete_chars > 0 else 0.0
    return round(delete_chars / edit_chars, 2)


def _mastery_hint(stage: Mapping[str, object]) -> str:
    revisions = int(stage.get("revision_count") or 0)
    failed_runs = int(stage.get("failed_run_count") or 0)
    duration_ms = int(stage.get("duration_ms") or 0)
    delete_ratio = float(stage.get("delete_edit_ratio") or 0)
    delete_chars = int(stage.get("delete_chars") or 0)

    if failed_runs >= 4 or revisions >= 8:
        return "not_mastered"
    if failed_runs >= 1 or revisions >= 4 or duration_ms >= 120_000:
        return "partial"
    if delete_chars >= 10 and delete_ratio >= 0.7:
        return "partial"
    if stage.get("execution_result") == "success":
        return "proficient"
    return "partial"


def _extend_unique(stage: dict[str, object], key: str, value: object) -> None:
    if not isinstance(value, list):
        return
    existing = stage.setdefault(key, [])
    if not isinstance(existing, list):
        return
    if key in {"knowledge_points", "ability_tags"}:
        existing[:] = _merge_tags([*existing, *value])
        return

    seen = {json.dumps(item, sort_keys=True, ensure_ascii=False) for item in existing}
    for item in value:
        marker = json.dumps(item, sort_keys=True, ensure_ascii=False)
        if marker not in seen:
            existing.append(item)
            seen.add(marker)


def _function_names(source: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"^\s*def\s+([A-Za-z_]\w*)\s*\(", source, re.M)))


def _normalized_target_code(
    segment: Mapping[str, object], label: Mapping[str, object]
) -> object:
    if segment.get("document_type") == "python_file":
        if segment.get("segment_type") in {"code_writing", "code_deletion", "code_paste"}:
            return "TARGET.EDITOR.PYTHON_FILE"
    return label.get("target_code")


def _drop_empty(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: cleaned
            for key, item in value.items()
            if (cleaned := _drop_empty(item)) not in (None, {}, [])
        }
    if isinstance(value, list):
        return [_drop_empty(item) for item in value]
    return value


def _short_tags(value: object, code_key: str) -> object:
    if not isinstance(value, list):
        return None
    tags = []
    category = "ability" if code_key == "ability_code" else ""
    for item in value:
        if not isinstance(item, Mapping):
            continue
        code = item.get(code_key)
        name = item.get("kp_name") or _taxonomy_name(category, code)
        if code_key == "kp_code":
            code, name = _canonical_knowledge(code, name)
        mastery = item.get("mastery")
        tag = _drop_empty(
            {
                "code": code,
                "name": name,
                "mastery": mastery,
                "mastery_name": _mastery_name(mastery),
            }
        )
        if tag:
            tags.append(tag)
    return _merge_tags(tags)


def _short_errors(value: object) -> object:
    if not isinstance(value, list):
        return None
    errors = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        error = _drop_empty(
            {
                "line_start": item.get("line_start"),
                "line_end": item.get("line_end"),
                "type": item.get("error_type_code"),
                "type_name": _taxonomy_name("error", item.get("error_type_code")),
                "reason": item.get("error_reason_code"),
                "reason_name": _taxonomy_name("error", item.get("error_reason_code")),
                "description": item.get("description"),
            }
        )
        if error:
            errors.append(error)
    return errors


def _canonical_knowledge(code: object, name: object) -> tuple[object, object]:
    if not isinstance(code, str):
        return code, name
    return KNOWLEDGE_ALIASES.get(code.upper(), (code, name))


def _merge_tags(tags: Sequence[object]) -> list[object]:
    by_code: dict[str, dict[str, object]] = {}
    ordered: list[str] = []
    for tag in tags:
        if not isinstance(tag, Mapping):
            continue
        code = tag.get("code")
        if not isinstance(code, str) or not code:
            continue
        current = by_code.get(code)
        if current is None:
            by_code[code] = dict(tag)
            ordered.append(code)
            continue
        if _mastery_rank(tag.get("mastery")) > _mastery_rank(current.get("mastery")):
            current["mastery"] = tag.get("mastery")
            current["mastery_name"] = _mastery_name(tag.get("mastery"))
        if not current.get("name") and tag.get("name"):
            current["name"] = tag.get("name")
    return [_drop_empty(by_code[code]) for code in ordered]


def _taxonomy_name(category: str, code: object) -> object:
    if not isinstance(code, str):
        return None
    return TAXONOMY.get(category, {}).get(code)


def _mastery_name(value: object) -> object:
    if not isinstance(value, str):
        return None
    return MASTERY_NAMES.get(value)


def _mastery_rank(value: object) -> int:
    return MASTERY_RANK.get(value, -1) if isinstance(value, str) else -1


def _write_status(
    path: Path,
    session_id: str,
    event_ids: Sequence[str],
    status: str,
    error: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "session_id": session_id,
        "updated_at": _now_iso(),
        "status": status,
        "event_ids": list(event_ids),
        "model": os.environ.get(ARK_MODEL_ENV_VAR, DEFAULT_ARK_MODEL),
        "prompt_version": PROMPT_VERSION,
    }
    if error:
        payload["error"] = error[:500]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe_error_code(exc: Exception) -> str:
    if isinstance(exc, LlmTransportError):
        return exc.safe_code
    if isinstance(exc, AiNotConfiguredError):
        return "ai_not_configured"
    return "labeling_failed"


def _append_error(
    path: Path,
    session_id: str,
    event_ids: Sequence[str],
    error_code: str,
) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for event_id in event_ids:
            record = {
                "schema_version": 1,
                "session_id": session_id,
                "event_id": event_id,
                "created_at": _now_iso(),
                "status": "error",
                "model": os.environ.get(ARK_MODEL_ENV_VAR, DEFAULT_ARK_MODEL),
                "prompt_version": PROMPT_VERSION,
                "error": error_code,
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _event_id(segment: Mapping[str, object]) -> str:
    event_id = segment.get("event_id")
    if isinstance(event_id, str) and event_id:
        return event_id
    return "unknown"


def _now_iso() -> str:
    return datetime.now(tz=LOCAL_TIMEZONE).isoformat()
