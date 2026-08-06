import json
import os

import pytest

from myextension.llm_labeler import label_segments
from myextension.schema_registry import validate_schema

LOG_DIR_ENV_VAR = "JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR"
SESSION_ID = "0d5f9d13-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def disable_ark_labeling(monkeypatch):
    monkeypatch.delenv("ARK_API_KEY", raising=False)


def make_segment(**overrides):
    segment = {
        "event_id": f"{SESSION_ID}:1",
        "session_seq": 1,
        "segment_type": "code_writing",
        "started_at": "2026-06-29T03:25:31.256Z",
        "ended_at": "2026-06-29T03:25:36.869Z",
        "duration_ms": 5613,
        "notebook_path": "Untitled.ipynb",
        "notebook_id": "notebook-1",
        "cell_id": "cell-1",
        "cell_index": 8,
        "cell_type": "code",
        "inserted_char_count": 16,
        "cell_source": "print(\"123456\")",
    }
    segment.update(overrides)
    return segment


async def test_hello(jp_fetch):
    # When
    response = await jp_fetch("myextension", "hello")

    # Then
    assert response.code == 200
    payload = json.loads(response.body)
    assert payload == {
        "data": (
            "Hello, world!"
            " This is the '/myextension/hello' endpoint."
            " Try visiting me in your browser!"
        ),
    }


async def test_ai_config_can_be_saved_from_sidebar(jp_fetch, monkeypatch, tmp_path):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    response = await jp_fetch(
        "myextension",
        "ai-config",
        method="POST",
        body=json.dumps({
            "base_url": "https://ark.example/api/coding/v3",
            "model": "glm5.2",
            "api_key": "ark-test-secret",
        }),
        headers={"Content-Type": "application/json"},
    )

    assert response.code == 200
    payload = json.loads(response.body)
    assert payload["base_url"] == "https://ark.example/api/coding/v3"
    assert payload["model"] == "glm5.2"
    assert payload["api_key_configured"] is True
    assert payload["api_key_preview"] == "...secret"
    assert "ark-test-secret" not in response.body.decode("utf-8")
    assert os.environ["ARK_API_KEY"] == "ark-test-secret"

    saved = json.loads((tmp_path / ".ark_ai_config.json").read_text(encoding="utf-8"))
    assert saved["ARK_API_KEY"] == "ark-test-secret"


