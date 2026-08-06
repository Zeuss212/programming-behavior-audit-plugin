# Automatic Training Record and Log Folder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用左侧单一“打开日志文件夹”入口替换历史日志查看/手工导出流程，并在会话停止、分析终态和教师复核后自动刷新同一个安全训练记录。

**Architecture:** 新增一个只接受服务端固定日志根的跨平台目录打开器，以及一个把现有 `SessionLogService.export_training_record()` 包装成“失败不回滚主流程”的刷新协调器。目录 API、finalize、worker 终态和 review 路由只调用这两个窄接口；前端只保留目录按钮。旧历史列表、详情和手工导出公开契约在新闭环可用后统一删除，内部训练记录投影继续保留。

**Tech Stack:** Python 3.10+、Jupyter Server/Tornado、pathlib/subprocess/os.startfile、JSON Schema/OpenAPI、TypeScript、JupyterLab 4、Jest、pytest、Hatch/Jupyter Builder。

## Global Constraints

- 本轮工作目录固定为 `/Users/sxh/编程行为监控分析插件_交付版_20260727`；项目当前不是 Git 仓库，不初始化 Git、不创建分支、不执行提交或推送。
- 只修改自动训练记录、日志文件夹入口、对应公开契约、测试和三份交付文档；不修改维度业务规则、采集事件格式或真实部署范围。
- 不删除、迁移、覆盖或清理任何已有 `sessions`、`jobs`、`analyses`、`reviews`、`audit` 或 `training_record.json` 用户数据。
- 保持 `SessionLogService.export_training_record(session_id: str) -> dict[str, object]` 为训练记录唯一构建和写入实现，不复制第二套投影逻辑，不改变 `training-record-v1.json`。
- 自动刷新失败必须返回 `False` 并记录固定安全日志，但不得抛回 finalize、worker 终态或 review 主流程；既有完整训练记录由现有原子写入语义保护。
- 目录目标只能由服务端推导为 `<log_root>/sessions`；HTTP 请求不能提交路径；响应不能包含绝对路径。
- macOS 固定调用 `subprocess.run(["open", path], check=True, shell=False, timeout=5)`；Windows 固定调用 `os.startfile(path, "open")`；不增加 shell 拼接或第三方依赖。
- Linux、未知平台、无桌面打开器返回固定安全错误；远程 JupyterHub 不承诺打开教师客户端文件夹。
- 前端固定文案：标题 `训练日志`，按钮 `打开日志文件夹`，说明 `训练记录会在每次监控结束后自动生成。`，成功 `已打开日志文件夹。`，失败 `无法打开日志文件夹，请确认 JupyterLab 运行在本机。`。
- 本轮不调用外部 AI，不使用真实学生数据；分析相关测试只用合成 provider。
- 旧 `0.2.0` wheel 回退基线为 `dist/myextension-0.2.0-py3-none-any.whl`，SHA-256 为 `57eb2407e92906bbcae8bf3d38fceaf14798793d3336ded3b0e24b693179687f`。
- 每个任务用测试输出和计划勾选记录检查点代替 Git commit；任一门禁失败时停在当前任务修复，不进入 wheel 重装或 GUI 冒烟。

---

## File Map

### 新建

- `myextension/log_folder_opener.py`：验证固定 `sessions` 目录边界并调用 macOS/Windows 原生文件管理器。
- `myextension/training_record_automation.py`：吞掉派生记录失败并返回布尔结果的唯一协调器。
- `myextension/api_schemas/log-folder-open-response-v1.json`：目录打开成功响应的 closed schema。
- `myextension/tests/test_log_folder_opener.py`：macOS、Windows、未知平台、符号链接、失败和超时单元测试。
- `myextension/tests/test_training_record_automation.py`：协调器成功/失败隔离测试。
- `src/models/logFolder.ts`：前端目录打开响应类型。
- `src/services/logFolderApi.ts`：固定空对象 POST 客户端。
- `src/__tests__/logFolderApi.spec.ts`：前端请求契约测试。
- `docs/2026-08-03-automatic-training-record-folder-verification.md`：最终命令、结果、wheel hash、macOS 冒烟和 Windows 未覆盖项证据。

### 修改

