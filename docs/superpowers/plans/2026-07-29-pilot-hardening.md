# 编程行为监控 Pilot 加固 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保持 0.2.0 数据与 API 兼容的前提下，封闭 Python 文件执行边界，
区分采集与 AI 结论，补齐 AI 配置控制、核心监控器测试和 Python 运行入口。

**Architecture:** 保留现有 Jupyter Server Route、分析结果 Schema 和命令 id。
后端只在执行与配置边界增加闭合校验；前端使用已有 `error_code` 分流显示；测试直接
覆盖 Route、结果渲染、Sidebar 和监控器行为。

**Tech Stack:** Python 3.10+、Jupyter Server 2、Tornado、pytest、
TypeScript 5.5、JupyterLab 4、Jest 29、Rspack/Jupyter Builder。

## Global Constraints

- 不读取或输出现有 `log/`、真实 Notebook、用户身份或真实 API Key。
- 测试只使用 `tmp_path`、固定合成代码和合成配置。
- 不修改 `requires-python >=3.10` 或 Python 3.10–3.14 classifiers。
- 不修改现有 `partial + ai_not_configured` 后端契约。
- 不迁移历史会话、分析结果或教师复核文件。
- 当前目录不是 Git 工作树；不得初始化 Git。每个任务用“变更文件 + 新鲜测试输出”
  作为检查点，跳过 commit。

---

### Task 1: 封闭 Python 文件运行边界

**Files:**
- Modify: `myextension/routes.py:203-266,1736-1764`
- Create: `myextension/tests/test_python_runner.py`

**Interfaces:**
- Consumes: Jupyter `contents_manager.get(path, content=False)`、
  `contents_manager.root_dir`、本地 `_get_os_path(path)`。
- Produces: `async _contents_os_path(handler, path) -> Path`；现有
  `POST /myextension/run-python-file` 成功响应保持不变。

- [ ] **Step 1: 写异步与越界回归测试**

在 `myextension/tests/test_python_runner.py` 使用只指向临时目录的合成 Manager：

```python
class SyntheticContentsManager:
    def __init__(self, root: Path):
        self.root_dir = str(root)
        self.awaited = False

    async def get(self, path, content=False):
        await asyncio.sleep(0)
        self.awaited = True
        candidate = Path(self.root_dir) / path
        if not candidate.exists():
            raise tornado.web.HTTPError(404)
        return {"path": path, "type": "file"}

    def _get_os_path(self, path):
        return str(Path(self.root_dir) / path)
```

加入以下独立行为测试：

```python
@pytest.mark.asyncio
async def test_contents_path_awaits_manager_and_accepts_root_file(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "student.py").write_text("print('synthetic')\n", encoding="utf-8")
    manager = SyntheticContentsManager(root)
    resolved = await _contents_os_path(handler_for(manager), "student.py")
    assert manager.awaited is True
    assert resolved == (root / "student.py").resolve()


@pytest.mark.asyncio
async def test_contents_path_rejects_symlink_outside_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("raise AssertionError('must not run')\n", encoding="utf-8")
    (root / "linked.py").symlink_to(outside)
    with pytest.raises(ValueError, match="Jupyter 根目录"):
        await _contents_os_path(
            handler_for(SyntheticContentsManager(root)),
            "linked.py",
        )
```

再加入目录、缺失文件、无 `root_dir`、无 `_get_os_path` 四个测试。期望分别抛出
`ValueError` 或 `OSError`，且断言中不包含临时绝对路径。

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
.venv/bin/python -m pytest -q myextension/tests/test_python_runner.py
```

Expected: 测试因 `_contents_os_path` 不是异步函数、Manager coroutine 未等待以及
越界链接被接受而失败。

- [ ] **Step 3: 实现最小安全解析**

将 Route 改为异步并等待 helper：

```python
@tornado.web.authenticated
async def post(self):
    ...
    path = _validate_python_path(body.get("path"))
    os_path = await _contents_os_path(self, path)
