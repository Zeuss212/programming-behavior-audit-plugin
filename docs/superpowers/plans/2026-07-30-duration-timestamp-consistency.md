# 行为区间时间一致性修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复合法行为区间因时间戳与持续时间相差 1–2 毫秒而被整体判为不可计算的问题，同时继续拒绝明显矛盾的时间数据。

**Architecture:** 前端时间线以最终持久化的 `started_at` 和 `ended_at` 为唯一持续时间来源，消除新采集数据的双时钟偏差。后端仅为历史及浏览器毫秒取时误差保留 2 毫秒兼容窗口，实际特征仍从时间戳区间计算；超过窗口的数据继续整体标记为时间信号不可计算。

**Tech Stack:** TypeScript 5.5、Jest 29、JupyterLab 4、Python 3.12、pytest、Jupyter Server 2。

## Global Constraints

- 不读取、输出或删除真实学生代码、身份、API Key 和旧会话内容。
- 不修改 Profile v2、知识点、测试建议或 AI Provider 配置契约。
- 不执行已保存的教师测试用例。
- 不收紧 `requires-python >=3.10` 或现有 Python classifiers。
- 后端兼容窗口固定为 2 毫秒；3 毫秒及以上差异仍视为矛盾数据。
- 新区间的 `duration_ms` 必须从同一条区间的最终时间戳计算，不能信任事件携带的独立持续时间。
- 当前目录不是 Git 仓库；不得初始化 Git，也不执行 commit。每个任务以变更文件和新鲜测试输出作为检查点。
- 重装后保留旧会话；UI 实测只创建一个合成会话。触发外部 AI 调用前再次说明目标、数据范围和停止条件。

---

### Task 1: 冻结后端 2 毫秒兼容边界

**Files:**

- Modify: `myextension/tests/test_feature_and_coverage.py:640-680`
- Modify: `myextension/feature_extractor.py:39-165`

**Interfaces:**

- Consumes: `_time_intervals(events: Sequence[Mapping[str, object]]) -> dict[int, Interval] | None`
- Produces: 时间戳与 `duration_ms` 相差不超过 2 毫秒时使用时间戳区间；相差 3 毫秒及以上时返回 `None`。

- [x] **Step 1: 写 2 毫秒兼容和 3 毫秒拒绝测试**

将原来的零容差测试拆为两个可观察行为测试：

```python
def test_duration_validation_accepts_two_millisecond_collector_drift(
    signal_dictionary: dict[str, object],
) -> None:
    idle = event(1, "idle", 0, 2000)
    idle["duration_ms"] = 1998

    features = extract_features([idle], signal_dictionary)

    assert features["valid_observation_duration_ms"] == 2000
    assert features["active_idle_count"] == 1
    assert features["active_idle_total_duration_ms"] == 2000


def test_duration_validation_rejects_three_millisecond_contradiction(
    signal_dictionary: dict[str, object],
) -> None:
    idle = event(1, "idle", 0, 2000)
    idle["duration_ms"] = 1997

    features = extract_features([idle], signal_dictionary)

    assert features["valid_observation_duration_ms"] is None
    assert features["active_idle_count"] is None
```

- [x] **Step 2: 运行新边界测试并确认 RED**

Run:

```bash
.venv/bin/python -m pytest -q \
  myextension/tests/test_feature_and_coverage.py::test_duration_validation_accepts_two_millisecond_collector_drift \
  myextension/tests/test_feature_and_coverage.py::test_duration_validation_rejects_three_millisecond_contradiction
```

Expected: 2 毫秒测试失败并得到 `None`；3 毫秒测试继续通过。

- [x] **Step 3: 加入最小后端兼容窗口**

在 `myextension/feature_extractor.py` 使用微秒常量，保留现有严格大于比较：

```python
# Frontend events can measure duration immediately before the logger stamps
# the end event. Keep a narrow compatibility window for that observed
# integer-millisecond drift while rejecting larger contradictions.
_DURATION_TOLERANCE_US = 2_000
```

