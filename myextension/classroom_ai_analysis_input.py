"""Build the private, bounded classroom AI input after student consent."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from .dimension_analyzer import (
    _scrub_untrusted_text,
    _select_candidate_events,
)

MAX_ANALYSIS_EVENTS = 20
MAX_ANALYSIS_SNAPSHOT_CHARACTERS = 12_000
_SUPPORTED_EVENT_TYPES = {
    "code_writing",
    "code_deletion",
    "code_paste",
    "code_execution",
}
_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_URL = re.compile(r"(?i)\b(?:https?|s3)://\S+")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_BARE_SECRET = re.compile(
    r"(?i)\b(?:bearer\s+)?(?:sk-|api[_-]?key[_:= -]?|token[_:= -]?)[A-Za-z0-9._-]{8,}\b"
)
_COMMON_SECRET = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"(?:AKIA|ASIA)[0-9A-Z]{16}|xox(?:b|p|a|r|s)-[A-Za-z0-9-]{10,}|"
    r"AIza[A-Za-z0-9_-]{20,}|ya29\.[A-Za-z0-9._-]{20,})\b"
)
_QUOTED_OPAQUE_LITERAL = re.compile(
    r"(?P<quote>['\"])(?P<secret>[A-Za-z0-9._+/=-]{32,})(?P=quote)"
)
_COMMENT_OPAQUE_LITERAL = re.compile(
    r"(?m)(?P<prefix>^\s*#\s*)(?P<secret>[A-Za-z0-9._+/=-]{32,})(?=\s*$)"
)
_OPAQUE_TOKEN = re.compile(
    r"(?<![A-Za-z0-9._+/=-])(?P<secret>[A-Za-z0-9._+/=-]{32,})"
    r"(?![A-Za-z0-9._+/=-])"
)


def _value(item: object, name: str) -> object:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def _remote_event_id(
    sequence: object,
    uploaded_ranges: Sequence[object],
) -> str | None:
    if not isinstance(sequence, int) or isinstance(sequence, bool):
        return None
    for uploaded in uploaded_ranges:
        chunk = _value(uploaded, "sequence")
        first = _value(uploaded, "first_event_sequence")
        last = _value(uploaded, "last_event_sequence")
        if (
            isinstance(chunk, int)
            and not isinstance(chunk, bool)
            and isinstance(first, int)
            and not isinstance(first, bool)
            and isinstance(last, int)
            and not isinstance(last, bool)
            and first <= sequence <= last
        ):
            return f"chunk-{chunk}#event-{sequence}"
    return None


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return _scrub_untrusted_text(value).strip()[:limit]


def _safe_source(value: str) -> str:
    scrubbed = _scrub_untrusted_text(value)
    for pattern in (_EMAIL, _URL, _JWT, _BARE_SECRET, _COMMON_SECRET):
        scrubbed = pattern.sub("[redacted]", scrubbed)
    scrubbed = _OPAQUE_TOKEN.sub(_redact_opaque_token, scrubbed)
    scrubbed = _QUOTED_OPAQUE_LITERAL.sub(_redact_quoted_opaque_literal, scrubbed)
    return _COMMENT_OPAQUE_LITERAL.sub(_redact_comment_opaque_literal, scrubbed)


def _is_opaque_secret(value: str) -> bool:
    """Conservatively recognise random-looking values only in literals/comments."""

    hexadecimal = (
        len(value) >= 48
        and re.fullmatch(r"[0-9A-Fa-f]+", value) is not None
        and any(char.isalpha() for char in value)
        and any(char.isdigit() for char in value)
    )
    categories = sum(
        (
            any(char.islower() for char in value),
            any(char.isupper() for char in value),
            any(char.isdigit() for char in value),
            any(char in "._-/+=" for char in value),
        )
    )
    return (hexadecimal or categories >= 3) and len(set(value)) >= 12


def _redact_quoted_opaque_literal(match: re.Match[str]) -> str:
    secret = match.group("secret")
    if not _is_opaque_secret(secret):
        return match.group(0)
    quote = match.group("quote")
    return f"{quote}[redacted]{quote}"


def _redact_opaque_token(match: re.Match[str]) -> str:
    secret = match.group("secret")
    return "[redacted]" if _is_opaque_secret(secret) else secret


def _redact_comment_opaque_literal(match: re.Match[str]) -> str:
    if not _is_opaque_secret(match.group("secret")):
        return match.group(0)
    return f"{match.group('prefix')}[redacted]"


def _knowledge_points(profile: Mapping[str, object]) -> list[dict[str, object]]:
    raw_points = profile.get("knowledge_points")
    raw_dimensions = profile.get("dimensions")
    dimensions = {
        item.get("knowledge_point_id"): item
        for item in raw_dimensions
        if isinstance(item, Mapping)
        and isinstance(item.get("knowledge_point_id"), str)
    } if isinstance(raw_dimensions, list) else {}
    rows: list[dict[str, object]] = []
    if not isinstance(raw_points, list):
        return rows
    for point in raw_points[:10]:
        if not isinstance(point, Mapping):
            continue
        point_id = point.get("id")
        if not isinstance(point_id, str) or not point_id:
            continue
        dimension = dimensions.get(point_id)
        criteria: list[dict[str, str]] = []
        if isinstance(dimension, Mapping):
            raw_criteria = dimension.get("evidence_criteria")
            if isinstance(raw_criteria, list):
                for criterion in raw_criteria[:10]:
                    if not isinstance(criterion, Mapping):
                        continue
                    criterion_id = criterion.get("id")
                    direction = criterion.get("direction")
                    statement = _text(criterion.get("statement"), 300)
                    if (
                        isinstance(criterion_id, str)
                        and direction in {"support", "exclude"}
                        and statement
                    ):
                        criteria.append(
                            {
                                "id": criterion_id,
                                "direction": str(direction),
                                "statement": statement,
                            }
                        )
        rows.append(
            {
                "knowledge_point_id": point_id,
                "name": _text(point.get("name"), 80),
                "description": _text(point.get("description"), 500),
                "question": _text(
                    dimension.get("question") if isinstance(dimension, Mapping) else "",
                    200,
                ),
                "evidence_criteria": criteria,
            }
        )
    return rows


def _event_description(event: Mapping[str, object]) -> tuple[str, str]:
    event_type = event.get("segment_type")
    if event_type == "code_execution":
        if event.get("execution_result") == "failure":
            return "run_failure", "运行出现异常，之后可结合后续编辑与重运行判断修正过程。"
        if event.get("execution_result") == "success":
            return "run_success", "完成一次无异常运行；这不代表答案一定正确。"
        return "run", "执行了一次代码。"
    labels = {
        "code_writing": ("edit", "编辑了代码。"),
        "code_deletion": ("edit", "删除或替换了代码。"),
        "code_paste": ("edit", "粘贴并编辑了代码。"),
    }
    return labels.get(str(event_type), ("edit", "编辑了代码。"))


def build_analysis_input(
    profile: Mapping[str, object],
    detail: Mapping[str, object],
    uploaded_ranges: Sequence[object],
) -> dict[str, object]:
    """Reuse the proven Jupyter selector/scrubber and expose no raw diagnostics."""

    raw_events = detail.get("behavior_events")
    events = [
        event
        for event in raw_events
        if isinstance(event, Mapping)
        and event.get("segment_type") in _SUPPORTED_EVENT_TYPES
        and _remote_event_id(event.get("session_seq"), uploaded_ranges) is not None
    ] if isinstance(raw_events, list) else []
    candidates = _select_candidate_events(
        events,
        {"active_idle_threshold_ms": 2_000},
    )[:MAX_ANALYSIS_EVENTS]

    evidence_events: list[dict[str, object]] = []
    code_snapshots: list[dict[str, object]] = []
    remaining = MAX_ANALYSIS_SNAPSHOT_CHARACTERS
    for event in candidates:
        sequence = event.get("session_seq")
        event_id = _remote_event_id(sequence, uploaded_ranges)
        if event_id is None:
            continue
        kind, description = _event_description(event)
        evidence_events.append(
            {
                "event_id": event_id,
                "sequence": sequence,
                "kind": kind,
                "description": description,
            }
        )
        source = event.get("cell_source")
        if remaining <= 0 or not isinstance(source, str) or not source:
            continue
        safe_source = _safe_source(source)[:remaining]
        if safe_source:
            code_snapshots.append(
                {"event_id": event_id, "source": safe_source}
            )
            remaining -= len(safe_source)

    lesson = {"title": _text(profile.get("title"), 200)}
    problem_context = profile.get("problem_context")
    if isinstance(problem_context, Mapping):
        statement = _text(problem_context.get("statement"), 2_000)
        if statement:
            lesson["statement"] = statement

    return {
        "lesson": lesson,
        "knowledge_points": _knowledge_points(profile),
        "evidence_events": evidence_events,
        "code_snapshots": code_snapshots,
    }
