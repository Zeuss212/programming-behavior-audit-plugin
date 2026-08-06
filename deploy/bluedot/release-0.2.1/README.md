# myextension 0.2.1 BLUEDOT 镜像交付包

本目录是修复“分析慢、偶发失败”后的独立交付包。它安装 JupyterLab 4 的 prebuilt 前端扩展和 Jupyter Server 2 后端扩展，不需要在目标镜像中安装 Node.js，也不会修改基础镜像原有的 `ENTRYPOINT` 或 `CMD`。

本包只提供文件和管理员执行步骤；没有登录镜像仓库、没有推送镜像、没有调用真实 AI，也没有修改 BLUEDOT 工作台。

## 1. 交付文件

| 文件 | 用途 |
| --- | --- |
| `artifacts/myextension-0.2.1-py3-none-any.whl` | 修复后的插件 wheel |
| `SHA256SUMS` | wheel 完整性校验 |
| `Dockerfile` | 在 BLUEDOT JupyterLab 4 基础镜像中离线安装 wheel |
| `.dockerignore` | 只允许 Dockerfile 和指定 wheel 进入构建上下文 |
| `build_image.sh` | 校验 wheel 后构建镜像 |
| `verify_image.sh` | 非交互检查镜像内插件与日志目录 |
| `runtime.env.example` | 不含密钥的运行环境变量示例 |

插件版本保持 `0.2.1`，本次新 wheel 通过所在目录和 SHA-256 与旧 `dist/` wheel 区分：

```text
8436b8e69f9e25c58df68c0024723c660e9fe8751c52a60b320c1e97f28ea16e  artifacts/myextension-0.2.1-py3-none-any.whl
```

## 2. 基础镜像要求

管理员提供的基础镜像必须已经包含：

- Python 3.10 或更高版本；
- JupyterLab 4.x；
- Jupyter Server 2.x；
- `jsonschema` 4.x；
- 可运行的 `python -m pip` 与 `python -m jupyter`；
- 对其 Python `sys-prefix` 的镜像构建期写权限。

Dockerfile 使用 `--no-deps`，不会联网补依赖。若基础镜像缺少上述组件，构建会直接失败，必须先修正基础镜像，不能临时使用另一套 `pip --user` 环境绕过。

## 3. 收包后先校验 wheel

进入本目录：

```bash
cd deploy/bluedot/release-0.2.1
```

Linux：

```bash
sha256sum -c SHA256SUMS
```

macOS：

```bash
shasum -a 256 -c SHA256SUMS
```

必须显示 `artifacts/myextension-0.2.1-py3-none-any.whl: OK`。不一致时停止，不要构建或安装。

## 4. 直接安装 wheel（已有 JupyterLab 环境）

如果不是制作镜像，而是安装到一个可重建的现有 JupyterLab 4 环境，使用该环境自己的 Python：

```bash
python -m pip install \
  --no-cache-dir \
  --no-deps \
  --force-reinstall \
  artifacts/myextension-0.2.1-py3-none-any.whl
python -m jupyter server extension enable myextension --sys-prefix
```

验证：

```bash
python -c "import myextension; assert myextension.__version__ == '0.2.1'; print(myextension.__version__)"
python -m jupyter server extension list
python -m jupyter labextension list
```

前后端列表都应出现 `myextension`。随后彻底停止并重新启动 JupyterLab/Jupyter Server；仅刷新浏览器不足以替换正在运行的旧 Python 代码。

同版本覆盖安装一定要保留 `--force-reinstall`。临时 PyPI Manager 安装只适合验证，工作台重建后可能丢失，正式交付应使用镜像。

## 5. 构建自定义镜像

先准备两个完整镜像名：

```bash
BASE_IMAGE='<BLUEDOT 提供的 JupyterLab 4 基础镜像完整地址>'
TARGET_IMAGE='<单位镜像仓库>/bluedot-jupyterlab4-behavior-audit:0.2.1-fixed'
```

执行经过校验的构建脚本：

```bash
chmod +x build_image.sh verify_image.sh
./build_image.sh "$BASE_IMAGE" "$TARGET_IMAGE"
```

脚本先检查 `SHA256SUMS`，只有通过后才调用 Docker。等价的手工构建命令是：

```bash
docker build \
  --build-arg "BLUEDOT_BASE_IMAGE=$BASE_IMAGE" \
  --tag "$TARGET_IMAGE" \
  .
```

Dockerfile 会在构建期确认 JupyterLab 4、Jupyter Server 2 和 `jsonschema` 已存在，强制覆盖安装本 wheel，启用 Server 扩展，并列出前后端扩展。它不复制源码、日志、Notebook、AI 配置或密钥。

## 6. 本地验收镜像

不启动 JupyterLab、不关闭鉴权，只运行非交互检查：

```bash
./verify_image.sh "$TARGET_IMAGE"
```

该脚本使用临时 `/workspace/result` tmpfs，确认：

- 安装版本为 `0.2.1`；
- Jupyter Server 扩展列表包含 `myextension`；
- JupyterLab 扩展列表包含 `myextension`；
- `/workspace/result/behavior-audit` 可创建私有测试文件。

## 7. 推送镜像（由镜像仓库管理员执行）

以下操作会改变外部仓库状态，只能由已授权管理员执行：

```bash
REGISTRY_HOST='<单位镜像仓库主机名>'
docker login "$REGISTRY_HOST"
docker push "$TARGET_IMAGE"
docker image inspect "$TARGET_IMAGE" --format '{{json .RepoDigests}}'
```

记录推送后的不可变 digest（`sha256:...`）。BLUEDOT 注册和回滚优先使用 digest；浮动标签只能作为便于阅读的别名。

