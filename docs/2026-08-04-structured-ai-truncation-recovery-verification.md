# 结构化 AI 截断恢复与失败态验证记录

日期：2026-08-04

## 实现结果

- 共享结构化 AI 请求首次使用 `max_tokens=8192`，只对 `finish_reason=length` 以 `max_tokens=16384` 恢复一次，连续截断后以 `provider_response_truncated` 失败关闭。
- 测试建议的合成首次截断可在一次教师操作内恢复，不需要第二次手工点击。
- 会话分析的合成首次截断可在同一 attempt 内恢复，私有 raw response 包含两条响应，结果进入 `ready` 并产生可核查证据。
- AI 分析失败且无可用维度结论时，界面显示“行为采集已完成”、“AI 分析未完成，可重试分析”与服务端数据质量原因，不显示占位结果卡、复核表单或“查看 0 条证据”。
- 同一失败态的侧栏任务状态显示“AI 分析未完成”，不再沿用通用的“分析完成（部分结果）”。
- ordinary partial 和有效 `not_observed` 结论仍保留；没有 claim 的单个维度不再生成空证据折叠控件。

## TDD 证据

| 阶段 | 命令 | 实际结果 |
| --- | --- | --- |
| 后端 RED | `.venv/bin/python -m pytest -q myextension/tests/test_dimension_analyzer.py::test_transport_retries_one_length_truncation_with_larger_budget myextension/tests/test_dimension_analyzer.py::test_transport_stops_after_second_length_truncation myextension/tests/test_dimension_analyzer.py::test_transport_does_not_retry_malformed_nontruncated_json myextension/tests/test_assessment_assistant.py::test_generated_tests_recovers_one_length_truncation_without_second_user_action myextension/tests/test_analysis_job_store.py::test_worker_recovers_truncated_response_in_same_attempt` | 退出 1；`5 failed in 0.89s`。失败原因为无 `max_tokens`、长度截断被当成 `provider_response_invalid`、不发起恢复请求，后台进入 `partial`。 |
| 后端 GREEN | 与后端 RED 相同的 5 项命令 | 退出 0；`5 passed in 0.11s`。 |
| 前端 RED | `PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm jest src/__tests__/analysisResultView.spec.ts --runInBand --coverage=false` | 退出 1；`3 failed, 15 passed`，耗时 `1.134 s`。失败精确复现误导 partial 卡和空证据控件。 |
| 前端 GREEN | 与前端 RED 相同的定向命令 | 退出 0；`18 passed`，耗时 `1.144 s`。 |
| 验收补测 RED/GREEN | `.venv/bin/jlpm jest src/__tests__/behaviorAnalysisSidebar.spec.ts --runInBand -t "does not label a completely failed AI analysis as partial results"` | 浏览器验收发现侧栏仍显示“分析完成（部分结果）”；测试先 `1 failed`，最小修复后 `1 passed`。 |

## 全量验证

| 验证 | 命令 | 实际结果 |
| --- | --- | --- |
| 后端关联回归 | `.venv/bin/python -m pytest -q myextension/tests/test_dimension_analyzer.py myextension/tests/test_assessment_assistant.py myextension/tests/test_analysis_job_store.py` | 退出 0；`137 passed in 0.99s`。 |
| 后端全量 | `.venv/bin/python -m pytest -q myextension/tests` | 沙箱内首次运行因 pytest-jupyter 无权绑定 `127.0.0.1` 临时端口，结果为 `498 passed, 106 errors in 15.56s`；对同一命令授予仅本机端口权限后退出 0，首次为 `604 passed in 18.04s`，最终验收复核为 `604 passed in 17.85s`。 |
| 前端关联回归 | `PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm jest src/__tests__/analysisResultView.spec.ts src/__tests__/behaviorAnalysisSidebar.spec.ts --runInBand --coverage=false` | 退出 0；`2` 个套件、`93 passed`，耗时 `1.409 s`。 |
| 前端 lint | `PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm lint:check` | 新增测试首次只有 Prettier 格式差异；仅格式化该测试文件后重跑退出 0，输出 `All matched files use Prettier code style!`。 |
| 前端全量 | `PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm test --runInBand` | 退出 0；`18` 个套件、最终 `268 passed`，耗时 `2.451 s`；覆盖率门槛通过。 |
| 生产构建 | `PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm build:prod` | 退出 0；TypeScript 编译和 JupyterLab prebuilt 扩展构建成功，Rspack 耗时 `133 ms`。 |
| wheel 构建 | `uv build --wheel --offline` | 沙箱内无权读取默认 uv 缓存；可写空缓存在离线模式下缺少 hatch 构建依赖。授予只读取现有 uv 缓存的权限后，同一离线命令退出 0，成功构建 `dist/myextension-0.2.0-py3-none-any.whl`。 |
| wheel 内容 | `.venv/bin/check-wheel-contents dist/myextension-0.2.0-py3-none-any.whl` | 退出 0；`OK`。 |
| wheel 完整性 | `.venv/bin/python -m zipfile -t dist/myextension-0.2.0-py3-none-any.whl` | 退出 0；`Done testing`。 |
| wheel 后端/前端逐字节比对 | Python `zipfile` 比对 `myextension/llm_transport.py` 与 `myextension/labextension` 的所有文件 | 退出 0；`WHEEL_BACKEND_AND_FRONTEND_MATCH`；最终复核同时输出 `ROLLBACK_WHEEL_PRESENT`。 |

