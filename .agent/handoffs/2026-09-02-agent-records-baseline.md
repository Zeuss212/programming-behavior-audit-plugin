# 交接：课堂同步服务协作文档基线

- 日期：2026-09-02
- 分支：当前工作分支
- 基线提交：建立记录时的 HEAD
- 负责人：Codex
- 状态：已完成，待后续功能变更使用

## 已完成

- 建立 `.agent` 状态、架构、接口、运行、开发、Git、产品、ADR 与交接模板文档。
- 记录本地课堂栈端口：sync API 为 `127.0.0.1:18080`，课堂代理为 `127.0.0.1:18081`。

## 未完成

- 独立评价维度与行为监测配置的后端持久化契约尚未实现。

## 关键文件

- `deploy/classroom/local-demo/docker-compose.yml`
- `services/classroom-sync/src/classroom_sync/`
- `docs/runbooks/bams-classroom-api-ingress.md`

## 验证结果

- 本次仅新增文档，未启动 Docker、未访问真实数据、未执行数据库迁移或部署。

## 风险与下一步

- 本机无 Docker 时不能验证本地课堂服务。
- 评价配置改造前先与前端确认 `assessment-config` 契约及发布时机。
