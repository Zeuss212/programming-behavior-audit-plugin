# ARCHITECTURE · 课堂同步服务

1. `services/classroom-sync` 是 FastAPI 服务，提供课堂草稿、发布、学生任务、事件、简报和教师复核 API。
2. `sync-api` 依赖 PostgreSQL 保存课堂版本、会话与任务；依赖 MinIO 保存行为证据；`deadline-worker` 处理截止与后台任务。
3. 本地演示编排位于 `deploy/classroom/local-demo/docker-compose.yml`：服务内部使用 `sync-api:8080`，宿主机访问 `127.0.0.1:18080`。
4. 前端与 BAMS 只经 `/classroom-api` 访问课堂服务；Nginx 必须转发 Authorization、请求 ID 与转发头，事件流禁用缓冲。
5. 课堂方案版本发布后不可变；草稿更新应使用 revision 乐观锁，学生任务由已发布版本同步生成。