```

将 helper 改为：

```python
async def _contents_os_path(handler, path):
    contents_manager = (
        getattr(handler, "contents_manager", None)
        or handler.settings.get("contents_manager")
    )
    if contents_manager is None:
        raise ValueError("当前 Jupyter Contents Manager 不支持本地运行。")

    get_os_path = getattr(contents_manager, "_get_os_path", None)
    root_dir = getattr(contents_manager, "root_dir", None)
    if not callable(get_os_path) or not isinstance(root_dir, str) or not root_dir:
        raise ValueError("当前 Jupyter Contents Manager 不支持本地运行。")

    try:
        model = await contents_manager.get(path, content=False)
    except tornado.web.HTTPError as error:
        if error.status_code == 404:
            raise OSError("Python file was not found.") from error
        raise ValueError("Jupyter Contents 校验未通过。") from error

    if not isinstance(model, dict) or model.get("type") != "file":
        raise ValueError("只能运行普通 Python 文件。")

    root = Path(root_dir).resolve()
    candidate = Path(get_os_path(path)).resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError("Python 文件必须位于 Jupyter 根目录内。") from error
    if not candidate.is_file():
        raise OSError("Python file was not found.")
    return candidate
```

对 `resolve(strict=True)` 的 `FileNotFoundError` 转换为闭合 404。保持 subprocess
超时、输出截断和响应字段不变。

- [ ] **Step 4: 验证 GREEN 与原行为**

Run:

```bash
.venv/bin/python -m pytest -q myextension/tests/test_python_runner.py
.venv/bin/python -m pytest -q myextension/tests/test_routes.py myextension/tests/test_pilot_api.py
```

Expected: 新测试通过；Legacy Route 注册和认证测试保持通过；没有
`coroutine was never awaited` warning。

- [ ] **Step 5: 记录检查点**

记录变更仅涉及 `myextension/routes.py` 和
`myextension/tests/test_python_runner.py`，保存两条 pytest 命令的退出码与通过数。

---

### Task 2: 未配置 AI 时不展示行为结论

**Files:**
- Modify: `src/models/analysisResult.ts:65-73`
- Modify: `src/ui/analysisResultView.ts:380-430`
- Modify: `src/__tests__/analysisResultView.spec.ts`

**Interfaces:**
- Consumes: 现有结果字段 `status: "partial"` 和
  `error_code: "ai_not_configured"`。
- Produces: `renderAnalysisResult()` 对未配置 AI 返回不可复核的独立空状态。

- [ ] **Step 1: 写未分析空状态失败测试**

在 `analysisResultView.spec.ts` 添加：

```typescript
it('separates captured data from a missing AI conclusion', () => {
  const value = result({
    status: 'partial',
    error_code: 'ai_not_configured'
  });
  const onReview = jest.fn();

  const node = renderAnalysisResult(value, profile, onReview);

  expect(node.textContent).toContain('数据采集完成，尚未进行 AI 分析');
  expect(node.textContent).toContain('配置 AI 服务后重试分析');
  expect(node.textContent).not.toContain('部分结果');
  expect(node.textContent).not.toContain('待复核');
  expect(node.querySelector('.jp-BehaviorAudit-resultCard')).toBeNull();
  expect(node.querySelector('form')).toBeNull();
});
```

添加第二个测试，证明其他 `partial` 且无 `ai_not_configured` 的结果仍渲染结果卡。

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
jlpm jest src/__tests__/analysisResultView.spec.ts --runInBand --coverage=false
```

Expected: 新测试因仍显示普通结果卡和“待复核”而失败。

- [ ] **Step 3: 实现兼容式显示分流**

在 `IAnalysisResult` 增加可选字段：

```typescript
error_code?: string;
```

在 `renderAnalysisResult()` 构建普通汇总前短路：

```typescript
if (result.error_code === 'ai_not_configured') {
  const root = element('section', 'jp-BehaviorAudit-results');
  root.dataset.sessionId = result.session_id;
  const heading = element('h2');
  heading.textContent = '本次会话数据';
  const state = element('div', 'jp-BehaviorAudit-resultEmpty');
  state.setAttribute('role', 'status');
  state.textContent =
    '数据采集完成，尚未进行 AI 分析。请配置 AI 服务后重试分析。';
  root.append(heading, state);
  return root;
}
```

不修改后端 Analyzer、Schema 或历史结果文件。

- [ ] **Step 4: 验证 GREEN**

Run:

