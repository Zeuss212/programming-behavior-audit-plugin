"""Deterministic extraction of the frozen Pilot objective-signal subset."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
import posixpath
from typing import NamedTuple, TypeAlias


FEATURE_NAMES = (
    "valid_observation_duration_ms",
    "edit_event_count",
    "delete_event_count",
    "paste_event_count",
    "run_count",
    "failed_run_count",
    "active_idle_count",
    "active_idle_total_duration_ms",
    "page_away_duration_ms",
    "failure_edit_success_chain_count",
    "error_type_change_count",
)

_EDIT_TYPES = {"code_writing", "code_deletion", "code_paste"}
_VALID_OBSERVATION_TYPES = _EDIT_TYPES | {"idle"}
_EXCLUDED_OBSERVATION_TYPES = {"page_away", "code_execution"}
_EXPECTED_SOURCES = {
    "valid_observation_duration_ms": _VALID_OBSERVATION_TYPES,
    "edit_event_count": _EDIT_TYPES,
    "delete_event_count": {"code_deletion"},
    "paste_event_count": {"code_paste", "code_writing"},
    "run_count": {"code_execution"},
    "failed_run_count": {"code_execution"},
    "active_idle_count": {"idle"},
    "active_idle_total_duration_ms": {"idle"},
    "page_away_duration_ms": {"page_away"},
    "failure_edit_success_chain_count": _EDIT_TYPES | {"code_execution"},
    "error_type_change_count": {"code_execution"},
}
# Frontend events can measure duration immediately before the logger stamps
# the end event. Keep a narrow compatibility window for that observed
# integer-millisecond drift while rejecting larger contradictions.
_DURATION_TOLERANCE_US = 2_000

Interval: TypeAlias = tuple[int, int]


class Context(NamedTuple):
    notebook_id: str | None
    notebook_path: str | None
    file_path: str | None
    cell_id: str | None
    cell_index: int | None


def _all_missing() -> dict[str, int | float | None]:
    return {name: None for name in FEATURE_NAMES}


def _valid_dictionary(
    signal_dictionary: Mapping[str, object],
) -> tuple[int, int] | None:
    if signal_dictionary.get("version") != "pilot-v1":
        return None
    signals = signal_dictionary.get("signals")
    if not isinstance(signals, Mapping) or set(signals) != set(FEATURE_NAMES):
        return None
    for name in FEATURE_NAMES:
        definition = signals.get(name)
        if not isinstance(definition, Mapping):
            return None
        if not all(
            isinstance(definition.get(field), str)
            and bool(str(definition[field]).strip())
            for field in ("unit", "scope", "missing_value_meaning")
        ):
            return None
        sources = definition.get("source_segment_types")
        if (
            not isinstance(sources, list)
            or not sources
            or not all(isinstance(item, str) and item for item in sources)
            or set(sources) != _EXPECTED_SOURCES[name]
        ):
            return None

    active_idle_threshold = signal_dictionary.get(
        "active_idle_threshold_ms"
    )
    verification_window = signal_dictionary.get(
        "verification_after_idle_window_ms"
    )
    if (
        active_idle_threshold != 2000
        or isinstance(active_idle_threshold, bool)
        or verification_window != 120000
        or isinstance(verification_window, bool)
    ):
        return None
    return int(active_idle_threshold), int(verification_window)


def _ordered_events(
    events: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]] | None:
    copied: list[Mapping[str, object]] = []
    for event in events:
        if not isinstance(event, Mapping):
            return None
        sequence = event.get("session_seq")
        if (
            not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence < 1
        ):
            return None
        if not isinstance(event.get("segment_type"), str):
            return None
        copied.append(event)
    copied.sort(key=lambda event: int(event["session_seq"]))
    if [
        event["session_seq"] for event in copied
    ] != list(range(1, len(copied) + 1)):
        return None
    return copied


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _time_intervals(
    events: Sequence[Mapping[str, object]],
) -> dict[int, Interval] | None:
    result: dict[int, Interval] = {}
    for event in events:
        duration = event.get("duration_ms")
        if (
            not isinstance(duration, int)
            or isinstance(duration, bool)
            or duration < 0
        ):
            return None
        started_at = _parse_timestamp(event.get("started_at"))
        ended_at = _parse_timestamp(event.get("ended_at"))
        if started_at is None or ended_at is None or ended_at < started_at:
            return None
        start_us = round(started_at.timestamp() * 1_000_000)
        end_us = round(ended_at.timestamp() * 1_000_000)
        if (
            abs((end_us - start_us) - duration * 1000)
            > _DURATION_TOLERANCE_US
        ):
            return None
        result[int(event["session_seq"])] = (start_us, end_us)
    return result


def _merge(intervals: Sequence[Interval]) -> list[Interval]:
    merged: list[Interval] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _subtract(
    included: Sequence[Interval],
    excluded: Sequence[Interval],
) -> list[Interval]:
    remaining: list[Interval] = []
    exclusions = _merge(excluded)
    for include_start, include_end in _merge(included):
        cursor = include_start
        for exclude_start, exclude_end in exclusions:
            if exclude_end <= cursor:
                continue
            if exclude_start >= include_end:
                break
            if exclude_start > cursor:
                remaining.append(
                    (cursor, min(exclude_start, include_end))
                )
            cursor = max(cursor, exclude_end)
            if cursor >= include_end:
                break
        if cursor < include_end:
            remaining.append((cursor, include_end))
    return remaining


def _duration_ms(intervals: Sequence[Interval]) -> int | float:
    microseconds = sum(end - start for start, end in intervals)
    if microseconds % 1000 == 0:
        return microseconds // 1000
    return microseconds / 1000


def _nonempty_string(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _normalized_path(value: object) -> str | None:
    path = _nonempty_string(value)
    if path is None:
        return None
    normalized = posixpath.normpath(path.replace("\\", "/"))
    return normalized if normalized not in {"", "."} else None


def _context(event: Mapping[str, object]) -> Context | None:
    notebook_id = _nonempty_string(event.get("notebook_id"))
    notebook_path = _normalized_path(event.get("notebook_path"))
    file_path = _normalized_path(event.get("file_path"))
    if notebook_id is None and notebook_path is None and file_path is None:
        return None
    if file_path is not None and (
        notebook_id is not None or notebook_path is not None
    ):
        return None

    cell_id = _nonempty_string(event.get("cell_id"))
    raw_cell_index = event.get("cell_index")
    if (
        isinstance(raw_cell_index, int)
        and not isinstance(raw_cell_index, bool)
        and raw_cell_index >= 0
    ):
        cell_index: int | None = raw_cell_index
    else:
        cell_index = None
    if cell_id is None and cell_index is None and file_path is None:
        return None
    return Context(
        notebook_id=notebook_id,
        notebook_path=notebook_path,
        file_path=file_path,
        cell_id=cell_id,
        cell_index=cell_index,
    )


def _same_context(first: Context, second: Context) -> bool:
    if first.file_path is not None or second.file_path is not None:
        same_document = (
            first.file_path is not None
            and second.file_path is not None
            and first.file_path == second.file_path
        )
    elif (
        first.notebook_id is not None
        and second.notebook_id is not None
    ):
        same_document = first.notebook_id == second.notebook_id
        if (
            same_document
            and first.notebook_path is not None
            and second.notebook_path is not None
        ):
            same_document = first.notebook_path == second.notebook_path
    elif (
        first.notebook_path is not None
        and second.notebook_path is not None
    ):
        same_document = first.notebook_path == second.notebook_path
    else:
        same_document = False
    if not same_document:
        return False

    if first.file_path is not None and second.file_path is not None:
        if first.cell_id is None and second.cell_id is None:
            if first.cell_index is None and second.cell_index is None:
                return True
    if first.cell_id is not None and second.cell_id is not None:
        if first.cell_id != second.cell_id:
            return False
        if (
            first.cell_index is not None
            and second.cell_index is not None
            and first.cell_index != second.cell_index
        ):
            return False
        return True
    if first.cell_index is not None and second.cell_index is not None:
        return first.cell_index == second.cell_index
    return False


def _recovery_chain_count(
    events: Sequence[Mapping[str, object]],
) -> int | None:
    pending: list[dict[str, object]] = []
    count = 0
    for event in events:
        segment_type = event["segment_type"]
        if segment_type not in _EDIT_TYPES | {"code_execution"}:
            continue
        context = _context(event)
        if context is None:
            return None

        if segment_type in _EDIT_TYPES:
            for failure in pending:
                stored = failure["context"]
                if isinstance(stored, tuple) and _same_context(
                    stored, context
                ):
                    failure["edited"] = True
            continue

        result = event.get("execution_result")
        if result not in {"failure", "success"}:
            return None
        if result == "failure":
            pending = [
                failure
                for failure in pending
                if not _same_context(failure["context"], context)  # type: ignore[arg-type]
            ]
            pending.append({"context": context, "edited": False})
            continue

        matching = [
            failure
            for failure in pending
            if _same_context(failure["context"], context)  # type: ignore[arg-type]
        ]
        if matching and matching[-1]["edited"] is True:
            count += 1
        pending = [
            failure
            for failure in pending
            if not _same_context(failure["context"], context)  # type: ignore[arg-type]
        ]
    return count


def _error_type_change_count(
    events: Sequence[Mapping[str, object]],
) -> int | None:
    previous_failures: list[tuple[Context, str]] = []
    count = 0
    for event in events:
        if event["segment_type"] != "code_execution":
            continue
        context = _context(event)
        if context is None:
            return None
        result = event.get("execution_result")
        if result not in {"failure", "success"}:
            return None
        matching = [
            pair
            for pair in previous_failures
            if _same_context(pair[0], context)
        ]
        previous_failures = [
            pair
            for pair in previous_failures
            if not _same_context(pair[0], context)
        ]
        if result == "success":
            continue
        error_type = _nonempty_string(event.get("error_type"))
        if error_type is None:
            return None
        if matching and matching[-1][1] != error_type:
            count += 1
        previous_failures.append((context, error_type))
    return count


def _paste_count(
    events: Sequence[Mapping[str, object]],
    intervals: Mapping[int, Interval] | None,
) -> int | None:
    explicit = [
        event for event in events if event["segment_type"] == "code_paste"
    ]
    inferred = [
        event
        for event in events
        if event["segment_type"] == "code_writing"
        and event.get("had_paste") is True
    ]
    if not explicit or not inferred:
        return len(explicit) + len(inferred)
    if intervals is None:
        return None

    count = len(explicit)
    for writing in inferred:
        writing_context = _context(writing)
        writing_interval = intervals[int(writing["session_seq"])]
        duplicate = False
        if writing_context is not None:
            for paste in explicit:
                paste_context = _context(paste)
                paste_interval = intervals[int(paste["session_seq"])]
                if (
                    paste_context is not None
                    and _same_context(writing_context, paste_context)
                    and paste_interval[0] <= writing_interval[1]
                    and paste_interval[1] >= writing_interval[0]
                ):
                    duplicate = True
                    break
        if not duplicate:
            count += 1
    return count


def extract_features(
    events: Sequence[Mapping[str, object]],
    signal_dictionary: Mapping[str, object],
) -> dict[str, int | float | None]:
    """Extract only the eleven objective features frozen for Pilot.

    A successful execution is represented only as a no-error run. It does not
    establish solution quality or any learning outcome.
    """

    dictionary_parameters = _valid_dictionary(signal_dictionary)
    if dictionary_parameters is None:
        return _all_missing()
    active_idle_threshold_ms, _verification_window_ms = (
        dictionary_parameters
    )
    ordered = _ordered_events(events)
    if ordered is None:
        return _all_missing()

    edit_count = sum(
        event["segment_type"] in _EDIT_TYPES for event in ordered
    )
    delete_count = sum(
        event["segment_type"] == "code_deletion" for event in ordered
    )
    executions = [
        event
        for event in ordered
        if event["segment_type"] == "code_execution"
    ]
    execution_results_valid = all(
        event.get("execution_result") in {"failure", "success"}
        for event in executions
    )
    failed_count: int | None
    if execution_results_valid:
        failed_count = sum(
            event.get("execution_result") == "failure"
            for event in executions
        )
    else:
        failed_count = None

    features: dict[str, int | float | None] = {
        "valid_observation_duration_ms": None,
        "edit_event_count": edit_count,
        "delete_event_count": delete_count,
        "paste_event_count": None,
        "run_count": len(executions),
        "failed_run_count": failed_count,
        "active_idle_count": None,
        "active_idle_total_duration_ms": None,
        "page_away_duration_ms": None,
        "failure_edit_success_chain_count": _recovery_chain_count(ordered),
        "error_type_change_count": _error_type_change_count(ordered),
    }

    intervals = _time_intervals(ordered)
    features["paste_event_count"] = _paste_count(ordered, intervals)
    if intervals is None:
        return features
    excluded = [
        intervals[int(event["session_seq"])]
        for event in ordered
        if event["segment_type"] in _EXCLUDED_OBSERVATION_TYPES
    ]
    valid_source = [
        intervals[int(event["session_seq"])]
        for event in ordered
        if event["segment_type"] in _VALID_OBSERVATION_TYPES
    ]
    page_away = [
        intervals[int(event["session_seq"])]
        for event in ordered
        if event["segment_type"] == "page_away"
    ]
    idle_source = [
        intervals[int(event["session_seq"])]
        for event in ordered
        if event["segment_type"] == "idle"
    ]
    effective_idle_fragments = _subtract(idle_source, excluded)
    qualifying_idle_fragments = [
        fragment
        for fragment in effective_idle_fragments
        if _duration_ms([fragment]) >= active_idle_threshold_ms
    ]
    active_idle_count = len(qualifying_idle_fragments)
    features.update(
        {
            "valid_observation_duration_ms": _duration_ms(
                _subtract(valid_source, excluded)
            ),
            "active_idle_count": active_idle_count,
            "active_idle_total_duration_ms": _duration_ms(
                _merge(qualifying_idle_fragments)
            ),
            "page_away_duration_ms": _duration_ms(_merge(page_away)),
        }
    )
    return features
