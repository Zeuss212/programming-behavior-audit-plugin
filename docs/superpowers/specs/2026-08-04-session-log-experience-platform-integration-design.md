# 会话日志体验与 BLUEDOT 平台接入设计

日期：2026-08-04

## 1. 目标

本轮只改进两个直接问题：

1. 让用户理解“有效观察时长”在统计什么，不再将它误解为日志或 AI 的等待时间。
2. 停止监控后，在 JupyterLab 左侧按固定顺序显示三份本次会话日志，支持平台内直接查看和下载，不要求用户进入 Finder/Explorer 寻找文件。

同时给出将最终 wheel 接入 BLUEDOT `PyTorch-2.5.1-JupyterLab4` 工作台的可复现部署方式。

## 2. 范围和非目标

本轮范围：

- 有效观察时长的标签、说明和不足提示；
- 当前会话的三项日志状态、查看、下载与错误态；
- 停止监控后立即生成两份非 AI 日志；
- AI 任务结束后生成独立的分析 JSON；
- 经 Jupyter 认证的会话日志查看/下载 API；
- BLUEDOT JupyterLab 4 容器镜像的安装、日志持久化和验收说明。

非目标：

- 不改自定义维度、方案创建、AI 结论或教师复核规则；
- 不恢复旧版的“掌握/未掌握”自动判定；
- 不将停顿当作学生心理状态的证据；
- 不修改 BLUEDOT 门户前端，不在真实平台执行部署；
- 不处理平台的教师/学生角色授权；当前插件仍继承 Jupyter 会话的访问边界。

## 3. 当前状态与根因

当前系统在上传行为批次时已经生成可读 Markdown、`raw_events.jsonl`、`timeline.jsonl` 和会话训练记录，但左侧“训练日志”只提供“打开日志文件夹”。

现有“高级数据”可以查询旧版全局最新日志，但它依赖日志位于 Jupyter Contents Manager 的 `root_dir` 之内。隔离 Demo 与平台持久化目录通常位于 Notebook 根目录之外，因此 `contents_path` 为空，页面无法直接打开。

新设计不再把任意服务器文件路径交给前端，而是以“当前会话 ID + 日志类型”作为白名单契约。

## 4. 有效观察时长

### 4.1 统计含义

页面标签改为“有效观察时长（证据覆盖）”。在进度条下始终显示：

> 统计监控期间的代码输入、删除、粘贴及页面活动时的动作间停顿。页面离开不计入；运行事件会写入日志，但运行耗时不计入该时长。

附加一行稳定提示：

> 达到门槛只表示行为证据覆盖足够，与日志生成或 AI 分析等待无关。

代码内部可保留 `idle` 事件名称，用户可见文案改为“动作间停顿”或“停顿”。不使用“思考时间”作为无限定结论；如日志为了与用户语言对齐而提到思考，必须写为“停顿（可能包含思考）”。

### 4.2 与日志等待解耦

有效观察门槛只用于数据质量与停止前警告；它不控制前两份本地日志何时显示。只要会话成功 finalized，前两份日志就立即就绪，即使观察不足也不延迟。

## 5. 左侧三项日志

“训练日志”改名为“本次日志”。当有当前会话时，按以下固定顺序显示三行：

三份持久化产物统一位于 `sessions/<session_id>/logs/`，不要求 Notebook 根目录与日志根目录相同。

### 5.1 操作日志

- 用户说明：“用户输入、删除、粘贴、运行成功/失败及输出。”
- 格式：缩进的 UTF-8 JSON，顶层为对象，包含会话摘要与按 `session_seq` 排序的事件数组。
- 来源：当前会话经公开安全投影的 `behavior_events`，不直接对外提供内部 batch/receipt。
- 时机：会话 finalized 后立即 ready，不等 AI。
- 文件名：`operation_log.json`。

### 5.2 过程日志

- 用户说明：“按时间顺序整理输入、修改、动作间停顿和运行结果。”
- 格式：UTF-8 Markdown，参考用户提供的 `20260708-133611.md`，保留“会话摘要—时间线—行为明细”结构。
- 安全文案：把无限定的“思考”替换为“停顿（可能包含思考）”。
- 时机：会话 finalized 后立即 ready，不等 AI。
- 文件名：`process_log.md`。

### 5.3 AI 分析日志

- 用户说明：“维度结论、数据质量、行为证据与分析来源。”
- 格式：缩进的 UTF-8 JSON，参考用户提供的 `stage_samples.pretty.json` 的可读排版，但字段来自当前已验证的 `ai_analysis`、`integrity` 和 provenance，不恢复旧版掌握度字段。
- 文件名：`analysis_log.json`。
- 时机：分析 `queued/running` 时显示“正在分析…”，不提供空文件；`ready` 后显示查看与下载。
- `partial/error` 时显示“分析未完成”及现有重试入口，不把占位结果生成为成功日志。

### 5.4 行内交互

每行包含序号、文件名、一行说明和状态。ready 文件显示两个原生 `button`：

