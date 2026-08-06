# macOS 真实 AI 完整演示

这套 Demo 用于手动演示：隔离部署→创建题目考核方案→AI 生成知识点和测试→确认测试并发布→监控编辑/失败/修复/成功→真实 AI 分析成功→安全导出日志。它不会使用或停止现有的 `127.0.0.1:8899`，默认使用 `127.0.0.1:18994`。

## 1. 部署

打开新的 macOS 终端：

```bash
cd "/Users/sxh/编程行为监控分析插件_交付版_20260727/demo/macos_real_ai"
./deploy_demo.sh --preflight
./deploy_demo.sh
```

第二条命令会创建一次性 Python 3.12 环境、安装交付 wheel，并自动打开带 Jupyter token 鉴权的浏览器。运行期目录位于 `$TMPDIR/myextension-real-ai-demo.*`。部署终端需保持打开。

如果需要修改端口、Base URL 或模型，先执行 `cp .env.example .env`，只修改非敏感值。不得把 Key 写入 `.env`、脚本、Notebook 或 shell 历史。

## 2. 配置真实 AI

> 这一演示会向真实 provider 发起请求，可能产生付费。请先确认账号、余额和数据政策；本代码不会自动调用付费 AI。

在 JupyterLab 左侧打开“编程行为观察”，展开“AI 服务配置”，手动输入：

- Base URL：`https://ark.cn-beijing.volces.com/api/coding/v3`
- 模型：`glm-5-2-260617`
- API Key：你的真实 Key（密码框输入）

点击“保存 AI 配置”，确认页面显示“AI 配置已保存”或“AI 状态：已配置”。

## 3. 从创建开始，生成并发布方案

1. 点击“创建题目考核方案”。
2. 输入以下固定合成题目：
   - 方案名称：`成绩统计真实 AI 演示`
   - 题目标识：`demo-analyze-scores`
   - 答题形式：函数
   - 函数入口：`analyze_scores`
   - 完整题目：`实现 analyze_scores(scores, pass_score=60)，返回数量、平均分、最高分、最低分和及格率；成绩必须在 0 到 100 之间，空列表返回约定的空统计。`
3. 进入“确认知识点”，使用 AI 生成建议。检查并保留至少这三类：函数与默认参数、输入范围校验、空列表边界处理，然后确认。
4. 进入“确认测试并发布”，让 AI 生成测试。逐项检查输入、预期输出和所属知识点，确认后发布试点方案。

成功标志是页面明确显示“试点方案已发布”。如 AI 生成失败，不要发布空测试；先检查 AI 配置和 provider 响应，再在当前步骤重试。

## 4. 开始监控并制造完整行为证据

1. 回到左侧“编程行为观察”，选择刚发布的 `成绩统计真实 AI 演示`。
2. 阅读并勾选数据采集/外部 AI 处理告知，点击“开始监控”。
3. 打开 `score_analysis_demo.ipynb`，运行第一个代码单元，加载故意不完整的函数。
4. 运行第二个代码单元，保留可见的 `ZeroDivisionError`。
5. 回到第一个代码单元，在 `count = len(scores)` 之前手动输入：

```python
    for score in scores:
        if score < 0 or score > 100:
            raise ValueError("成绩必须在 0 到 100 之间")

    if not scores:
        return {
            "count": 0,
            "average": 0.0,
            "highest": None,
            "lowest": None,
            "pass_rate": 0.0,
        }
```

6. 重新运行第一个单元，再运行第三个单元。只有出现 `Demo tests passed` 才算代码验收成功。
7. 保持浏览器页面可见并继续查看/编辑，直到左侧显示达到最低观察要求；整段有效观察至少 **30 秒**。
8. 点击“停止监控”。这一步是停止采集，不是关闭 JupyterLab。

停止后立即在左侧“本次日志”验证：

- `operation_log.json` 显示“已生成”，点击文件名可在 JupyterLab 中打开；
- `process_log.md` 显示“已生成”，内容包含输入、停顿、运行失败和修复后成功的时间线；
- `analysis_log.json` 此时可显示“正在分析…”，不应出现空文件或假的成功状态。

“有效观察时长（证据覆盖）”仅表示行为证据覆盖：代码输入、删除、粘贴和页面活动时的动作间停顿计入，页面离开和运行耗时不计入。该时长不会阻塞前两份日志生成。

## 5. 等待真实 AI 分析成功

保持 JupyterLab 运行，等待侧栏从排队/分析中变为分析结果，确认：

- 结果不是“部分完成”，也不是超时或失败；
- 页面至少显示一个维度结果和对应行为证据；
- 内部成功门槛是 `analysis_status == ready`，训练记录也必须完整。

如为 partial、超时或 provider 结构化输出无效，在 UI 中点击重试分析，直到出现完整结果。导出脚本不会把 partial 误判为成功。

分析成功后，确认 `analysis_log.json` 改为“已生成”，可通过文件名/“查看”打开并可下载。

## 6. 核验并导出日志

打开第二个终端：

```bash
cd "/Users/sxh/编程行为监控分析插件_交付版_20260727/demo/macos_real_ai"
./export_latest_demo.sh
```

只有最新 finalized 会话同时具备编辑、执行失败、执行成功、ready AI 结果和完整的 `operation_log.json` / `process_log.md` / `analysis_log.json` 时，命令才输出 `DEMO PASS`。导出包位于运行期目录的 `exports/demo-<session-id>-<timestamp>.zip`，仅包含白名单教学记录、三份本次日志和 SHA-256 manifest，不包含 AI 配置文件、provider 原始响应、Jupyter token 或 Cookie。

Demo 仅使用合成数据。**禁止将真实学生日志直接对外分享**；真实数据必须另行完成授权、脱敏和安全审查。

## 7. 清除 Key 并停止 Demo

1. 在左侧“AI 服务配置”中点击“清除已保存 Key”，在二次确认框点击“清除”，确认 AI 状态变为未配置。
2. 在第二个终端执行：

```bash
./stop_demo.sh
```

停止脚本只会向当前 Demo 状态文件、隔离 venv 和精确端口同时匹配的 Jupyter PID 发送 `SIGINT`；不会停止 8899 或未知进程。运行期目录和日志默认保留供验收。删除前先备份所需导出包；本 Demo 不自动清理证据。

## 故障排查

- `wheel SHA-256 mismatch`：不要继续；恢复已核对的 `dist/myextension-0.2.1-py3-none-any.whl`。
- 端口 18994 被占用：在 `.env` 中改用未占用的 1024–65535 端口，重新运行预检；不要结束未知进程。
- `uv`/依赖安装失败：检查网络和磁盘后重试；失败的临时目录会保留供诊断。
- 页面仍是旧 UI：确认地址是 18994，再做浏览器强制刷新；不要在 8899 上验收本 Demo。
- 分析 partial/超时：检查 provider 额度、Base URL、模型和 Key，在 UI 内重试；未 ready 时导出必须失败。
- 找不到日志：确认已停止监控并等待训练记录刷新，不要改用项目其他 `log/` 目录。
