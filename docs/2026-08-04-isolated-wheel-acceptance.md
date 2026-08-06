# 0.2.0 隔离 wheel 验收记录

日期：2026-08-04

## 验收边界

- 使用 `dist/myextension-0.2.0-py3-none-any.whl` 安装到 `/private/tmp` 下的隔离目录，不修改现有虚拟环境或系统安装。
- 在独立的本机回环端口启动 JupyterLab 和合成 OpenAI 兼容服务；现有 `127.0.0.1:8899` 未重启、未修改。
- 全部题目、代码、事件和模型响应均为合成数据；未读取真实 API Key，未调用真实或付费 AI。
- 最终 wheel SHA-256：`f95a375acf49947a9921cf688a6e4cd6854fe88da4efea3c57fc3e90421c516c`。

## P0-1：测试建议首次截断自动恢复

浏览器中只执行了一次“确认知识点/生成测试建议”操作。合成服务第一次返回 `finish_reason=length`，第二次返回合法测试建议；页面直接进入“确认测试并发布”，显示“测试建议已生成”，并成功发布隔离试点方案。

合成服务计数在该阶段为：

```json
{"analysis": 0, "knowledge": 1, "tests": 2}
```

这证明一次教师操作只产生一次知识点请求，测试建议在首次截断后由共享传输层自动追加一次恢复请求，不需要第二次手工点击。

## P0-2：AI 完全失败时不显示误导性部分结果

先完成 30 秒最低观察门槛并产生真实浏览器编辑/运行事件，再让合成服务连续两次返回长度截断。最终 wheel 页面显示：

- `分析任务：AI 分析未完成`
- `行为采集已完成，AI 分析未完成，可重试分析。`
- 服务端数据质量原因：`已达到最低观察要求`
- “重试分析”入口

同一 DOM 快照明确不包含：

- `部分结果`
- `查看 0 条证据`
- 占位维度结果卡
- 教师复核表单

最终会话记录为 54.7 秒有效观察、8 个采集事件、0 个待上传事件。合成服务的 `analysis` 计数从 2 增加到 4，证明最终会话同样只进行了首次请求和一次受限恢复请求。

## 验收中发现并闭环的显示遗漏

第一次隔离流程中，结果区已经正确隐藏占位卡，但任务状态仍显示“分析完成（部分结果）”。本轮按 TDD 补充侧栏级回归：

```text
RED: 1 failed，收到“分析任务：分析完成（部分结果）”
GREEN: 1 passed，改为“分析任务：AI 分析未完成”
```

最小修复只影响 `status=partial + error_code=ai_analysis_failed + 无可用结论`；普通 partial 和含有效结论的 partial 仍保留原有显示。

## 最终验证证据

| 范围 | 命令或操作 | 结果 |
| --- | --- | --- |
| 后端全量 | `.venv/bin/python -m pytest -q myextension/tests` | `604 passed in 17.85s` |
| 前端全量 | `PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm test --runInBand` | `18` 套件、`268 passed`，覆盖率门槛通过 |
| 前端 lint | `PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm lint:check` | 退出 0 |
| 生产构建 | `PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm build:prod` | 编译成功 |
| wheel 构建 | `uv build --wheel --offline` | 成功生成 0.2.0 wheel |
| wheel 内容/完整性 | `check-wheel-contents`、`zipfile -t` | `OK`、`Done testing` |
| wheel 源码比对 | `cmp` 后端文件、`diff -qr` 前端预构建目录 | 均退出 0 |
| 浏览器验收 | 隔离 JupyterLab + 合成 provider | 两项 P0 均通过 |

## 清理与剩余边界

- 隔离浏览器页已关闭，JupyterLab 与合成 provider 已停止；端口 18991、18992 均无监听进程。
- 合成验收产物保留在 `/private/tmp/myextension-acceptance.KueFej` 供本轮复核，系统清理临时目录时可移除。
- 未执行真实/付费 AI 验收、Windows 真机验收或正式部署。
- 非阻断体验发现：方案名称、题目标识和函数入口是必填项，但位于折叠的“高级设置”内；首次填写时不够显眼，建议后续单独优化，不扩大本轮 P0 范围。
