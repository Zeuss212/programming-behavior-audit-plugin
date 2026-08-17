# BAMS 课堂同步 HTTPS 接入与候选发布设计

**日期：** 2026-08-14
**状态：** 已获用户确认，等待规格复核后进入实施计划

## 目标

让 BAMS 学生 JupyterLab 插件通过可访问的 HTTPS 地址调用既有课堂同步服务，并让候选发布在 Linux AMD64 服务器上可验证、可回滚。教师/学生网页继续通过候选 FinColab 前端访问同一服务；正式 `5179` 不在本轮替换范围内。

## 范围与非目标

本轮交付包含：BAMS 入口的可审计反向代理配置模板与静态测试、学生镜像的 HTTPS 配置与文档、候选发布前的架构校验，以及针对 `no match for platform in manifest` 的 AMD64 发布门禁。

本轮不包含：把 API 的 loopback 端口公开到互联网、写入任何密钥、替换正式前端、修改在用 BAMS 模板、迁移或删除课堂数据库/MinIO 数据、真实教师/学生身份的端到端验收。BAMS 的实际 Nginx 配置、可达的私有上游地址和 TLS 证书由 BAMS 运维在新的部署授权后提供和执行。

## 方案比较

1. **BAMS HTTPS 入口反向代理（采用）**：学生插件配置为 BAMS 已配置 HTTPS 入口下的 `/classroom-api` 路径；入口终止 TLS、转发认证头，并在受控网络内抵达课堂服务。插件不需要知道服务端 loopback 地址，满足 HTTPS 校验，也不会把令牌交给浏览器。
2. **课堂服务独立公网域名**：可行，但需要新增域名、证书、网络策略和长期运维面；当前没有这些已确认的基础设施，超出最小范围。
3. **直接暴露 `18080` 或让插件请求 HTTP**：拒绝。该端口当前仅绑定 loopback，且学生模式明确拒绝非 HTTPS 地址；暴露它会扩大攻击面并重现此前 HTTPS 端口收到纯 HTTP 请求的问题。

## 架构与数据流

```text
FinColab 候选前端 (5180) --内部 /classroom-api--> classroom-sync-api

BAMS JupyterLab 插件 --HTTPS /classroom-api--> BAMS ingress
    --受控私有上游--> classroom-sync-api --私有--> PostgreSQL / MinIO
```

学生浏览器只把一次性课堂 ticket 交给本机 Jupyter Server。Jupyter Server 读取
`JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL` 后，调用以下既有插件 API：

- `POST /v1/classroom/plugin/sessions/register`
- `POST /v1/classroom/plugin/sessions/{session_id}/context/refresh`
- `PUT /v1/classroom/plugin/sessions/{session_id}/evidence/{sequence}`
- `POST /v1/classroom/plugin/sessions/{session_id}/submit`

入口仅剥离 `/classroom-api/` 前缀，向同步服务保留 `/v1/...` 路径；转发 `Authorization`、`X-Request-ID`、`X-Forwarded-*`，不记录 Authorization、query 或请求体。上传上限维持 2 MiB，SSE 配置不缓冲。插件 session token 只保存在 Jupyter Server 的受限文件中，永不返回浏览器或写入代理日志。

## 组件边界

| 组件 | 责任 | 不负责 |
| --- | --- | --- |
| BAMS ingress 配置模板 | HTTPS 路由、前缀重写、安全头、认证头转发与超时 | 终止课堂业务鉴权、保存日志或对象存储凭据 |
| `PlatformConfig` / `PlatformSyncClient` | 强制 HTTPS 基地址并由 Jupyter Server 调用同步 API | 将 API 地址、plugin token 或 ticket 暴露给网页 |
| 课堂同步服务 | ticket 交换、会话 token 鉴权、证据与简报 | 向学生暴露 MinIO、教师原始日志或平台 JWT |
| 候选发布脚本/文档 | 构建、导出前确认 `linux/amd64`，记录候选与回滚步骤 | 推送镜像、替换 BAMS 模板或正式 5179 |

## 失败处理与安全边界

- BAMS ingress 不可达、证书错误或上游 5xx 时，插件沿用既有发件箱/恢复逻辑，调用方得到可重试的服务不可用错误；不会切换到 HTTP 或 loopback 备用地址。
- 401/403/409 仍由同步服务按一次性 ticket 和短期 plugin token 决定；反向代理不添加、修改或记录凭据。
- API 只对 `/classroom-api/` 提供代理；不代理 BAMS 工作台、FinColab `40002`、MinIO 管理端口或对象存储端口。
- BAMS 运维必须在实际部署前确认 ingress 到课堂服务的受控私有上游；该配置值不会写入 Git 的默认配置，也不能替换为 `127.0.0.1:18080`（该地址只对课堂服务主机本身有效）。

## 发布与回滚

学生候选镜像继续使用不可变 JupyterLab 4 / Jupyter Server 2 基础镜像和 `--platform linux/amd64` 构建；导出脚本在 `docker save` 前检查 `linux/amd64`。候选前端构建同样要求 `docker buildx build --platform linux/amd64`，再在服务器导入或拉取前检查 manifest 平台，防止 `no match for platform in manifest`。

候选阶段仅允许：BAMS 测试模板指向新学生镜像 digest、候选网页运行在 `5180`、同步服务保持现有数据卷。若验收失败，停止向测试模板创建新工作台、将该模板指回已记录的旧 digest，并恢复候选前端旧镜像；不删除方案、简报、证据、数据库卷、MinIO bucket 或正式 `5179` 容器。

## 验收标准

1. 静态测试证明 BAMS ingress 只代理 `/classroom-api/`，保留认证头、不记录敏感字段、限制上传并为 SSE 关闭缓冲。
2. 插件配置测试接受带 `/classroom-api` 前缀的 HTTPS URL，拒绝公网 HTTP 和跨主机 loopback。
3. 代理契约测试证明四个 plugin API 请求在剥离前缀后到达既有 `/v1/classroom/plugin/...` 路由，且 Authorization 不泄露到日志。
4. 学生镜像发布测试证明构建命令和导出制品均为 `linux/amd64`；非 AMD64 制品在导出/发布前失败。
5. 在取得单独部署授权后，候选环境以真实教师和学生登录态完成：教师发布、学生注册/上传/提交、教师读取简报；未完成这些步骤前，状态只能是“实现完成、生产验收未完成”。

## 实施停止点

代码、测试、镜像候选包和运维文档全部通过本地验证后停止。真实 BAMS 配置、镜像上传、模板替换、候选容器替换与正式发布进入新的部署授权门槛；执行前需列明目标入口、私有上游、证书状态、候选/旧 digest、备份、回滚命令和停止条件。
