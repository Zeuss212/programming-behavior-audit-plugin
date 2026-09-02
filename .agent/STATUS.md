# STATUS · 课堂同步服务当前状态

## 2026-09-02 协作文档基线已建立

- 本仓库已建立 `.agent` 状态、接口契约、运行手册、决策与交接记录机制。
- 本地课堂服务由 `deploy/classroom/local-demo/docker-compose.yml` 统一启动：`sync-api` 对主机暴露 `127.0.0.1:18080`，课堂代理暴露 `127.0.0.1:18081/classroom-api`。
- 前端开发服务器应将 `/classroom-api` 代理到同步服务；Docker 内部服务名 `classroom-sync-api:8080` 仅适用于同一容器网络。
- 当前课堂同步 API 的发布、任务同步和知识点评分策略已存在；独立评价维度与行为监测配置尚待与前端共同定义并实现，不能由任一端单独伪造字段。

## 已知风险与待办

- 本机未安装 Docker 时无法启动完整本地课堂栈；不要尝试只启动 API 而忽略 PostgreSQL、MinIO 与 deadline worker。
- 真实 BAMS/生产环境的 upstream、凭据和部署动作不记录在此目录；必须由获授权运维单独执行。
- 评价配置若从知识点权重升级为独立维度，需保持旧 `evaluation-policy` 接口兼容，并新增版本化配置资源与契约测试。

## 后续记录要求

- 每个可交接变更在 `.agent/handoffs/` 新增记录，并更新本文件。
- 接口字段、状态码、鉴权或代理变更同步更新 `API_CONTRACTS.md` 与测试。
- 长期技术选择使用 `.agent/decisions/` 记录 ADR。