```bash
jlpm jest src/__tests__/analysisResultView.spec.ts --runInBand --coverage=false
jlpm jest src/__tests__/behaviorAnalysisSidebar.spec.ts --runInBand --coverage=false
```

Expected: 未配置 AI 使用独立状态；其他 partial 和 ready 行为保持通过。

- [ ] **Step 5: 记录检查点**

记录三个变更文件与两条 Jest 命令的退出码；确认没有修改 API Schema。

---

### Task 3: AI 配置返回可操作的闭合错误

**Files:**
- Modify: `myextension/llm_transport.py:50-90,284-325`
- Modify: `myextension/routes.py:321-347`
- Modify: `myextension/tests/test_routes.py:53-79`
- Modify: `myextension/tests/test_dimension_analyzer.py:506-541`

**Interfaces:**
- Consumes: `save_ai_config(config)`。
- Produces: `AiConfigValidationError(field, reason)`；AI Config Route 返回
  `error-v1` 闭合错误。

- [ ] **Step 1: 写 400/500 错误契约测试**

在 `test_routes.py` 添加：

```python
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
    assert payload["code"] == "ai_config_validation_failed"
    assert payload["retryable"] is False
    assert payload["details"] == {
        "field": "base_url",
        "reason": "insecure_url",
    }
    assert "example.invalid" not in response.body.decode("utf-8")
```

通过 monkeypatch `save_ai_config` 抛出 `OSError`，断言 500、
`ai_config_save_failed`、`retryable=true`，且响应不含异常消息。

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
.venv/bin/python -m pytest -q myextension/tests/test_routes.py -k ai_config
```

Expected: 非安全 URL 当前返回 500/unhandled envelope，新契约测试失败。

- [ ] **Step 3: 增加类型化配置错误**

在 `llm_transport.py` 增加：

```python
class AiConfigValidationError(ValueError):
    def __init__(self, field: str, reason: str) -> None:
        super().__init__(reason)
        self.field = field
        self.reason = reason
```

让 Base URL 校验以该异常的 `field="base_url"` 和稳定 reason 抛出；该类仍继承
`ValueError`，保留现有测试兼容。

将 `AiConfigRouteHandler` 改为继承 `JsonAPIHandler`，使用
`read_json_object()`、`finish_json()` 和 `finish_error()`：

```python
except AiConfigValidationError as error:
    self.finish_error(
        400,
        "ai_config_validation_failed",
        "AI 配置格式不正确。",
        details={"field": error.field, "reason": error.reason},
    )
except ValueError:
    self.finish_error(
        400,
        "ai_config_validation_failed",
        "AI 配置格式不正确。",
        details={"field": "$", "reason": "invalid_config"},
    )
except OSError:
    self.finish_error(
        500,
        "ai_config_save_failed",
        "AI 配置保存失败。",
        retryable=True,
    )
```

成功响应保留 `status/base_url/model/api_key_configured/api_key_preview`，只增加
`schema_version/request_id`，不破坏现有消费者。

- [ ] **Step 4: 验证 GREEN**

Run:

```bash
.venv/bin/python -m pytest -q myextension/tests/test_routes.py -k ai_config
.venv/bin/python -m pytest -q myextension/tests/test_dimension_analyzer.py -k ai_config
```

Expected: 400、500 和原保存/清除/文件权限测试全部通过。

- [ ] **Step 5: 记录检查点**

记录四个变更文件、稳定错误码和两条 pytest 结果。

---

### Task 4: 在 Sidebar 中清除 Key 并定位字段错误

**Files:**
- Modify: `src/ui/behaviorAnalysisSidebar.ts:1-115,170-190,1030-1170`
- Modify: `src/__tests__/behaviorAnalysisSidebar.spec.ts`
- Modify: `style/index.css`

**Interfaces:**
- Consumes: `ApiError.details`、`requestAIConfig(init)`。
- Produces: `confirmClearAIKey(): Promise<boolean>` 依赖；
  `{"clear_api_key":true}` 清除请求；Base URL 字段错误节点。

- [ ] **Step 1: 写清除与字段错误失败测试**

扩展 Sidebar 测试依赖：

```typescript
confirmClearAIKey: jest.fn(async () => true)
```

添加三个独立测试：

```typescript
it('clears a configured key after confirmation', async () => {
  deps.requestAIConfig = jest
    .fn()
    .mockResolvedValueOnce({ status: 'success', api_key_configured: true })
    .mockResolvedValueOnce({ status: 'success', api_key_configured: false });
  const sidebar = new BehaviorAnalysisSidebar(deps);
  await flush();

  findButton(sidebar, '清除已保存 Key').click();
  await flush();

  expect(deps.confirmClearAIKey).toHaveBeenCalledTimes(1);
  expect(deps.requestAIConfig).toHaveBeenLastCalledWith(
    expect.objectContaining({
      body: JSON.stringify({ clear_api_key: true })
    })
  );
  expect(sidebar.node.textContent).toContain('AI 状态：未配置');
});
```

- 取消确认：断言只有初始 GET，没有 POST；
- `new ApiError(400, "ai_config_validation_failed", ..., false,
  {field:"base_url",reason:"insecure_url"})`：断言 Base URL 输入框
  `aria-invalid=true` 且字段下显示 HTTPS/回环地址提示。

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
jlpm jest src/__tests__/behaviorAnalysisSidebar.spec.ts --runInBand --coverage=false
```

