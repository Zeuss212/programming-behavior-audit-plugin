# BLUEDOT 平台接入说明

适用版本：`myextension 0.2.1`  
目标工作台：BLUEDOT `PyTorch-2.5.1-JupyterLab4`

## 结论

插件应安装到 BLUEDOT 创建 Notebook 工作台时使用的 **JupyterLab 基础镜像**，不是修改算法详情门户页。平台的 PyPI Manager 可用于一次性验证，但工作台重建后可能丢失，不应作为正式部署方式。

本仓库只提供可复现镜像资产和验收步骤；本次未登录 BLUEDOT 镜像仓库、未构建/推送线上镜像、未修改正在运行的工作台。

## 1. 准备 0.2.1 wheel

在项目根目录完成质量门禁和构建：

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm lint:check
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm build:prod
.venv/bin/python -m build --wheel
shasum -a 256 dist/myextension-0.2.1-py3-none-any.whl
```

只将已核对 SHA-256 的 `dist/myextension-0.2.1-py3-none-any.whl` 放入镜像构建上下文。
本轮本地构建的确切 SHA-256 为：

```text
7138965244a5f71b9307ca89c5585cdb58aa1206a2ba1a0c13e57147bbeecf98
```

## 2. 构建平台镜像

[`deploy/bluedot/Dockerfile`](../deploy/bluedot/Dockerfile) 使用平台提供的原始工作台镜像作为基础镜像。在项目根目录执行：

```bash
docker build \
  -f deploy/bluedot/Dockerfile \
  --build-arg BLUEDOT_BASE_IMAGE='<平台的 PyTorch-2.5.1-JupyterLab4 完整镜像地址>' \
  -t '<单位镜像仓库>/bluedot-pytorch-jupyterlab4-behavior-audit:0.2.1' \
  .
```

Dockerfile 会：

- 用镜像内的 Python 安装 prebuilt wheel；
- 在 `--sys-prefix` 启用 Jupyter Server 扩展；
- 保留 JupyterLab 原有启动命令；
- 默认将日志根目录设为 `/workspace/result/behavior-audit`。

如基础镜像以非 root 用户构建且无法安装到 sys-prefix，应由平台镜像维护者在 Dockerfile 中使用该镜像既有的提权/回切用户模式；不要把 `pip --user` 与另一个 Jupyter 运行环境混用。

## 3. 平台配置

### 日志持久化

将 BLUEDOT 的输出结果持久化卷挂载到 `/workspace/result`，并保持：

```text
JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR=/workspace/result/behavior-audit
```

每次会话的用户可读日志位于：

```text
/workspace/result/behavior-audit/sessions/<session_id>/logs/operation_log.json
/workspace/result/behavior-audit/sessions/<session_id>/logs/process_log.md
/workspace/result/behavior-audit/sessions/<session_id>/logs/analysis_log.json
```

不建议挂载或对外公开单个会话目录；持久化根目录可能包含学生代码、输出和错误文本，应继承平台的工作台访问控制、保留和删除政策。

### AI 配置

配置文件的路径优先级为：

1. `JUPYTERLAB_BEHAVIOR_AUDIT_AI_CONFIG_PATH` 指定的文件；
2. `JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR` 下的 `.ark_ai_config.json`；
3. `/workspace/code/.behavior-audit/.ark_ai_config.json`（仅在 `/workspace/code` 存在时）；
4. 普通本地默认位置。

如果单用户试点工作台无法注入环境变量，可直接在插件页面保存，插件会自动使用第 3 项。该文件为 `0600` 私有文件，不能提交、下载分发或打包进 wheel；容器重建后是否保留取决于 BLUEDOT 对 `/workspace/code` 的持久化策略。

需要使用受控私有目录时，在工作台运行环境设置：

```text
JUPYTERLAB_BEHAVIOR_AUDIT_AI_CONFIG_PATH=/受控私有目录/.ark_ai_config.json
```

非敏感配置可作为工作台环境变量注入：

```text
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/coding/v3
ARK_MODEL=glm-5-2-260617
```

正式多用户部署中，`ARK_API_KEY` 必须由 BLUEDOT/容器编排系统的密钥管理能力在**运行时**注入。禁止将 Key 写入 Dockerfile、镜像层、Notebook、启动参数、日志或项目文档。

## 4. 动态路径兼容

BLUEDOT 工作台地址包含类似 `/notebook_<uuid>/` 的动态前缀。插件不使用根相对 `/myextension/...` URL，而是通过 JupyterLab `ServerConnection.ISettings.baseUrl` 组合 list/view/download 地址，因此无需在门户层硬编码 workspace UUID。

反向代理必须保持 Jupyter 的 Cookie/token 认证和原有 base URL，不要为方便调试关闭鉴权。

## 5. 镜像与工作台验收

在镜像中先检查：

```bash
python -c "import myextension; print(myextension.__version__)"
jupyter server extension list
jupyter labextension list
```

预期版本为 `0.2.1`，且前后端都显示 `myextension` 已启用。随后在新建的 BLUEDOT 工作台中完成一次合成流程：

1. 创建题目方案并发布。
2. 开始监控，输入代码，运行一次错误，修复后成功运行。
3. 停止监控后，确认 `operation_log.json` 和 `process_log.md` 不等 AI 即可查看/下载。
4. 确认 `analysis_log.json` 先显示“正在分析…”，只在分析成功后可打开。
5. 刷新页面，确认动态 `/notebook_<uuid>/` 前缀下三份日志仍可打开。
6. 重建工作台后，按平台保留策略检查 `/workspace/result/behavior-audit`。

## 6. 回滚

回滚时将 BLUEDOT 工作台模板指回上一个已验证镜像标签，然后新建一个工作台验证。软件回滚不会自动删除 `/workspace/result/behavior-audit` 中的教学数据；数据处理必须单独授权。

## 7. 当前边界

- 插件继承当前 Jupyter Server 会话的访问边界，未实现平台级教师/学生角色授权。
- 当前是 Pilot，不应描述为已具备 JupyterHub/BLUEDOT 多租户生产隔离。
- AI 请求可能产生费用并涉及数据出境/外部处理，真实环境启用前必须完成单位的密钥、数据和 provider 审批。
- 本说明中的镜像构建和平台验收仍需 BLUEDOT 管理员在真实基础镜像上执行。
