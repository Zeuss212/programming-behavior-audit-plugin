# AI 建议中文输出验证记录

日期：2026-08-04

## 实现结果

- 知识点与测试建议的系统提示使用中文，并明确要求客户可见自然语言使用简体中文。
- 知识点的五个自然语言字段必须包含中文字符；测试建议名称必须包含中文字符。
- 纯英文响应通过既有 `AssessmentAssistantOutputError` 失败关闭，不会进入前端建议列表；前端继续显示既有中文失败提示。
- 测试输入、预期输出、Python 标识符及必要技术术语允许保留原文。

## 验证证据

| 验证 | 命令 | 结果 |
| --- | --- | --- |
| TDD RED | `.venv/bin/python -m pytest myextension/tests/test_assessment_assistant.py -q` | 修复前 `4 failed, 5 passed`，准确捕获缺少中文提示及英文响应未拒绝 |
| 定向测试 | `.venv/bin/python -m pytest myextension/tests/test_assessment_assistant.py -q` | `9 passed in 0.02s` |
| 最终后端回归 | `.venv/bin/python -m pytest myextension/tests -q` | `599 passed in 17.65s` |
| wheel 构建 | `uv build --wheel --offline` | 成功 |
| wheel 内容 | `.venv/bin/check-wheel-contents dist/myextension-0.2.0-py3-none-any.whl` | `OK` |
| wheel 完整性 | `.venv/bin/python -m zipfile -t dist/myextension-0.2.0-py3-none-any.whl` | `Done testing` |
| wheel 源码比对 | Python `zipfile` 逐字节比较 `myextension/assessment_assistant.py` | `WHEEL_SOURCE_MATCH` |

## 交付产物

- wheel：`/Users/sxh/编程行为监控分析插件_交付版_20260727/dist/myextension-0.2.0-py3-none-any.whl`
- SHA-256：`01e754cad2eeeb30f60acc29c7300571b8cdf68c85598c14305a8e2afa64c085`
- 旧版回退 wheel `dist/myextension-0.1.0-py3-none-any.whl` 保留未删除。

## 演示状态与未执行项

- 本机预览已在 `127.0.0.1:8899` 重启并加载当前服务端扩展。
- 为避免未经授权的外部调用和费用，本轮没有调用真实 AI 服务；中文提示、中文响应和英文失败关闭均由可重复的模型客户端测试覆盖。
- 浏览器刷新后应重新点击“获取 AI 建议”；重启前页面内存中的英文卡片不代表新服务端行为。
