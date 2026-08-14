# 0.4.0 课堂镜像构建安装包设计

## 目标

向 BAMS 运维人员交付一个可离线校验、可在 Linux AMD64 构建宿主机上复现的
`myextension` 0.4.0 镜像构建安装包。运维人员基于 BAMS 提供的兼容基础镜像构建、
验证并导出 Docker 镜像 tar；本任务不直接替换 BAMS 模板、不推送镜像，也不重启任何
线上工作台。

## 已确认约束

- 学生工作台入口始终为 `https://14.103.139.131:40037`；不暴露或使用 `40002`。
- 当前正在运行的 `dap_pytorch_1.10.0:cpu@sha256:c7c2…4632` 只含
  JupyterLab 3.2.9 / Jupyter Server 1.13.5，不能作为 0.4.0 的基础镜像。
- 运维提供的基础镜像必须为 Linux AMD64，包含 Python 3.10+、JupyterLab 4.x、
  Jupyter Server 2.x 与 `jsonschema`。
- 任何 API key、平台 JWT、S3 凭据和一次性课堂票据只可由 BAMS 在运行时注入，
  不得进入 wheel、Dockerfile、脚本、镜像历史、压缩包或命令输出。

## 交付物

发布归档 `releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz`
包含下列固定内容：

| 内容 | 作用 |
| --- | --- |
| `artifacts/myextension-0.4.0-py3-none-any.whl` | 已预构建的学生课堂插件 |
| `Dockerfile` | 在 BAMS 基础镜像上离线安装 wheel |
| `build_image.sh` | 先校验 wheel，再以 Linux AMD64 构建指定标签 |
| `verify_image.sh` | 验证版本、学生能力、扩展启用、无内置密钥和持久目录 |
| `runtime.env.example` | 不含密钥的学生运行参数模板 |
| `INSTALL.md` | 运维构建、验证、导出、导入测试模板和回滚步骤 |
| `SHA256SUMS` | 归档内每个交付文件的完整性校验 |

归档不是最终 Docker 镜像 tar，文件名明确含 `buildkit`。只有运维提供满足约束的
不可变基础镜像 digest 后，才能生成最终
`behavior-audit-0.4.0-linux-amd64.tar`。这样避免把不兼容基础镜像误标为可部署产物。

## 构建与验收流程

1. 运维写入基础镜像的完整不可变引用（形如 `repository@sha256:实际摘要值`），并在隔离 Linux AMD64
   Docker 主机执行 `build_image.sh`。
2. 脚本先校验 wheel；校验失败时不能调用 Docker。
3. 构建强制 `linux/amd64`，安装时禁止下载 Python 依赖；Dockerfile 在构建期断言
   JupyterLab 4、Jupyter Server 2、`jsonschema` 和插件版本。
4. 运行 `verify_image.sh`。它使用临时可写目录，检查学生只能采集/提交、不能创建方案，
   并断言镜像不含 AI、平台或对象存储密钥。
5. 只有前四步均通过时，运维执行 `docker save` 生成最终镜像 tar，再将 tar 导入 BAMS
   的**测试**环境模板。运行时挂载 `/workspace/result` 持久卷，并配置真实课堂同步服务
   HTTPS 地址。
6. 在 `40037` 新建测试工作台验证后，才由运维决定是否将指定课程模板切换到新 digest。
   回滚仅把模板指回已记录的旧 digest；不删除学生日志、简报或证据目录。

## 验收标准

- 安装包内的每个文件通过 SHA-256 校验，且压缩包自身也有 SHA-256。
- 构建脚本在错误校验和或错误平台条件下安全失败；正确基础镜像下产出 Linux AMD64 镜像。
- 验收脚本通过 JupyterLab/Jupyter Server/插件/学生能力/持久目录/无密钥检查。
- 安装文档不包含 Secret、宿主机凭据、可变镜像标签作为生产输入，或任何自动远程部署命令。
- 未提供兼容基础镜像 digest 时，交付状态只能是“构建安装包已验证，最终镜像待运维构建”。

## 非目标与后续边界

本交付不修改 FinColab 页面，不创建或替换 BAMS 模板，不执行 Docker push、SSH 上传或
数据库迁移。镜像交接后，下一项开发工作是本地课堂同步服务和端到端联调环境：教师发布
方案、学生接收并注册、事件/证据补传、手动或截止自动提交、教师读取单份简报。
