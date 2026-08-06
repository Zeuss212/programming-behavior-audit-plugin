# 自动训练记录与日志文件夹入口验证

日期：2026-08-03  
范围：`myextension 0.2.0` 本地交付门禁与 macOS GUI 冒烟

## 验证边界

- 本记录已覆盖文档、源码扫描、Python/Jest/lint/生产构建、wheel、项目 `.venv` 重装、扩展注册、隔离合成 HTTP 流程与 macOS GUI 冒烟。
- 浏览器与 Finder 操作均使用用户明确允许的本机测试；JupyterLab 只监听 `127.0.0.1:8899`，测试后正常关闭。
- Windows Explorer 真机验收：**待后续真机验证**；本机只运行不启动 GUI 的隔离平台分支测试。
- 本次不部署共享 JupyterHub，不调用外部 AI，不使用真实学生数据。

## 产品行为契约

- 停止监控后自动生成 `sessions/<session_id>/training_record.json`。
- AI 分析进入 `ready`/`partial` 和教师复核成功保存后，刷新同一文件。
- 侧栏操作是“打开日志文件夹”，并说明“训练记录会在每次监控结束后自动生成。”
- 该操作面向运行 Jupyter Server 的机器；本地 macOS/Windows 受支持，远程 JupyterHub 不会打开教师/客户端电脑的文件夹。
- 真实日志可能包含学生代码，不得提交到版本库或发送到外部服务、聊天或共享位置。

## 已完成验证证据

| 项目                   | 实际命令或检查                                                              | 结果                                                                                                                                    |
| ---------------------- | --------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| 历史契约扫描           | 对三份文档、`src`、`myextension`、OpenAPI 执行批准的精确 `rg`               | exit 1，无匹配（预期 clean）                                                                                                            |
| 文档正向契约扫描       | 分别核对入口、自动生成、路径、Windows、macOS 与 JupyterHub                  | exit 0，三份文档均命中                                                                                                                  |
| Python 全量测试        | `.venv/bin/python -m pytest myextension/tests -q`                           | exit 0；补齐认证/XSRF 与 missing-provider 回归后 597 passed in 17.68s                                                                   |
| 安全/刷新聚焦回归      | 新增未认证矩阵、cookie 认证无 XSRF、missing-provider worker callback        | exit 0；22 passed；403 在文件夹 opener 调用前返回，同一训练记录更新状态/hash 且不伪造 AI 结果                                           |
| 已安装包聚焦回归       | 从 `/private/tmp` 对 site-packages 中 opener/refresher/record service 测试  | exit 0；75 passed in 9.13s；确认不依赖项目源码优先导入                                                                                  |
| 前端全量测试           | `PATH="$PWD/.venv/bin:$PATH" jlpm test --runInBand`                         | exit 0；18 suites、265 tests passed                                                                                                     |
| lint                   | `PATH="$PWD/.venv/bin:$PATH" jlpm lint:check`                               | exit 0；Prettier/ESLint/Stylelint 通过                                                                                                  |
| 生产构建               | `PATH="$PWD/.venv/bin:$PATH" jlpm build:prod`                               | exit 0；Rspack 2.0.8 compiled successfully                                                                                              |
| wheel 校验             | `.venv/bin/check-wheel-contents dist/myextension-0.2.0-py3-none-any.whl`    | exit 0；OK                                                                                                                              |
| wheel 内容比对         | Python `zipfile` 检查必要/禁止条目并比对 bundle SHA-256                     | 4 个必要条目存在、3 个旧 schema 缺席，wheel 的 `811.*.js` 与当前生产构建逐字节哈希一致                                                  |
| `.venv` 强制重装       | `.venv/bin/python -m pip install --force-reinstall --no-deps ...`           | exit 0；成功重装 myextension 0.2.0                                                                                                      |
| 前端扩展注册           | `.venv/bin/jupyter labextension list`                                       | exit 0；myextension v0.2.0 enabled/OK                                                                                                   |
| 服务端扩展注册         | 隔离 `JUPYTER_CONFIG_DIR` 后运行 `.venv/bin/jupyter server extension list`  | exit 0；myextension enabled，myextension 0.2.0 OK                                                                                       |
| Windows 打开器隔离测试 | `.venv/bin/python -m pytest myextension/tests/test_log_folder_opener.py -q` | exit 0；8 passed                                                                                                                        |
| 合成 HTTP 冒烟         | `synthetic_server_smoke.py` 对本机隔离 JupyterLab 执行完整 API 流程         | create 201、publish 200、start 201、segment 202、finalize 202；schema 有效，1 个事件；初始分析为 `null`，同一文件随后自动刷新为 `ready` |
| 浏览器/Finder 冒烟     | 应用内浏览器点击“打开日志文件夹”，随后只读检查 Finder                       | 页面显示“已打开日志文件夹。”；Finder 打开配置根的 `sessions`，其中含本次合成会话目录                                                    |