- “查看”：在 JupyterLab 主区域打开只读日志页签；
- “下载”：经认证 API 下载完整文件。

文件名本身也可点击，与“查看”行为一致。所有按钮支持键盘操作，状态文案使用 `aria-live="polite"`，不只用颜色表示 ready/running/error。

“打开日志文件夹”不再是日常主入口。为兼容本机管理员诊断，可保留在“高级数据”折叠区，但不影响远程平台使用。

### 5.5 生成一致性与安全读取

- 操作日志和过程日志以 finalized 后的首次成功生成为冻结点；后续 AI 状态变化或教师复核不得改写这两份文件。
- AI 任务先进入 `ready`、日志回调尚未落盘的短暂窗口仍显示“正在分析…”，客户端继续最多五分钟的退避轮询；不得把瞬态缺失固定成“分析未完成”。
- 查看和下载必须通过同一已打开文件描述符完成校验与读取：禁止跟随符号链接，并核对 `fstat` 与当前目录项为同一普通文件、权限为 `0600`。
- 完整下载按 64 KiB 分块写入响应，保留原生 `application/json` 或 `text/markdown` 媒体类型，不把完整文件一次性载入服务内存。

## 6. 文件生成与状态流

```text
监控中
├─ 操作日志：监控结束后生成
├─ 过程日志：监控结束后生成
└─ AI 分析日志：等待监控结束

上传/结束中
├─ 操作日志：正在生成
├─ 过程日志：正在生成
└─ AI 分析日志：等待会话提交

会话 finalized
├─ 操作日志：ready
├─ 过程日志：ready
└─ AI 分析日志：正在分析…

AI ready
├─ 操作日志：ready
├─ 过程日志：ready
└─ AI 分析日志：ready
```

前两份日志由会话 finalize 路径在服务端本地生成，不发起网络或 AI 请求。AI 分析日志在已验证结果成为 terminal ready 后原子生成，不从 provider 原始响应直接构造。

## 7. 服务端契约

### 7.1 列表

`GET /myextension/sessions/{session_id}/logs`

返回固定三项，每项包含：

```json
{
  "kind": "operation|process|analysis",
  "filename": "operation_log.json",
  "label": "操作日志",
  "description": "用户输入、删除、粘贴、运行成功/失败及输出。",
  "status": "pending|generating|ready|error",
  "media_type": "application/json",
  "size_bytes": 12345,
  "generated_at": "2026-08-04T00:00:00+00:00",
  "error_code": null
}
```

顺序由 API 固定为 operation、process、analysis，前端不根据文件系统枚举顺序。

### 7.2 查看和下载

- `GET /myextension/sessions/{session_id}/logs/{kind}`：返回经大小上限保护的 UTF-8 内容用于插件内查看；
- `GET /myextension/sessions/{session_id}/logs/{kind}/download`：返回完整文件，使用 `Content-Disposition: attachment`。

两者都继承 Jupyter 认证，严格验证 UUID 会话 ID，`kind` 只允许三个常量，目标路径必须是日志根下当前会话目录中的非符号链接普通文件。任何客户端路径、`..`、绝对路径、未知文件名都失败关闭。

查看端点设有字节上限；超限时页签明确显示“内容过大，请下载查看”，不静默截断。下载端点不读取 `.ark_ai_config.json`、batch、receipt、job、provider 原始响应或其他会话。

## 8. JupyterLab 只读查看器

日志页签以 `session_id + kind` 作为稳定 ID，重复点击时激活现有页签，不重复创建。页签标题使用中文日志名。

- Markdown 使用 JupyterLab 已有 rendermime 渲染并进行安全消毒；
- JSON 在前端解析后以两空格缩进显示，如解析失败则报错而不当作 HTML；
- 页签顶部显示文件名、会话 ID、生成时间和“下载”按钮；
- 页签是只读的，不会回写日志或破坏完整性哈希。

## 9. BLUEDOT 平台接入

### 9.1 接入点

截图中 BLUEDOT 门户会为算法任务启动一个带路径前缀的 JupyterLab 4 工作台。插件是 JupyterLab 预构建前端扩展 + Jupyter Server 扩展，因此安装目标是工作台的 Python/Jupyter 运行环境，不是 BLUEDOT 门户 Vue/React 前端。

正式方案是在 `PyTorch-2.5.1-JupyterLab4` 基础镜像上构建平台自定义镜像，将通过验收的 wheel 固定在镜像中。页面中的 PyPI Manager 只用于一次性技术验证；它可能只修改当前容器，无法保证新工作台或容器重建后仍存在。

### 9.2 推荐镜像层

新交付 wheel 版本升为 `0.2.1`，避免与当前 `0.2.0` 产物混淆。平台构建流水线通过 `BLUEDOT_BASE_IMAGE` build argument 传入已有 JupyterLab 4 基础镜像：

