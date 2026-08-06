# 有效观察进度与停止提醒 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Apply `test-driven-development` for every behavior change and `verification-before-completion` before claiming success.

**Goal:** 让普通老师在 JupyterLab 侧栏实时看到有效观察时间、页面离开时间和最低门槛，并在不足时停止前获得明确提醒；同时保证界面进度与后端实际分析语义一致。

**Architecture:** 新增不保存代码内容的纯 TypeScript 观察进度计算单元，由上传器维护已闭合区间的确定进度；侧栏只为当前前台、已越过空闲阈值的未闭合间隔增加临时显示值。停止时由时间线构建器显式结算末尾空闲区间，再执行原有上传与分析流程。

**Tech Stack:** TypeScript、Jest、JupyterLab Lumino Widget、CSS、Python/pytest（回归验证）、jlpm/jupyter-builder。

**Execution status (2026-07-30):** Implemented. Fresh verification completed with
258/258 frontend tests, 497/497 backend tests, `lint:check`, production build,
wheel integrity validation, and both Jupyter extension registries reporting
`myextension 0.2.0` enabled/OK. Browser smoke testing and external AI calls were
intentionally not run in this stage.

## Global Constraints

- 本项目没有 Git 元数据，不初始化仓库、不创建提交；每个检查点以测试结果和文件变更作为证据。
- 不更改 AI 分析规则、知识点、测试建议、Provider/API Key、Python 版本矩阵或 30 秒默认配置。
- 不读取或输出真实学生代码；进度计算器只保存行为类型和时间戳。
- 门槛必须从已发布方案各维度的
  `analysis_config.minimum_observation.valid_observation_duration_ms`
  动态读取并取最大值，禁止在侧栏硬编码 30 秒。
- 老师始终可以停止监控；不足时使用二次确认，不禁用停止按钮。
- 临时进度不得改变上传数据，最终结论仍以后端持久化区间为准。
- 本阶段停止点为本地测试、生产构建、wheel 产物和安装说明均完成；不调用外部 AI 服务，不发送任何真实数据。

---

## Task 1: 建立观察进度的纯计算契约

**Files:**

- Create: `src/observationProgress.ts`
- Modify: `src/models/session.ts`
- Test: `src/__tests__/observationProgress.spec.ts`

- [ ] **Step 1: 写出区间合并、排除与异常时间戳的失败测试**

新增测试，至少覆盖：

```ts
it('merges valid intervals and subtracts execution/page-away overlaps', () => {
  const progress = calculateObservationProgress([
    segment('code_writing', 0, 10_000),
    segment('idle', 8_000, 20_000),
    segment('code_execution', 9_000, 11_000),
    segment('page_away', 15_000, 18_000)
  ]);

  expect(progress.validObservationDurationMs).toBe(15_000);
  expect(progress.pageAwayDurationMs).toBe(3_000);
  expect(progress.observationAnchorAt).toBe(iso(20_000));
});

it('derives duration from timestamps instead of duration_ms', () => {
  const progress = calculateObservationProgress([
    { ...segment('idle', 0, 5_000), duration_ms: 999_999 }
  ]);
  expect(progress.validObservationDurationMs).toBe(5_000);
});

it('ignores invalid timestamps without producing negative progress', () => {
  const progress = calculateObservationProgress([
    { ...segment('idle', 0, 5_000), started_at: 'invalid' }
  ]);
  expect(progress).toEqual({
    validObservationDurationMs: 0,
    pageAwayDurationMs: 0,
    observationAnchorAt: null
  });
});
```

- [ ] **Step 2: 运行定向测试并确认因模块不存在而失败**

Run:

```bash
.venv/bin/jlpm jest src/__tests__/observationProgress.spec.ts --runInBand
```

Expected: FAIL，原因是 `src/observationProgress.ts` 或导出函数尚不存在。

- [ ] **Step 3: 实现最小纯计算单元**

实现：

```ts
export interface IObservationProgress {
  validObservationDurationMs: number;
  pageAwayDurationMs: number;
  observationAnchorAt: string | null;
}

export function calculateObservationProgress(
  segments: ReadonlyArray<
    Pick<IBehaviorSegment, 'segment_type' | 'started_at' | 'ended_at'>
  >
): IObservationProgress;
```

规则：

- 有效类型：`code_writing`、`code_deletion`、`code_paste`、`idle`；
- 排除类型：`page_away`、`code_execution`；
- 先分别合并半开区间，再从有效区间中减去排除区间；
- `pageAwayDurationMs` 只计算合并后的 `page_away`；
- 锚点使用所有有效时间戳区间中最晚的 `ended_at`；
- 无效或倒置时间戳不参与计算；合法零长度区间不增加时长，但可作为后续前台空闲的计时锚点；
- 不读取 `code_excerpt`、`source` 等内容字段。