`_time_intervals()` 仍将 `(start_us, end_us)` 写入结果，不能用传入的 `duration_ms` 重建区间。

- [x] **Step 4: 运行后端定向回归并确认 GREEN**

Run:

```bash
.venv/bin/python -m pytest -q myextension/tests/test_feature_and_coverage.py
```

Expected: 文件内全部测试通过；1–2 毫秒可计算，3 毫秒及明显矛盾仍不可计算。

### Task 2: 让新采集区间只使用最终时间戳

**Files:**

- Modify: `src/__tests__/behaviorEventUploader.spec.ts:650-700`
- Modify: `src/behaviorTimelineBuilder.ts:76-153`

**Interfaces:**

- Consumes: `BehaviorTimelineBuilder.enqueue(event: IBehaviorEvent): void`
- Produces: `code_writing` 和 `code_deletion` 的 `duration_ms` 始终等于 `Date.parse(ended_at) - Date.parse(started_at)`。

- [x] **Step 1: 写持久化区间一致性测试**

在 `src/__tests__/behaviorEventUploader.spec.ts` 增加真实 `BehaviorTimelineBuilder` 测试：

```typescript
describe('timeline duration consistency', () => {
  it('derives edit durations from the timestamps persisted on each segment', () => {
    const segments: IBehaviorSegment[] = [];
    const builder = new BehaviorTimelineBuilder({
      enqueue: segment => {
        segments.push(segment);
      },
      flush: async () => undefined
    });
    const context = {
      document_type: 'notebook_cell' as const,
      notebook_path: 'synthetic.ipynb',
      cell_id: 'cell-1',
      cell_index: 0
    };

    builder.enqueue({
      event_type: 'typing_start',
      occurred_at: '2026-07-30T03:00:00.000Z',
      ...context
    });
    builder.enqueue({
      event_type: 'typing_end',
      occurred_at: '2026-07-30T03:00:00.002Z',
      duration_ms: 1,
      inserted_char_count: 1,
      ...context
    });
    builder.enqueue({
      event_type: 'code_input_completed',
      occurred_at: '2026-07-30T03:00:00.003Z',
      input_ended_at: '2026-07-30T03:00:00.002Z',
      cell_source: 'x',
      ...context
    });
    builder.enqueue({
      event_type: 'deleting_start',
      occurred_at: '2026-07-30T03:00:01.000Z',
      ...context
    });
    builder.enqueue({
      event_type: 'deleting_end',
      occurred_at: '2026-07-30T03:00:01.002Z',
      duration_ms: 1,
      deleted_char_count: 1,
      ...context
    });

    expect(
      segments.map(({ segment_type, duration_ms }) => ({
        segment_type,
        duration_ms
      }))
    ).toEqual([
      { segment_type: 'code_writing', duration_ms: 2 },
      { segment_type: 'code_deletion', duration_ms: 2 }
    ]);
  });
});
```

- [x] **Step 2: 运行前端定向测试并确认 RED**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm test --runInBand \
  --coverage=false \
  src/__tests__/behaviorEventUploader.spec.ts
```

Expected: 新测试得到两个 `duration_ms: 1`，与手工推导的 `2` 不同。

- [x] **Step 3: 最小化前端时间来源**

在 `BehaviorTimelineBuilder` 中：

```typescript
private endTyping(event: IBehaviorEvent): void {
  const interval = this.typingIntervals.get(contextKey(event));
  if (!interval) {
    return;
  }

  interval.endedAt = event.occurred_at;
  interval.insertedCharCount = event.inserted_char_count;
  interval.context = { ...interval.context, ...copyContext(event) };
  if (event.had_paste) {
    interval.hadPaste = true;
    interval.pasteCharCount = event.paste_char_count;
  }
}
```

生成写入区间时固定使用：

```typescript
duration_ms: durationMs(interval.startedAt, endedAt),
```

生成删除区间时固定使用：

```typescript
duration_ms: durationMs(interval.startedAt, event.occurred_at),
```

随后删除 `IOpenTypingInterval.durationMs`，避免旧来源再次进入持久化区间。

- [x] **Step 4: 运行前端定向测试并确认 GREEN**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm test --runInBand \
  --coverage=false \
  src/__tests__/behaviorEventUploader.spec.ts
```

