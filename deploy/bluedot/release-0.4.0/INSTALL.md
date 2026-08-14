# 0.4.0 课堂学生镜像构建与安装

此目录是构建安装包，不是已经可直接导入 BAMS 的最终镜像 tar。请仅在隔离的 Linux
AMD64 Docker 构建主机上执行本说明；不要使用当前的
`dap_pytorch_1.10.0:cpu@sha256:c7c2…4632`，它是 JupyterLab 3.2.9 / Jupyter Server
1.13.5，无法运行本插件。

## 1. 前置条件

- BAMS 运维提供一个不可变基础镜像引用，格式为
  `repository@sha256:实际摘要值`，并确认其包含 Python 3.10+、JupyterLab 4.x、
  Jupyter Server 2.x 和 `jsonschema`。
- 构建主机使用 Linux AMD64 Docker Engine，并可取得该基础镜像。
- BAMS 已记录待替换**测试模板**的旧镜像 digest；不要直接修改正在使用的工作台。

## 2. 校验并解压构建安装包

```bash
shasum -a 256 -c behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz.sha256
tar -xzf behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz
cd behavior-audit-classroom-0.4.0
shasum -a 256 -c SHA256SUMS
```

`SHA256SUMS` 必须全部通过。失败时停止，不要继续构建或上传。

## 3. 构建并验收镜像

将下方基础镜像引用替换为 BAMS 运维确认的真实不可变 digest；不要使用可变 tag。

```bash
BASE_IMAGE='repository@sha256:实际摘要值'
TARGET_IMAGE='behavior-audit:0.4.0-classroom'
./build_image.sh "$BASE_IMAGE" "$TARGET_IMAGE"
./verify_image.sh "$TARGET_IMAGE"
./export_image.sh "$TARGET_IMAGE" behavior-audit-0.4.0-linux-amd64.tar
shasum -a 256 -c behavior-audit-0.4.0-linux-amd64.tar.sha256
```

`build_image.sh` 固定构建 `linux/amd64`，并在构建期验证 JupyterLab 4、Jupyter
Server 2、`jsonschema` 与插件版本。`verify_image.sh` 检查学生权限、扩展启用状态、
无内置密钥和 `/workspace/result/behavior-audit` 可写。任一步失败都不能导出、上传或替换
任何 BAMS 模板。

## 4. BAMS 测试模板配置

由 BAMS 运维使用平台既有的私有镜像导入流程处理生成的 tar，并将生成后的不可变 digest
配置到一个新建或复制的**测试**环境模板。容器运行时必须挂载持久卷至
`/workspace/result`，并通过 Secret/环境变量注入：

```text
JUPYTERLAB_BEHAVIOR_AUDIT_PLATFORM_MODE=student
JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL=https://classroom-sync.example.invalid/classroom-api
JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR=/workspace/result/behavior-audit
JUPYTERLAB_BEHAVIOR_AUDIT_DEADLINE_POLL_SECONDS=30
```

不要在镜像、模板文本、日志或 ticket URL 中写入 API key、平台 JWT、对象存储凭据或课堂
票据。该同步地址必须是 BAMS 反向代理提供的 HTTPS `/classroom-api` 路径，不能填写课堂服务
主机的 loopback 地址或 `40037` 工作台端口。学生仅从 BAMS 的 `https://14.103.139.131:40037`
入口进入新建测试工作台。

## 5. 回滚

若测试模板验证失败，停止向该模板创建新工作台，将**该测试模板**指回步骤 1 记录的旧
digest。保留 `/workspace/result/behavior-audit`、已生成的简报和证据，供故障分析；不要
删除现有工作台、镜像或学生数据。
