"""Deterministic, private-safe classroom brief rendering."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


_ACTIVE_SEGMENT_TYPES = {"code_writing", "code_deletion", "code_paste"}
_LONG_PASTE_ATTENTION = (
    "页面离开后出现较长粘贴，建议教师结合过程记录进行询问。"
)


def _events_from(detail: Mapping[str, object]) -> list[Mapping[str, object]]:
    events = detail.get("behavior_events")
    if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
        raise ValueError("Classroom brief requires behavior events.")
    if not all(isinstance(event, Mapping) for event in events):
        raise ValueError("Classroom brief behavior events must be objects.")
    return list(events)


def _duration_ms(event: Mapping[str, object]) -> int:
    duration = event.get("duration_ms")
    if (
        not isinstance(duration, int)
        or isinstance(duration, bool)
        or duration < 0
    ):
        raise ValueError("Classroom brief event duration is invalid.")
    return duration


def _is_failed_run(event: Mapping[str, object]) -> bool:
    if event.get("execution_result") == "failure":
        return True
    return bool(event.get("error_type") or event.get("error_message"))


def _attention_message(events: list[Mapping[str, object]]) -> str | None:
    for previous, current in zip(events, events[1:]):
        paste_count = current.get("paste_char_count")
        if (
            previous.get("segment_type") == "page_away"
            and current.get("segment_type") == "code_paste"
            and isinstance(paste_count, int)
            and not isinstance(paste_count, bool)
            and paste_count >= 200
        ):
            return _LONG_PASTE_ATTENTION
    return None


def build_classroom_brief(
    detail: Mapping[str, object],
) -> dict[str, object]:
    """Build a compact brief without copying source, diagnostics, or AI data."""

    session = detail.get("session")
    if not isinstance(session, Mapping):
        raise ValueError("Classroom brief requires a session summary.")
    session_id = session.get("session_id")
    lifecycle_status = session.get("status")
    generated_at = session.get("ended_at")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("Classroom brief session id is invalid.")
    if lifecycle_status not in {"finalized", "abandoned"}:
        raise ValueError("Classroom brief requires a terminal session.")
    if not isinstance(generated_at, str) or not generated_at:
        raise ValueError("Classroom brief requires a terminal timestamp.")

    events = _events_from(detail)
    active_duration_ms = sum(
        _duration_ms(event)
        for event in events
        if event.get("segment_type") in _ACTIVE_SEGMENT_TYPES
    )
    runs = [
        event for event in events if event.get("segment_type") == "code_execution"
    ]
    failed_runs = sum(1 for event in runs if _is_failed_run(event))
    successful_runs = len(runs) - failed_runs
    edit_count = sum(
        1 for event in events if event.get("segment_type") in _ACTIVE_SEGMENT_TYPES
    )
    paste_count = sum(
        1 for event in events if event.get("segment_type") == "code_paste"
    )

    highlights: list[str] = []
    if edit_count:
        highlights.append(f"记录到 {edit_count} 个代码编辑片段")
    if runs:
        highlights.append(f"完成 {len(runs)} 次代码运行")
    if paste_count:
        highlights.append(f"出现 {paste_count} 个粘贴片段")

    complete = lifecycle_status == "finalized"
    return {
        "schema_version": 1,
        "session_id": session_id,
        "status": "complete" if complete else "partial",
        "data_completeness": "complete" if complete else "partial",
        "active_duration_ms": active_duration_ms,
        "run_summary": (
            f"运行 {len(runs)} 次，其中 {successful_runs} 次成功、"
            f"{failed_runs} 次失败"
        ),
        "process_highlights": highlights[:3],
        "attention_message": _attention_message(events),
        "generated_at": generated_at,
    }