```dockerfile
ARG BLUEDOT_BASE_IMAGE
FROM ${BLUEDOT_BASE_IMAGE}

COPY myextension-0.2.1-py3-none-any.whl /opt/bluedot/wheels/
RUN python -m pip install --no-cache-dir \
      /opt/bluedot/wheels/myextension-0.2.1-py3-none-any.whl \
    && jupyter server extension enable myextension --sys-prefix \
    && jupyter labextension list \
    && jupyter server extension list

ENV JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR=/workspace/result/behavior-audit
```

`BLUEDOT_BASE_IMAGE` 使用平台已有镜像的真实内部地址。安装层需要写入该 Python 环境的权限；如基础镜像默认用户无权限，平台在现有 Dockerfile 的 root 构建阶段插入这段，然后恢复其已有非 root `USER`；插件仓库不猜测平台用户名。构建完成后将新镜像登记为新的 AI 框架版本，再让算法工作台选择该镜像。

日志使用截图中的“输出结果目录” `/workspace/result`，子目录固定为 `behavior-audit`。平台必须保证 Jupyter 运行用户对该目录有读写权限，并在工作台停止后持久化该目录。

### 9.3 AI 配置

平台正式环境使用秘密管理/容器环境注入，不把 Key 写入镜像、算法代码、启动命令或结果目录：

```text
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/coding/v3
ARK_MODEL=glm-5-2-260617
ARK_API_KEY 由平台密钥 `behavior-audit-ark-api-key` 注入
```

平台不使用当前 provider 时，由平台运维替换 Base URL/模型/密钥。开放真实外部 AI 前，必须确认容器出站网络、TLS、额度、调用成本与数据政策。

当前 AI 配置 UI 会允许 Jupyter 会话用户修改/清除配置。因此在有多角色或学生直接使用的正式环境上，平台不应仅依赖该 UI 作为密钥权限边界。角色锁定/服务端管理配置是独立安全需求，未完成前不将本 Pilot 宣称为多租户生产就绪。

### 9.4 路径前缀兼容

BLUEDOT 使用类似 `/notebook_<uuid>/lab/...` 的动态前缀。前端所有 API 和下载 URL 必须使用 Jupyter `ServerConnection.ISettings.baseUrl` 构建，不得写死 `/myextension/...`。后端仍使用 Jupyter Server route 注册，不单独开放端口。

### 9.5 平台验收

镜像构建时与工作台启动后都验证：

```bash
python -c "import myextension; print(myextension.__version__)"
jupyter labextension list
jupyter server extension list
test -w /workspace/result
```

页面验收：

1. 经 BLUEDOT “打开工作台”进入带动态前缀的 JupyterLab；
2. 左侧出现“行为分析”，前后端探针正常；
3. 完成一次无真实 AI 的会话，停止后前两份日志立即 ready，可查看与下载；
4. 在已授权的真实 AI 验收中，第三行从“正在分析…”变为 ready；
5. 停止工作台后，平台结果管理仍能获取 `/workspace/result/behavior-audit` 内的文件。

## 10. 错误处理

- finalize 失败：前两行显示“会话尚未完整提交”，沿用“重试上传/结束”；
- 前两份本地生成失败：分别显示失败，不阻塞 AI 任务，但验收不通过；
- AI 未配置/超时/输出无效：第三行显示稳定中文原因与重试操作，前两行仍可用；
- 查看内容过大：提示下载，不截断后伪装为完整内容；
- 下载文件在列表后被替换、符号链接或越界：失败关闭并返回通用错误，不泄露服务器绝对路径。

## 11. 测试与验收门槛

后端：

- finalize 后不调用 AI 也会原子生成 `operation_log.json` 与 `process_log.md`；
- `analysis_log.json` 只由已验证 ready 结果生成；
- 列表顺序、状态、媒体类型、文件大小和时间符合契约；
- 查看/下载的认证、UUID、白名单、路径越界、符号链接、大文件和消失文件路径都有失败关闭测试；
- 日志不含 API Key、Cookie、Jupyter token、provider 原始响应和服务器绝对路径。

前端：

- 有效观察时长的完整文案、进度和页面离开数字可见；
- 三行日志顺序固定，不同会话/分析状态下的文案和按钮正确；
- 文件名和“查看”打开同一个只读页签，“下载”走 Jupyter base URL；
- 查看中、查看失败、内容过大和下载失败都有非空状态；
- 按钮可键盘访问，状态可由屏幕阅读器读出。

回归：

- 后端全量 pytest、前端 Jest、lint、生产构建、wheel 内容检查全部通过；
- 新 wheel 在隔离 JupyterLab 4 环境验证前两份日志无 AI 即时生成与动态路径前缀；
- 真实 BLUEDOT 镜像发布、真实 AI 与平台持久化属于用户/运维授权的独立部署验收，本任务不自动执行。

## 12. 完成边界

本轮实现完成于：代码、契约、测试、新 wheel、无付费隔离验证和 BLUEDOT 部署说明均完成。不登录或更改 BLUEDOT 平台，不调用付费 AI，不推送，不发布平台镜像。

当前目录不是 Git 仓库，设计与交付使用文件 SHA-256、wheel SHA-256 和验证记录作为检查点，不伪造 commit 标识。