- `myextension/routes.py`：注册目录路由；在 finalize/review 后调用刷新协调器；最终删除旧三个公开 handler 和路由。
- `myextension/analysis_worker.py`：接收 `terminal_callback: Callable[[str], object] | None`，仅在 ready/partial 持久化完成后通知。
- `myextension/__init__.py`：用现有 stores 构造 `SessionLogService`、`TrainingRecordRefresher`，并将回调注入新建 worker。
- `myextension/session_log_service.py`：保留详情投影和导出；删除仅供历史列表使用的分页方法和游标帮助函数。
- `myextension/tests/test_pilot_api.py`：新增目录/finalize/review自动刷新测试，删除旧公开列表、详情、手工导出测试。
- `myextension/tests/test_analysis_job_store.py`：补充 worker 终态回调与扩展生命周期 wiring 测试。
- `myextension/tests/test_session_log_service.py`：保留并强化自动记录所依赖的 schema、隐私、原子写入、无 AI 投影测试。
- `myextension/tests/test_schema_registry.py`：新增目录契约，删除旧三个公开 operation/schema 断言。
- `docs/openapi/myextension-v1.yaml`：新增 `/myextension/log-folder/open`，删除三个旧历史日志接口。
- `src/index.ts`：移除日志查看器命令，向侧栏注入 `openLogFolder`。
- `src/ui/behaviorAnalysisSidebar.ts`：删除历史日志状态/渲染/导出，新增单按钮状态机。
- `src/__tests__/behaviorAnalysisSidebar.spec.ts`：用按钮 pending/success/error/dispose/duplicate 测试替换旧列表测试。
- `src/__tests__/myextension.spec.ts`：删除查看器命令测试，验证插件注入 `openLogFolder`。
- `style/index.css`：删除只服务于旧日志列表/查看器的选择器。
- `README.md`、`项目说明.md`、`启动说明.md`：改写为自动生成和打开服务器本机 `sessions` 文件夹的流程。

### 删除

- `myextension/api_schemas/session-log-list-v1.json`
- `myextension/api_schemas/session-log-detail-v1.json`
- `myextension/api_schemas/training-record-response-v1.json`
- `src/models/sessionLog.ts`
- `src/services/sessionLogApi.ts`
- `src/ui/sessionLogViewer.ts`
- `src/ui/sessionLogCommand.ts`
- `src/__tests__/sessionLogApi.spec.ts`
- `src/__tests__/sessionLogViewer.spec.ts`

---

### Task 1: 跨平台固定目录打开器

**Files:**

- Create: `myextension/log_folder_opener.py`
- Create: `myextension/tests/test_log_folder_opener.py`

**Interfaces:**

- Consumes: 既有日志根 `Path`；不读取 HTTP 参数。
- Produces: `LogFolderOpenUnsupportedError`、`LogFolderOpenError`、`LogFolderOpener.open_sessions_folder() -> Literal["macos", "windows"]`。

- [x] **Step 1: 写入 macOS 和 Windows 失败测试**

测试使用注入函数，不实际启动 Finder/Explorer：

```python
def test_macos_opens_only_sessions_without_shell(tmp_path):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return object()

    platform = LogFolderOpener(
        tmp_path,
        platform="darwin",
        command_runner=run,
    ).open_sessions_folder()

    assert platform == "macos"
    assert calls == [
        (
            ["open", str((tmp_path / "sessions").resolve())],
            {"check": True, "shell": False, "timeout": 5},
        )
    ]
    assert (tmp_path / "sessions").is_dir()


def test_windows_uses_startfile_open_action(tmp_path):
    calls = []

    def startfile(path, operation):
        calls.append((path, operation))

    platform = LogFolderOpener(
        tmp_path,
        platform="win32",
        windows_startfile=startfile,
    ).open_sessions_folder()

    assert platform == "windows"
    assert calls == [(str((tmp_path / "sessions").resolve()), "open")]
```

- [x] **Step 2: 写入安全边界和错误分类失败测试**

覆盖 `linux` -> `LogFolderOpenUnsupportedError`、macOS `TimeoutExpired`、macOS `CalledProcessError`、Windows `OSError` -> `LogFolderOpenError`，以及 `sessions` 为指向根外的符号链接时拒绝且目标文件不变。目录新建后断言 POSIX 权限不向 group/other 开放：`stat.S_IMODE(path.stat().st_mode) & 0o077 == 0`。

- [x] **Step 3: 运行定向测试确认先失败**

Run: `.venv/bin/python -m pytest myextension/tests/test_log_folder_opener.py -q`

Expected: collection FAIL，原因是 `myextension.log_folder_opener` 尚不存在。

- [x] **Step 4: 实现最小固定目标打开器**

实现以下公开接口和关键调用，不接受任意目标路径：

```python
from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal


class LogFolderOpenUnsupportedError(RuntimeError):
    """The server platform has no supported local desktop opener."""


class LogFolderOpenError(RuntimeError):
    """The fixed sessions directory could not be opened safely."""


class LogFolderOpener:
    def __init__(
        self,
        root: Path,
        *,
        platform: str | None = None,
        command_runner: Callable = subprocess.run,
        windows_startfile: Callable[[str, str], object] | None = None,
    ) -> None:
        self._root = Path(root).expanduser().resolve()
        self._platform = sys.platform if platform is None else platform
        self._command_runner = command_runner
        self._windows_startfile = windows_startfile

    def open_sessions_folder(self) -> Literal["macos", "windows"]:
        target = self._safe_sessions_directory()
        try:
            if self._platform == "darwin":
                self._command_runner(
                    ["open", str(target)],
                    check=True,
                    shell=False,
                    timeout=5,
                )
                return "macos"
            if self._platform.startswith("win"):
                startfile = self._windows_startfile or getattr(os, "startfile", None)
                if startfile is None:
                    raise LogFolderOpenUnsupportedError(
                        "Windows desktop opener is unavailable."
                    )
                startfile(str(target), "open")
                return "windows"
            raise LogFolderOpenUnsupportedError("Platform is unsupported.")
        except LogFolderOpenUnsupportedError:
            raise
        except (OSError, subprocess.SubprocessError) as error:
            raise LogFolderOpenError("Sessions directory could not be opened.") from error
```

