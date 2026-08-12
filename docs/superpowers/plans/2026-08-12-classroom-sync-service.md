# 课堂监控同步服务实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建一个可信共享服务，完成方案版本、学生任务、一次性票据、监控会话、证据上传、15 分钟自动收口、单份简报和教师权限读取。

**Architecture:** 服务采用端口无关的 FastAPI 应用、SQLAlchemy 仓储、PostgreSQL 持久化和私有 S3/MinIO 对象存储。所有写操作使用数据库约束和幂等键；自动收口由数据库租约驱动的 worker 执行，服务重启可恢复；FinColab Bearer Token 经上游只读身份接口验证，插件使用单独的会话令牌。

**Tech Stack:** Python 3.10+、FastAPI、Pydantic 2、SQLAlchemy 2、Alembic、PostgreSQL 16、boto3、httpx、PyJWT、argon2-cffi、ruff、mypy、pytest、testcontainers。

## Global Constraints

- 共享契约写入仓库根目录 `contracts/classroom/v1/`，Profile v2 内容与现有 `myextension/api_schemas/profile-version-v2.json` 保持一致。
- 票据明文不得落库或写日志；只保存 SHA-256 哈希，60 秒有效、单次消费。
- 插件会话令牌最长 30 分钟并可刷新，只允许操作一个 `monitor_session_id`。
- 任何教师/学生 Web API 都必须从上游身份解析主体，不能信任请求体中的角色或用户名。
- 证据正文不进入 PostgreSQL；数据库只存清单、哈希、字节数、时间范围和对象键。
- 自动收口截止时间固定为实际下课时间加 15 分钟；调度状态必须在数据库持久化。
- AI 不属于同步服务提交事务的必需依赖。

---

## File Map

```text
services/classroom-sync/
  pyproject.toml                    # 依赖、ruff、mypy、pytest 配置
  alembic.ini
  src/classroom_sync/
    main.py                         # FastAPI 工厂和生命周期
    config.py                       # 环境配置与启动校验
    db.py                           # engine、session factory、事务依赖
    errors.py                       # 稳定错误码与异常映射
    auth/fincolab.py                # 上游身份和成员校验
    auth/plugin_tokens.py           # 票据哈希与插件 JWT
    domain/enums.py                 # 契约枚举
    domain/schemas.py               # Pydantic 请求/响应
    models.py                       # SQLAlchemy 表和约束
    repositories.py                 # 查询与幂等持久化
    storage.py                      # S3 put/head/presign 抽象
    services/plans.py               # 发布、版本、实验绑定
    services/assignments.py         # 学生任务同步、接受、票据
    services/sessions.py            # 注册、心跳、证据、提交
    services/briefs.py              # 确定性简报与教师复核
    services/deadlines.py           # 数据库租约和自动收口
    routers/plans.py
    routers/student.py
    routers/plugin.py
    routers/teacher.py
    worker.py                       # 独立截止任务进程
  migrations/versions/0001_classroom_core.py
  tests/
    conftest.py
    contract/test_schemas.py
    unit/test_plans.py
    unit/test_assignments.py
    unit/test_ticket_tokens.py
    unit/test_deadlines.py
    integration/test_classroom_flow.py
    integration/test_authorization.py
    integration/test_evidence_storage.py
contracts/classroom/v1/*.schema.json
scripts/generate_classroom_types.py
```

### Task 1: 服务骨架、配置和健康检查

**Files:**
- Create: `services/classroom-sync/pyproject.toml`
- Create: `services/classroom-sync/src/classroom_sync/config.py`
- Create: `services/classroom-sync/src/classroom_sync/main.py`
- Create: `services/classroom-sync/tests/test_health.py`

**Interfaces:**
- Produces: `Settings.from_env() -> Settings`；`create_app(settings: Settings) -> FastAPI`；`GET /health/live` 和 `GET /health/ready`。
- Consumes: Python 3.10+；不读取仓库根应用的隐式全局环境。

- [ ] **Step 1: 写失败测试**

```python
def test_ready_rejects_missing_database(client):
    response = client.get('/health/ready')
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'dependency_unavailable'
```

- [ ] **Step 2: 验证测试先失败**

Run: `cd services/classroom-sync && python -m pytest tests/test_health.py -q`

Expected: FAIL，因为 `classroom_sync.main` 尚不存在。

- [ ] **Step 3: 实现最小应用和严格配置**

