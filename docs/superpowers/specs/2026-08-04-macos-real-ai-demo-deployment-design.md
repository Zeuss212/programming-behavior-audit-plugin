# macOS 真实 AI 完整部署 Demo 设计

日期：2026-08-04

## 1. 目标

交付一套可在 macOS 上现场运行的 Demo 代码。演示者执行一个部署脚本后，在自动打开的、带 Jupyter token 鉴权的浏览器中手动完成以下闭环：

1. 从空环境创建题目方案；
2. 使用真实 AI 生成并确认知识点与测试；
3. 发布方案；
4. 开始监控；
5. 在示例 Notebook 中产生编辑、报错、修正和成功运行事件；
6. 停止监控并等待真实 AI 分析成功；
7. 查看分析结论与证据；
8. 导出本次 Demo 会话日志；
9. 运行核验脚本，得到明确的 PASS/FAIL 结果。

本 Demo 面向当前 `myextension 0.2.0` wheel，不修改现有 `127.0.0.1:8899`，不替代 Windows 或生产部署验收。

## 2. 已选方案与取舍

采用“隔离部署 + 手动 UI + 自动产物核验”的方案。

- 相比直接复用现有 `.venv`，隔离部署更能证明 wheel 可独立安装，也不会污染当前开发环境。
- 相比浏览器全自动化，手动 UI 更适合现场演示，能真实展示创建、发布、监控和分析页面，且避免自动化脚本处理 Jupyter token 与真实 API Key。
- 相比故障注入，本 Demo 只展示真实 AI 成功主路径；截断恢复和失败空状态继续由已有确定性自动化与隔离合成验收覆盖。

## 3. 交付目录

新增目录 `demo/macos_real_ai/`：

| 文件 | 职责 |
| --- | --- |
| `README.md` | 从部署到导出的逐步演示手册、预期页面、故障排查和清理说明 |
| `.env.example` | 仅保存非敏感示例值：端口、Base URL、模型名和 wheel 路径；不包含 API Key |
| `deploy_demo.sh` | 校验 wheel、创建隔离运行目录和虚拟环境、安装依赖、复制 Notebook、启动带鉴权 JupyterLab |
| `stop_demo.sh` | 只停止当前 Demo 状态文件指向的 JupyterLab，不影响其他服务 |
| `export_latest_demo.sh` | 调用核验器定位最新成功会话并生成安全导出压缩包 |
| `verify_demo.py` | 核验会话、行为事件、AI 成功状态和导出内容，使用非零退出码报告失败 |
| `demo_notebook.ipynb` | 现场编辑用 Notebook，包含题目、故意失败的初始实现、修正目标和测试单元 |
| `tests/test_verify_demo.py` | 用纯合成目录测试核验与导出逻辑，不调用真实 AI |

## 4. 隔离部署设计

### 4.1 运行目录

`deploy_demo.sh` 使用 `mktemp -d` 在 macOS 的 `$TMPDIR` 下创建唯一目录：

```text
$TMPDIR/myextension-real-ai-demo.XXXXXX/
├── venv/
├── workspace/
├── log/
├── jupyter-config/
├── jupyter-data/
├── jupyter-runtime/
├── ipython/
├── server.pid
└── server.port
```

当前运行目录路径写入 `$TMPDIR/myextension-real-ai-demo-current`。状态文件只包含本机临时路径，不包含 token、Cookie、API Key 或学生数据。

脚本默认使用端口 `18994`，并设置 `port_retries=0`。端口被占用时失败关闭并提示更换端口，不自动连接或停止未知服务。现有 `8899` 不在操作范围内。

### 4.2 安装与启动

部署脚本按顺序执行：

1. 检查 macOS、`uv`、Python 3.12 和最终 wheel 是否存在；
2. 核对 wheel SHA-256 是否为本 Demo 记录的交付哈希 `f95a375acf49947a9921cf688a6e4cd6854fe88da4efea3c57fc3e90421c516c`；
3. 创建隔离 venv；
4. 安装固定版本 `jupyterlab==4.6.1`、`jupyter-server==2.20.0` 和当前 wheel；
5. 执行 `jupyter labextension list` 与 `jupyter server extension list`，确认前后端扩展均启用；
6. 复制 Notebook 模板到隔离 workspace；
7. 设置隔离的 Jupyter/IPython 目录和 `JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR`；
8. 启动 JupyterLab，由 Jupyter 自动生成 token 并打开默认浏览器。

脚本不使用空 token、不关闭密码/token 鉴权、不解析或写入 token URL。

部署脚本只从可选的 `.env` 中读取 `DEMO_PORT`、`DEMO_BASE_URL`、`DEMO_MODEL` 和 `DEMO_WHEEL` 四个白名单字段；不执行 `.env` 中的 shell 代码，也不接受或读取 `DEMO_API_KEY`。端口另存为隔离目录内的 `server.port`，供停止脚本交叉核对。

### 4.3 AI 配置

真实 API Key 只由演示者在扩展的“AI 服务配置”中手动输入。README 提供 Base URL 和模型名位置说明，但不要求把 Key 放入 `.env`、命令参数或 shell 历史。

扩展生成的 `.ark_ai_config.json` 位于隔离 `log/` 根目录，权限规则继续由产品代码负责。导出器明确排除此文件。

