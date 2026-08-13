# myextension 0.4.0 课堂学生镜像候选包

本目录是仅供本地构建和验收的候选交付包。它不会推送镜像、替换 BAMS 模板或写入任何平台密钥。

| 文件 | 用途 |
| --- | --- |
| `artifacts/myextension-0.4.0-py3-none-any.whl` | 含 JupyterLab 前端和服务端的候选 wheel |
| `SHA256SUMS` | 候选 wheel 的完整性校验 |
| `Dockerfile` | 基于 BAMS JupyterLab 4 镜像安装候选 wheel |
| `runtime.env.example` | 学生课堂运行参数示例，不含任何密钥 |
| `build_image.sh` | 校验 wheel 后构建本地镜像 |
| `verify_image.sh` | 离线检查版本、学生权限、扩展、持久目录和内置密钥 |

## 本地构建

基础镜像必须由 BAMS 提供，并已包含 Python 3.10+、JupyterLab 4、Jupyter Server 2 和 `jsonschema`。构建脚本使用 `--no-deps`，不会在构建时下载 Python 依赖。

```bash
BASE_IMAGE='<BAMS 提供的 JupyterLab 4 基础镜像>'
TARGET_IMAGE='behavior-audit:0.4.0-classroom'
./build_image.sh "$BASE_IMAGE" "$TARGET_IMAGE"
./verify_image.sh "$TARGET_IMAGE"
```

`verify_image.sh` 使用临时目录运行容器，并明确要求镜像内没有 AI、平台或对象存储密钥。它验证 JupyterLab 4、Jupyter Server 2、插件版本、学生能力、扩展启用状态和 `/workspace/result/behavior-audit` 可写。

## BAMS 运行参数

将 `runtime.env.example` 复制为平台运行配置，并替换其中的 `JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL` 为真实课堂同步服务 HTTPS 地址。BAMS 必须把持久卷挂载到 `/workspace/result`。

不要把 API Key、平台 JWT、S3 凭据或一次性票据写进 Dockerfile、环境示例或镜像。它们只能由 BAMS Secret 在运行时注入。学生入口仍只使用 `https://14.103.139.131:40037`。

## 停止点与回滚

本地镜像验证结束即停止。不得执行 `docker push`、`scp`、模板替换或容器重启。保留已验证的 0.3.0 镜像 digest；若后续发布出现问题，将 BAMS 工作台模板改回该 digest，新建工作台验证即可，不能删除 `/workspace/result/behavior-audit`。