`Settings` 明确定义 `database_url`、`s3_endpoint_url`、`s3_bucket`、`s3_access_key`、`s3_secret_key`、`fincolab_base_url`、`plugin_jwt_secret`、`feature_enabled`。生产模式缺少任一 Secret 时启动失败；测试通过依赖注入假连接，不读取真实密钥。

- [ ] **Step 4: 运行骨架门禁**

Run: `python -m ruff check . && python -m mypy src && python -m pytest tests/test_health.py -q`

Expected: 全部退出 0，ready 在依赖失败时返回 503、依赖成功时返回 200。

- [ ] **Step 5: 提交**

```bash
git add services/classroom-sync
git commit -m "feat: scaffold classroom sync service"
```

### Task 2: 冻结共享 JSON Schema 与生成类型

**Files:**
- Create: `contracts/classroom/v1/*.schema.json`
- Create: `scripts/generate_classroom_types.py`
- Create: `services/classroom-sync/src/classroom_sync/domain/enums.py`
- Create: `services/classroom-sync/src/classroom_sync/domain/schemas.py`
- Create: `services/classroom-sync/tests/contract/test_schemas.py`

**Interfaces:**
- Produces: `PlanVersionV1`、`StudentAssignmentV1`、`MonitorSessionV1`、`EvidenceChunkManifestV1`、`StudentBriefV1`、`TeacherReviewV1`。
- Produces enums: `AssignmentStatus`、`SessionStatus`、`SubmissionReason`、`MasteryStatus`，取值与总计划完全一致。
- Consumes: `myextension/api_schemas/profile-version-v2.json` 作为 `PlanVersionV1.profile` 的唯一执行结构。

- [ ] **Step 1: 写契约失败测试**

```python
def test_student_brief_rejects_unknown_mastery_status(schema_registry):
    payload = valid_student_brief()
    payload['knowledge_points'][0]['status'] = 'failed'
    with pytest.raises(jsonschema.ValidationError):
        schema_registry.validate('student-brief', payload)
```

- [ ] **Step 2: 运行并确认缺少 Schema**

Run: `python -m pytest tests/contract/test_schemas.py -q`

Expected: FAIL，错误指出 `student-brief.schema.json` 不存在。

- [ ] **Step 3: 写八个关闭对象 Schema**

所有对象使用 `additionalProperties: false`。简报知识点项固定包含：

```json
{
  "knowledge_point_id": "kp-dict",
  "name": "字典数据结构",
  "status": "partial",
  "evidence_refs": ["chunk-3#event-18"],
  "demonstrated": "能够创建和读取字典",
  "gap": "未证明空键处理",
  "teacher_suggestion": "查看失败测试并追问边界输入"
}
```

- [ ] **Step 4: 生成并校验 Python 类型**

Run: `python scripts/generate_classroom_types.py --check && python -m pytest tests/contract/test_schemas.py -q`

Expected: 生成结果无差异；合法样本通过，未知字段、未知枚举、错误哈希和缺失证据引用均失败。

- [ ] **Step 5: 提交**

```bash
git add contracts scripts/generate_classroom_types.py services/classroom-sync
git commit -m "feat: define classroom platform contracts"
```

### Task 3: 数据模型与可逆迁移

**Files:**
- Create: `services/classroom-sync/src/classroom_sync/db.py`
- Create: `services/classroom-sync/src/classroom_sync/models.py`
- Create: `services/classroom-sync/migrations/versions/0001_classroom_core.py`
- Create: `services/classroom-sync/tests/integration/test_migrations.py`

**Interfaces:**
- Produces tables: `plan_drafts`、`plan_versions`、`experiment_plan_bindings`、`student_assignments`、`classroom_tickets`、`monitor_sessions`、`evidence_chunks`、`student_briefs`、`teacher_reviews`、`classroom_deadline_jobs`、`audit_events`。
- Produces unique keys: `(profile_id, version)`、`(space_id, parent_algorithm_id, student_id, child_algorithm_id)`、`(assignment_id, active_slot)`、`(session_id, sequence)`、`(session_id, revision)`。

- [ ] **Step 1: 写迁移往返测试**

测试执行 `upgrade head -> downgrade base -> upgrade head`，并断言重复学生任务与重复证据序号触发唯一约束。

- [ ] **Step 2: 运行并确认迁移缺失**

Run: `python -m pytest tests/integration/test_migrations.py -q`

Expected: FAIL，因为 Alembic revision 不存在。

- [ ] **Step 3: 实现模型和迁移**