在 `IUploadSnapshot` 中增加必填字段：

```ts
validObservationDurationMs: number;
pageAwayDurationMs: number;
observationAnchorAt: string | null;
```

- [ ] **Step 4: 运行定向测试并确认通过**

Run:

```bash
.venv/bin/jlpm jest src/__tests__/observationProgress.spec.ts --runInBand
```

Expected: PASS。

---

## Task 2: 让上传器发布确定进度并在新会话重置

**Files:**

- Modify: `src/behaviorEventUploader.ts`
- Modify: `src/__tests__/behaviorEventUploader.spec.ts`
- Modify: `src/ui/behaviorAnalysisSidebar.ts`（仅补齐 `EMPTY_UPLOAD` 编译契约）
- Modify: 所有直接构造 `IUploadSnapshot` 的测试文件

- [ ] **Step 1: 写出上传器快照和重置的失败测试**

新增测试：

```ts
it('publishes observation progress from enqueued finalized segments', async () => {
  await uploader.start(profile);
  uploader.enqueue(segment('code_writing', 0, 10_000));
  uploader.enqueue(segment('page_away', 4_000, 6_000));

  expect(uploader.snapshot()).toMatchObject({
    validObservationDurationMs: 8_000,
    pageAwayDurationMs: 2_000,
    observationAnchorAt: iso(10_000)
  });
});

it('resets observation progress when a new session starts', async () => {
  await uploader.start(profile);
  uploader.enqueue(segment('idle', 0, 5_000));
  await uploader.start(profile);

  expect(uploader.snapshot()).toMatchObject({
    validObservationDurationMs: 0,
    pageAwayDurationMs: 0,
    observationAnchorAt: null
  });
});
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run:

```bash
.venv/bin/jlpm jest src/__tests__/behaviorEventUploader.spec.ts --runInBand
```

Expected: FAIL，快照缺少进度字段或新会话未重置。

- [ ] **Step 3: 实现无敏感内容的进度跟踪**

- 上传器只保存 `{segment_type, started_at, ended_at}` 投影，不复制代码内容；
- `start()` 清空投影；
- `enqueue()` 在接受已完成区间时更新计算结果；
- `snapshot()` 发布三个新增字段；
- `EMPTY_UPLOAD` 和测试基准快照使用 `0 / 0 / null`。

- [ ] **Step 4: 运行上传器测试和 TypeScript 编译**

Run:

```bash
.venv/bin/jlpm jest src/__tests__/behaviorEventUploader.spec.ts --runInBand
.venv/bin/jlpm build:lib
```

Expected: PASS；编译错误不得通过把新字段改成可选来规避。

---

## Task 3: 停止前结算最后一段前台空闲

**Files:**

- Modify: `src/behaviorTimelineBuilder.ts`
- Modify: `src/behaviorCapture.ts`
- Modify: `src/__tests__/behaviorEventUploader.spec.ts`
- Modify: `src/__tests__/behaviorCapture.spec.ts`

- [ ] **Step 1: 写出时间线闭合的失败测试**

新增测试：

```ts
it('emits the trailing idle interval when observation closes', () => {
  builder.recordExecutionFinished(at(0), context);
  builder.closeObservation(at(10_000), context);

  expect(emitted.at(-1)).toMatchObject({
    event_type: 'idle',
    started_at: at(0),
    ended_at: at(10_000),
    duration_ms: 10_000
  });
});

