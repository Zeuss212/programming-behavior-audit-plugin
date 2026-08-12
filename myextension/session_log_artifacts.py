"""Human-readable, public-safe artifacts derived from a training record."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from gzip import compress
from typing import Final
from uuid import UUID

from .canonical_json import canonical_json_bytes
from .evidence_outbox import EvidenceChunk


SESSION_LOG_DEFINITIONS: Final[tuple[dict[str, str], ...]] = (
    {
        "kind": "operation",
        "filename": "operation_log.json",
        "label": "操作日志",
        "description": "用户输入、删除、粘贴、运行成功/失败及输出。",
        "media_type": "application/json; charset=utf-8",
    },
    {
        "kind": "process",
        "filename": "process_log.md",
        "label": "过程日志",
        "description": "按时间顺序整理输入、修改、动作间停顿和运行结果。",
        "media_type": "text/markdown; charset=utf-8",
    },
    {
        "kind": "analysis",
        "filename": "analysis_log.json",
        "label": "AI 分析日志",
        "description": "维度结论、数据质量、行为证据与分析来源。",
        "media_type": "application/json; charset=utf-8",
    },
)
SESSION_LOG_FILENAMES: Final[frozenset[str]] = frozenset(
    row["filename"] for row in SESSION_LOG_DEFINITIONS
)
SESSION_LOG_BY_KIND: Final[dict[str, dict[str, str]]] = {
    row["kind"]: row for row in SESSION_LOG_DEFINITIONS
}
MAX_INLINE_LOG_BYTES: Final[int] = 2 * 1024 * 1024
MAX_COMPRESSED_EVIDENCE_BYTES: Final[int] = 2 * 1024 * 1024
MAX_UNCOMPRESSED_EVIDENCE_BYTES: Final[int] = 10 * 1024 * 1024


def _require_mapping(value: object, *, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object.")
    return value


def _require_list(value: object, *, field: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array.")
    return value


def _pretty_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            separators=(",", ": "),
        )
        + "\n"
    ).encode("utf-8")


def build_evidence_chunk(
    session_id: str,
    *,
    sequence: int,
    events: Sequence[Mapping[str, object]],
    created_at: datetime,
) -> EvidenceChunk:
    """Serialize a consecutive local event range into one deterministic gzip body.

    The sequence used by the classroom service is supplied by the caller;
    ``session_seq`` remains the canonical event sequence preserved in the
    evidence payload and HTTP metadata.
    """

    try:
        if str(UUID(session_id)) != session_id:
            raise ValueError
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("session_id must be a canonical UUID.") from error
    if not events:
        raise ValueError("events must not be empty.")
    normalized_events: list[dict[str, object]] = []
    for expected_sequence, event in enumerate(events, start=1):
        if not isinstance(event, Mapping):
            raise ValueError("events must contain objects.")
        event_sequence = event.get("session_seq")
        if (
            not isinstance(event_sequence, int)
            or isinstance(event_sequence, bool)
            or (normalized_events and event_sequence != normalized_events[-1]["session_seq"] + 1)
        ):
            raise ValueError("events must have continuous session_seq values.")
        event_id = event.get("event_id")
        if event_id != f"{session_id}:{event_sequence}":
            raise ValueError("events must retain their canonical event_id values.")
        if expected_sequence == 1 and event_sequence < 1:
            raise ValueError("events must have positive session_seq values.")
        normalized_events.append(dict(event))
    first_event_sequence = int(normalized_events[0]["session_seq"])
    last_event_sequence = int(normalized_events[-1]["session_seq"])
    payload = {
        "schema_version": 1,
        "session_id": session_id,
        "first_event_sequence": first_event_sequence,
        "last_event_sequence": last_event_sequence,
        "events": normalized_events,
    }
    body_source = canonical_json_bytes(payload)
    if len(body_source) > MAX_UNCOMPRESSED_EVIDENCE_BYTES:
        raise ValueError("Evidence content exceeds the uncompressed size limit.")
    body = compress(body_source, mtime=0)
    if len(body) > MAX_COMPRESSED_EVIDENCE_BYTES:
        raise ValueError("Evidence content exceeds the compressed size limit.")
    return EvidenceChunk(
        sequence=sequence,
        first_event_sequence=first_event_sequence,
        last_event_sequence=last_event_sequence,
        body=body,
        created_at=created_at,
    )


def render_operation_log(record: Mapping[str, object]) -> bytes:
    """Render the deterministic operation log from validated public fields."""

    export = _require_mapping(record.get("export"), field="export")
    session = _require_mapping(record.get("session"), field="session")
    events = _require_list(record.get("behavior_events"), field="behavior_events")
    integrity = _require_mapping(record.get("integrity"), field="integrity")
    ordered_events = sorted(
        events,
        key=lambda row: (
            row.get("session_seq", 0) if isinstance(row, Mapping) else 0
        ),
    )
    return _pretty_json(
        {
            "schema_version": 1,
            "generated_at": export.get("generated_at"),
            "session": dict(session),
            "events": ordered_events,
            "integrity": dict(integrity),
        }
    )


_EVENT_LABELS: Final[dict[str, str]] = {
    "code_writing": "代码输入",
    "code_deletion": "代码删除",
    "code_paste": "代码粘贴",
    "idle": "停顿（可能包含思考）",
    "page_away": "页面离开",
}


def _event_label(event: Mapping[str, object]) -> str:
    segment_type = str(event.get("segment_type") or "未知事件")
    if segment_type != "code_execution":
        return _EVENT_LABELS.get(segment_type, segment_type)
    result = str(event.get("execution_result") or "").strip().lower()
    if result in {"success", "ok", "passed"}:
        return "运行成功"
    if result or event.get("error_type") or event.get("error_message"):
        return "运行失败"
    return "代码运行"


def _duration_text(value: object) -> str:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return "未知"
    if value < 1000:
        return f"{value} 毫秒"
    return f"{value / 1000:.1f} 秒"


def _markdown_code(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    indented = "\n".join(f"    {line}" for line in value.splitlines())
    return f"\n\n{indented}" if indented else ""


def render_process_log(record: Mapping[str, object]) -> bytes:
    """Render a chronological Markdown report without psychological inference."""

    export = _require_mapping(record.get("export"), field="export")
    session = _require_mapping(record.get("session"), field="session")
    events = _require_list(record.get("behavior_events"), field="behavior_events")
    ordered_events = sorted(
        (
            dict(row)
            for row in events
            if isinstance(row, Mapping)
        ),
        key=lambda row: row.get("session_seq", 0),
    )
    counts: dict[str, int] = {}
    for event in ordered_events:
        label = _event_label(event)
        counts[label] = counts.get(label, 0) + 1

    lines = [
        "# 编程行为过程日志",
        "",
        "## 会话摘要",
        "",
        f"- 会话 ID：`{session.get('session_id', '')}`",
        f"- 题目：{session.get('problem_title', '')}",
        f"- 开始时间：{session.get('started_at', '')}",
        f"- 结束时间：{session.get('ended_at', '')}",
        f"- 事件数量：{len(ordered_events)}",
        f"- 生成时间：{export.get('generated_at', '')}",
        "- 时长口径：记录页面活动时的输入、删除、粘贴与动作间停顿；页面离开和代码运行耗时不计入行为记录时长。",
        "",
        "### 行为统计",
        "",
    ]
    if counts:
        lines.extend(f"- {label}：{count} 次" for label, count in counts.items())
    else:
        lines.append("- 本次会话没有行为事件。")

    lines.extend(["", "## 时间线", ""])
    for event in ordered_events:
        lines.append(
            "- "
            f"{event.get('started_at', '')} · #{event.get('session_seq', '')} "
            f"{_event_label(event)} · {_duration_text(event.get('duration_ms'))}"
        )
    if not ordered_events:
        lines.append("- 无事件。")

    lines.extend(["", "## 行为明细", ""])
    for event in ordered_events:
        label = _event_label(event)
        lines.extend(
            [
                f"### #{event.get('session_seq', '')} {label}",
                "",
                f"- 时间：{event.get('started_at', '')} 至 {event.get('ended_at', '')}",
                f"- 持续：{_duration_text(event.get('duration_ms'))}",
                f"- 文档：{event.get('document_name') or event.get('file_name') or '未命名'}",
            ]
        )
        for field, field_label in (
            ("inserted_char_count", "输入字符"),
            ("deleted_char_count", "删除字符"),
            ("paste_char_count", "粘贴字符"),
            ("execution_result", "运行结果"),
            ("error_type", "错误类型"),
            ("error_message", "错误信息"),
        ):
            value = event.get(field)
            if value not in {None, "", 0}:
                lines.append(f"- {field_label}：{value}")
        source = _markdown_code(event.get("cell_source"))
        if source:
            lines.extend(["- 当时代码：", source])
        lines.append("")
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def render_analysis_log(record: Mapping[str, object]) -> bytes | None:
    """Render only a validated successful AI analysis projection."""

    session = _require_mapping(record.get("session"), field="session")
    analysis = record.get("ai_analysis")
    if session.get("analysis_status") != "ready" or not isinstance(
        analysis, Mapping
    ):
        return None
    if analysis.get("status") != "ready":
        return None
    export = _require_mapping(record.get("export"), field="export")
    reviews = _require_list(record.get("teacher_reviews"), field="teacher_reviews")
    integrity = _require_mapping(record.get("integrity"), field="integrity")
    return _pretty_json(
        {
            "schema_version": 1,
            "generated_at": export.get("generated_at"),
            "session": dict(session),
            "ai_analysis": dict(analysis),
            "teacher_reviews": reviews,
            "integrity": dict(integrity),
        }
    )


def render_session_log_artifacts(
    record: Mapping[str, object],
) -> dict[str, bytes]:
    """Return the complete set of artifacts allowed for the record state."""

    session = _require_mapping(record.get("session"), field="session")
    if session.get("status") != "finalized":
        return {}
    artifacts = {
        "operation_log.json": render_operation_log(record),
        "process_log.md": render_process_log(record),
    }
    analysis = render_analysis_log(record)
    if analysis is not None:
        artifacts["analysis_log.json"] = analysis
    return artifacts