`monitor_sessions` 保存 `scheduled_end_at`、`actual_end_at`、`evidence_cutoff_at`、`last_activity_at`、`last_heartbeat_at`、`submission_reason`、`missing_ranges`、`completeness`；票据表只保存 `ticket_hash`、绑定外键、`expires_at`、`consumed_at`。

- [ ] **Step 4: 验证迁移与约束**

Run: `python -m alembic upgrade head && python -m pytest tests/integration/test_migrations.py -q`

Expected: 往返迁移通过，唯一约束和外键测试通过。

- [ ] **Step 5: 提交**

```bash
git add services/classroom-sync
git commit -m "feat: persist classroom workflow state"
```

### Task 4: FinColab 身份与课程权限适配器

**Files:**
- Create: `services/classroom-sync/src/classroom_sync/auth/fincolab.py`
- Create: `services/classroom-sync/src/classroom_sync/errors.py`
- Create: `services/classroom-sync/tests/integration/test_authorization.py`

**Interfaces:**
- Produces: `resolve_principal(bearer: str) -> Principal`；`require_teacher_owner(principal, space_id, experiment_id)`；`require_student_member(principal, space_id)`。
- Upstream calls: `GET /v1/user/info`、`GET /v1/organizations/{org_id}/spaces/{space_id}/users?limit=100&page=N`、`GET /v1/spaces/{space_id}/algorithm_development/{experiment_id}` 与分页 `GET /v1/spaces/{space_id}/algorithm_development`，转发当前 Bearer Token。
- Ownership: 教师发布前必须验证 parent experiment 的 `username` 与可信 principal 一致；若真实接口不返回 owner、owner 不一致或字段语义无法由只读样本证明，默认拒绝并将平台后端补充可信归属接口列为阻塞项。
- Roster: 服务端根据空间成员、child experiment 的 `[FINCOLAB_PARENT_PROJECT_ID:<parent_id>]` 标记及 workbench 的 `user_id/username` 建立任务；缺 parent、重复 child 或身份不一致时隔离记录，不自动猜测。
- Security: 分页直到 `total_page`；找不到成员、上游结果不完整或角色不确定时默认拒绝。

- [ ] **Step 1: 写越权和分页失败测试**

覆盖学生伪造 `role=teacher`、教师访问他人实验、owner 字段缺失、child 重复、第二页成员、上游 401、分页不完整七种情况。

- [ ] **Step 2: 确认测试失败**

Run: `python -m pytest tests/integration/test_authorization.py -q`

Expected: FAIL，因为 `FincolabIdentityGateway` 不存在。

- [ ] **Step 3: 实现默认拒绝的网关**

缓存仅用于同一 token 哈希的短期只读身份结果，TTL 30 秒；日志只记录主体 ID 和拒绝原因，不记录 Bearer Token。

- [ ] **Step 4: 验证权限矩阵**

Run: `python -m pytest tests/integration/test_authorization.py -q`

Expected: 所有越权返回 403，上游认证失败映射 401，上游不可用映射可重试 503。

- [ ] **Step 5: 提交**

```bash
git add services/classroom-sync
git commit -m "feat: verify fincolab classroom identities"
```

### Task 5: 方案发布、版本固定与任务同步

**Files:**
- Create: `services/classroom-sync/src/classroom_sync/repositories.py`
- Create: `services/classroom-sync/src/classroom_sync/services/plans.py`
- Create: `services/classroom-sync/src/classroom_sync/services/assignments.py`
- Create: `services/classroom-sync/src/classroom_sync/routers/plans.py`
- Create: `services/classroom-sync/src/classroom_sync/routers/student.py`
- Create: `services/classroom-sync/tests/integration/test_plan_assignment_flow.py`

**Interfaces:**
- Produces: `publish_plan(draft_id, actor) -> PlanVersionV1`；`sync_assignments(binding_id, actor) -> SyncResult`（内部调用可信 FinColab catalog/roster gateway）；`accept_assignment(id, student) -> StudentAssignmentV1`。
- Rule: 发布计算 canonical JSON SHA-256；已开始 assignment 不切版本，未开始 assignment 切换到最新版本；重复同步返回相同任务 ID。

- [ ] **Step 1: 写版本与幂等失败测试**

```python
def test_republish_moves_only_unstarted_assignments(api, teacher, roster):
    v1 = api.publish(profile_v1, teacher)
    assignments = api.sync(v1, roster)
    api.accept(assignments[0], roster[0])
    v2 = api.publish(profile_v2, teacher)
    result = api.sync(v2, roster)
    assert result[0].plan_version == 1
    assert result[1].plan_version == 2
```