Expected: 新测试和现有上传、空闲阈值测试全部通过。

### Task 3: 全量回归、文档与交付包

**Files:**

- Modify: `CHANGELOG.md:3-14`
- Regenerate: `lib/`
- Regenerate: `myextension/labextension/`
- Regenerate: `dist/myextension-0.2.0-py3-none-any.whl`

**Interfaces:**

- Consumes: 项目现有 lint、Jest、pytest、JupyterLab 生产构建和 `uv build` 命令。
- Produces: 可重装的 `myextension 0.2.0` wheel 和新的 SHA-256。

- [x] **Step 1: 记录修复边界**

在 `CHANGELOG.md` 的 0.2.0 条目增加：

```markdown
- 修复行为区间时间戳与持续时间的毫秒级取时偏差：新数据由最终时间戳
  统一计算持续时间，历史数据兼容最多 2 毫秒漂移，较大矛盾仍拒绝分析。
```

- [x] **Step 2: 执行全量质量门槛**

Run:

```bash
.venv/bin/python -m pytest -q myextension/tests
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm lint:check
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm test --runInBand
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm build:prod
.venv/bin/python -m compileall -q myextension
```

Expected: 后端、lint、前端测试、生产构建和 Python 编译全部退出码 0。

- [x] **Step 3: 构建并检查 wheel**

Run:

```bash
uv build --wheel
.venv/bin/python -m zipfile -t dist/myextension-0.2.0-py3-none-any.whl
shasum -a 256 dist/myextension-0.2.0-py3-none-any.whl
```

Expected: wheel 完整性为 `Done testing`，并输出新的 SHA-256。

- [x] **Step 4: 重装本地交付包并检查扩展发现状态**

Run:

```bash
uv pip install --python .venv/bin/python --reinstall \
  dist/myextension-0.2.0-py3-none-any.whl
.venv/bin/jupyter labextension list
.venv/bin/jupyter server extension list
```

Expected: `myextension v0.2.0 enabled OK`，Server 扩展启用成功。

### Task 4: 新会话 UI 冒烟验证

**Files:**

- No source changes.
- Preserve: `~/.jupyterlab-behavior-audit/logs/sessions/` 下现有会话。

**Interfaces:**

- Consumes: 新安装扩展、已发布的合成题目方案、JupyterLab Notebook。
- Produces: 一个新合成会话的分析状态及不再出现 `required_signal_not_computable` 的验证证据。

- [x] **Step 1: 保存 Notebook 并重启准确的本地 Jupyter 进程**

先通过 UI 保存当前 Notebook，再只读解析监听 `localhost:8888` 的 PID；只停止该 PID，使用项目 `.venv/bin/jupyter lab` 重新启动。不得终止其他 Python、终端或 Jupyter 进程。

- [x] **Step 2: 使用 Computer Use 采集一个 30 秒以上合成会话**

选中已发布的平均值函数试点方案，勾选用途说明并开始监控。仅输入和运行无身份信息的合成代码，确保至少一次编辑和总观察时长超过 30 秒，然后停止监控。

- [x] **Step 3: 在触发外部 AI 前执行阶段门槛**

向用户明确：目标是验证一个合成会话；发送范围只有合成题目、合成代码片段和行为证据；预计最多一次分析调用；收到一个终态或首次明确错误后立即停止。获得确认后才允许该调用继续。

- [x] **Step 4: 核对新会话状态与时间特征**

刷新分析状态，并用不输出代码内容的只读摘要核对：

```text
session_status = finalized
valid_observation_duration_ms >= 30000
dimension missing_required_signals 不包含 valid_observation_duration_ms
```

Expected: 新会话不再因毫秒漂移显示“所需信号缺失或无效”。AI Provider 若失败，应单独显示为 AI 配置或调用失败，不能回退为数据不可计算。
