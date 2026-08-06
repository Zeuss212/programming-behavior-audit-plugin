# AI 建议中文输出 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 保证客户界面不会展示纯英文的 AI 知识点或测试建议。

**Architecture:** 在既有 AI 辅助服务边界同时施加生成约束和响应约束。提示词提高模型首次返回中文的概率，服务端闭合校验负责失败关闭，因此前端与 API 契约无需变化。

**Tech Stack:** Python 3.10+、pytest、既有 `AssessmentAssistantOutputError` 错误契约

## Global Constraints

- 不新增依赖、外部调用或重试。
- JSON 键、Python 标识符、测试输入和预期字面量不强制翻译。
- 英文模型响应不得进入前端展示。
- 当前目录没有 Git 元数据，不执行提交步骤。

---

### Task 1: 中文生成和闭合校验

**Files:**
- Modify: `myextension/assessment_assistant.py`
- Test: `myextension/tests/test_assessment_assistant.py`

**Interfaces:**
- Consumes: `recommend_knowledge_points(...)`、`generate_assessment_tests(...)` 的现有模型响应。
- Produces: `_chinese_text(value, *, field, maximum)`，返回规范化且包含中文字符的字符串，否则抛出 `AssessmentAssistantOutputError`。

- [x] **Step 1: 写入失败测试**

  增加断言，要求知识点与测试生成的 system message 包含“简体中文”；增加纯英文知识点字段及纯英文测试名称被拒绝的用例。

- [x] **Step 2: 验证测试按预期失败**

  Run: `.venv/bin/python -m pytest myextension/tests/test_assessment_assistant.py -q`

  Result: `4 failed, 5 passed`；失败均为缺少中文提示或英文响应未被拒绝。

- [x] **Step 3: 实施最小修复**

  在两个系统提示中加入简体中文约束；实现中文字符检测；知识点五个自然语言字段和测试名称改用该校验。

- [x] **Step 4: 运行定向测试**

  Run: `.venv/bin/python -m pytest myextension/tests/test_assessment_assistant.py -q`

  Result: `9 passed in 0.02s`。

- [x] **Step 5: 运行后端回归**

  Run: `.venv/bin/python -m pytest myextension/tests -q`

  Result: 沙箱内因禁止绑定本机临时端口产生 `106 errors`；使用获准的本机端口权限重跑后 `599 passed in 19.04s`。

### Task 2: 客户交付 wheel

**Files:**
- Regenerate: `dist/myextension-0.2.0-py3-none-any.whl`
- Create: `docs/2026-08-04-chinese-ai-suggestions-verification.md`

- [x] **Step 1: 离线重建 wheel**

  Run: `uv build --wheel --offline`

  Result: `Successfully built dist/myextension-0.2.0-py3-none-any.whl`。

- [x] **Step 2: 校验 wheel 结构与源码一致性**

  Run: `.venv/bin/check-wheel-contents dist/myextension-0.2.0-py3-none-any.whl`

  Run: `.venv/bin/python -m zipfile -t dist/myextension-0.2.0-py3-none-any.whl`

  Run: Python `zipfile` 逐字节比较 wheel 内与源码树的 `myextension/assessment_assistant.py`

  Result: `OK`、`Done testing`、`WHEEL_SOURCE_MATCH`。

- [x] **Step 3: 记录交付哈希**

  Result: SHA-256 `01e754cad2eeeb30f60acc29c7300571b8cdf68c85598c14305a8e2afa64c085`。