## 无 Git 检查点

| 文件 | SHA-256 |
| --- | --- |
| `myextension/llm_transport.py` | `c68444692e746b7be21731ee1df303299834ae1660c016e1250f45e0b5ce96c1` |
| `myextension/tests/test_dimension_analyzer.py` | `dcf116eb62614737f08015d60b42a32fb5bbba3d7e70ea04cf2f28f5c109cda3` |
| `myextension/tests/test_assessment_assistant.py` | `b5d24e0b46d979cf9ec9d496d64037a9bea98eaa9fcd91626ba58043e30374e4` |
| `myextension/tests/test_analysis_job_store.py` | `535b23c73e32e023a1baa6445ea01791175f91f1598da21de80dba931575afca` |
| `src/ui/analysisResultView.ts` | `c91f9a743443c6a8296c760fcbaa31c1bf39cb3901b6905205acedfe3a01eeb0` |
| `src/__tests__/analysisResultView.spec.ts` | `66eb0c3990222eaf2e98ebca5707888688b32add52abad2d666e517cedef6afb` |
| `src/ui/behaviorAnalysisSidebar.ts` | `53965ff1d5f82857eea3cc575448961e9dc4aa44a9dab1b6def9a2c04aa64430` |
| `src/__tests__/behaviorAnalysisSidebar.spec.ts` | `c9eed1839a3b7ad2f76b4a5ed3e82711e0649833b94807b0fdc9f2471f2efa34` |

项目目录不是 Git 仓库，因此无 commit SHA。本轮使用文件 SHA-256、新鲜命令输出和 wheel SHA-256 作为可核查证据。

## 交付产物

- wheel：`/Users/sxh/编程行为监控分析插件_交付版_20260727/dist/myextension-0.2.0-py3-none-any.whl`
- SHA-256：`f95a375acf49947a9921cf688a6e4cd6854fe88da4efea3c57fc3e90421c516c`
- 回退 wheel 保留：`dist/myextension-0.1.0-py3-none-any.whl`

## 复核结论

本轮按用户选择在当前任务内串行实施，且项目无 Git worktree，因此未启用子代理审查。主任务逐项复核了截断触发条件、调用上限、非截断失败关闭、后台 attempt/私有审计契约、前端可用结论判定和 ordinary partial 兼容性，未发现阻断交付的新问题。

## 隔离 wheel 浏览器验收

最终 wheel 已安装到 `/private/tmp` 隔离 target，并通过独立 JupyterLab 与本机合成 provider 验收。测试建议阶段一次页面操作触发两次 provider 调用，第一次长度截断、第二次成功；AI 分析阶段达到最低观察门槛后连续两次截断，最终页面不包含“部分结果”“查看 0 条证据”或占位卡。完整证据见 [0.2.0 隔离 wheel 验收记录](2026-08-04-isolated-wheel-acceptance.md)。

## 未执行项与停止点

- 未调用真实或付费 AI；自动恢复由合成 provider 测试覆盖。
- 未重启或修改当前 `127.0.0.1:8899` JupyterLab；新 wheel 仅安装到隔离临时目录，并完成合成 provider 的真实浏览器流程。
- 未执行 Windows 真机验收、推送或部署。
- 本阶段在源码、全量测试、最终 wheel 和隔离浏览器验收完成后停止，等待真实 AI、目标环境或 Windows 真机验收授权。