- [ ] **Step 2: 确认测试失败**

Run: `python -m pytest tests/integration/test_plan_assignment_flow.py -q`

Expected: FAIL，因为发布和同步服务不存在。

- [ ] **Step 3: 实现事务边界**

发布、绑定和任务同步各自使用单事务；重复请求依赖唯一约束后重新读取稳定结果。Profile v2 先经 JSON Schema 校验，再计算哈希并写不可变版本。

- [ ] **Step 4: 验证正常与冲突路径**

Run: `python -m pytest tests/unit/test_plans.py tests/unit/test_assignments.py tests/integration/test_plan_assignment_flow.py -q`

Expected: 发布幂等、版本固定、后来加入学生补建、重复 roster 和权限拒绝全部通过。

- [ ] **Step 5: 提交**

```bash
git add services/classroom-sync
git commit -m "feat: publish plans and assign students"
```

### Task 6: 一次性票据、插件会话与证据对象存储

**Files:**
- Create: `services/classroom-sync/src/classroom_sync/auth/plugin_tokens.py`
- Create: `services/classroom-sync/src/classroom_sync/storage.py`
- Create: `services/classroom-sync/src/classroom_sync/services/sessions.py`
- Create: `services/classroom-sync/src/classroom_sync/routers/plugin.py`
- Create: `services/classroom-sync/tests/unit/test_ticket_tokens.py`
- Create: `services/classroom-sync/tests/integration/test_evidence_storage.py`

**Interfaces:**
- Produces: `issue_ticket(assignment_id) -> plaintext_ticket`；`register(ticket, plugin_instance_id) -> SessionCredentials`；`put_evidence_chunk(session_id, sequence, sha256, body) -> EvidenceReceipt`。
- Object key: `classrooms/{classroom_id}/sessions/{session_id}/chunks/{sequence:08d}-{sha256}.json.gz`。
- Chunk limit: compressed 2 MiB, uncompressed 10 MiB；SHA-256 按上传字节校验；相同序号/相同哈希幂等成功，相同序号/不同哈希返回 409。

- [ ] **Step 1: 写票据重放和证据冲突测试**

覆盖过期票据、第二次消费、绑定错误、重复相同块、重复不同块和越界压缩包。

- [ ] **Step 2: 运行并确认失败**

Run: `python -m pytest tests/unit/test_ticket_tokens.py tests/integration/test_evidence_storage.py -q`

Expected: FAIL，因为票据和存储接口不存在。

- [ ] **Step 3: 实现票据与私有存储**

票据使用 32 字节随机值；数据库事务原子设置 `consumed_at`。插件 JWT 的 `sub` 是 session ID，audience 固定 `classroom-plugin-v1`。对象上传先校验边界和哈希，再写私有对象并保存索引。

- [ ] **Step 4: 验证重放、幂等和存储失败**

Run: `python -m pytest tests/unit/test_ticket_tokens.py tests/integration/test_evidence_storage.py -q`

Expected: 重放拒绝；重复相同块返回原 receipt；S3 暂时失败不写数据库完成记录。

- [ ] **Step 5: 提交**

```bash
git add services/classroom-sync
git commit -m "feat: register plugin sessions and store evidence"
```

### Task 7: 单份简报、教师复核和证据读取

**Files:**
- Create: `services/classroom-sync/src/classroom_sync/services/briefs.py`
- Create: `services/classroom-sync/src/classroom_sync/routers/teacher.py`
- Create: `services/classroom-sync/tests/integration/test_briefs.py`

**Interfaces:**
- Produces: `submit_brief(session_id, brief, manifest, reason) -> StudentBriefV1`；`review_brief(session_id, patch, teacher) -> TeacherReviewV1`；`presign_evidence(object_key, teacher) -> PresignedEvidence`。
- Rule: 同一会话只有一个逻辑简报，补传创建递增 revision；不改变首次 `submitted_at` 和 `evidence_cutoff_at`。

- [ ] **Step 1: 写单份简报与审计失败测试**

覆盖重复提交、补传修订、AI 字段缺失、教师改结论、跨课程查看和短期证据 URL。

- [ ] **Step 2: 确认测试失败**

Run: `python -m pytest tests/integration/test_briefs.py -q`

Expected: FAIL，因为 brief service 不存在。

- [ ] **Step 3: 实现规则优先简报事务**

