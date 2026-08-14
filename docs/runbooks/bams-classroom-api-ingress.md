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

待本轮代码和候选包本地验证完成后，在本节追加实际命令、结果、提交 SHA、归档 SHA-256，以及未执行的外部动作。不得在此记录中写入真实上游地址、凭据或 bearer token。