`_safe_sessions_directory()` 必须先拒绝现存符号链接，再以 `mode=0o700` 创建，随后 `resolve(strict=True)` 并用 `relative_to(self._root)` 验证边界；任何 `OSError` 或边界失败都归一化为 `LogFolderOpenError`。

- [x] **Step 5: 运行定向测试并记录检查点**

Run: `.venv/bin/python -m pytest myextension/tests/test_log_folder_opener.py -q`

Expected: 全部 PASS，测试替身记录的参数数组、`shell=False`、5 秒超时和 Windows `open` action 完全匹配。

---

### Task 2: 认证目录打开 API 与契约

**Files:**

- Create: `myextension/api_schemas/log-folder-open-response-v1.json`
- Modify: `myextension/routes.py:55-58, 1165-1277, 1897-2084`
- Modify: `myextension/tests/test_pilot_api.py:317-350`
- Modify: `myextension/tests/test_schema_registry.py:740-880`
- Modify: `docs/openapi/myextension-v1.yaml:127-174, 330-380, 554-590`

**Interfaces:**

- Consumes: `LogFolderOpener(resolve_log_root()).open_sessions_folder()`。
- Produces: authenticated/XSRF-protected `POST /myextension/log-folder/open`，空 `{}` 请求，成功响应 `{schema_version, request_id, opened: true, platform}`。