## 5. 现场演示数据

所有数据均为固定的合成教学 Demo，不使用真实学生信息。

### 5.1 方案输入

- 方案名称：`成绩统计真实 AI 演示`
- 题目标识：`demo-analyze-scores`
- 函数入口：`analyze_scores`
- 题目：实现 `analyze_scores(scores, pass_score=60)`，返回数量、平均分、最高分、最低分和及格率；成绩必须在 0 到 100 之间，空列表返回约定的空统计。
- 建议知识点：函数与默认参数、输入范围校验、空列表边界处理。

演示者从“创建题目考核方案”开始，让真实 AI 生成知识点与测试，逐项确认后发布，不预置 profile。

### 5.2 Notebook 行为序列

Notebook 不包含最终完整答案。演示者执行：

1. 运行故意遗漏空列表和范围校验的初始实现；
2. 运行空列表测试，制造一次可见错误；
3. 修改实现，增加空列表处理和 `0..100` 校验；
4. 运行正常、空列表和越界测试；
5. 保持页面活动并继续编辑，直到侧栏显示达到最低观察要求；
6. 停止监控。

该序列确保至少存在代码编辑、执行失败、再次编辑、执行成功和 30 秒有效观察，不依赖粘贴完整答案。

## 6. 分析成功标准

Demo 只有同时满足以下条件才判定通过：

1. 最新会话 `session.json.status == "finalized"`；
2. `training_record.json` 存在且 `integrity.complete == true`；
3. `training_record.json.session.analysis_status == "ready"`；
4. `training_record.json.ai_analysis.status == "ready"`；
5. 至少有一个维度结果；
6. 行为事件同时包含代码编辑、执行失败和执行成功；
7. `event_count` 与导出的事件数量一致且大于零；
8. 分析 provenance 中存在模型名、提示词版本和输入快照哈希；
9. 导出包不包含 API 配置、Key、Jupyter token、Cookie、`jobs/*raw_response*` 或 provider 原始响应。

若真实 AI 返回 partial、超时或结构化输出无效，核验器返回失败并提示在 UI 中重试分析；不把 partial 描述为成功。

## 7. 日志导出设计

`export_latest_demo.sh` 只读取当前 Demo 状态文件指向的隔离 `log/`。核验器按 `session.json.ended_at` 选择最新的 finalized 会话；如果最新会话未 ready，则直接失败，不静默回退到更早的成功会话。它调用 `verify_demo.py --export`，先通过成功门槛，再创建：

```text
demo/macos_real_ai/exports/
└── demo-<session-id>-<timestamp>.zip
```

压缩包使用明确白名单，仅包含：

- `training_record.json`
- `session.json`
- `profile.json`
- `signal_dictionary.json`
- `raw_events.jsonl`
- 与该会话对应的可读 Markdown 记录（存在时）
- `manifest.json`：会话 ID、导出时间、文件相对路径和 SHA-256

不导出 `batches/`、`receipts/`、`jobs/`、`.ark_ai_config.json`、Jupyter runtime、token、Cookie 或其他会话。

导出文件仍包含合成示例代码和行为时间线；README 明确禁止用该脚本导出真实学生会话后直接对外发送。

## 8. 错误处理与停止规则

- wheel 不存在或哈希不匹配：部署前停止，不安装未知产物。
- 缺少 `uv`、Python 3.12 或网络依赖安装失败：保留临时目录并打印恢复命令。
- 端口占用：停止，不自动杀进程。
- 扩展检查失败：停止 Jupyter 启动，报告前端或服务端缺失项。
- 已有仍运行的 Demo：拒绝启动第二个实例，先执行 `stop_demo.sh`。
- `stop_demo.sh` 必须同时核对状态文件、PID 命令行、隔离 venv 和端口；不满足时拒绝发送信号。
- 分析未 ready：不生成“成功”导出包，打印当前状态和 UI 重试建议。
- 未找到会话或训练记录：非零退出，不回退到项目真实 `log/`。

停止脚本只终止当前 Demo JupyterLab。默认保留隔离日志供核验；是否删除临时目录由演示者明确执行，不自动清除证据。

## 9. 无付费验证策略

实现阶段不调用真实或付费 AI。代码验证包括：

- `bash -n` 检查三个 shell 脚本；
- Python 单元测试构造 ready、partial、缺文件、事件不完整、敏感文件混入等合成目录；
- 验证导出 zip 白名单和 manifest 哈希；
- 用 `python -m json.tool` 或 `nbformat` 检查 Notebook JSON；
- 在临时目录使用伪造状态文件验证停止脚本会拒绝未知 PID；
- 运行现有相关自动化回归，确认 Demo 文件不改变产品逻辑。

真实 AI 的最终成功只能由演示者实际执行 Demo 后确认。核验器提供可复现证据，但不会主动发起模型调用。

## 10. 完成边界

本任务完成于：Demo 代码、测试、使用说明和验证记录均已生成并通过无付费验证。不会执行真实 AI、不会部署到客户机器、不会修改现有 8899、不会推送或发布。

后续真实演示者负责：确认 provider 费用、输入 API Key、完成 UI 操作、核对分析结果并决定是否保留或删除导出包。