简报必须通过共享 Schema；每个知识点至少有一个 `evidence_ref`，`not_demonstrated` 可引用完整度/缺失范围证据。教师复核不覆盖机器结果，单独保存 overlay 和理由。

- [ ] **Step 4: 验证读取权限和修订语义**

Run: `python -m pytest tests/integration/test_briefs.py tests/integration/test_authorization.py -q`

Expected: 单份逻辑简报、修订历史、审计、权限和私有证据读取全部通过。

- [ ] **Step 5: 提交**

```bash
git add services/classroom-sync
git commit -m "feat: persist classroom briefs and reviews"
```

### Task 8: 持久化截止任务与自动收口

**Files:**
- Create: `services/classroom-sync/src/classroom_sync/services/deadlines.py`
- Create: `services/classroom-sync/src/classroom_sync/worker.py`
- Create: `services/classroom-sync/tests/unit/test_deadlines.py`
- Create: `services/classroom-sync/tests/integration/test_worker_recovery.py`

**Interfaces:**
- Produces: `claim_due_jobs(worker_id, now, lease_seconds=60) -> list[DeadlineJob]`；`close_session(session_id, reason='system_deadline')`。
- Rule: `actual_end_at` 初始等于 `scheduled_end_at`，教师提前下课时只允许改为更早值；`evidence_cutoff_at = actual_end_at + 15 minutes`。在线插件可在截止前主动提交，截止后 worker 必须把会话推进到 `completed` 或 `partial`。

- [ ] **Step 1: 写时间边界和重启恢复测试**

使用注入时钟验证夏令时无关的 UTC、教师提前下课、两个 worker 竞争租约、worker 崩溃后租约重领和重复执行。

- [ ] **Step 2: 确认测试失败**

Run: `python -m pytest tests/unit/test_deadlines.py tests/integration/test_worker_recovery.py -q`

Expected: FAIL，因为 deadline worker 不存在。

- [ ] **Step 3: 实现数据库租约 worker**

使用 `SELECT ... FOR UPDATE SKIP LOCKED` 领取任务；任务处理幂等。已有最终简报则直接完成任务；只有部分证据时生成 `partial` 基础简报并写明缺失范围，不调用外部 AI。

- [ ] **Step 4: 验证自动收口终态**

Run: `python -m pytest tests/unit/test_deadlines.py tests/integration/test_worker_recovery.py -q`

Expected: 服务/worker 重启和并发竞争不漏交、不重复生成逻辑简报。

- [ ] **Step 5: 提交**

```bash
git add services/classroom-sync
git commit -m "feat: close classroom sessions after deadline"
```

### Task 9: OpenAPI、容器和服务总门禁

**Files:**
- Create: `services/classroom-sync/Dockerfile`
- Create: `services/classroom-sync/docker-compose.test.yml`
- Create: `services/classroom-sync/openapi.json`
- Create: `services/classroom-sync/README.md`
- Create: `.github/workflows/classroom-sync.yml`

**Interfaces:**
- Produces: API 容器和 worker 容器使用同一不可变镜像；`openapi.json` 是前端/插件生成客户端的固定输入。
- Health: API `/health/ready` 同时验证数据库和对象存储；worker 暴露进程级健康状态但不开放业务 API。

- [ ] **Step 1: 写 OpenAPI 快照和容器 smoke 测试**

断言所有设计接口存在、学生/教师/插件安全 scheme 分离，Docker Compose 启动后迁移只执行一次。

- [ ] **Step 2: 运行并确认产物缺失**

Run: `python -m pytest tests/contract/test_openapi.py -q`

Expected: FAIL，因为 OpenAPI 快照和容器不存在。

- [ ] **Step 3: 实现最小运行与文档**

README 精确列出环境变量、迁移命令、API/worker 启动命令、Secret 规则、对象桶私有策略、备份和非破坏回滚。

- [ ] **Step 4: 执行完整门禁**

```bash
python -m ruff check .
python -m mypy src
python -m pytest -q
python -m alembic upgrade head
python -m alembic downgrade -1
python -m alembic upgrade head
docker compose -f docker-compose.test.yml up -d --wait
curl -fsS http://127.0.0.1:8088/health/ready
docker compose -f docker-compose.test.yml down
```

Expected: 所有命令退出 0，无未提交的 OpenAPI 生成差异。

- [ ] **Step 5: 提交**

```bash
git add services/classroom-sync .github/workflows/classroom-sync.yml
git commit -m "build: package classroom sync service"
```
