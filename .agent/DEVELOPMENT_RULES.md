# DEVELOPMENT_RULES · 课堂同步服务

- 先修改领域服务、契约和测试，再修改路由；不要在前端或 Nginx 中补造后端状态。
- 课堂发布、学生任务、行为证据和成绩相关变更必须保留权限校验、版本不可变和乐观锁语义。
- 不记录或提交 token、AI Key、真实学生源码、事件、数据库导出或对象存储凭据。
- 新接口、状态码或 schema 变更同步更新 `.agent/API_CONTRACTS.md`、OpenAPI/schema 和相应测试。
- Docker 本地演示数据与真实环境隔离；不得将本地 compose 配置直接当作生产部署配置。
