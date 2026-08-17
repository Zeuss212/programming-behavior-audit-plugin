# BAMS 课堂 API HTTPS 入口运行手册

## 目的与边界

本手册说明如何将 BAMS 已有 HTTPS 入口中的 `/classroom-api/` 路径转发到课堂同步服务。它不提供真实上游地址、密钥、令牌或执行线上变更的命令。实际渲染、配置加载和模板替换必须由 BAMS 运维在单独的部署授权后执行。

`bams-classroom-api-http.conf.template` 必须在 Nginx `http` 上下文加载；`bams-classroom-api-location.conf.template` 必须在 BAMS 既有 TLS `server` 中加载。运维渲染 location 模板时，只能为 `CLASSROOM_SYNC_UPSTREAM` 传入 BAMS ingress 可达的受控私有上游，不能使用课堂服务主机的 loopback 地址。

## 部署前记录

在执行任何写操作前，记录并复核：

- BAMS HTTPS DNS 与证书验证结果；
- BAMS ingress 到课堂同步服务私有上游的连通性结果；
- 当前测试学生模板 digest 与拟使用的候选 student image digest；
- 候选/旧前端 image digest；
- 数据库备份标识；
- 仅针对测试模板和候选前端的回滚动作。

该代理只允许 `/classroom-api/`。它必须保留 Authorization、`X-Request-ID` 与 `X-Forwarded-*`，请求体上限为 2 MiB；事件流路径禁用缓冲。访问日志只使用 `classroom_safe` 格式，不能记录 query、Authorization、ticket、plugin token 或请求体。不得代理 FinColab `40002`、BAMS 工作台、MinIO 或对象存储端口。

## 候选验收

完成配置后，只在测试模板与候选前端执行以下只读/业务验收：

1. `GET /classroom-api/health/ready` 返回就绪状态。
2. 学生通过一次性 ticket 注册插件会话。
3. 学生上传一个 gzip 证据分片并得到幂等回执。
4. 学生提交一份课堂简报。
5. 教师只能读取本课程的对应简报。

任一检查失败时，停止向测试模板创建新工作台，将测试模板或候选前端恢复到部署前记录的旧 digest。保留 PostgreSQL、MinIO、方案、简报和证据数据，供诊断使用；不得删除课堂同步服务容器或持久卷。

## 本地验证证据

2026-08-14 本地验证结果：

- 课堂插件与 ingress 回归：使用隔离 uv 依赖执行 `myextension/tests/test_platform_config.py`、`test_platform_registration.py`、`test_classroom_release_040.py`、`scripts/__tests__/test_bams_classroom_nginx_config.py` 和 `test_classroom_nginx_config.py`，结果为 `38 passed`。该过程仅临时绑定本机 loopback，并启动自动删除的 `nginx:1.27-alpine` 容器执行渲染后 `nginx -t`。
- 候选离线构建包：`releases/` 目录内执行 `shasum -a 256 -c behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz.sha256`，结果为 `OK`；归档 SHA-256 为 `4d54d4f4f51268447a7dfdc117371d492e1c9f009a3d0175093e29a345c3a9ff`。
- 前端候选门禁：`build-classroom-candidate.test.ts` 与既有 Nginx 代理测试均通过；`npm run type-check`、`npm run build`、新增测试文件的 Oxlint/ESLint 和脚本语法检查均通过。
- 前端干净基线的全量测试为 `105 passed`，但全量 Oxlint 仍有 7 个既存错误、ESLint 仍有 5 个既存错误，均位于本轮未修改文件；它们分别来自 `681faa08`、`20f0ad2f` 与 `dfb3c515`，本轮未将全量 lint 描述为通过。
- 相关提交：`ce361c2`（HTTPS loopback 防护）、`4a8122e`（BAMS ingress 模板与手册）、`0cbfb79`（更新后的学生离线构建包）、前端 `3e0a7d4`（AMD64 候选构建门禁）。

真实 BAMS 配置、镜像推送、测试模板替换、候选容器替换、数据库迁移和正式前端发布均未执行。不得在此记录中写入真实上游地址、凭据或 bearer token。