- [x] **Step 1: 写入 closed response schema**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "log-folder-open-response-v1.json",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "request_id", "opened", "platform"],
  "properties": {
    "schema_version": { "const": 1 },
    "request_id": {
      "type": "string",
      "pattern": "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    },
    "opened": { "const": true },
    "platform": { "enum": ["macos", "windows"] }
  }
}
```

- [x] **Step 2: 写入路由失败测试**

在 `test_pilot_api.py` 中用 monkeypatch 替换 `LogFolderOpener.open_sessions_folder`，断言：

```python
async def test_log_folder_open_returns_closed_private_response(jp_fetch, monkeypatch):
    monkeypatch.setattr(
        "myextension.routes.LogFolderOpener.open_sessions_folder",
        lambda self: "macos",
    )
    response = await jp_fetch(
        "myextension",
        "log-folder",
        "open",
        method="POST",
        body="{}",
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    assert response.code == 200
    payload = response_json(response)
    assert payload["opened"] is True
    assert payload["platform"] == "macos"
    assert set(payload) == {"schema_version", "request_id", "opened", "platform"}
    validate_schema("log-folder-open-response-v1", payload)
    assert str(Path.home()) not in response.body.decode("utf-8")
```

另加三组测试：非空 body 返回 422/`log_folder_open_validation_failed`；unsupported 返回 409/`log_folder_open_unsupported`；失败或超时归一化后返回 500/`log_folder_open_failed`，响应不反射异常或路径。

- [x] **Step 3: 运行路由测试确认失败**

Run: `.venv/bin/python -m pytest myextension/tests/test_pilot_api.py -q -k 'log_folder_open'`

Expected: FAIL，路由当前返回 404 或缺少 handler。

- [x] **Step 4: 实现 `LogFolderOpenRouteHandler` 并注册固定路由**

```python
class LogFolderOpenRouteHandler(PilotAPIHandler):
    @tornado.web.authenticated
    def post(self):
        try:
            if self.read_json_object():
                raise ApiRequestError(
                    422,
                    "log_folder_open_validation_failed",
                    "请求内容未通过校验。",
                )
            platform = LogFolderOpener(resolve_log_root()).open_sessions_folder()
            result = {"opened": True, "platform": platform}
            validate_schema(
                "log-folder-open-response-v1",
                {**result, "schema_version": 1, "request_id": self.request_id()},
            )
            self.finish_json(result)
        except ApiRequestError as error:
            self._finish_request_error(error)
        except LogFolderOpenUnsupportedError:
            self.finish_error(
                409,
                "log_folder_open_unsupported",
                "当前环境不支持打开本机日志文件夹。",
            )
        except LogFolderOpenError:
            self.finish_error(
                500,
                "log_folder_open_failed",
                "日志文件夹暂时无法打开。",
            )
        except Exception:
            self.finish_error(
                500,
                "log_folder_open_failed",
                "日志文件夹暂时无法打开。",
            )
```

注册 pattern `url_path_join(base_url, "myextension", "log-folder", "open")`。不读取 query/path 参数，不把异常对象传给 `finish_error`。

- [x] **Step 5: 同步 OpenAPI 和 schema 注册断言**

增加 `LogFolderOpenResponse` component、空对象 `LogFolderOpen` request body，以及 `200/401/403/409/413/422/500` 响应集合；schema registry 测试验证 operationId `openLogFolder`、请求体 closed、成功响应 component 完整。

- [x] **Step 6: 运行 API/契约测试并记录检查点**

Run: `.venv/bin/python -m pytest myextension/tests/test_log_folder_opener.py myextension/tests/test_pilot_api.py myextension/tests/test_schema_registry.py -q -k 'log_folder or schema_registry or openapi'`

Expected: 全部选中测试 PASS，测试替身未启动真实 GUI。

---

### Task 3: 自动刷新协调器与 worker 终态触发

**Files:**

- Create: `myextension/training_record_automation.py`
- Create: `myextension/tests/test_training_record_automation.py`
- Modify: `myextension/analysis_worker.py:198-225, 692-847`
- Modify: `myextension/__init__.py:12-17, 44-111`
- Modify: `myextension/tests/test_analysis_job_store.py:423-484, 1180-1242, 1600-1757`
- Modify: `myextension/tests/test_session_log_service.py:122-146, 217-247`

**Interfaces:**

- Consumes: `SessionLogService.export_training_record(session_id: str) -> dict[str, object]`。
- Produces: `TrainingRecordRefresher.refresh(session_id: str) -> bool`；`AnalysisWorker` 新增关键字参数 `terminal_callback: Callable[[str], object] | None = None`。

- [x] **Step 1: 写入刷新协调器失败隔离测试**

```python
def test_refresher_returns_false_without_exposing_service_exception():
    class Service:
        def export_training_record(self, session_id):
            raise OSError("/private/synthetic-secret-path")

    logger = Mock()
    refresher = TrainingRecordRefresher(Service(), logger=logger)

    assert refresher.refresh("10000000-0000-4000-8000-000000000001") is False
    logger.warning.assert_called_once_with("training_record_refresh_failed")
    assert "/private/synthetic-secret-path" not in str(logger.mock_calls)
```

另加成功测试，断言服务仅收到同一个 session UUID 一次并返回 `True`。

- [x] **Step 2: 运行协调器测试确认失败**

Run: `.venv/bin/python -m pytest myextension/tests/test_training_record_automation.py -q`

Expected: collection FAIL，模块尚不存在。

- [x] **Step 3: 实现固定安全日志的协调器**

```python
from __future__ import annotations

import logging

from .session_log_service import SessionLogService


class TrainingRecordRefresher:
    def __init__(
        self,
        service: SessionLogService,
        *,
        logger: logging.Logger,
    ) -> None:
        self._service = service
        self._logger = logger

    def refresh(self, session_id: str) -> bool:
        try:
            self._service.export_training_record(session_id)
        except Exception:
            self._logger.warning("training_record_refresh_failed")
            return False
        return True
```

不使用 `logger.exception`，避免异常文本中的服务器路径进入日志。

- [x] **Step 4: 写入 worker ready/partial 回调测试**

在现有 `create_worker_job()` 合成 fixture 上新增：

```python
def test_worker_notifies_terminal_callback_after_ready_commit(tmp_path):
    session_store, job_store, job = create_worker_job(tmp_path)
    session_id = str(job["session_id"])
    observed = []

    def callback(value):
        observed.append((value, job_store.get(str(job["job_id"]))["status"]))

    worker = AnalysisWorker(
        tmp_path,
        job_store=job_store,
        session_store=session_store,
        provider_client=lambda request, *, timeout_sec: provider_response(session_id),
        terminal_callback=callback,
        synchronous=True,
    )
    worker.enqueue(str(job["job_id"]))
    assert observed == [(session_id, "ready")]
    worker.shutdown()
```

另加 partial 测试和回调抛异常测试；回调异常不得把持久化 job 从 `ready`/`partial` 改为 `error`。

- [x] **Step 5: 在持久化终态后通知一次**

构造函数保存 `self._terminal_callback`。在成功路径 `job_store.finish_attempt` 返回之后、`return` 之前调用：

```python
def _notify_terminal(self, session_id: str) -> None:
    callback = self._terminal_callback
    if callback is None:
        return
    try:
        callback(session_id)
    except Exception:
        return
```

error 终态不调用；只接受 `_public_result` 已验证出的 `ready`/`partial`。

- [x] **Step 6: 在扩展生命周期中注入真实 refresher**

在构造 worker 前，用同一 `root/session_store/job_store` 创建：

```python
review_store = ReviewStore(root)
session_log_service = SessionLogService(
    root=root,
    session_store=session_store,
    job_store=job_store,
    review_store=review_store,
)
training_record_refresher = TrainingRecordRefresher(
    session_log_service,
    logger=server_app.log,
)
```

新建 `AnalysisWorker` 时传入 `terminal_callback=training_record_refresher.refresh`。更新 lifecycle FakeWorker 构造参数并断言注入的是 callable；既有自定义 worker 不被替换或关闭。

- [x] **Step 7: 运行协调器、worker、生命周期和服务回归**

Run: `.venv/bin/python -m pytest myextension/tests/test_training_record_automation.py myextension/tests/test_analysis_job_store.py myextension/tests/test_session_log_service.py -q`

Expected: 全部 PASS；ready/partial 回调只在 durable terminal commit 后发生，失败回调不改变 job 终态。

---

### Task 4: finalize 基础记录与 review 刷新触发

**Files:**

- Modify: `myextension/routes.py:951-997, 1402-1499, 1762-1894`
- Modify: `myextension/tests/test_pilot_api.py:2304-2390, 3138-3285`

**Interfaces:**

- Consumes: `TrainingRecordRefresher.refresh(session_id: str) -> bool` 和 handler 现有 `_session_log_service()`。
- Produces: finalize 在 attach 后/enqueue 前生成基础记录；review append 后刷新记录；两处失败均不改变原响应成功语义。

- [x] **Step 1: 写入 finalize 自动生成失败测试**

扩展 `test_session_start_finalize_replay_and_public_job_use_live_services`：第一次 finalize 202 后读取 `<root>/sessions/<id>/training_record.json`，执行 `validate_schema("training-record-v1", record)`，并断言 `record["ai_analysis"] is None`、`record["teacher_reviews"] == []`、响应不包含根绝对路径。

再使用 monkeypatch 令 `TrainingRecordRefresher.refresh` 返回 `False`，断言 finalize 仍为 202、session 已 finalized、job 已 attach/enqueue、原始事件仍存在。

- [x] **Step 2: 写入 review 自动刷新失败测试**

在 `test_review_overlays_latest_append_without_mutating_result` 中，review 前先由 refresher 写基础/分析记录并保存 `source_state_hash`；PATCH 200 后重新读取同一文件，断言：

```python
record = worker.session_store.read_training_record(started["session_id"])
assert record["teacher_reviews"][-1]["dimension_code"] == "CUSTOM_A1B2C3D4"
assert record["teacher_reviews"][-1]["revision"] == 1
assert record["export"]["source_state_hash"] != before_source_state_hash
```

另加 refresh 返回 `False` 的 PATCH，断言 review 仍追加且响应 200，旧完整训练记录 bytes 保持不变。

- [x] **Step 3: 运行定向测试确认缺少自动触发**

Run: `.venv/bin/python -m pytest myextension/tests/test_pilot_api.py -q -k 'finalize_replay or review_overlays or training_refresh_failure'`

Expected: 自动文件或更新 hash 断言 FAIL。

- [x] **Step 4: 为 handler 增加无抛出的刷新帮助方法**

```python
def _refresh_training_record(self, session_id: str) -> bool:
    return TrainingRecordRefresher(
        self._session_log_service(),
        logger=self.log,
    ).refresh(session_id)
```

- [x] **Step 5: 在两个精确事务边界调用**

finalize 顺序固定为：`session_store.finalize` -> `job_store.create` -> `session_store.attach_job` -> `_refresh_training_record(canonical_id)` -> `worker.enqueue` -> 202。review 顺序固定为：`ReviewStore.append` -> `_refresh_training_record(canonical_id)` -> 构建 effective response -> 200。

- [x] **Step 6: 运行 API 和训练记录服务测试**

Run: `.venv/bin/python -m pytest myextension/tests/test_pilot_api.py myextension/tests/test_session_log_service.py myextension/tests/test_training_record_automation.py -q`

Expected: 全部 PASS；无 AI 基础记录有效，analysis/review 后同一路径刷新，派生失败不回滚主数据。

---

### Task 5: 前端只保留“打开日志文件夹”

**Files:**

- Create: `src/models/logFolder.ts`
- Create: `src/services/logFolderApi.ts`
- Create: `src/__tests__/logFolderApi.spec.ts`
- Modify: `src/index.ts:19-35, 91-123`
- Modify: `src/ui/behaviorAnalysisSidebar.ts:21-36, 60-137, 266-337, 463-516, 905-940, 1303-1465, 1778-1784`
- Modify: `src/__tests__/behaviorAnalysisSidebar.spec.ts:1-180, 331-646, 857-859`
- Modify: `src/__tests__/myextension.spec.ts:1-65, 262-381`
- Modify: `style/index.css`
- Delete: `src/models/sessionLog.ts`
- Delete: `src/services/sessionLogApi.ts`
- Delete: `src/ui/sessionLogViewer.ts`
- Delete: `src/ui/sessionLogCommand.ts`
- Delete: `src/__tests__/sessionLogApi.spec.ts`
- Delete: `src/__tests__/sessionLogViewer.spec.ts`

**Interfaces:**

- Consumes: `POST log-folder/open` 成功契约。
- Produces: `openLogFolder(settings: ServerConnection.ISettings) -> Promise<ILogFolderOpenResponse>`；侧栏单按钮 pending/success/error 状态机。

- [x] **Step 1: 写入前端 API 类型和失败测试**

`src/models/logFolder.ts` 目标类型：

```typescript
export interface ILogFolderOpenResponse {
  schema_version: 1;
  request_id: string;
  opened: true;
  platform: 'macos' | 'windows';
}
```

测试 mock `requestAPI` 并断言完整调用：

```typescript
await openLogFolder(SETTINGS);
expect(requestAPI).toHaveBeenCalledWith('log-folder/open', SETTINGS, {
  method: 'POST',
  body: '{}',
  headers: { 'Content-Type': 'application/json' }
});
```

- [x] **Step 2: 运行 API 测试确认失败，再实现固定请求**

Run: `jlpm jest src/__tests__/logFolderApi.spec.ts --runInBand`

Expected before implementation: FAIL，module 不存在。

实现后再次运行，Expected: PASS。

- [x] **Step 3: 写入侧栏五个行为测试**

替换旧日志列表测试，覆盖：

1. 只出现标题、说明和一个按钮，DOM 不含 `本地日志`、`刷新日志`、`查看日志`、`导出训练记录`。
2. 点击后立即 disabled 且 `aria-busy="true"`，第二次 click 不增加调用。
3. resolve 后显示 `已打开日志文件夹。` 并恢复按钮。
4. reject 后显示 `无法打开日志文件夹，请确认 JupyterLab 运行在本机。`，不显示异常文本或路径。
5. dispose 后再 resolve/reject 不 render、不抛未处理异常。

核心测试形式：

```typescript
const pending = deferred<ILogFolderOpenResponse>();
deps.openLogFolder = jest.fn(() => pending.promise);
const sidebar = await buildSidebar(deps);
const open = findButton(sidebar, '打开日志文件夹');
open.click();
open.click();
expect(deps.openLogFolder).toHaveBeenCalledTimes(1);
expect(findButton(sidebar, '打开日志文件夹').disabled).toBe(true);
pending.resolve({
  schema_version: 1,
  request_id: REQUEST_ID,
  opened: true,
  platform: 'macos'
});
await flushPromises();
expect(sidebar.node.textContent).toContain('已打开日志文件夹。');
```

- [x] **Step 4: 运行侧栏测试确认旧 UI 不满足新契约**

Run: `jlpm jest src/__tests__/behaviorAnalysisSidebar.spec.ts --runInBand`

Expected: 新测试 FAIL，当前 DOM 仍渲染旧历史日志列表。

- [x] **Step 5: 实现侧栏窄状态机并删除旧状态**

依赖改为 `openLogFolder: typeof openLogFolder`；删除 `sessionLogs*`、`trainingExport*`、`SESSION_LOG_STATUS`、`refreshSessionLogs()`、`localLogItem()`、`exportTrainingRecord()` 和 stop/delete 后的刷新调用。

使用对象 token 隔离迟到响应：

```typescript
private logFolderOpenRequest: object | null = null;
private logFolderFeedback: { message: string; tone: 'info' | 'error' } | null = null;

private openLogFolder(): void {
  if (this.logFolderOpenRequest !== null) return;
  const request = {};
  this.logFolderOpenRequest = request;
  this.logFolderFeedback = null;
  this.render();
  void this.deps.openLogFolder(this.deps.settings).then(
    () => this.finishLogFolderOpen(request, '已打开日志文件夹。', 'info'),
    () => this.finishLogFolderOpen(
      request,
      '无法打开日志文件夹，请确认 JupyterLab 运行在本机。',
      'error'
    )
  );
}
```

`finishLogFolderOpen` 先检查 `this.isDisposed || this.logFolderOpenRequest !== request`，有效时清 pending、设置固定 feedback、render。`trainingLogsSection()` 渲染固定标题/说明/button，pending 时 button 文案不变但 disabled + `aria-busy`。

- [x] **Step 6: 简化插件激活和删除查看器代码**

`src/index.ts` 只导入 `openLogFolder`，删除 `registerSessionLogCommand` 和 `getSessionLogDetail/exportTrainingRecord/listSessionLogs`；`sidebarDependencies` actions 传 `openLogFolder`。更新 `myextension.spec.ts` 断言激活时没有注册日志查看命令，侧栏 actions 的 `openLogFolder(settings)` 委托给新 service 一次。

- [x] **Step 7: 删除旧专用 CSS 并跑前端定向回归**

删除只匹配 `.jp-BehaviorAudit-sessionLogList`、`.jp-BehaviorAudit-sessionLogItem`、`.jp-BehaviorAudit-sessionLogActions`、`.jp-BehaviorAudit-sessionLogTechnicalId`、旧 viewer 根节点的样式块；保留通用 sidebar section/notice/fieldError。

Run: `jlpm jest src/__tests__/logFolderApi.spec.ts src/__tests__/behaviorAnalysisSidebar.spec.ts src/__tests__/myextension.spec.ts --runInBand`

Expected: 全部 PASS，TypeScript 不再引用 `sessionLog`、`SessionLogViewer` 或 `registerSessionLogCommand`。

---

### Task 6: 删除旧公开日志 API、schema 和死代码

**Files:**

- Modify: `myextension/routes.py:1165-1277, 1959-1977, 2069-2071`
- Modify: `myextension/session_log_service.py:240-280, 1031-1060`
- Modify: `myextension/tests/test_pilot_api.py:317-1189, 1980-2070`
- Modify: `myextension/tests/test_schema_registry.py:740-880`
- Modify: `docs/openapi/myextension-v1.yaml:127-173, 336-377, 575-587`
- Delete: `myextension/api_schemas/session-log-list-v1.json`
- Delete: `myextension/api_schemas/session-log-detail-v1.json`
- Delete: `myextension/api_schemas/training-record-response-v1.json`

**Interfaces:**

- Consumes: Task 2 新目录 API、Task 3/4 内部自动生成、Task 5 新前端入口。
- Produces: 只保留内部 `get_detail()`/`export_training_record()` 和 `training-record-v1.json`；旧三个 HTTP URL 不再注册。

- [x] **Step 1: 先写旧路由缺席测试**

在 route 注册表测试中断言 handlers pattern 不包含：

```text
/myextension/session-logs
/myextension/sessions/([^/]+)/log-detail
/myextension/sessions/([^/]+)/training-record
```

同时断言 `/myextension/log-folder/open` 存在。

- [x] **Step 2: 删除三个 handler、pattern 和注册项**

删除 `SessionLogsRouteHandler`、`SessionLogDetailRouteHandler`、`TrainingRecordRouteHandler`。保留 `PilotAPIHandler._session_log_service()` 给自动刷新使用；`_require_present_session_log()` 若 `rg` 确认无调用则一并删除。

- [x] **Step 3: 删除仅服务公开列表的 service 代码**

删除 `list_sessions()`、`_encode_cursor()`、`_decode_cursor()`；保留 `_summary()`，因为内部 `get_detail()` 仍用它构建训练记录。

- [x] **Step 4: 删除旧 schema 和旧公开 API 测试**

删除 `test_pilot_api.py` 中所有函数名以 `test_session_log_list_`、`test_session_log_detail_`、`test_training_record_` 开头且只验证 HTTP 的测试；保留 `test_session_log_service.py` 的所有训练记录内容、隐私、限制、原子性和 symlink 测试。

- [x] **Step 5: 收敛 OpenAPI 和 schema registry**

移除三条 path、`SessionLogLimit`、`SessionLogCursor`、三个 response components 和 `TrainingRecordExport` request body。保留 `training-record-v1.json` 作为内部持久化契约，并新增断言：

```python
assert not {
    "session-log-list-v1.json",
    "session-log-detail-v1.json",
    "training-record-response-v1.json",
} & {path.name for path in SCHEMA_ROOT.glob("*.json")}
assert (SCHEMA_ROOT / "training-record-v1.json").is_file()
```

- [x] **Step 6: 运行悬空引用扫描和后端全量测试**

Run: `rg -n 'session-logs|log-detail|training-record-response-v1|session-log-list-v1|session-log-detail-v1|TrainingRecordResponse|SessionLogListResponse|SessionLogDetailResponse' myextension src docs/openapi`

Expected: 无输出。

Run: `.venv/bin/python -m pytest myextension/tests -q`

Expected: 全部 PASS；`training-record-v1.json` 的内容/安全测试仍运行。

---

### Task 7: 文档、全量门禁、wheel 重装和本机冒烟

**Files:**

- Modify: `README.md`
- Modify: `项目说明.md`
- Modify: `启动说明.md`
- Create: `docs/2026-08-03-automatic-training-record-folder-verification.md`
- Verify only: `dist/myextension-0.2.0-py3-none-any.whl`

**Interfaces:**

- Consumes: 完成后的后端、前端和 OpenAPI。
- Produces: 与实现一致的教师流程、可安装 wheel、macOS Finder 证据和明确的 Windows 真机待验项。

- [x] **Step 1: 同步三份交付文档**

统一写明：停止监控后自动生成 `sessions/<session_id>/training_record.json`；分析/复核会刷新同一文件；左侧点击“打开日志文件夹”打开运行 Jupyter Server 的机器；本地 macOS/Windows 支持，远程 JupyterHub 不会打开客户端；Windows 仍需真机验收；真实日志含学生代码，不提交或外传。删除“本地日志列表”“查看日志”“导出训练记录”的用户步骤。

- [x] **Step 2: 运行源码与文档一致性扫描**

Run: `rg -n '查看日志|导出训练记录|刷新日志|session-logs|log-detail|training-record-response-v1' README.md 项目说明.md 启动说明.md src myextension docs/openapi`

Expected: 无输出。

Run: `rg -n '打开日志文件夹|每次监控结束后自动生成|training_record.json|Windows|macOS|JupyterHub' README.md 项目说明.md 启动说明.md`

Expected: 三份文档均包含新流程和本地/远程边界。

- [x] **Step 3: 运行全量质量门禁**

Run: `.venv/bin/python -m pytest myextension/tests -q`

Run: `jlpm test --runInBand`

Run: `jlpm lint:check`

Run: `jlpm build:prod`

Expected: 四条命令全部退出 0；若 Prettier 只报告本轮文件格式，使用项目格式化命令修复后重跑完整 lint，不跳过规则。

- [x] **Step 4: 重建并检查 wheel**

Run: `PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m build --wheel`

Run: `.venv/bin/check-wheel-contents dist/myextension-0.2.0-py3-none-any.whl`

Run: `.venv/bin/python -m zipfile -l dist/myextension-0.2.0-py3-none-any.whl`

Run: `shasum -a 256 dist/myextension-0.2.0-py3-none-any.whl`

Expected: wheel 校验通过；包含新 Python 模块、新 response schema 和重建 labextension；不包含已删除三个 schema/旧 viewer 源文件；记录新的 SHA-256，不删除 `0.1.0` wheel。

- [x] **Step 5: 强制重装项目 `.venv` 并检查扩展注册**

Run: `.venv/bin/python -m pip install --force-reinstall --no-deps dist/myextension-0.2.0-py3-none-any.whl`

Run: `PATH="$PWD/.venv/bin:$PATH" .venv/bin/jupyter labextension list`

Run: `PATH="$PWD/.venv/bin:$PATH" .venv/bin/jupyter server extension list`

Expected: `myextension 0.2.0` 前端和服务端均 enabled/OK。

- [x] **Step 6: 启动隔离合成服务器并做浏览器/Finder 冒烟**

用 `mktemp -d` 创建合成日志根，设置 `JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR` 后从 `.venv` 启动只绑定 `127.0.0.1` 的 JupyterLab。使用合成 profile/session/event 完成 stop/finalize，验证无需手工导出即出现 schema 有效的 `training_record.json`；刚停止的初始 `ai_analysis` 可为 `null`，后台随后在不调用外部模型的覆盖度路径刷新为 `ready`；需要模型但未配置时，`session.analysis_status` 刷新为 `partial`、公开 `ai_analysis` 保持 `null`；响应、初始记录和最终记录均无绝对路径或凭据。

在页面点击左侧“打开日志文件夹”，确认按钮 pending 后显示 `已打开日志文件夹。`，并由 macOS Finder 打开该合成根的 `sessions`。GUI 状态不明、弹窗未知或打开目录不匹配时立即停止，不继续点击。

- [x] **Step 7: 记录 Windows 平台证据和停止点**

在验证报告记录：Windows 分支的 `os.startfile(path, "open")` 隔离测试 PASS；本机为 macOS，未执行 Windows Explorer 真机测试。停止在本地 `.venv` 和合成日志根，不部署共享 JupyterHub、不推送、不调用外部 AI。

- [x] **Step 8: 写最终验证报告**

`docs/2026-08-03-automatic-training-record-folder-verification.md` 必须列出每条实际命令、退出码/测试结果、新 wheel 绝对路径和 SHA-256、安装注册输出摘要、合成 training record 绝对位置、Finder 冒烟结果、旧 wheel 回滚 hash、Windows 真机未覆盖项和所有残余风险。

---

## Self-Review Result

- Spec coverage：目标、非目标、固定 UI 文案、macOS/Windows 原生调用、远程边界、三个自动触发点、失败不回滚、旧接口删除、数据保留、全量测试、wheel 和真机停止点均有对应任务。
- Placeholder scan：计划未使用待补实现标记；每个新接口给出精确签名、调用顺序、固定文案、错误码和验证命令。
- Type consistency：后端统一使用 `TrainingRecordRefresher.refresh(session_id: str) -> bool`；worker callback 接收同一 session ID；前端统一使用 `openLogFolder(settings) -> Promise<ILogFolderOpenResponse>`；OpenAPI/schema 字段与 TypeScript 类型一致。
- Scope control：不改变 `training-record-v1.json`、当前会话分析/复核 UI 或任何用户数据；Windows 真机和远程部署明确留到下一阶段。
