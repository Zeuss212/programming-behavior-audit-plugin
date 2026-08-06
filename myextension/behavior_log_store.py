"""Local Markdown storage for aggregated behavior timeline segments.

Logs are written as Markdown (.md) files with:
  - Session summary table
  - Timeline table
  - Per-segment detail sections

Metadata (structured segment data) is stored in a companion .meta.json
file alongside the .md file.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

LOG_DIR_ENV_VAR = "JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR"
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
SEPARATOR = "---"
_PROJECTION_LOCKS_GUARD = threading.RLock()
_PROJECTION_LOCKS: dict[str, threading.RLock] = {}


def resolve_log_root() -> Path:
    """Return the configured behavior log root directory."""
    configured_root = os.environ.get(LOG_DIR_ENV_VAR)
    if configured_root:
        return Path(configured_root).expanduser()
    return Path.home() / ".jupyterlab-behavior-audit" / "logs"


def validate_session_id(session_id: object) -> str:
    """Return a canonical UUID session id or raise ``ValueError``."""
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id must be a UUID string.")

    try:
        parsed = uuid.UUID(session_id)
    except ValueError as exc:
        raise ValueError("session_id must be a valid UUID.") from exc

    return str(parsed)


def append_segments(
    session_id: str,
    segments: Sequence[Mapping[str, object]],
    log_root: Path | None = None,
) -> tuple[int, str]:
    """Append behavior segments and regenerate the full Markdown log file.

    Returns
    -------
    (accepted_count, relative_log_path)
    """
    canonical_session_id = validate_session_id(session_id)
    session_started_at = _first_segment_time(segments)
    date_part = session_started_at.date().isoformat()
    root = log_root if log_root is not None else resolve_log_root()
    log_dir = root / date_part
    _assert_projection_path(root, log_dir)
    log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    _assert_projection_path(root, log_dir / ".session_index.json")

    log_stem = _session_log_stem(canonical_session_id, session_started_at, log_dir)
    log_path = log_dir / f"{log_stem}.md"
    raw_path = log_dir / f"{log_stem}.raw_events.jsonl"
    timeline_path = log_dir / f"{log_stem}.timeline.jsonl"
    for target in (
        log_path,
        raw_path,
        timeline_path,
        log_path.with_suffix(".meta.json"),
    ):
        _assert_projection_path(root, target)
    relative_log_path = f"{date_part}/{log_stem}.md"

    # Load existing meta store (parsed from .md file), append new segments
    store = _MetaStore(log_path)
    store.append(segments)
    _append_raw_events(raw_path, segments)

    # Generate full Markdown content
    content = _generate_markdown(store)
    log_path.write_text(content, encoding="utf-8")
    _write_timeline(timeline_path, store.segments)

    return len(segments), relative_log_path


def write_session_projection(
    session_id: str,
    events: Sequence[Mapping[str, object]],
    *,
    log_root: Path | None = None,
) -> str:
    """Atomically render one complete legacy projection from canonical events.

    The caller owns idempotency.  This function never reads or appends to a
    dated raw-event file, so canonical session JSONL remains authoritative.
    """
    canonical_session_id = validate_session_id(session_id)
    copied_events = [dict(event) for event in events]
    session_started_at = _first_segment_time(copied_events)
    date_part = session_started_at.date().isoformat()
    root = Path(log_root) if log_root is not None else resolve_log_root()
    log_dir = root / date_part
    _assert_projection_path(root, log_dir)
    log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    _assert_projection_path(root, log_dir / ".session_index.json")

    log_stem = _session_log_stem(
        canonical_session_id,
        session_started_at,
        log_dir,
    )
    log_path = log_dir / f"{log_stem}.md"
    meta_path = log_path.with_suffix(".meta.json")
    raw_path = log_dir / f"{log_stem}.raw_events.jsonl"
    timeline_path = log_dir / f"{log_stem}.timeline.jsonl"
    for target in (log_path, meta_path, raw_path, timeline_path):
        _assert_projection_path(root, target)

    store = _MetaStore(log_path)
    store.segments = copied_events
    raw_text = "".join(
        json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        for event in copied_events
    )
    timeline_text = "".join(
        json.dumps(row, ensure_ascii=False) + "\n"
        for event in copied_events
        if (row := _timeline_row(event)) is not None
    )
    _atomic_write_text(
        meta_path,
        json.dumps(
            {"segments": copied_events},
            ensure_ascii=False,
            indent=2,
        ),
    )
    _atomic_write_text(raw_path, raw_text)
    _atomic_write_text(timeline_path, timeline_text)
    _atomic_write_text(log_path, _generate_markdown(store))
    return f"{date_part}/{log_stem}.md"


# ---------------------------------------------------------------------------
# Meta store – persists as companion .meta.json alongside the .md file
# ---------------------------------------------------------------------------


class _MetaStore:
    """Stores all segments for a session, persisted in a .meta.json file.

    The metadata file is stored alongside the .md log file so that the
    .md contains only visible Markdown content.
    """

    def __init__(self, md_path: Path) -> None:
        self.md_path = md_path
        self.meta_path = md_path.with_suffix(".meta.json")
        self.segments: list[dict[str, Any]] = self._load()

    # -- public API -------------------------------------------------------

    def append(self, new_segments: Sequence[Mapping[str, object]]) -> None:
        for seg in new_segments:
            self.segments.append(dict(seg))
        self._save()

    def _save(self) -> None:
        """Write segments to the companion .meta.json file."""
        meta = {"segments": self.segments}
        _atomic_write_text(
            self.meta_path,
            json.dumps(meta, ensure_ascii=False, indent=2),
        )

    def functions(self) -> list[str]:
        """Return sorted unique function names extracted from code segments."""
        functions: set[str] = set()
        for seg in self.segments:
            source = seg.get("cell_source")
            if isinstance(source, str):
                for match in re.finditer(r"def\s+(\w+)\s*\(", source):
                    functions.add(match.group(1))
        return sorted(functions)

    def total_duration_ms(self) -> int:
        if not self.segments:
            return 0
        first = _safe_parse_time(self.segments[0].get("started_at"))
        last = _safe_parse_time(self.segments[-1].get("ended_at"))
        if first is None or last is None:
            return 0
        return max(0, int((last - first).total_seconds() * 1000))

    def segment_type_stats(self) -> dict[str, float]:
        """Return total duration (ms) per segment type."""
        stats: dict[str, float] = {}
        for seg in self.segments:
            st = str(seg.get("segment_type", "unknown"))
            stats[st] = stats.get(st, 0) + float(seg.get("duration_ms", 0))
        return stats

    def execution_stats(self) -> dict[str, dict[str, Any]]:
        """Return per-cell/file execution statistics."""
        cells: dict[str, dict[str, Any]] = {}
        for seg in self.segments:
            if seg.get("segment_type") != "code_execution":
                continue
            cell_id = _execution_key(seg)
            if cell_id not in cells:
                cells[cell_id] = {
                    "failures": 0,
                    "success": False,
                    "errors": [],
                    "cell_index": seg.get("cell_index"),
                    "cell_type": seg.get("cell_type"),
                    "label": _execution_label(seg),
                }
            result = seg.get("execution_result")
            if result == "success":
                cells[cell_id]["success"] = True
            else:
                cells[cell_id]["failures"] += 1
                err = seg.get("error_type")
                if isinstance(err, str) and err:
                    cells[cell_id]["errors"].append(err)
        return cells

    def total_executions(self) -> tuple[int, int]:
        """Return (failure_count, success_count)."""
        failures = 0
        successes = 0
        for seg in self.segments:
            if seg.get("segment_type") != "code_execution":
                continue
            if seg.get("execution_result") == "success":
                successes += 1
            else:
                failures += 1
        return failures, successes

    def failures_before_success_at(self, seg_index: int) -> int:
        """Count how many failures occurred *before* this segment for the same cell."""
        if seg_index >= len(self.segments):
            return 0
        target = self.segments[seg_index]
        if target.get("segment_type") != "code_execution":
            return 0
        if target.get("execution_result") != "success":
            return 0
        cell_id = target.get("cell_id")
        key = _execution_key(target)
        if key == "unknown":
            return 0
        failures = 0
        for i in range(seg_index):
            seg = self.segments[i]
            if seg.get("segment_type") != "code_execution":
                continue
            if _execution_key(seg) != key:
                continue
            if seg.get("execution_result") != "success":
                failures += 1
        return failures

    # -- internals --------------------------------------------------------

    def _load(self) -> list[dict[str, Any]]:
        if not self.meta_path.exists():
            return []
        try:
            raw = json.loads(self.meta_path.read_text(encoding="utf-8"))
            return list(raw.get("segments") or [])
        except (json.JSONDecodeError, OSError, ValueError):
            return []


def _execution_key(segment: Mapping[str, object]) -> str:
    if segment.get("document_type") == "python_file":
        return str(segment.get("file_path") or "unknown")
    return str(segment.get("cell_id") or "unknown")


def _execution_label(segment: Mapping[str, object]) -> str:
    if segment.get("document_type") == "python_file":
        return f"文件 {segment.get('file_path') or 'unknown'}"
    return f"Cell #{segment.get('cell_index')}"


def _append_raw_events(
    path: Path, segments: Sequence[Mapping[str, object]]
) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for segment in segments:
            handle.write(json.dumps(segment, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")


def _write_timeline(path: Path, segments: Sequence[Mapping[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for segment in segments:
            row = _timeline_row(segment)
            if row:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Markdown generation
# ---------------------------------------------------------------------------


def _generate_markdown(store: _MetaStore) -> str:
    """Generate the complete Markdown content for a session log file."""
    if not store.segments:
        return "# 编程行为记录\n\n（暂无行为数据）\n"

    parts: list[str] = [
        "# 编程行为记录\n",
        _render_summary(store),
        _render_timeline(store),
        "## 行为明细\n",
    ]

    for index, segment in enumerate(store.segments):
        parts.append(_format_segment_markdown(index, segment, store))

    return "\n".join(parts) + "\n"


def _render_summary(store: _MetaStore) -> str:
    """Render the session summary section."""
    total_ms = store.total_duration_ms()
    total_sec = total_ms / 1000
    failures, successes = store.total_executions()
    stats = store.segment_type_stats()
    funcs = store.functions()

    lines = [
        "## 会话摘要\n",
        "| 指标 | 数据 |",
        "|------|------|",
        f"| 总耗时 | {total_sec:.1f} 秒 |",
    ]

    label_map: dict[str, str] = {
        "code_writing": "写代码",
        "code_deletion": "删除代码",
        "code_paste": "粘贴代码",
        "code_execution": "运行代码",
        "idle": "停顿（可能包含思考）",
        "page_away": "离开页面",
        "cell_switch": "切换 Cell",
        "notebook_switch": "切换 Notebook",
        "kernel_restart": "Kernel 重启",
    }

    for stype, label in label_map.items():
        dur = stats.get(stype, 0)
        if dur > 0:
            pct = (dur / total_ms * 100) if total_ms > 0 else 0
            lines.append(f"| {label} | {dur / 1000:.1f} 秒 ({pct:.1f}%) |")

    if failures > 0 or successes > 0:
        lines.append(f"| 运行失败 | {failures} 次 |")
        lines.append(f"| 运行成功 | {successes} 次 |")

    if funcs:
        lines.append(f"| 定义函数 | `{'`, `'.join(funcs)}` |")

    # Per-cell execution failure detail
    cell_stats = store.execution_stats()
    has_failures = any(v["failures"] > 0 for v in cell_stats.values())
    if has_failures:
        cell_detail = []
        for cell_id, info in cell_stats.items():
            label = info.get("label") or cell_id
            if info["failures"] > 0 and info["success"]:
                cell_detail.append(
                    f"- {label}：失败 {info['failures']} 次"
                    f"（{' → '.join(info['errors']) if info['errors'] else '—'}）后成功"
                )
            elif info["failures"] > 0 and not info["success"]:
                cell_detail.append(
                    f"- {label}：失败 {info['failures']} 次，未成功"
                )
        if cell_detail:
            lines.append("| 运行细节 | |\n")
            lines.extend(cell_detail)

    lines.append("")
    return "\n".join(lines)


def _render_timeline(store: _MetaStore) -> str:
    """Render a Markdown timeline table."""
    segments = store.segments
    if not segments:
        return ""

    lines = [
        "## 时间线\n",
        "| 序号 | 时刻 | 时长 | 行为 | 详情 |",
        "|------|------|------|------|------|",
    ]

    first_time = _safe_parse_time(segments[0].get("started_at"))
    if first_time is None:
        return ""

    for i, seg in enumerate(segments):
        seg_type = str(seg.get("segment_type", ""))
        start = _safe_parse_time(seg.get("started_at"))
        duration_ms = int(seg.get("duration_ms") or 0)

        offset = _format_offset(
            (start - first_time).total_seconds() if start else 0
        )
        dur = _format_duration(duration_ms)
        label = _behavior_label(seg_type)
        detail = _timeline_detail(seg, seg_type)
        lines.append(f"| {i + 1} | {offset} | {dur} | {label} | {detail} |")

    lines.append("")
    return "\n".join(lines)


def _timeline_detail(seg: Mapping[str, object], seg_type: str) -> str:
    """Generate a short one-line detail for the timeline table."""
    if seg_type == "code_writing":
        cnt = seg.get("inserted_char_count")
        parts = []
        if isinstance(cnt, int) and cnt:
            parts.append(f"+{cnt} 字符")
        if _is_likely_paste(seg):
            parts.append("含粘贴")
        return "，".join(parts) if parts else ""
    if seg_type == "code_deletion":
        cnt = seg.get("deleted_char_count")
        if isinstance(cnt, int) and cnt:
            return f"删 {cnt} 字符"
        return ""
    if seg_type == "code_paste":
        cnt = seg.get("paste_char_count")
        if isinstance(cnt, int) and cnt:
            return f"粘贴 {cnt} 字符"
        return ""
    if seg_type == "code_execution":
        result = seg.get("execution_result")
        if result == "success":
            return "成功 ✓"
        err = seg.get("error_type")
        if isinstance(err, str) and err:
            return f"失败 ✗ {err}"
        return "失败 ✗"
    if seg_type == "cell_switch":
        prev = seg.get("previous_cell_index")
        nxt = seg.get("next_cell_index")
        if isinstance(prev, int) and isinstance(nxt, int):
            return f"Cell #{prev} → #{nxt}"
        return ""
    if seg_type == "notebook_switch":
        nxt = seg.get("next_notebook_path")
        if isinstance(nxt, str):
            return f"→ {Path(nxt).name}"
        return ""
    return ""


def _timeline_row(segment: Mapping[str, object]) -> dict[str, str] | None:
    started_at = _safe_parse_time(segment.get("started_at"))
    ended_at = _safe_parse_time(segment.get("ended_at"))
    if started_at is None or ended_at is None:
        return None

    segment_type = str(segment.get("segment_type", ""))
    detail = _timeline_detail(segment, segment_type)
    behavior = _behavior_label(segment_type)
    if detail:
        behavior = f"{behavior}：{detail}"
    return {
        "time_range": f"{_format_clock_time(started_at)} - {_format_clock_time(ended_at)}",
        "behavior": behavior,
    }


def _format_segment_markdown(
    seq: int, segment: Mapping[str, object], store: _MetaStore
) -> str:
    """Format one behavior segment as a Markdown block."""
    segment_type = str(segment.get("segment_type", ""))
    started_at = _safe_parse_time(segment.get("started_at"))
    ended_at = _safe_parse_time(segment.get("ended_at"))
    duration_ms = int(segment.get("duration_ms") or 0)

    start_str = _format_local_time(started_at) if started_at else "—"
    end_str = _format_local_time(ended_at) if ended_at else "—"
    dur_str = _format_duration_detail(duration_ms)

    lines = [
        f"### {seq + 1}. {_behavior_label(segment_type)}\n",
        "| 字段 | 值 |",
        "|------|-----|",
        f"| 时间段 | {start_str} — {end_str} |",
        f"| 时长 | {dur_str} |",
    ]

    if segment.get("document_type") == "python_file":
        lines.insert(4, f"| 文件 | {_text_value(segment.get('file_path'), '—')} |")
    else:
        lines.insert(4, f"| Cell | {_format_cell(segment)} |")
        lines.insert(4, f"| Notebook | {_text_value(segment.get('notebook_path'), '—')} |")

    if segment_type == "code_writing":
        inserted = segment.get("inserted_char_count")
        if isinstance(inserted, int):
            lines.append(f"| 输入字符数 | {inserted} |")
        source = segment.get("cell_source")
        is_paste = _is_likely_paste(segment) if segment_type == "code_writing" else False
        if isinstance(source, str) and source.strip():
            lines.append("")
            lines.append("```python")
            lines.append(source.rstrip("\n"))
            lines.append("```")
            if is_paste:
                lines.append("")
                lines.append("本代码块是学生直接粘贴的。")

    elif segment_type == "code_deletion":
        deleted = segment.get("deleted_char_count")
        if isinstance(deleted, int):
            lines.append(f"| 删除字符数 | {deleted} |")
        deleted_content = segment.get("deleted_content")
        # Show deleted code block for full-line deletions
        if isinstance(deleted_content, str) and deleted_content.strip():
            is_full_line = segment.get("deleted_is_full_line", False)
            if is_full_line:
                lines.append(f"| 删除内容 | 见下方 |")
                lines.append("")
                lines.append("```text")
                lines.append(deleted_content.rstrip("\n"))
                lines.append("```")

    elif segment_type == "code_paste":
        pasted = segment.get("paste_char_count")
        if isinstance(pasted, int):
            lines.append(f"| 粘贴字符数 | {pasted} |")

    elif segment_type == "code_execution":
        result = segment.get("execution_result")
        result_label = "成功 ✓" if result == "success" else "失败 ✗"
        lines.append(f"| 执行结果 | {result_label} |")

        # Show prior failures when this execution succeeds
        if result == "success":
            prior_failures = store.failures_before_success_at(seq)
            lines.append(f"| 之前失败 | {prior_failures} 次后成功 |")

        source = segment.get("cell_source")
        if isinstance(source, str) and source.strip():
            lines.append("")
            lines.append("```python")
            lines.append(source.rstrip("\n"))
            lines.append("```")

        if result != "success":
            err_type = segment.get("error_type")
            err_msg = segment.get("error_message")
            if isinstance(err_type, str) and err_type:
                lines.append(f"| 错误类型 | `{err_type}` |")
            if isinstance(err_msg, str) and err_msg:
                lines.append(f"| 错误信息 | `{err_msg}` |")

    elif segment_type in ("idle", "page_away", "cell_switch", "notebook_switch", "kernel_restart"):
        pass

    lines.append("")
    lines.append(SEPARATOR)
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_likely_paste(segment: Mapping[str, object]) -> bool:
    """Detect if a code_writing segment is likely a paste.

    Uses the frontend's ``had_paste`` flag as the primary signal, and falls
    back to a heuristic (high character count + high insertion speed) for
    cases where paste detection was missed on the frontend.
    """
    # Frontend explicitly marked it
    if segment.get("had_paste"):
        return True

    inserted = segment.get("inserted_char_count")
    duration = segment.get("duration_ms")
    if isinstance(inserted, int) and isinstance(duration, int) and inserted > 0 and duration > 0:
        # Heuristic: ≥ 15 characters AND average speed < 100 ms/char
        # Normal typing is typically > 200 ms/char; paste is << 100 ms/char.
        if inserted >= 15 and duration / inserted < 100:
            return True

    return False


def _safe_parse_time(value: object) -> datetime | None:
    """Parse an ISO time string safely, returning None on failure."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(LOCAL_TIMEZONE)
    except (ValueError, TypeError):
        return None