it('does not emit a trailing idle shorter than the active idle threshold', () => {
  builder.recordExecutionFinished(at(0), context);
  builder.closeObservation(at(1_999), context);
  expect(emitted).not.toContainEqual(
    expect.objectContaining({ event_type: 'idle' })
  );
});
```

并在捕获控制器测试中断言 `finalize()` 前已经 enqueue 末尾 idle。

- [ ] **Step 2: 运行捕获相关测试并确认失败**

Run:

```bash
.venv/bin/jlpm jest src/__tests__/behaviorEventUploader.spec.ts src/__tests__/behaviorCapture.spec.ts --runInBand
```

Expected: FAIL，`closeObservation` 尚不存在或 stop 未调用。

- [ ] **Step 3: 实现显式闭合**

在 `BehaviorTimelineBuilder` 增加：

```ts
closeObservation(occurredAt: string, context: IBehaviorContext): void {
  this.emitIdleBefore(occurredAt, context);
}
```

保持以下边界：

- 已处于 `page_away` 时不生成 idle；
- 小于 `ACTIVE_IDLE_THRESHOLD_MS` 不生成 idle；
- 不新增运行、切换或代码事件；
- 不改变既有区间的时间戳。

捕获控制器在 `editState.close('context_change')` 后、`uploader.finalize()` 前调用
`timelineBuilder.closeObservation(...)`。如测试需要，新增可注入的 `nowIso()` 依赖，
生产默认值为 `new Date().toISOString()`。

- [ ] **Step 4: 运行定向测试并确认通过**

Run:

```bash
.venv/bin/jlpm jest src/__tests__/behaviorEventUploader.spec.ts src/__tests__/behaviorCapture.spec.ts --runInBand
```

Expected: PASS。

---

## Task 4: 在侧栏显示真实门槛、实时进度和中文状态

**Files:**

- Modify: `src/ui/behaviorAnalysisSidebar.ts`
- Modify: `src/__tests__/behaviorAnalysisSidebar.spec.ts`
- Modify: `style/base.css`

- [ ] **Step 1: 写出门槛、前台临时进度和状态翻译的失败测试**

新增测试：

```ts
it('uses the maximum published dimension observation threshold', () => {
  sidebar.setProfiles([
    profileWithThresholds([10_000, 30_000, 20_000])
  ]);
  expect(sidebar.node.textContent).toContain('0.0 / 30.0 秒');
});

it('renders confirmed and provisional foreground observation time', () => {
  now.mockReturnValue(Date.parse('2026-07-30T08:00:10.000Z'));
  isDocumentActive.mockReturnValue(true);
  capture.snapshot.mockReturnValue({
    ...activeCapture,
    upload: {
      ...BASE_UPLOAD,
      validObservationDurationMs: 5_000,
      pageAwayDurationMs: 3_000,
      observationAnchorAt: '2026-07-30T08:00:00.000Z'
    }
  });

  sidebar.render();
  expect(sidebar.node.textContent).toContain('15.0 / 30.0 秒');
  expect(sidebar.node.textContent).toContain(
    '页面离开：3.0 秒（不计入有效观察）'
  );
});

it('does not advance provisional progress while the document is inactive', () => {
  isDocumentActive.mockReturnValue(false);
  // 相同快照仅显示已闭合的 5 秒
  expect(sidebar.node.textContent).toContain('5.0 / 30.0 秒');
});

it.each([
  ['queued', '已排队'],
  ['running', '分析中'],
  ['ready', '分析完成'],
  ['partial', '分析完成（部分结果）'],
  ['error', '分析失败']
])('translates %s analysis status to %s', (status, label) => {
  // 构造状态并断言侧栏不暴露内部枚举
});
```

- [ ] **Step 2: 运行侧栏测试并确认失败**

Run:

```bash
.venv/bin/jlpm jest src/__tests__/behaviorAnalysisSidebar.spec.ts --runInBand
```

Expected: FAIL，页面尚无进度区、活动状态依赖或完整中文映射。

- [ ] **Step 3: 实现门槛与进度显示**

实现私有纯辅助函数：

- `requiredObservationDurationMs(profile)`：遍历维度并取最大合法非负数；
- `displayedValidObservationDurationMs(snapshot, now, active)`：
  确定值加上符合条件的临时值；
- `formatDurationMs(ms)`：秒数保留一位小数；
- `analysisStatusLabel(status)`：完整中文映射。

侧栏 UI：

```html
<div class="behavior-analysis-progress">
  <div class="behavior-analysis-progress__summary">
    <span>有效观察时间</span>
    <span>17.8 / 30.0 秒</span>
  </div>
  <progress aria-label="有效观察时间进度" max="30000" value="17800"></progress>
  <div>页面离开：158.1 秒（不计入有效观察）</div>
</div>
```

- 无门槛时不渲染进度条；
- 进度条 `value` 封顶，旁边文字保留真实值；
- 达标后显示“已达到最低要求”；
- 监控活动时每秒刷新一次，停止或 dispose 时清除；
- `isDocumentActive` 默认实现为
  `document.visibilityState === 'visible' && document.hasFocus()`；
- 临时间隔不足 `ACTIVE_IDLE_THRESHOLD_MS` 时不增加。

- [ ] **Step 4: 使用 Jupyter 主题变量完成响应式样式**

仅使用已有或 Jupyter CSS 变量设置间距、边框、文字和提醒颜色。进度区不得挤压
“停止监控”按钮；窄侧栏中摘要允许换行。按钮保持原生焦点样式和键盘操作。

- [ ] **Step 5: 运行侧栏测试、样式检查和编译**

Run:

```bash
.venv/bin/jlpm jest src/__tests__/behaviorAnalysisSidebar.spec.ts --runInBand
.venv/bin/jlpm stylelint:check
.venv/bin/jlpm build:lib
```

Expected: PASS。

---

## Task 5: 有效时间不足时二次确认停止

**Files:**

- Modify: `src/ui/behaviorAnalysisSidebar.ts`
- Modify: `src/__tests__/behaviorAnalysisSidebar.spec.ts`
- Modify: `style/base.css`

- [ ] **Step 1: 写出停止保护和重试边界的失败测试**

新增测试：

```ts
it('does not stop on the first click when observation is below the threshold', async () => {
  click('停止监控');
  expect(capture.stop).not.toHaveBeenCalled();
  expect(alert()).toHaveTextContent(
    '当前有效观察 17.8 / 30.0 秒。现在停止将得到“数据不足”'
  );
});