Expected: 缺少清除按钮、确认依赖和字段错误节点而失败。

- [ ] **Step 3: 实现确认依赖和可访问控件**

在默认依赖中使用 JupyterLab `showDialog()`：

```typescript
confirmClearAIKey: async () => {
  const result = await showDialog({
    title: '清除已保存的 API Key？',
    body: '清除后，新的分析需要重新配置 Key。',
    buttons: [
      Dialog.cancelButton({ label: '取消' }),
      Dialog.warnButton({ label: '清除' })
    ]
  });
  return result.button.accept;
}
```

AI 配置区增加：

- Base URL 字段错误节点及 `aria-describedby`；
- 仅在 `aiKeyConfigured` 时显示的“清除已保存 Key”危险按钮；
- 清除中 busy/disabled 状态；
- 成功后 `aiKeyConfigured=false`、`aiStatus="AI 状态：未配置"`；
- 错误时按 `ApiError.details.field` 定位，否则写入通用 `aria-live` 状态。

`syncAISection()` 同步清除按钮的 `hidden/disabled` 状态。样式只复用或扩展现有
danger button，不引入新视觉体系。

- [ ] **Step 4: 验证 GREEN**

Run:

```bash
jlpm jest src/__tests__/behaviorAnalysisSidebar.spec.ts --runInBand --coverage=false
jlpm stylelint:check
```

Expected: 确认、取消、成功、失败和字段错误测试通过；样式检查通过。

- [ ] **Step 5: 记录检查点**

记录 Sidebar、测试和样式文件，以及 Jest/stylelint 退出码。

---

### Task 5: 直接测试 Notebook/Python 监控器并设置门禁

**Files:**
- Create: `src/__tests__/notebookMonitor.spec.ts`
- Create: `src/__tests__/pythonFileMonitor.spec.ts`
- Modify: `jest.config.js:5-17`
- Modify only if failing tests require a real behavior fix:
  `src/notebookMonitor.ts`, `src/pythonFileMonitor.ts`

**Interfaces:**
- Consumes: `NotebookBehaviorMonitor.start/getCurrentContext/
  emitCodeInputCompleted`、`PythonFileMonitor.start/getCurrentContext/
  getCurrentSource/close`。
- Produces: 两个核心文件的直接回归测试与文件级覆盖率门禁。

- [ ] **Step 1: 建立真实 Signal 测试夹具**

在测试文件使用 `@lumino/signaling` 的 `Signal`，只伪造 Jupyter 对象边界：

```typescript
const currentChanged = new Signal<object, unknown>({});
const contentChanged = new Signal<object, void>({});
const logger = {
  emit: jest.fn()
} as unknown as BehaviorEventLogger;
```

完整 fixture 必须包含生产代码会读取的 `shell.currentWidget`、
`context.path/model/pathChanged`、`sharedModel.getSource/changed` 等字段。

- [ ] **Step 2: 写 PythonFileMonitor 行为测试并确认 RED/基线**

测试：

- `.py` 当前文件返回 path/name/source；
- 内容变化发出 typing_start，advance fake timers 后发出
  typing_end/code_input_completed/idle；