def _first_segment_time(segments: Sequence[Mapping[str, object]]) -> datetime:
    if segments:
        parsed = _safe_parse_time(segments[0].get("started_at"))
        if parsed is not None:
            return parsed
    return datetime.now().astimezone(LOCAL_TIMEZONE)


def _session_log_stem(session_id: str, started_at: datetime, log_dir: Path) -> str:
    with projection_index_lock(log_dir):
        index_path = log_dir / ".session_index.json"
        index = _load_session_index(index_path)
        if session_id in index:
            return index[session_id]

        base = started_at.strftime("%Y%m%d-%H%M%S")
        stem = base
        used = set(index.values())
        suffix = 2
        while stem in used or (log_dir / f"{stem}.md").exists():
            stem = f"{base}-{suffix}"
            suffix += 1

        index[session_id] = stem
        _atomic_write_text(
            index_path,
            json.dumps(index, ensure_ascii=False, indent=2),
        )
        return stem


def projection_index_lock(log_dir: Path) -> threading.RLock:
    """Return the process-wide lock for one canonical dated projection root."""
    key = str(Path(log_dir).resolve())
    with _PROJECTION_LOCKS_GUARD:
        return _PROJECTION_LOCKS.setdefault(key, threading.RLock())


def _load_session_index(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            os.fchmod(temporary.fileno(), 0o600)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass


def _assert_projection_path(root: Path, path: Path) -> None:
    root_absolute = Path(root).absolute()
    candidate = Path(path).absolute()
    try:
        relative = candidate.relative_to(root_absolute)
    except ValueError as error:
        raise ValueError("Projection path escapes the log root.") from error

    cursor = root_absolute
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("Projection path traverses a symbolic link.")
    try:
        candidate.resolve(strict=False).relative_to(Path(root).resolve())
    except ValueError as error:
        raise ValueError("Projection path escapes the log root.") from error


def _format_local_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _format_clock_time(value: datetime) -> str:
    return value.strftime("%H:%M:%S")


def _format_offset(total_sec: float) -> str:
    """Format seconds as mm:ss or h:mm:ss from session start."""
    total_sec = int(total_sec)
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    seconds = total_sec % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _format_duration(duration_ms: int) -> str:
    """Short duration string for timeline table."""
    total_sec = duration_ms / 1000
    if total_sec < 1:
        return f"{duration_ms}ms"
    if total_sec < 60:
        return f"{total_sec:.1f}s"
    minutes = int(total_sec // 60)
    seconds = int(total_sec % 60)
    return f"{minutes}m{seconds:02d}s"


def _format_duration_detail(duration_ms: int) -> str:
    """Detailed duration string for segment detail."""
    total_sec = duration_ms / 1000
    if total_sec < 0:
        return "—"
    return f"{total_sec:.3f} 秒"


def _behavior_label(segment_type: str) -> str:
    labels: dict[str, str] = {
        "code_writing": "写代码",
        "code_deletion": "删除代码",
        "code_paste": "粘贴代码",
        "code_execution": "运行代码",
        "idle": "停顿（可能包含思考）",
        "page_away": "离开页面",
        "cell_switch": "切换 Cell",
        "notebook_switch": "切换 Notebook",
        "kernel_restart": "Kernel 重启",
    }
    return labels.get(segment_type, "未知行为")


def _format_cell(segment: Mapping[str, object]) -> str:
    cell_index = segment.get("cell_index")
    cell_type = segment.get("cell_type")
    if not isinstance(cell_index, int):
        return "—"
    if cell_type == "code":
        return f"第 {cell_index} 个代码单元"
    if cell_type == "markdown":
        return f"第 {cell_index} 个 Markdown 单元"
    return f"第 {cell_index} 个单元"


def _text_value(value: object, fallback: str) -> str:
    return value if isinstance(value, str) else fallback