async def test_ai_config_invalid_base_url_returns_actionable_400(jp_fetch):
    response = await jp_fetch(
        "myextension",
        "ai-config",
        method="POST",
        body=json.dumps({"base_url": "http://example.invalid"}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert response.code == 400
    payload = json.loads(response.body)
    validate_schema("error-v1", payload)
    assert payload["code"] == "ai_config_validation_failed"
    assert payload["retryable"] is False
    assert payload["details"] == {
        "field": "base_url",
        "reason": "insecure_url",
    }
    assert "example.invalid" not in response.body.decode("utf-8")


async def test_ai_config_write_failure_returns_retryable_closed_500(
    jp_fetch,
    monkeypatch,
):
    import myextension.routes as routes_module

    private_marker = "SYNTHETIC_PRIVATE_CONFIG_WRITE_FAILURE"

    def fail_save(_body):
        raise OSError(private_marker)

    monkeypatch.setattr(routes_module, "save_ai_config", fail_save)

    response = await jp_fetch(
        "myextension",
        "ai-config",
        method="POST",
        body=json.dumps({"model": "synthetic-model"}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert response.code == 500
    payload = json.loads(response.body)
    validate_schema("error-v1", payload)
    assert payload["code"] == "ai_config_save_failed"
    assert payload["retryable"] is True
    assert private_marker not in response.body.decode("utf-8")


async def test_behavior_segments_post_writes_human_readable_blocks(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    segments = [
        make_segment(),
        make_segment(
            segment_type="code_deletion",
            started_at="2026-06-29T03:25:36.869Z",
            ended_at="2026-06-29T03:25:37.161Z",
            duration_ms=292,
            deleted_char_count=1,
            inserted_char_count=None,
            cell_source=None,
        ),
        make_segment(
            segment_type="idle",
            started_at="2026-06-29T03:25:37.161Z",
            ended_at="2026-06-29T03:25:42.500Z",
            duration_ms=5339,
            inserted_char_count=None,
            cell_source=None,
        ),
        make_segment(
            segment_type="code_execution",
            started_at="2026-06-29T03:26:00.000Z",
            ended_at="2026-06-29T03:26:02.300Z",
            duration_ms=2300,
            execution_result="failure",
            error_type="NameError",
            error_message="name 'x' is not defined",
            inserted_char_count=None,
            cell_source=None,
        ),
        make_segment(
            segment_type="page_away",
            started_at="2026-06-29T03:26:10.000Z",
            ended_at="2026-06-29T03:26:25.000Z",
            duration_ms=15000,
            inserted_char_count=None,
            cell_source=None,
        ),
    ]

    response = await post_behavior_segments(jp_fetch, {
        "session_id": SESSION_ID,
        "segments": segments,
    })

    assert response.code == 200
    payload = json.loads(response.body)
    assert payload["status"] == "success"
    assert payload["accepted_count"] == 5
    assert payload["log_file"] == "2026-06-29/20260629-112531.md"
    assert payload["llm_labeling"] == "disabled"
    assert ".." not in payload["log_file"]
    assert "\\" not in payload["log_file"]

    log_files = list(tmp_path.rglob("*.md"))
    assert len(log_files) == 1
    text = log_files[0].read_text(encoding="utf-8")

    # Verify companion .meta.json exists
    meta_files = list(tmp_path.rglob("*.meta.json"))
    assert len(meta_files) == 1
    meta_file = meta_files[0]
    meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
    assert "segments" in meta_data
    assert len(meta_data["segments"]) == 5
    assert meta_data["segments"][0]["event_id"] == f"{SESSION_ID}:1"

    raw_files = list(tmp_path.rglob("*.raw_events.jsonl"))
    assert len(raw_files) == 1
    raw_records = [
        json.loads(line)
        for line in raw_files[0].read_text(encoding="utf-8").splitlines()
    ]
    assert len(raw_records) == 5
    assert raw_records[0]["event_id"] == f"{SESSION_ID}:1"

    timeline_files = list(tmp_path.rglob("*.timeline.jsonl"))
    assert len(timeline_files) == 1
    timeline_rows = [
        json.loads(line)
        for line in timeline_files[0].read_text(encoding="utf-8").splitlines()
    ]
    assert timeline_rows[0] == {
        "time_range": "11:25:31 - 11:25:36",
        "behavior": "写代码：+16 字符",
    }
    assert set(timeline_rows[0]) == {"time_range", "behavior"}

    # .md must NOT contain BEHAVIOR_META HTML comments
    assert "<!--BEHAVIOR_META" not in text

    # Markdown format assertions
    assert "2026-06-29 11:25:31.256 — 2026-06-29 11:25:36.869" in text
    assert "### 1. 写代码" in text
    assert "| 输入字符数 | 16 |" in text
    assert "```python" in text
    assert 'print("123456")' in text
    assert "### 2. 删除代码" in text
    assert "| 删除字符数 | 1 |" in text
    assert "### 3. 停顿（可能包含思考）" in text
    assert "| 时长 | 5.339 秒 |" in text
    assert "### 4. 运行代码" in text
    assert "| 执行结果 | 失败 ✗ |" in text
    assert "| 错误类型 | `NameError` |" in text
    assert "| 错误信息 | `name 'x' is not defined` |" in text
    assert "### 5. 离开页面" in text
    # Timeline table
    assert "| 序号 | 时刻 | 时长 | 行为 | 详情 |" in text
    assert "typing_start" not in text
    assert "kernel_busy" not in text
    # Each of the 5 segments ends with --- separator
    assert text.count("---") >= 5


async def test_behavior_segments_shows_prior_failures_on_success(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    # Two failures on same cell followed by a success
    segments = [
        make_segment(
            segment_type="code_execution",
            execution_result="failure",
            error_type="NameError",
            error_message="x undefined",
            inserted_char_count=None,
            cell_source=None,
        ),
        make_segment(
            segment_type="code_execution",
            execution_result="failure",
            error_type="SyntaxError",
            error_message="invalid syntax",
            inserted_char_count=None,
            cell_source=None,
        ),
        make_segment(
            segment_type="code_execution",
            execution_result="success",
            inserted_char_count=None,
            cell_source='print("ok")',
        ),
    ]

    response = await post_behavior_segments(jp_fetch, {
        "session_id": SESSION_ID,
        "segments": segments,
    })

    assert response.code == 200
    log_files = list(tmp_path.rglob("*.md"))
    text = log_files[0].read_text(encoding="utf-8")

    # Success segment (#3) shows prior failures count
    assert "### 3. 运行代码" in text
    assert "| 执行结果 | 成功 ✓ |" in text
    assert "| 之前失败 | 2 次后成功 |" in text
    assert "```python" in text
    assert 'print("ok")' in text


async def test_behavior_segments_success_shows_zero_prior_failures(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    # Single success — should still show "0 次后成功"
    segments = [
        make_segment(
            segment_type="code_execution",
            execution_result="success",
            inserted_char_count=None,
            cell_source='print("hello")',
        ),
    ]

    response = await post_behavior_segments(jp_fetch, {
        "session_id": SESSION_ID,
        "segments": segments,
    })

    assert response.code == 200
    log_files = list(tmp_path.rglob("*.md"))
    text = log_files[0].read_text(encoding="utf-8")

    assert "| 执行结果 | 成功 ✓ |" in text
    assert "| 之前失败 | 0 次后成功 |" in text


async def test_behavior_segments_rejects_invalid_session_id(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))

    response = await post_behavior_segments(jp_fetch, {
        "session_id": "../not-a-uuid",
        "segments": [make_segment()],
    })

    assert response.code == 400
    assert list(tmp_path.rglob("*.md")) == []


async def test_behavior_segments_rejects_missing_segments(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))

    response = await post_behavior_segments(jp_fetch, {"session_id": SESSION_ID})

    assert response.code == 400
    assert list(tmp_path.rglob("*.md")) == []


async def test_behavior_segments_rejects_non_array_segments(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))

    response = await post_behavior_segments(jp_fetch, {
        "session_id": SESSION_ID,
        "segments": make_segment(),
    })

    assert response.code == 400
    assert list(tmp_path.rglob("*.md")) == []


async def test_behavior_segments_rejects_invalid_segment_type(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))

    response = await post_behavior_segments(jp_fetch, {
        "session_id": SESSION_ID,
        "segments": [make_segment(segment_type="typing_start")],
    })

    assert response.code == 400
    assert list(tmp_path.rglob("*.txt")) == []


async def test_behavior_segments_rejects_missing_required_time(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    segment = make_segment()
    del segment["started_at"]

    response = await post_behavior_segments(jp_fetch, {
        "session_id": SESSION_ID,
        "segments": [segment],
    })

    assert response.code == 400
    assert list(tmp_path.rglob("*.md")) == []


async def test_behavior_segments_request_body_cannot_control_file_path(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))

    response = await post_behavior_segments(jp_fetch, {
        "session_id": SESSION_ID,
        "segments": [make_segment()],
        "log_file": "../../evil.txt",
        "path": "C:/temp/evil.txt",
    })

    assert response.code == 200
    payload = json.loads(response.body)
    assert payload["log_file"] == "2026-06-29/20260629-112531.md"
    assert list(tmp_path.glob("evil.txt")) == []
    assert list(tmp_path.rglob("20260629-112531.md"))


async def test_behavior_segments_same_session_reuses_time_log_file(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))

    first = await post_behavior_segments(jp_fetch, {
        "session_id": SESSION_ID,
        "segments": [make_segment(session_seq=1)],
    })
    second = await post_behavior_segments(jp_fetch, {
        "session_id": SESSION_ID,
        "segments": [make_segment(session_seq=2)],
    })

    first_payload = json.loads(first.body)
    second_payload = json.loads(second.body)

    assert first_payload["log_file"] == "2026-06-29/20260629-112531.md"
    assert second_payload["log_file"] == first_payload["log_file"]
    assert len(list(tmp_path.rglob("*.md"))) == 1
    raw_file = next(tmp_path.rglob("*.raw_events.jsonl"))
    assert len(raw_file.read_text(encoding="utf-8").splitlines()) == 2


async def test_latest_analysis_returns_newest_stage_file(jp_fetch, monkeypatch, tmp_path):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    day_dir = tmp_path / "2026-06-29"
    day_dir.mkdir()
    old_file = day_dir / "20260629-100000.stage_samples.jsonl"
    new_file = day_dir / "20260629-110000.stage_samples.pretty.json"
    empty_file = day_dir / "20260629-120000.stage_samples.jsonl"
    raw_file = day_dir / "20260629-110000.raw_events.jsonl"
    source_file = day_dir / "20260629-110000.md"
    old_file.write_text('{"stage_seq":1}\n', encoding="utf-8")
    new_file.write_text('[{"stage_seq":2}]', encoding="utf-8")
    empty_file.write_text("", encoding="utf-8")
    raw_file.write_text('{"segment_type":"code_writing"}\n', encoding="utf-8")
    source_file.write_text("# 编程行为记录\n", encoding="utf-8")
    os.utime(old_file, (1, 1))
    os.utime(new_file, (2, 2))
    os.utime(empty_file, (3, 3))
    os.utime(raw_file, (2, 2))

    response = await jp_fetch("myextension", "latest-analysis", raise_error=False)

    assert response.code == 200
    payload = json.loads(response.body)
    assert payload["status"] == "success"
    assert payload["path"] == "2026-06-29/20260629-110000.stage_samples.pretty.json"
    assert payload["source_path"] == "2026-06-29/20260629-110000.md"
    assert payload["raw_path"] == "2026-06-29/20260629-110000.raw_events.jsonl"
    assert payload["log_groups"][0]["category"] == "可读记录"
    assert payload["log_groups"][1]["category"] == "训练数据"
    assert payload["content"] == '[{"stage_seq":2}]'
    assert payload["truncated"] is False


async def test_latest_analysis_returns_an_empty_state_without_legacy_files(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))

    response = await jp_fetch("myextension", "latest-analysis", raise_error=False)

    assert response.code == 200
    assert json.loads(response.body) == {
        "status": "empty",
        "log_groups": [],
        "content": "",
        "truncated": False,
    }


def test_label_segments_writes_async_label_files(monkeypatch, tmp_path):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    segment = make_segment(
        event_id=f"{SESSION_ID}:42",
        session_seq=42,
        document_type="python_file",
        file_path="beheav.py",
    )

    def fake_client(segments):
        return {
            "labels": [
                {
                    "event_id": segments[0]["event_id"],
                    "behavior_code": "BEHAVIOR.CODE.WRITE_CODE",
                    "target_code": "TARGET.EDITOR.NOTEBOOK_CELL",
                    "ability_tags": [
                        {
                            "ability_code": "ABILITY.CODE.SYNTAX",
                            "mastery": "partial",
                        }
                    ],
                    "knowledge_points": [],
                    "code_errors": [],
                    "teacher_summary": "学生正在编写代码。",
                    "confidence": 0.8,
                }
            ]
        }

    label_segments(
        SESSION_ID,
        "2026-06-29/20260629-112531.md",
        [segment],
        client=fake_client,
    )

    label_file = tmp_path / "2026-06-29" / "20260629-112531.llm_labels.jsonl"
    sample_file = tmp_path / "2026-06-29" / "20260629-112531.samples.jsonl"
    stage_file = tmp_path / "2026-06-29" / "20260629-112531.stage_samples.jsonl"
    pretty_stage_file = (
        tmp_path / "2026-06-29" / "20260629-112531.stage_samples.pretty.json"
    )
    status_file = tmp_path / "2026-06-29" / "20260629-112531.analysis_status.json"
    label_record = json.loads(label_file.read_text(encoding="utf-8").splitlines()[0])
    sample = json.loads(sample_file.read_text(encoding="utf-8").splitlines()[0])
    stage = json.loads(stage_file.read_text(encoding="utf-8").splitlines()[0])
    pretty_stage = json.loads(pretty_stage_file.read_text(encoding="utf-8"))
    status = json.loads(status_file.read_text(encoding="utf-8"))

    assert label_record["event_id"] == f"{SESSION_ID}:42"
    assert label_record["status"] == "success"
    assert label_record["label"]["behavior_code"] == "BEHAVIOR.CODE.WRITE_CODE"
    assert sample["seq"] == 42
    assert sample["event_type"] == "code_writing"
    assert sample["start_time"] == "2026-06-29T03:25:31.256Z"
    assert sample["duration_ms"] == 5613
    assert sample["file_path"] == "beheav.py"
    assert sample["edit_chars"] == 16
    assert sample["code"] == 'print("123456")'
    assert sample["behavior_code"] == "BEHAVIOR.CODE.WRITE_CODE"
    assert sample["behavior_name"] == "编写代码"
    assert sample["target_code"] == "TARGET.EDITOR.PYTHON_FILE"
    assert sample["target_name"] == "Python 文件编辑区"
    assert sample["ability_tags"] == [
        {
            "code": "ABILITY.CODE.SYNTAX",
            "name": "基础语法",
            "mastery": "partial",
            "mastery_name": "部分掌握",
        }
    ]
    assert sample["confidence"] == 0.8
    assert "sample_id" not in sample
    assert "session_id" not in sample
    assert "schema_version" not in sample
    assert "event_id" not in sample
    assert "end_time" not in sample
    assert "context" not in sample
    assert "code_snapshot" not in sample
    assert "label" not in sample
    assert "model" not in sample
    assert "prompt_version" not in sample
    assert stage["stage_seq"] == 1
    assert stage["start_seq"] == 42
    assert stage["end_seq"] == 42
    assert stage["behavior_code"] == "BEHAVIOR.STAGE.CODE_EDIT"
    assert stage["behavior_name"] == "编码编辑阶段"
    assert stage["file_path"] == "beheav.py"
    assert stage["edit_chars"] == 16
    assert stage["target_code"] == "TARGET.EDITOR.PYTHON_FILE"
    assert stage["target_name"] == "Python 文件编辑区"
    assert stage["mastery_name"] == "部分掌握"
    assert pretty_stage[0]["stage_seq"] == 1
    assert "event_type" not in stage
    assert "code" not in stage
    assert "sample_id" not in stage
    assert "session_id" not in stage
    assert status["status"] == "ready"


def test_stage_samples_are_sorted_and_aggregated(monkeypatch, tmp_path):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    segments = [
        make_segment(
            event_id=f"{SESSION_ID}:2",
            session_seq=2,
            segment_type="code_deletion",
            deleted_char_count=1,
            inserted_char_count=None,
            cell_source=None,
            document_type="python_file",
            file_path="beheav.py",
        ),
        make_segment(
            event_id=f"{SESSION_ID}:1",
            session_seq=1,
            inserted_char_count=10,
            cell_source="x = 1",
            document_type="python_file",
            file_path="beheav.py",
        ),
        make_segment(
            event_id=f"{SESSION_ID}:3",
            session_seq=3,
            segment_type="code_execution",
            execution_result="success",
            inserted_char_count=None,
            cell_source="x = 1",
            document_type="python_file",
            file_path="beheav.py",
        ),
    ]

    def fake_client(segments):
        return {
            "labels": [
                {
                    "event_id": segment["event_id"],
                    "behavior_code": "BEHAVIOR.RUN.RUN_CODE"
                    if segment["segment_type"] == "code_execution"
                    else "BEHAVIOR.CODE.WRITE_CODE",
                    "target_code": "TARGET.EDITOR.PYTHON_FILE",
                    "ability_tags": [],
                    "knowledge_points": [],
                    "code_errors": [],
                    "confidence": 0.9,
                }
                for segment in segments
            ]
        }

    label_segments(
        SESSION_ID,
        "2026-06-29/20260629-112531.md",
        segments,
        client=fake_client,
    )

    stage_file = tmp_path / "2026-06-29" / "20260629-112531.stage_samples.jsonl"
    stage = json.loads(stage_file.read_text(encoding="utf-8").splitlines()[0])

    assert stage["stage_seq"] == 1
    assert stage["start_seq"] == 1
    assert stage["end_seq"] == 3
    assert stage["edit_chars"] == 10
    assert stage["delete_chars"] == 1
    assert stage["run_count"] == 1
    assert stage["execution_result"] == "success"
    assert stage["behavior_code"] == "BEHAVIOR.STAGE.CODE_RUN"
    assert stage["mastery_hint"] == "proficient"
    assert "revision_count" not in stage


def test_stage_samples_keep_failed_runs_before_final_success(monkeypatch, tmp_path):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    segments = [
        make_segment(
            event_id=f"{SESSION_ID}:1",
            session_seq=1,
            inserted_char_count=10,
            cell_source="def f():\n    return x",
            document_type="python_file",
            file_path="beheav.py",
        ),
        make_segment(
            event_id=f"{SESSION_ID}:2",
            session_seq=2,
            segment_type="code_execution",
            execution_result="failure",
            error_type="NameError",
            inserted_char_count=None,
            cell_source="def f():\n    return x",
            document_type="python_file",
            file_path="beheav.py",
        ),
        make_segment(
            event_id=f"{SESSION_ID}:3",
            session_seq=3,
            segment_type="code_deletion",
            deleted_char_count=1,
            inserted_char_count=None,
            cell_source=None,
            document_type="python_file",
            file_path="beheav.py",
        ),
        make_segment(
            event_id=f"{SESSION_ID}:4",
            session_seq=4,
            inserted_char_count=6,
            cell_source="def f():\n    return 1",
            document_type="python_file",
            file_path="beheav.py",
        ),
        make_segment(
            event_id=f"{SESSION_ID}:5",
            session_seq=5,
            segment_type="code_execution",
            execution_result="success",
            inserted_char_count=None,
            cell_source="def f():\n    return 1",
            document_type="python_file",
            file_path="beheav.py",
        ),
    ]

    def fake_client(segments):
        return {
            "labels": [
                {
                    "event_id": segment["event_id"],
                    "behavior_code": "BEHAVIOR.RUN.RUN_CODE"
                    if segment["segment_type"] == "code_execution"
                    else "BEHAVIOR.CODE.WRITE_CODE",
                    "target_code": "TARGET.EDITOR.PYTHON_FILE",
                    "ability_tags": [],
                    "knowledge_points": [],
                    "code_errors": [],
                    "confidence": 0.9,
                }
                for segment in segments
            ]
        }

    label_segments(
        SESSION_ID,
        "2026-06-29/20260629-112531.md",
        segments,
        client=fake_client,
    )

    stage_file = tmp_path / "2026-06-29" / "20260629-112531.stage_samples.jsonl"
    rows = stage_file.read_text(encoding="utf-8").splitlines()
    stage = json.loads(rows[0])

    assert len(rows) == 1
    assert stage["start_seq"] == 1
    assert stage["end_seq"] == 5
    assert stage["run_count"] == 2
    assert stage["failed_run_count"] == 1
    assert stage["execution_result"] == "success"
    assert stage["mastery_hint"] == "partial"
    assert stage["mastery_scope"] == "function"
    assert stage["function_name"] == "f"
    assert stage["function_names"] == ["f"]


def test_stage_samples_normalize_duplicate_knowledge_points(monkeypatch, tmp_path):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    segment = make_segment(
        event_id=f"{SESSION_ID}:1",
        session_seq=1,
        cell_source="def climb_stairs(n):\n    return n",
        document_type="python_file",
        file_path="dp.py",
    )

    def fake_client(segments):
        return {
            "labels": [
                {
                    "event_id": segments[0]["event_id"],
                    "behavior_code": "BEHAVIOR.CODE.WRITE_CODE",
                    "target_code": "TARGET.EDITOR.PYTHON_FILE",
                    "ability_tags": [],
                    "knowledge_points": [
                        {
                            "kp_code": "KP.DP.BASIC",
                            "kp_name": "动态规划基础",
                            "mastery": "proficient",
                        },
                        {
                            "kp_code": "KP.ALGO.DYNAMIC_PROGRAMMING",
                            "kp_name": "动态规划",
                            "mastery": "partial",
                        },
                        {
                            "kp_code": "KP.PYTHON.LOOP_BOUNDARY",
                            "kp_name": "循环边界",
                            "mastery": "partial",
                        },
                        {
                            "kp_code": "KP.LOOP.RANGE",
                            "kp_name": "range循环",
                            "mastery": "not_mastered",
                        },
                    ],
                    "code_errors": [],
                    "confidence": 0.9,
                }
            ]
        }

    label_segments(
        SESSION_ID,
        "2026-06-29/20260629-112531.md",
        [segment],
        client=fake_client,
    )

    stage_file = tmp_path / "2026-06-29" / "20260629-112531.stage_samples.jsonl"
    stage = json.loads(stage_file.read_text(encoding="utf-8").splitlines()[0])

    assert stage["knowledge_points"] == [
        {
            "code": "KP.ALGO.DYNAMIC_PROGRAMMING",
            "name": "动态规划",
            "mastery": "partial",
            "mastery_name": "部分掌握",
        },
        {
            "code": "KP.PYTHON.RANGE_BOUNDARY",
            "name": "range循环边界",
            "mastery": "not_mastered",
            "mastery_name": "未掌握",
        },
    ]


async def post_behavior_segments(jp_fetch, payload):
    return await jp_fetch(
        "myextension",
        "behavior-events",
        method="POST",
        body=json.dumps(payload),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