- pathChanged 后结束事件使用新路径；
- 非 `.py` 返回 null；
- `close()` 立即 flush 区间并取消 idle timer，之后的新编辑仍可记录。

Run:

```bash
jlpm jest src/__tests__/pythonFileMonitor.spec.ts --runInBand --coverage=false
```

若测试揭示实际缺陷，必须先观察预期行为失败，再做最小生产修复；不能为覆盖率加入
无行为断言。

- [ ] **Step 3: 写 NotebookBehaviorMonitor 行为测试并确认 RED/基线**

测试：

- restored 后当前 Notebook/Cell 上下文正确；
- Cell change 产生 editState 调用；
- execution scheduled/success/error 产生对应 logger 事件；
- error output 能回退提取 `ename/evalue`；
- Notebook/Cell 切换更新上下文；
- 同一 Panel/Cell 重复添加不会重复绑定。

Run:

```bash
jlpm jest src/__tests__/notebookMonitor.spec.ts --runInBand --coverage=false
```

预期每个新增测试能够在删除对应 connect、事件分支或 WeakSet 防重逻辑时失败。

- [ ] **Step 4: 修复测试揭示的最小生产缺陷**

仅对真实失败行为修改监控器。例如 `start()` 重复调用导致 tracker 信号重复绑定时，
增加：

```typescript
private started = false;

start(): void {
  if (this.started) return;
  this.started = true;
  ...
}
```

不为测试暴露私有 getter，不在生产类加入只供测试使用的清理方法。

- [ ] **Step 5: 增加文件级覆盖率门禁**

在 `jest.config.js` 增加：

```javascript
coverageThreshold: {
  'src/notebookMonitor.ts': {
    statements: 70,
    branches: 60,
    functions: 70,
    lines: 70
  },
  'src/pythonFileMonitor.ts': {
    statements: 70,
    branches: 60,
    functions: 70,
    lines: 70
  }
}
```

- [ ] **Step 6: 验证 GREEN 与门禁**

Run:

```bash
jlpm test --runInBand
```

Expected: 全部 Jest 测试通过；两个核心文件分别满足门禁，而不是依赖全局平均值。

- [ ] **Step 7: 记录检查点**

记录新增测试、必要的最小生产修复、实际覆盖率和 Jest 总通过数。

---

### Task 6: 增加 Python 运行入口并中文化

**Files:**
- Modify: `package.json`
- Modify: `yarn.lock`
- Modify: `src/index.ts:1-75,133-233`
- Replace: `src/__tests__/myextension.spec.ts`

**Interfaces:**
- Consumes: `ICommandPalette`、`IMainMenu`、
  `myextension:run-current-python-file`。
- Produces: 同一命令出现在命令面板、Run 菜单和文件编辑器右键菜单。

- [ ] **Step 1: 写命令注册与中文结果失败测试**

将注册函数和格式化函数导出用于真实行为测试：

```typescript
export { formatRunResult, registerPythonFileRunner };
```

测试使用完整命令 registry fake，断言：

```typescript
expect(command.label).toBe('运行当前 Python 文件');
expect(palette.addItem).toHaveBeenCalledWith({
  command: 'myextension:run-current-python-file',
  category: '编程行为分析'
});
expect(runMenu.addGroup).toHaveBeenCalledWith(
  [{ command: 'myextension:run-current-python-file' }],
  expect.any(Number)
);
expect(contextMenu.addItem).toHaveBeenCalledWith(
  expect.objectContaining({ selector: '.jp-FileEditor' })
);
expect(formatRunResult(success)).toContain('退出代码：0');
expect(formatRunResult(success)).toContain('耗时：');
```