## wheel 与回退基线

- 新 wheel：`/Users/sxh/编程行为监控分析插件_交付版_20260727/dist/myextension-0.2.0-py3-none-any.whl`
- 新 wheel SHA-256：`72676381652d92c3827be5ff87839043d24ab38a7a3e883e6693486f73fbf6d0`
- 最终审查前候选备份：`/Users/sxh/编程行为监控分析插件_交付版_20260727/dist/rollback/myextension-0.2.0-pre-final-review-7835681f.whl`，SHA-256 为 `7835681f42a906000cbce09ecbf66cddd7af9d4e585a20a818cba049738ce3f6`。
- 回退备份：`/Users/sxh/编程行为监控分析插件_交付版_20260727/dist/rollback/myextension-0.2.0-baseline-57eb2407.whl`
- 回退备份 SHA-256：`57eb2407e92906bbcae8bf3d38fceaf14798793d3336ded3b0e24b693179687f`
- `dist/myextension-0.1.0-py3-none-any.whl` 保留，SHA-256 为 `48da402c5bc7229aee2683ae69c8f3a63af85bf8e3aa38555756ea53ea2b5fc7`。

## 隔离合成与 GUI 结果

- 合成根：`/private/tmp/automatic-training-record-smoke.7g1pMy`。
- 最终安全加固脚本复跑会话：`83c07960-90b5-4542-840c-641bd582ebb6`。
- 最终安全加固脚本复跑产物：`/private/tmp/automatic-training-record-smoke.7g1pMy/sessions/83c07960-90b5-4542-840c-641bd582ebb6/training_record.json`。
- HTTP 冒烟验证记录 schema 有效、事件数为 1、教师复核为空；第一次读取得到 `ai_analysis: null`，随后在同一路径自动刷新为 `ready`，原因为 `minimum_observation_not_met`。该覆盖判断路径有自动化测试证明不会调用外部模型。响应、初始记录与最终记录均未泄漏 token 或合成根绝对路径。
- 脚本在发送 token 前强制要求明文 HTTP 回环 IP、显式端口、直接位于系统临时目录且以 `automatic-training-record-smoke.` 开头的根目录和本地 workspace 哨兵。第一个携带 token 的请求只读检查 Jupyter Contents 哨兵，服务端哨兵匹配后才创建资料。远端 URL 与非隔离根负向检查均在网络调用前被拒绝。
- 真实页面只显示“训练日志”、自动生成说明和“打开日志文件夹”；点击后成功状态为“已打开日志文件夹。”。pending/禁用/`aria-busy` 状态由前端自动化测试覆盖，真实请求完成很快，未把肉眼捕获 pending 作为额外结论。
- Finder 窗口标题为 `sessions`，GUI 冒烟会话项 URL 为 `file:///private/tmp/automatic-training-record-smoke.7g1pMy/sessions/5194f124-6acc-4f6f-9fa2-a3a971cb0037/`，与配置目录一致；最终脚本复跑使用同一 `sessions` 根并新增上述会话。
- 测试脚本：`.superpowers/sdd/2026-08-03-automatic-training-record-folder/synthetic_server_smoke.py`；安全加固后的 `py_compile` 与真实 localhost 复跑均通过。
- Windows `os.startfile(path, "open")` 隔离测试通过；Windows Explorer 真机操作仍需 Windows 电脑。

## 停止点

隔离 JupyterLab 已确认正常关闭（退出码 0）。当前停在项目 `.venv`、新 wheel、回退 wheel 以及保留的合成日志根；Finder 保持打开便于人工查看。不部署、不推送、不使用外部 AI 或真实学生数据，也不删除任何日志。

Task 7 最终独立审查结论为 PASS，Critical/High/Medium/Low 均无发现；Windows Explorer 真机验收继续作为明确的后续平台项。

最终全项目审查的 2 个 Important 与 1 个 Minor 均已修复，独立复审结论为 Ready for delivery、无 Critical/Important/Minor 发现。额外尝试直接收集 site-packages 内全部测试时，源树专用的 `test_labextension_artifact.py` 因安装目录不含仓库根 `package.json` 而在收集阶段停止；这不是正式门禁或运行时失败。作为安装包验证，已改用不依赖源树的 75 项聚焦测试，并结合源码/wheel/安装文件哈希一致、597 项源树全量测试和扩展注册结果完成验收。