## 8. 在 BLUEDOT 注册并创建工作台

不同租户的菜单名称可能不同，管理员按平台实际入口创建“自定义镜像/自定义框架”，字段至少满足：

1. 镜像地址：填写刚推送的完整地址，优先固定 digest。
2. 工作台类型：选择 JupyterLab 4；不要改成独立 Web 服务。
3. 启动命令：保留基础镜像默认命令，不额外覆盖认证、base URL 或 token 参数。
4. 持久化目录：将平台结果卷挂载到 `/workspace/result`。
5. 非密钥环境变量：参考 `runtime.env.example` 注入。
6. AI 密钥：在平台 Secret/密钥管理中创建名为 `ARK_API_KEY` 的运行时密钥引用，不写入镜像、Notebook、环境文件或启动参数。
7. 网络策略：允许工作台按单位策略访问所配置的 AI Provider；反向代理必须保留 Jupyter 的 Cookie/token 鉴权和动态 base URL。

创建一个全新的工作台验证，不能只重启旧容器，因为旧工作台可能仍固定到旧镜像 digest。

## 9. 0.2.1 最新运行配置逻辑

### 分析时间预算

```text
JUPYTERLAB_BEHAVIOR_AUDIT_ANALYSIS_TIMEOUT_SEC=120
```

- 默认整次 AI 分析预算为 120 秒；
- 仅接受 `60` 到 `180` 的整数，越界或非法值回退到 120；
- 单次 Provider 请求最多 60 秒，且不能超过整次分析剩余时间；
- 网络错误、Provider 超时、HTTP 429 或 5xx 最多重试一次，等待 2 秒；
- 初始请求、一次截断恢复、一次无效维度修复和瞬时重试共享同一预算。

若真实模型稳定超过 120 秒，可在完成成本和容量评估后设置为 `180`；不建议继续增大，因为前端和平台需要有明确终态。

### AI 配置文件优先级

1. `JUPYTERLAB_BEHAVIOR_AUDIT_AI_CONFIG_PATH` 指定文件；
2. `JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR/.ark_ai_config.json`；
3. `/workspace/code/.behavior-audit/.ark_ai_config.json`（仅在 `/workspace/code` 存在时）；
4. 普通本地默认目录。

推荐正式环境直接由 Secret 注入 `ARK_API_KEY`，并通过非密钥环境变量设置 `ARK_BASE_URL` 和 `ARK_MODEL`。页面保存配置只适合当前单用户 Pilot；配置文件权限为 `0600`，其跨容器持久性取决于平台卷策略。

### 数据目录

```text
JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR=/workspace/result/behavior-audit
```

目录中可能包含学生代码、输出和错误文本，必须继承工作台访问控制、保留、下载和删除策略，不能作为公共静态目录暴露。

## 10. 工作台验收步骤

### 不启用真实 AI 的功能验收

1. 不注入 `ARK_API_KEY`，打开 JupyterLab，确认左侧活动栏出现插件。
2. 创建并发布一个合成题目方案。
3. 开始监控，输入合成代码，运行一次失败，再修复并成功运行。
4. 停止监控，确认 `operation_log.json` 和 `process_log.md` 可立即查看/下载。
5. AI 任务应进入可解释的 `ai_not_configured` 状态，不应无限等待或伪造分析结果。
6. 刷新页面并新建工作台，确认平台持久化策略符合预期。

### 已获授权的真实 AI 验收

1. 只使用合成数据，并由 Secret Manager 注入 AI Key。
2. 确认 Base URL、模型名、额度、网络和模型权限正确。
3. 执行同一合成流程，分析应在默认 120 秒预算内进入 `ready`、`partial` 或明确错误终态。
4. 验证成功结果引用当前会话事件，且每个维度最多返回 3 条主要证据。
5. 验证 `analysis_log.json` 只在终态开放，不出现 Key、Provider 响应正文或本机绝对路径。

真实 AI 可能产生费用或外部数据处理，未完成单位审批时不要执行本节。

## 11. 错误排查

| 错误码 | 含义与处理 |
| --- | --- |
| `ai_analysis_timeout` | 整体预算或 Provider 请求超时；先重试，再检查延迟并考虑将预算调到 180 秒。 |
| `ai_provider_network_error` | 检查网络、DNS、TLS、代理和出口策略。 |
| `ai_provider_rate_limited` | 检查额度、QPS 和并发限制，稍后重试。 |
| `ai_provider_auth_failed` | 检查 Secret 是否注入、API Key 是否有效、模型权限是否开放。 |
| `ai_provider_request_rejected` | 检查 Base URL、模型名和 Provider API 兼容性。 |
| `ai_provider_unavailable` | Provider 5xx；稍后重试并查看 Provider 状态。 |
| `ai_response_truncated` | 两次结构化输出仍被截断；减少维度/事件量或换用支持更长输出的模型。 |
| `ai_response_invalid` | 模型没有返回符合约束的 JSON；检查模型的结构化输出能力。 |
| `ai_not_configured` | 未配置 AI Key；用平台 Secret 注入。 |

排查时只使用稳定错误码、Jupyter 扩展列表和平台容器日志。不要把 API Key、完整请求/响应正文或学生代码复制到工单。

## 12. 回滚

1. 保留本次镜像 digest 和上一个已验证镜像 digest。
2. 将 BLUEDOT 自定义框架/工作台模板指回上一个 digest。
3. 新建工作台，执行第 10 节的不启用 AI 验收。
4. 确认后停止新镜像继续创建工作台；是否删除新标签由镜像仓库管理员另行决定。

软件回滚不会删除 `/workspace/result/behavior-audit`。教学数据删除、迁移或恢复必须单独授权，不能通过切换镜像完成。