增加超时、无输出和 stderr 中文标题测试。

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
jlpm jest src/__tests__/myextension.spec.ts --runInBand --coverage=false
```

Expected: 当前英文标签、未注册 palette/Run menu，测试失败。

- [ ] **Step 3: 增加 Main Menu 依赖**

运行：

```bash
jlpm add @jupyterlab/mainmenu@^4.0.0
```

在插件 optional tokens 中加入 `IMainMenu`，并把 `palette/mainMenu` 传给
`registerPythonFileRunner()`。依赖安装必须只修改 `package.json/yarn.lock`；
若受网络限制，申请许可后重试，不手工伪造 lockfile。

- [ ] **Step 4: 实现入口和中文文案**

注册函数签名：

```typescript
function registerPythonFileRunner(
  app: JupyterFrontEnd,
  palette: ICommandPalette | null,
  mainMenu: IMainMenu | null,
  logger: BehaviorEventLogger,
  pythonFileMonitor: PythonFileMonitor
): void
```

加入：

```typescript
palette?.addItem({
  command: RUN_CURRENT_PYTHON_FILE_COMMAND,
  category: '编程行为分析'
});
mainMenu?.runMenu.addGroup(
  [{ command: RUN_CURRENT_PYTHON_FILE_COMMAND }],
  40
);
```

命令、对话框和 `formatRunResult()` 使用中文；stdout/stderr 保留原技术名称并增加
中文说明，如“标准输出（stdout）”。不改变 command id、请求路径和行为事件。

- [ ] **Step 5: 验证 GREEN**

Run:

```bash
jlpm jest src/__tests__/myextension.spec.ts --runInBand --coverage=false
jlpm lint:check
jlpm build:prod
```

Expected: 命令注册测试、lint 和生产构建通过。

- [ ] **Step 6: 记录检查点**

记录依赖、源码、测试和 lockfile 变更，以及 Jest/lint/build 退出码。

---

### Task 7: 全量回归与隔离 Smoke

**Files:**
- Modify: `README.md`
- Modify: `启动说明.md`
- Modify: `项目说明.md`
- Modify: `docs/2026-07-29-blind-audit.md`

**Interfaces:**
- Consumes: Tasks 1–6 的已验证行为。
- Produces: 与实际实现一致的使用说明和最终验证证据。

- [ ] **Step 1: 更新文档**

文档明确：

- Python 文件只能运行 Jupyter 根目录内的本地 `.py` 文件；
- 数据采集完成不等于 AI 已分析；
- Key 可以清除，配置错误会定位字段；
- Python 运行入口位于命令面板、Run 菜单和右键菜单；
- Python 版本声明保持 3.10–3.14，但本轮实际 smoke 环境仍需如实列出。

- [ ] **Step 2: 运行完整静态与自动化验证**

Run:

```bash
jlpm lint:check
jlpm test --runInBand
.venv/bin/python -m pytest -q myextension/tests
jlpm build:prod
.venv/bin/python -m pytest -q myextension/tests/test_labextension_artifact.py
```

Expected: 所有命令退出码 0，无 warning 被忽略。

- [ ] **Step 3: 构建 wheel 并做隔离安装**

使用临时目录构建当前 wheel；在临时 Python 环境安装 JupyterLab 4.6.1、
Jupyter Server 2.20.0 和该 wheel，执行：

```bash
jupyter server extension list
jupyter labextension list
```

Expected: Server extension 和 Labextension 都显示 `myextension 0.2.0 enabled OK`。

- [ ] **Step 4: 执行隔离 JupyterLab 浏览器回归**

仅使用固定合成文件和合成配置验证：

1. 根目录内 Python 文件正常运行；
2. 指向根目录外的符号链接被拒绝，根外脚本没有执行；
3. 未配置 AI 的会话只显示“数据采集完成，尚未进行 AI 分析”；
4. 保存合成 Key 后可确认并清除；
5. 非安全 Base URL 显示字段错误；
6. 命令面板、Run 菜单、右键菜单都能找到中文 Python 运行命令。

不得调用真实外部 AI。结束后清除合成配置并关闭临时 Server。

- [ ] **Step 5: 核对规格与隐私边界**

逐条对照
`docs/superpowers/specs/2026-07-29-pilot-hardening-design.md`，确认：

- 未修改 Python 版本范围；
- 未修改分析结果 Schema；
- 未读取或复制真实日志、Notebook、身份和 Key；
- 报告中不包含临时 token、绝对测试路径或合成 Key 原文。

- [ ] **Step 6: 记录最终检查点**

列出实际变更文件、测试通过数、覆盖率、构建结果、隔离安装结果和仍未覆盖的
Python 版本矩阵。当前目录不是 Git 仓库，因此不创建 commit 或 PR。