it('continues monitoring when the teacher dismisses the warning', () => {
  click('停止监控');
  click('继续监控');
  expect(capture.stop).not.toHaveBeenCalled();
  expect(alert()).toBeNull();
});

it('stops when the teacher explicitly confirms', async () => {
  click('停止监控');
  click('仍要停止');
  expect(capture.stop).toHaveBeenCalledTimes(1);
});

it('does not repeat the duration warning when retrying a failed finalize', async () => {
  // 首次明确停止后模拟上传失败，再点击重试。
  expect(capture.stop).toHaveBeenCalledTimes(2);
});
```

同时覆盖：达到门槛后直接停止；开始新会话、达到门槛、停止完成会清除提醒。

- [ ] **Step 2: 运行侧栏测试并确认失败**

Run:

```bash
.venv/bin/jlpm jest src/__tests__/behaviorAnalysisSidebar.spec.ts --runInBand
```

Expected: FAIL，首次点击仍直接调用 `capture.stop()`。

- [ ] **Step 3: 实现可访问的内联确认**

- 首次不足停止只设置本地警告状态并 `render()`；
- 警告容器使用 `role="alert"`；
- “继续监控”只关闭警告；
- “仍要停止”调用带显式 `force` 的现有停止流程；
- 上传失败后的重试绕过时长确认；
- 门槛不存在时保持原有单击停止行为；
- 不禁用老师的最终停止权。

- [ ] **Step 4: 运行侧栏测试并确认通过**

Run:

```bash
.venv/bin/jlpm jest src/__tests__/behaviorAnalysisSidebar.spec.ts --runInBand
```

Expected: PASS。

---

## Task 6: 回归验证、交付说明与可安装产物

**Files:**

- Modify: `CHANGELOG.md`
- Modify: `README.md` 或现有中文启动说明（仅在命令与当前实现不一致时）
- Generated: `dist/*.whl`

- [ ] **Step 1: 更新变更说明**

记录：

- 有效观察时间和页面离开时间现在可见；
- 不足停止会先提醒但仍可强制停止；
- `ready` 等内部状态已中文化；
- 停止时会结算末尾前台空闲；
- 这些变化不会更改 AI 规则或重新分析旧会话。

- [ ] **Step 2: 运行完整前端质量命令**

Run:

```bash
.venv/bin/jlpm test --runInBand
.venv/bin/jlpm lint:check
.venv/bin/jlpm build:prod
```

Expected: 全部 PASS；记录测试数量和构建产物。

- [ ] **Step 3: 运行后端回归测试**

Run:

```bash
uv run pytest
```

Expected: 全部 PASS。虽然本轮不改后端，仍验证前后端契约未受影响。

- [ ] **Step 4: 构建并校验 wheel**

Run:

```bash
uv build --wheel
.venv/bin/python -m zipfile -t dist/*.whl
shasum -a 256 dist/*.whl
```

Expected: wheel 构建成功、压缩包完整，并记录新的 SHA-256。若 `dist` 中存在多份
wheel，先只读列出并明确本轮新产物，不删除旧产物。

- [ ] **Step 5: 安装本地产物并验证扩展注册**

Run:

```bash
uv pip install --reinstall dist/myextension-*.whl
.venv/bin/jupyter labextension list
.venv/bin/jupyter server extension list
```

Expected: 前端扩展和服务端扩展均显示启用。安装不等于浏览器已加载新版；最终
交接必须提醒关闭旧 Jupyter 进程、重新启动并硬刷新页面。

- [ ] **Step 6: 最终核验和停止**

最终报告必须包含：

- 实际执行的命令及结果；
- 修改文件和关键设计取舍；
- 新 wheel 绝对路径和 SHA-256；
- 未执行的真实外部 AI 调用与浏览器端人工冒烟测试；
- 用户下一步最短测试流程。

此处停止，不自动调用外部 AI、不上传真实会话、不重启用户正在使用的 Jupyter
进程，等待用户授权下一阶段的浏览器冒烟测试。
