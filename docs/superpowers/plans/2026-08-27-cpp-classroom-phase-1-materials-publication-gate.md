# C++ Classroom Phase 1 Materials and Publication Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不运行学生代码、不生成学生掌握结论的前提下，完成 C++ 课堂第一阶段闭环：真实材料只读接入、profile v3、服务端编写会话与一次 AI 建议、可恢复草稿、确定性发布门禁，以及教师四步配置页面。

**Architecture:** `classroom-sync` 通过受限的 `AssessmentMaterialGateway` 读取 FinColab 父实验已经结构化并带哈希的材料；浏览器只消费安全投影。`PlanAuthoringSession` 在数据库中约束同一教师/父实验只有一个开放编写会话，并把一次 AI 建议、正式草稿和最终发布绑定到同一会话。profile v3 与现有 Python profile v2 并存；草稿允许不完整，但发布前由服务端使用最新材料重新计算 `PublicationGate`。Vue 端在独立功能开关下保留旧三步向导，并新增基于真实材料的四步 C++ 向导。

**Tech Stack:** Python 3.10+、FastAPI、Pydantic 2、SQLAlchemy 2、Alembic、JSON Schema 2020-12、httpx、pytest、Vue 3、TypeScript 6、Vitest、Vite 8、现有 FinColab 本地演示 Compose。

## Global Constraints

- 后端工作树：`/Users/sxh/编程行为监控分析插件_交付版_20260727/.worktrees/classroom-ai-integration-ready`，分支 `codex/classroom-ai-integration-ready`，设计基线提交 `243acc4`。
- 前端工作树：`/Users/sxh/编程行为监控分析插件_交付版_20260727/.worktrees/lab-platform-frontend-classroom-ui`，分支 `codex/classroom-ui`，设计基线提交 `f19d2c7`。
- 前端当前已有未提交修改，且与本阶段会修改的 `PlanWizard.vue`、`api.ts`、`plan-draft.ts`、`types.ts` 和测试重叠。执行到前端任务前必须由用户确认这些修改已提交到安全分支，或逐文件完成意图对照与合并；禁止自动 stash、reset、checkout 或覆盖。
- 只实现阶段一。禁止实现学生 C++ 运行器、`assessment_run` 事件、掌握状态机、brief v2、课堂掌握列表或教师复核新逻辑。
- 浏览器不得解析 TXT/C++、不得构造空测试、不得伪造哈希、不得计算发布门禁；`classroom-sync` Web 进程不得编译或执行材料。原始材料解析和保护区预检只在离线、固定参数的材料导入器中发生。
- 保留 profile v2、旧建议接口和旧三步向导；新 profile v3 流程默认关闭，仅本地演示显式打开。回滚只需关闭 `VITE_CPP_TRUSTED_ASSESSMENT_ENABLED`，不删除新表或历史审计。
- 不对真实环境执行迁移、部署、推送或数据库修复。本计划只允许在测试数据库和 `classroom-local-demo` 命名环境中验证。
- AI 只返回课堂标题、知识点名称、测评维度文字和已存在的 `material_requirement_id`；AI 不创建测试、不改源码、不新增材料要求、不自动发布。
- 阶段停止点：顺序表真实材料稳定显示发布阻塞；链表原始维度问题稳定显示，教师改为“尾插 + 逆置”后可以发布 profile v3；全程不创建学生分配、不运行学生代码。

---

## Contract Freeze

### Profile v3

profile v3 使用以下稳定字段。下面是使用第一条真实链表测试的合法草稿（尚未完成全部知识点，因此确认值为 `null`）；草稿 schema 允许空数组和 `null` 确认值，发布 schema 要求至少一个知识点、测试、维度，且所有确认哈希为 64 位小写十六进制字符串。

```json
{
  "schema_version": 3,
  "problem_id": "linked-list-reverse",
  "title": "链表尾插与逆置",
  "problem_context": {
    "statement": "完善带头结点的单链表类定义，完成尾插与链表倒置。",
    "language": "cpp",
    "submission_contract": {"kind": "stdin_stdout"},
    "toolchain_profile": "cpp17_stdio_v1",
    "entry_file": "链表操作练习02.cpp",
    "source_encoding": "utf-8"
  },
  "starter_source": {
    "artifact_id": "ART_LINKED_LIST_CPP_01",
    "display_name": "链表操作练习02.cpp",
    "file_name": "链表操作练习02.cpp",
    "sha256": "6468a373fbf602c7c8d5012fca2a5ead4973bb8daf54714c7ef22d84bbd68cb0",
    "size_bytes": 1788,
    "source": "fincolab_experiment"
  },
  "knowledge_points": [
    {
      "id": "KP_LINKTAL1",
      "material_requirement_id": "REQ_LINK_TAIL_INSERT",
      "name": "链表尾插",
      "description": "能够把输入元素依次插入链表尾部，并使倒置前输出与输入顺序一致。",
      "source": "teacher",
      "order": 0
    }
  ],
  "assessment_tests": [
    {
      "id": "TEST_LINK0001",
      "name": "六元素链表逆置",
      "knowledge_point_ids": ["KP_LINKTAL1"],
      "criterion_ids": ["CRIT_LINKTAL1"],
      "kind": "stdin_stdout",
      "input": "6\n1 2 3 4 5 6\n",
      "expected_stdout": "倒置前为：1 2 3 4 5 6 \n倒置后为：6 5 4 3 2 1\n",
      "comparison": "normalized_text_v1",
      "timeout_ms": 2000,
      "enabled": true,
      "source": "teacher",
      "order": 0,
      "content_hash": "be72985ec292c437094bcd6f84478aff76623db18ca87645dfa4a70e26755b5e"
    }
  ],
  "dimensions": [
    {
      "knowledge_point_id": "KP_LINKTAL1",
      "name": "链表尾插",
      "question": "能够把输入元素依次插入链表尾部，并使倒置前输出与输入顺序一致。",
      "evidence_criteria": [
        {
          "id": "CRIT_LINKTAL1",
          "material_requirement_id": "REQ_LINK_TAIL_INSERT",
          "statement": "倒置前输出与输入顺序一致。",
          "required": true
        }
      ],
      "verification_bindings": [
        {
          "criterion_id": "CRIT_LINKTAL1",
          "kind": "assessment_test",
          "assessment_test_id": "TEST_LINK0001"
        }
      ],
      "analysis_config": {"mode": "evidence_binding"}
    }
  ],
  "confirmations": {
    "material_bundle_hash": null,
    "starter_source_hash": null,
    "knowledge_points_hash": null,
    "dimensions_hash": null,
    "tests_hash": null
  }
}
```

`size_bytes` 和各 `content_hash` 必须由真实夹具计算。上例的 `1788` 与测试哈希已经按 2026-08-27 原始材料计算；实施测试必须复算而不是信任文档。哈希定义固定为：

```python
starter_source_hash = sha256_json(starter_source)
knowledge_points_hash = sha256_json({"knowledge_points": knowledge_points})
dimensions_hash = sha256_json({
    "knowledge_points_hash": knowledge_points_hash,
    "dimensions": dimensions,
})
tests_hash = sha256_json({"assessment_tests": assessment_tests_without_content_hash})
```

每个测试自身的 `content_hash` 是该测试去掉 `content_hash` 后的 canonical JSON SHA-256。`material_bundle_hash` 由材料接口返回并在发布时与最新材料重比。

### Assessment materials response

`GET /v1/classroom/experiments/{space_id}/{parent_algorithm_id}/assessment-materials` 只返回元数据和有界诊断，不返回源码正文：

```python
class AssessmentMaterialBundle(BaseModel):
    schema_version: Literal[1]
    space_id: str
    parent_algorithm_id: str
    title: str
    statement: str
    starter_source: StarterSourceCandidate | None
    requirements: tuple[MaterialRequirement, ...]
    assessment_tests: tuple[MaterialAssessmentTest, ...]
    detector_profiles: tuple[DetectorProfileAvailability, ...]
    issues: tuple[MaterialIssue, ...]
    bundle_hash: str

class MaterialRequirement(BaseModel):
    id: str
    name: str
    source_statement: str
    student_responsibility: bool
    test_ids: tuple[str, ...]
    detector_profile_ids: tuple[str, ...]

class MaterialIssue(BaseModel):
    code: Literal[
        "starter_source_non_utf8_confirmation_required",
        "starter_source_protected_compile_error",
        "detector_profile_unavailable",
        "teacher_dimension_not_student_responsibility",
        "teacher_dimension_outside_task",
        "required_student_dimension_missing",
        "boundary_coverage_incomplete",
    ]
    severity: Literal["blocking", "warning"]
    scope: Literal["classroom", "source", "requirement", "test"]
    requirement_id: str | None
    message: str
```

材料 issue 分两类处理。源编码未确认和保护区编译错误是无条件阻塞；检测器不可用只在教师选中的要求依赖该检测器时阻塞。`teacher_dimension_not_student_responsibility`、`teacher_dimension_outside_task`、`required_student_dimension_missing` 描述原始 TXT 与源码任务的差异，配置页必须展示，但发布门禁要针对当前 profile 重新计算；教师删除遍历/删除并补上逆置后，这三项不得继续无条件阻塞。`boundary_coverage_incomplete` 始终只是警告。

### Publication gate response

```json
{
  "status": "blocked",
  "blocking_count": 2,
  "warning_count": 1,
  "issues": [
    {
      "code": "starter_source_protected_compile_error",
      "scope": "source",
      "knowledge_point_id": null,
      "requirement_id": null,
      "message": "受保护 main 使用未声明标识符 value。"
    }
  ]
}
```

稳定发布门禁码另包括：`material_bundle_changed`、`starter_source_mismatch`、`stale_profile_confirmation`、`unknown_material_requirement`、`requirement_not_student_responsibility`、`missing_required_requirement`、`unknown_test_reference`、`criterion_binding_missing`、`detector_binding_unavailable`。只有 `status == "ready"` 才允许发布。

### Authoring-session response

```json
{
  "authoring_session_id": "uuid",
  "status": "open",
  "space_id": "course-001",
  "parent_algorithm_id": "linked-list-experiment-002",
  "draft": null,
  "suggestion": {
    "status": "not_requested",
    "job_id": null,
    "input_hash": null,
    "suggestion": null,
    "failure_code": null
  }
}
```

终态失败仍占用本编写会话的一次建议额度；返回手工编辑状态，不提供第二个 POST 成功路径。

---

### Task 1: Preserve and prove the four real teacher materials

**Files:**

- Create: `deploy/classroom/local-demo/materials/sequence-list/顺序表操作练习01.cpp`
- Create: `deploy/classroom/local-demo/materials/sequence-list/编码习题1-线性表的基本操作(1).txt`
- Create: `deploy/classroom/local-demo/materials/linked-list/链表操作练习02.cpp`
- Create: `deploy/classroom/local-demo/materials/linked-list/编码习题2-链表的逆置操作.txt`
- Create: `deploy/classroom/local-demo/materials/source-manifest.json`
- Test: `scripts/__tests__/test_cpp_assessment_material_fixtures.py`

- [ ] **Step 1: Verify source files still match the approved hashes before copying**

Run each command separately from the backend root:

```bash
shasum -a 256 '/Users/sxh/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_oo3m7d49d1xb11_b736/temp/drag/顺序表操作练习01.cpp'
shasum -a 256 '/Users/sxh/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_oo3m7d49d1xb11_b736/temp/drag/链表操作练习02.cpp'
shasum -a 256 '/Users/sxh/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_oo3m7d49d1xb11_b736/temp/drag/编码习题1-线性表的基本操作(1).txt'
shasum -a 256 '/Users/sxh/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_oo3m7d49d1xb11_b736/temp/drag/编码习题2-链表的逆置操作.txt'
```

Expected, in the same order:

```text
44717d0b2e1a9c2fe829db08e0171b2aa86f82b3e0cd0ab97b41512af190eecc
6468a373fbf602c7c8d5012fca2a5ead4973bb8daf54714c7ef22d84bbd68cb0
2feef3567dd6ce47eda7dda2f14a775fc756684f21de9a8c315ccf91d3bb6cff
78c32919dc9bcbf2cf1477f44faf1cda649f5c2c93aa160788ffa8ad2a2d8afd
```

If any file is missing or differs, stop. Do not substitute a similarly named file.

- [ ] **Step 2: Write a failing immutable-fixture test**

The test reads the four destination files as bytes, asserts the exact hashes, asserts only the sequence source fails UTF-8 decoding, and asserts the TXT files preserve the exact full-width/half-width colons used by expected output.

Run:

```bash
uv run --extra test pytest -q scripts/__tests__/test_cpp_assessment_material_fixtures.py
```

Expected: FAIL because the destination assets and manifest do not exist.

- [ ] **Step 3: Copy bytes exactly and add provenance metadata**

Use `mkdir -p` and `cp` only after Step 1 succeeds. `source-manifest.json` records original basename, location class `teacher_provided_local_material`, approved SHA-256, detected encoding (`gb18030` or `utf-8`), media type, and `captured_at: "2026-08-27"`. It must not contain the original absolute path, WeChat identifiers, user tokens, or source text.

- [ ] **Step 4: Re-run the immutable-fixture test**

Expected: PASS; the sequence source remains byte-for-byte GB18030 and is not silently converted.

- [ ] **Step 5: Commit only the immutable fixtures and test**

```bash
git add deploy/classroom/local-demo/materials scripts/__tests__/test_cpp_assessment_material_fixtures.py
git commit -m "test: preserve real C++ assessment materials"
```

### Task 2: Add profile v3 JSON contracts without changing profile v2

**Files:**

- Create: `myextension/api_schemas/profile-draft-v3.json`
- Create: `myextension/api_schemas/profile-version-v3.json`
- Modify: `contracts/classroom/v1/plan-draft.schema.json`
- Modify: `contracts/classroom/v1/plan-version.schema.json`
- Modify: `services/classroom-sync/src/classroom_sync/domain/schemas.py`
- Modify: `services/classroom-sync/tests/contract/test_schemas.py`
- Modify: `myextension/tests/test_schema_registry.py`
- Create: `myextension/tests/profile_v3_fixtures.py`

- [ ] **Step 1: Add RED schema tests for the frozen contract**

Add tests that assert:

- one complete C++ profile v3 validates;
- `language != "cpp"`, unsafe `entry_file`, non-UTF-8 source, `function_call`, unknown comparison mode, timeout outside `100..10000`, malformed SHA, unknown requirement reference, and an extra property each fail;
- profile v2 still validates unchanged;
- plan draft/version accept both v2 and v3 profiles.

Use the exact linked-list inputs and outputs from Task 1 in `profile_v3_fixtures.py`. Calculate hashes with the existing `sha256_json`; never hardcode a fake test hash.

Run:

```bash
uv run --extra test pytest -q myextension/tests/test_schema_registry.py
cd services/classroom-sync
uv run --extra dev pytest -q tests/contract/test_schemas.py
cd ../..
```

Expected: FAIL because profile v3 schemas are absent and plan contracts only reference v2.

- [ ] **Step 2: Implement the closed profile v3 schemas**

Use `additionalProperties: false` at every object level. Safe `entry_file` must match `^[^/\\\\\u0000]{1,120}\\.(?:cpp|cc|cxx)$`. IDs use existing uppercase stable shapes; `material_requirement_id` uses `^REQ_[A-Z0-9_]{1,80}$`; comparison is exactly `normalized_text_v1`; detector bindings allow only `address_undefined_leak_v1` in this phase.

- [ ] **Step 3: Make plan contracts use explicit `oneOf`**

```json
"profile": {
  "oneOf": [
    {"$ref": "https://classroom.local/plugin/api-schemas/profile-draft-v2.json"},
    {"$ref": "https://classroom.local/plugin/api-schemas/profile-draft-v3.json"}
  ]
}
```

Apply the corresponding version schema reference in `plan-version.schema.json`; do not loosen the contract to an untyped object.

- [ ] **Step 4: Register all four profile schemas**

Set:

```python
PROFILE_SCHEMA_FILENAMES = (
    "profile-draft-v2.json",
    "profile-version-v2.json",
    "profile-draft-v3.json",
    "profile-version-v3.json",
)
```

- [ ] **Step 5: Run contract tests and commit**

Expected: PASS with profile v2 regression intact.

```bash
git add myextension/api_schemas contracts/classroom/v1 services/classroom-sync/src/classroom_sync/domain/schemas.py services/classroom-sync/tests/contract/test_schemas.py myextension/tests/test_schema_registry.py myextension/tests/profile_v3_fixtures.py
git commit -m "feat: define C++ assessment profile v3"
```

### Task 3: Add profile v3 semantic validation and immutable storage support

**Files:**

- Modify: `myextension/profile_validator.py`
- Modify: `myextension/dimension_profile_store.py`
- Modify: `myextension/tests/test_assessment_profile.py`

- [ ] **Step 1: Add RED semantic tests**

Cover duplicate IDs, non-continuous order, test-to-knowledge mismatch, test-to-criterion mismatch, missing dimension per knowledge point, duplicate material requirement mapping, missing verification binding, stale confirmation hashes, invalid per-test content hash, and successful v3 publish/reload. Also assert profile v2 normalization remains unchanged.

Run:

```bash
uv run --extra test pytest -q myextension/tests/test_assessment_profile.py -k 'v3 or v2'
```

Expected: new v3 cases FAIL with `unsupported_schema_version`.

- [ ] **Step 2: Generalize non-destructive normalization**

Add `_validate_v3_profile_draft`. Strip teacher-facing prose but restore exact `input` and `expected_stdout` bytes after shape validation. Do not reuse `_normalize_dimensions`, because v3 uses `evidence_binding`, not `llm_evidence`, and must not inject old levels or behavioral exclusions.

Use this dispatch:

```python
if schema_version == 3:
    return _validate_v3_profile_draft(payload)
```

- [ ] **Step 3: Enforce one-to-one material responsibility and bindings**

For every knowledge point, require exactly one dimension and one unique `material_requirement_id`. Every required criterion must be referenced by at least one verification binding; assessment-test bindings must reference an enabled test whose `criterion_ids` and `knowledge_point_ids` contain that criterion and point.

- [ ] **Step 4: Verify canonical hashes**

Implement small pure helpers `assessment_test_content_hash`, `profile_v3_confirmation_hashes`, and `validate_profile_v3_confirmations` in `profile_validator.py`. Drafts accept `null`; any non-null value must match. Publish requires all five values.

- [ ] **Step 5: Extend immutable store keys and publication checks**

Add `_STORED_VERSION_V3_KEYS`; persist `starter_source` plus all v3 fields; choose `profile-version-v3` when reloading. Keep v1/v2 branches byte-for-byte compatible.

- [ ] **Step 6: Run tests and commit**

```bash
uv run --extra test pytest -q myextension/tests/test_assessment_profile.py myextension/tests/test_schema_registry.py
git add myextension/profile_validator.py myextension/dimension_profile_store.py myextension/tests/test_assessment_profile.py
git commit -m "feat: validate immutable C++ assessment profiles"
```

Expected: PASS; old v1/v2 tests remain green.

### Task 4: Build the offline, versioned real-material importer

**Files:**

- Create: `scripts/import_cpp_assessment_materials.py`
- Create: `deploy/classroom/local-demo/materials/sequence-list/import-config.json`
- Create: `deploy/classroom/local-demo/materials/linked-list/import-config.json`
- Create: `deploy/classroom/local-demo/materials/sequence-list/bundle.json`
- Create: `deploy/classroom/local-demo/materials/linked-list/bundle.json`
- Create: `scripts/__tests__/test_import_cpp_assessment_materials.py`

- [ ] **Step 1: Add RED importer tests for both exercises**

The tests call a Python function, not a shell command, and assert:

- the importer parses exactly two stdin/stdout tests from each TXT;
- sequence source is reported as GB18030 with a UTF-8 candidate hash but remains unconfirmed;
- sequence preflight reports protected `main` error `value` and never rewrites it;
- linked-list preflight accepts missing student implementations as configured editable targets;
- linked-list requirements contain tail insertion and reverse;
- traversal is `student_responsibility: false`, deletion is outside task, and reverse is reported missing from the teacher TXT;
- sequence space release requires unavailable `address_undefined_leak_v1`;
- boundary coverage warnings are present; no test is generated.

Run:

```bash
uv run --extra test pytest -q scripts/__tests__/test_import_cpp_assessment_materials.py
```

Expected: FAIL because the importer does not exist.

- [ ] **Step 2: Implement strict, offline-only parsing**

Expose pure functions plus one CLI:

```python
def import_material_bundle(config_path: Path) -> dict[str, object]: ...

def main(argv: Sequence[str] | None = None) -> int: ...
```

The config contains exact approved input hashes, explicit editable symbol names, explicit expected requirement IDs, and fixed `cpp17_stdio_v1`. Reject hash drift, symlinks, files over 256 KiB, path traversal, unexpected encodings, more/fewer than two parsed tests, and unrecognized TXT headings. The parser only recognizes the two approved Chinese heading formats; it must fail closed on format drift.

- [ ] **Step 3: Keep compilation out of the Web service**

The importer may invoke only an allowlisted compiler discovered as `clang++` or `g++`, with argument vector (never shell text):

```python
[compiler, "-std=c++17", "-fsyntax-only", "-Wall", "-Wextra", "-Wpedantic", probe_path]
```

Build the probe in a temporary directory. Editable functions are replaced or supplied from adapter-owned minimal bodies declared by the importer version, not from teacher-controlled compiler flags. Bound runtime to 10 seconds and sanitize diagnostics to `{line, column, code, message}` without absolute paths. If no compiler is present, emit `material_preflight_unavailable` as blocking; never mark the source ready.

- [ ] **Step 4: Generate and verify sealed bundles**

Run:

```bash
uv run --extra test python scripts/import_cpp_assessment_materials.py deploy/classroom/local-demo/materials/sequence-list/import-config.json --output deploy/classroom/local-demo/materials/sequence-list/bundle.json
uv run --extra test python scripts/import_cpp_assessment_materials.py deploy/classroom/local-demo/materials/linked-list/import-config.json --output deploy/classroom/local-demo/materials/linked-list/bundle.json
uv run --extra test pytest -q scripts/__tests__/test_import_cpp_assessment_materials.py scripts/__tests__/test_cpp_assessment_material_fixtures.py
```

Expected: PASS. Review JSON diff to ensure no source body, absolute path, score, generated test, or compiler host path is persisted.

- [ ] **Step 5: Commit importer and sealed bundles**

```bash
git add scripts/import_cpp_assessment_materials.py scripts/__tests__/test_import_cpp_assessment_materials.py deploy/classroom/local-demo/materials
git commit -m "feat: import trusted C++ teacher materials"
```

### Task 5: Add the material gateway and safe teacher route

**Files:**

- Create: `services/classroom-sync/src/classroom_sync/services/assessment_materials.py`
- Create: `services/classroom-sync/src/classroom_sync/auth/fincolab_materials.py`
- Create: `services/classroom-sync/src/classroom_sync/routers/materials.py`
- Modify: `services/classroom-sync/src/classroom_sync/application.py`
- Modify: `services/classroom-sync/src/classroom_sync/runtime.py`
- Modify: `services/classroom-sync/src/classroom_sync/main.py`
- Modify: `services/classroom-sync/src/classroom_sync/config.py`
- Create: `services/classroom-sync/tests/unit/test_assessment_materials.py`
- Create: `services/classroom-sync/tests/integration/test_assessment_materials_route.py`
- Modify: `services/classroom-sync/tests/test_runtime.py`

- [ ] **Step 1: Add RED domain and route tests**

Assert strict Pydantic validation, recomputed `bundle_hash`, bounded messages, no source body in the public projection, teacher ownership before gateway access, 403 for a different owner, 503 stable codes for upstream unavailable/contract invalid, and 200 for both real bundle fixtures.

Run:

```bash
cd services/classroom-sync
uv run --extra dev pytest -q tests/unit/test_assessment_materials.py tests/integration/test_assessment_materials_route.py tests/test_runtime.py
```

Expected: FAIL because the gateway and route are absent.

- [ ] **Step 2: Implement strict material models and projection**

`AssessmentMaterialService.get_bundle(principal, space_id, parent_algorithm_id)` receives a private gateway payload, validates artifact/test hashes, recalculates the public `bundle_hash`, strips private `content_base64` and adapter metadata, and returns an immutable Pydantic model.

- [ ] **Step 3: Implement the FinColab adapter contract**

`FincolabAssessmentMaterialGateway` calls only:

```text
GET /v1/spaces/{space_id}/algorithm_development/{parent_algorithm_id}/assessment_materials
```

It forwards the already-resolved principal bearer, applies the existing 10-second httpx timeout, accepts only schema version 1, caps the response body at 1 MiB, and maps non-success responses to existing stable domain errors. Do not accept a caller-provided URL or download path.

- [ ] **Step 4: Wire the public route and runtime dependency**

Add the router path from the approved spec. Reuse `resolve_bearer_principal` and `require_teacher_owner`. Extend `ClassroomServices` with `assessment_material_service: AssessmentMaterialService | None`; return `assessment_materials_not_configured` when absent in test/minimal applications.

- [ ] **Step 5: Run tests and commit**

```bash
cd services/classroom-sync
uv run --extra dev pytest -q tests/unit/test_assessment_materials.py tests/integration/test_assessment_materials_route.py tests/test_runtime.py
uv run --extra dev ruff check src tests/unit/test_assessment_materials.py tests/integration/test_assessment_materials_route.py
uv run --extra dev mypy src
```

Expected: all PASS.

```bash
git add services/classroom-sync/src/classroom_sync services/classroom-sync/tests/unit/test_assessment_materials.py services/classroom-sync/tests/integration/test_assessment_materials_route.py services/classroom-sync/tests/test_runtime.py
git commit -m "feat: expose verified assessment materials"
```

### Task 6: Add PlanAuthoringSession persistence and migration

**Files:**

- Modify: `services/classroom-sync/src/classroom_sync/models.py`
- Create: `services/classroom-sync/migrations/versions/0008_plan_authoring_sessions.py`
- Modify: `services/classroom-sync/tests/integration/test_migrations.py`

- [ ] **Step 1: Add RED migration tests**

Assert upgrade from `0007_evidence_analysis_manifest`, downgrade back to 0007, one open session per `(teacher_id, space_id, parent_algorithm_id, active_slot)`, one suggestion job per non-null `authoring_session_id`, and one draft per non-null `authoring_session_id`.

Run:

```bash
cd services/classroom-sync
uv run --extra dev pytest -q tests/integration/test_migrations.py
```

Expected: FAIL because revision 0008 and columns are absent.

- [ ] **Step 2: Add the model and nullable compatibility links**

```python
class PlanAuthoringSession(Base):
    __tablename__ = "plan_authoring_sessions"
    __table_args__ = (
        UniqueConstraint(
            "teacher_id", "space_id", "parent_algorithm_id", "active_slot",
            name="uq_plan_authoring_sessions_open",
        ),
    )

    id: Mapped[str]
    teacher_id: Mapped[str]
    space_id: Mapped[str]
    parent_algorithm_id: Mapped[str]
    status: Mapped[str]
    active_slot: Mapped[int | None]
    suggestion_job_id: Mapped[str | None]
    published_plan_id: Mapped[str | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    closed_at: Mapped[datetime | None]
```

Add nullable `authoring_session_id` to `PlanDraft` and `ClassroomPlanSuggestionJob`. Use foreign keys to `plan_authoring_sessions.id` with `ondelete="RESTRICT"`; add separate unique constraints for each non-null link. Keep `PlanAuthoringSession.suggestion_job_id` and `published_plan_id` as nullable, indexed audit links without reverse foreign keys so SQLite migrations do not create a circular DDL dependency; the service verifies both links against the locked job/draft before writing them. Existing rows remain null and valid.

- [ ] **Step 3: Implement reversible migration**

Use batch alteration for SQLite-compatible tests. Do not drop or reinterpret the old request-hash uniqueness constraint. Downgrade removes new foreign keys/columns and then the session table without deleting old job or draft rows.

- [ ] **Step 4: Run migration tests and commit**

```bash
cd services/classroom-sync
uv run --extra dev pytest -q tests/integration/test_migrations.py
git add src/classroom_sync/models.py migrations/versions/0008_plan_authoring_sessions.py tests/integration/test_migrations.py
git commit -m "feat: persist classroom plan authoring sessions"
```

### Task 7: Enforce one AI suggestion per authoring session

**Files:**

- Create: `services/classroom-sync/src/classroom_sync/services/plan_authoring.py`
- Modify: `services/classroom-sync/src/classroom_sync/services/plan_suggestion_jobs.py`
- Modify: `services/classroom-sync/src/classroom_sync/services/plan_suggestions.py`
- Modify: `services/classroom-sync/src/classroom_sync/repositories.py`
- Modify: `services/classroom-sync/tests/unit/test_plan_suggestion_jobs.py`
- Create: `services/classroom-sync/tests/unit/test_plan_authoring.py`

- [ ] **Step 1: Add RED service tests for session boundaries**

Cover create-or-return-open, concurrent insert winner, different teacher isolation, abandon, new session only after close, first suggestion request, duplicate POST with different text returning the original job/input hash, ready result recovery, terminal failure consuming the one attempt, and owner mismatch.

Run:

```bash
cd services/classroom-sync
uv run --extra dev pytest -q tests/unit/test_plan_authoring.py tests/unit/test_plan_suggestion_jobs.py
```

Expected: FAIL because the session service and job link do not exist.

- [ ] **Step 2: Add immutable snapshots and input hashing**

```python
@dataclass(frozen=True)
class PlanAuthoringSnapshot:
    authoring_session_id: str
    status: Literal["open", "published", "abandoned"]
    space_id: str
    parent_algorithm_id: str
    draft_id: str | None
    suggestion: AuthoringSuggestionSnapshot

@dataclass(frozen=True)
class AuthoringSuggestionSnapshot:
    status: Literal["not_requested", "pending", "ready", "failed"]
    job_id: str | None
    input_hash: str | None
    suggestion: PlanSuggestion | None
    failure_code: str | None
```

The input hash covers fixed-key canonical JSON: `profile_kind`, `title`, `statement`, and `material_bundle_hash`. It does not contain source code or tests.

- [ ] **Step 3: Bind job creation to a locked open session**

`PlanAuthoringService.request_suggestion` locks the session, verifies owner/open status, and returns the already-linked job before considering the new payload. Only when `suggestion_job_id is None` may it call `PlanSuggestionJobService.submit(..., authoring_session_id=session.id)` and save the job ID.

- [ ] **Step 4: Restrict C++ AI output to existing material requirements**

Extend `PlanSuggestionInput` with `profile_kind: Literal["python_v2", "cpp_v3"]`, `material_bundle_hash`, and a bounded tuple of `{id, name, source_statement}` for C++ only. Extend suggested knowledge points with optional `material_requirement_id`. Validate C++ output against the submitted ID set; reject generated tests, unknown IDs, duplicate IDs, more than 10 points, or extra fields. Preserve the old Python provider prompt and output tests.

- [ ] **Step 5: Keep job retries internal**

Existing provider retry behavior remains inside one job. On ready or terminal failed, clear source text but retain `request_hash` as the public `input_hash`. Do not clear `authoring_session_id`; do not expose provider metadata.

- [ ] **Step 6: Run tests and commit**

```bash
cd services/classroom-sync
uv run --extra dev pytest -q tests/unit/test_plan_authoring.py tests/unit/test_plan_suggestion_jobs.py tests/unit/test_plan_suggestions.py
git add src/classroom_sync/services/plan_authoring.py src/classroom_sync/services/plan_suggestion_jobs.py src/classroom_sync/services/plan_suggestions.py src/classroom_sync/repositories.py tests/unit/test_plan_authoring.py tests/unit/test_plan_suggestion_jobs.py tests/unit/test_plan_suggestions.py
git commit -m "feat: limit AI suggestions to one per authoring session"
```

### Task 8: Add authoring-session HTTP APIs and ownership checks

**Files:**

- Create: `services/classroom-sync/src/classroom_sync/routers/authoring.py`
- Modify: `services/classroom-sync/src/classroom_sync/application.py`
- Modify: `services/classroom-sync/src/classroom_sync/runtime.py`
- Modify: `services/classroom-sync/src/classroom_sync/main.py`
- Replace coverage in: `services/classroom-sync/tests/integration/test_plan_suggestions_route.py`
- Create: `services/classroom-sync/tests/integration/test_plan_authoring_routes.py`

- [ ] **Step 1: Add RED route tests for every approved endpoint**

Test POST create, GET current, POST suggestion, GET suggestion, POST abandon, missing bearer, wrong teacher, wrong parent ownership, duplicate POST with changed input, refresh after ready, and failed-without-regenerate. Assert suggestion response includes `input_hash` but not teacher statement.

Run:

```bash
cd services/classroom-sync
uv run --extra dev pytest -q tests/integration/test_plan_authoring_routes.py tests/integration/test_plan_suggestions_route.py
```

Expected: FAIL because the new routes are absent.

- [ ] **Step 2: Implement exact routes and response mapping**

Use the five approved paths under `/v1/classroom/plan-authoring-sessions`. Every request resolves the bearer and rechecks parent ownership before session mutation. The GET-by-session suggestion path also checks session owner; knowing a UUID is insufficient.

- [ ] **Step 3: Preserve old v2 suggestion endpoints**

Keep `/v1/classroom/plan-suggestions` for legacy profile v2 compatibility, but the new C++ UI must never call it. Mark the old route docstring as legacy and cover it with the existing tests.

- [ ] **Step 4: Wire runtime and run tests**

```bash
cd services/classroom-sync
uv run --extra dev pytest -q tests/integration/test_plan_authoring_routes.py tests/integration/test_plan_suggestions_route.py tests/test_runtime.py
git add src/classroom_sync/routers/authoring.py src/classroom_sync/application.py src/classroom_sync/runtime.py src/classroom_sync/main.py tests/integration/test_plan_authoring_routes.py tests/integration/test_plan_suggestions_route.py tests/test_runtime.py
git commit -m "feat: expose resumable classroom authoring sessions"
```

### Task 9: Add draft recovery and deterministic publication gate

**Files:**

- Create: `services/classroom-sync/src/classroom_sync/services/publication_gate.py`
- Modify: `services/classroom-sync/src/classroom_sync/services/plans.py`
- Modify: `services/classroom-sync/src/classroom_sync/routers/plans.py`
- Modify: `services/classroom-sync/src/classroom_sync/errors.py`
- Modify: `services/classroom-sync/tests/integration/test_plan_assignment_flow.py`
- Modify: `services/classroom-sync/tests/integration/test_classroom_routes.py`
- Create: `services/classroom-sync/tests/unit/test_publication_gate.py`

- [ ] **Step 1: Add RED gate tests against both real bundles**

The sequence profile must remain blocked by non-UTF-8 confirmation, protected `value` error, and unavailable sanitizer binding. The linked-list raw teacher dimensions must report traversal-as-framework-provided, deletion-outside-task, and missing reverse. A corrected linked-list profile with tail insertion + reverse and both real tests must be `ready`. Changing a source/test/bundle hash after confirmation must block.

Run:

```bash
cd services/classroom-sync
uv run --extra dev pytest -q tests/unit/test_publication_gate.py
```

Expected: FAIL because `PublicationGate` is absent.

- [ ] **Step 2: Implement a pure gate evaluator**

```python
class PublicationGate:
    def evaluate(
        self,
        profile: Mapping[str, object],
        materials: AssessmentMaterialBundle,
    ) -> PublicationGateResult: ...

    def require_ready(self, profile: Mapping[str, object], materials: AssessmentMaterialBundle) -> None: ...
```

For v2, return `ready` without material rules. For v3, apply the stable-code checks in Contract Freeze order, deduplicate by `(code, scope, knowledge_point_id, requirement_id)`, and sort blockers before warnings. Never infer from Chinese names; compare only stable IDs and hashes.

- [ ] **Step 3: Add recoverable draft endpoints**

Extend draft creation with optional `authoring_session_id`. Add owner-checked `GET /v1/classroom/plans/drafts/{draft_id}` and `PUT /v1/classroom/plans/drafts/{draft_id}` with `expected_revision`. PUT updates title, profile, schedule and AI policy atomically, increments revision, and returns the current `publication_gate`. Revision mismatch returns 409 `plan_draft_revision_conflict` without overwriting.

- [ ] **Step 4: Close the session in the publish transaction**

On v3 publish, the router fetches the latest material bundle after ownership verification. `PlanService.publish_draft` locks draft and authoring session, revalidates the gate, creates the immutable version, then sets session status `published`, `active_slot=None`, `published_plan_id=draft.id`, and `closed_at=now` in the same database transaction. If plan validation/insertion fails, the session remains open.

- [ ] **Step 5: Return safe gate details on blocked publication**

Extend `ClassroomServiceError` with optional bounded `details`; the global error handler includes details only when supplied. `PublicationGateBlockedError` is HTTP 409, code `publication_gate_blocked`, retryable false, details only the safe gate projection. Existing errors retain their envelope.

- [ ] **Step 6: Run assignment and route regressions**

```bash
cd services/classroom-sync
uv run --extra dev pytest -q tests/unit/test_publication_gate.py tests/integration/test_plan_assignment_flow.py tests/integration/test_classroom_routes.py tests/integration/test_plan_authoring_routes.py
```

Expected: PASS, including old v2 publish/re-publish tests.

- [ ] **Step 7: Commit**

```bash
git add src/classroom_sync/services/publication_gate.py src/classroom_sync/services/plans.py src/classroom_sync/routers/plans.py src/classroom_sync/errors.py tests/unit/test_publication_gate.py tests/integration/test_plan_assignment_flow.py tests/integration/test_classroom_routes.py tests/integration/test_plan_authoring_routes.py
git commit -m "feat: gate C++ classroom plan publication"
```

### Task 10: Extend the local FinColab façade and phase-one backend smoke

**Files:**

- Modify: `deploy/classroom/local-demo/fincolab_demo.py`
- Modify: `deploy/classroom/local-demo/Dockerfile`
- Modify: `scripts/__tests__/test_local_classroom_demo_facade.py`
- Create: `scripts/cpp_classroom_phase1_smoke.py`
- Create: `scripts/__tests__/test_cpp_classroom_phase1_smoke.py`
- Modify: `deploy/classroom/local-demo/README.md`

- [ ] **Step 1: Add RED façade tests for two new parent experiments**

Keep the existing Python parent. Add `sequence-list-experiment-001` and `linked-list-experiment-002`. Teacher may read both material bundles; students and cross-course users receive 403. Assert the façade serves sealed JSON only and never serves raw source paths.

Run:

```bash
uv run --extra test pytest -q scripts/__tests__/test_local_classroom_demo_facade.py
```

Expected: new cases FAIL with 404.

- [ ] **Step 2: Package and serve sealed material bundles**

The Dockerfile copies only `bundle.json` files, not the raw source/TXT fixtures. `fincolab_demo.py` maps exact parent IDs to exact bundle resources and preserves silent request logging. The private upstream response may include adapter-only metadata required by `AssessmentMaterialService`; the public classroom route still strips it.

- [ ] **Step 3: Add a RED phase-one smoke**

The smoke must:

1. authenticate teacher;
2. read sequence materials and assert the exact blocker codes;
3. read linked-list materials and assert the raw dimension issue codes;
4. create/recover one authoring session twice and assert the same ID;
5. create/save a corrected linked-list profile v3;
6. publish it and assert the session closes;
7. assert no assignment-sync or student-run endpoint was called.

The smoke must not require a paid AI provider. AI once-only behavior remains covered by injected service tests; optional `--require-ai` may validate one real provider call without printing its output.

- [ ] **Step 4: Implement and run fixture-level smoke tests**

```bash
uv run --extra test pytest -q scripts/__tests__/test_local_classroom_demo_facade.py scripts/__tests__/test_cpp_classroom_phase1_smoke.py
```

Expected: PASS.

- [ ] **Step 5: Commit local closure assets**

```bash
git add deploy/classroom/local-demo/fincolab_demo.py deploy/classroom/local-demo/Dockerfile deploy/classroom/local-demo/README.md scripts/cpp_classroom_phase1_smoke.py scripts/__tests__/test_local_classroom_demo_facade.py scripts/__tests__/test_cpp_classroom_phase1_smoke.py
git commit -m "test: close C++ publication gate in local demo"
```

### Task 11: Resolve the frontend dirty-worktree gate and preserve the legacy wizard

**Files:**

- Inspect only first: all current modified/untracked frontend files
- Create after approval: `src/modules/classroom-monitoring/components/LegacyPlanWizard.vue`
- Create: `src/modules/classroom-monitoring/components/CppPlanWizard.vue`
- Modify: `src/modules/classroom-monitoring/components/PlanWizard.vue`
- Modify: `src/config/app-config.ts`
- Modify: `src/config/__tests__/app-config.local-demo.test.ts`
- Modify: `.env.local-demo`

- [ ] **Step 1: Stop and resolve overlapping user work before editing**

Run:

```bash
git status --short
git diff -- src/modules/classroom-monitoring/components/PlanWizard.vue src/modules/classroom-monitoring/api.ts src/modules/classroom-monitoring/plan-draft.ts src/modules/classroom-monitoring/types.ts
```

Expected today: existing modifications are present. Present the diff summary to the user and obtain explicit direction to commit them, continue on a dedicated branch/worktree, or reconcile them. Do not proceed while ownership is ambiguous.

- [ ] **Step 2: Record a clean/reconciled baseline**

After user-approved preservation, require `git status --short` to be clean or to contain only explicitly enumerated non-overlapping files. Record the baseline commit ID in the execution notes.

- [ ] **Step 3: Add RED feature-flag tests**

Assert unset `VITE_CPP_TRUSTED_ASSESSMENT_ENABLED` is false and local-demo is true. Mount `PlanWizard` under both values and assert the old three-step UI remains under false while the new four-step shell appears under true.

Run:

```bash
npm test -- --run src/config/__tests__/app-config.local-demo.test.ts src/modules/classroom-monitoring/__tests__/PlanWizard.test.ts
```

Expected: FAIL because the flag and wrapper split are absent.

- [ ] **Step 4: Split without rewriting legacy behavior**

Move the reconciled existing component implementation to `LegacyPlanWizard.vue`. Make `PlanWizard.vue` a thin feature-flag wrapper. `CppPlanWizard.vue` initially renders only the four step names. Do not copy mastery-list or brief UI into the new wizard.

```ts
export function isCppTrustedAssessmentEnabled(): boolean {
  return appConfig.cppTrustedAssessmentEnabled
}
```

- [ ] **Step 5: Run tests and commit only this compatibility seam**

```bash
npm test -- --run src/config/__tests__/app-config.local-demo.test.ts src/modules/classroom-monitoring/__tests__/PlanWizard.test.ts
npm run type-check
git add .env.local-demo src/config/app-config.ts src/config/__tests__/app-config.local-demo.test.ts src/modules/classroom-monitoring/components/PlanWizard.vue src/modules/classroom-monitoring/components/LegacyPlanWizard.vue src/modules/classroom-monitoring/components/CppPlanWizard.vue src/modules/classroom-monitoring/__tests__/PlanWizard.test.ts
git commit -m "feat: gate trusted C++ plan authoring UI"
```

### Task 12: Add strict frontend contracts for materials, sessions, drafts and gates

**Files:**

- Modify: `src/modules/classroom-monitoring/types.ts`
- Modify: `src/modules/classroom-monitoring/api.ts`
- Modify: `src/modules/classroom-monitoring/__tests__/api.test.ts`
- Create: `src/modules/classroom-monitoring/suggestion-input-hash.ts`
- Create: `src/modules/classroom-monitoring/__tests__/suggestion-input-hash.test.ts`

- [ ] **Step 1: Add RED API mapping tests**

Cover material bundle parsing, unknown issue/status rejection, no source body, create/current/abandon session, one suggestion request/poll, ready result with `inputHash`, get/create/update draft with revision, and safe publication-gate errors. Assert all URL path segments are encoded.

Run:

```bash
npm test -- --run src/modules/classroom-monitoring/__tests__/api.test.ts src/modules/classroom-monitoring/__tests__/suggestion-input-hash.test.ts
```

Expected: FAIL because the new methods/types are absent.

- [ ] **Step 2: Add concrete TypeScript types**

```ts
export type PublicationGateStatus = 'ready' | 'blocked'
export type AuthoringSuggestionStatus = 'not_requested' | 'pending' | 'ready' | 'failed'

export interface ClassroomPlanAuthoringSession {
  authoringSessionId: string
  status: 'open' | 'published' | 'abandoned'
  spaceId: string
  parentAlgorithmId: string
  draft: ClassroomPlanDraftSnapshot | null
  suggestion: ClassroomAuthoringSuggestion
}
```

Define exact unions for the issue codes in Contract Freeze. Do not use `Record<string, unknown>` for profile v3; introduce `ClassroomCppProfileDraftV3` and its nested interfaces. Keep `ClassroomProfile` compatible with v2 reads.

- [ ] **Step 3: Implement strict wire mappers and API methods**

Add:

```ts
getAssessmentMaterials(spaceId, parentAlgorithmId)
openPlanAuthoringSession(input)
getCurrentPlanAuthoringSession(spaceId, parentAlgorithmId)
requestAuthoringSuggestion(authoringSessionId, input)
getAuthoringSuggestion(authoringSessionId)
abandonPlanAuthoringSession(authoringSessionId)
getPlanDraft(draftId)
updatePlanDraft(draftId, input, expectedRevision)
```

Existing `generatePlanSuggestion` remains for legacy v2 only.

- [ ] **Step 4: Match the server input hash exactly**

Use `TextEncoder`, fixed-key `JSON.stringify`, and `crypto.subtle.digest('SHA-256', bytes)`. Add a test vector copied from the backend unit test so TypeScript and Python produce the same 64-character hash.

- [ ] **Step 5: Run tests/type check and commit**

```bash
npm test -- --run src/modules/classroom-monitoring/__tests__/api.test.ts src/modules/classroom-monitoring/__tests__/suggestion-input-hash.test.ts
npm run type-check
git add src/modules/classroom-monitoring/types.ts src/modules/classroom-monitoring/api.ts src/modules/classroom-monitoring/suggestion-input-hash.ts src/modules/classroom-monitoring/__tests__/api.test.ts src/modules/classroom-monitoring/__tests__/suggestion-input-hash.test.ts
git commit -m "feat: consume trusted C++ authoring contracts"
```

### Task 13: Build profile v3 form state, exact hashes and server-backed recovery

**Files:**

- Create: `src/modules/classroom-monitoring/cpp-plan-draft.ts`
- Create: `src/modules/classroom-monitoring/use-plan-authoring.ts`
- Create: `src/modules/classroom-monitoring/__tests__/cpp-plan-draft.test.ts`
- Create: `src/modules/classroom-monitoring/__tests__/use-plan-authoring.test.ts`

- [ ] **Step 1: Add RED pure-form tests**

Assert form creation uses only material requirements/tests, never fabricates a test; traversal/delete do not become knowledge points; corrected linked requirements map to dimensions/bindings; expected stdout preserves punctuation; confirmations match backend vectors; sequence blockers remain blockers; empty dimension prevents step advance.

Run:

```bash
npm test -- --run src/modules/classroom-monitoring/__tests__/cpp-plan-draft.test.ts
```

Expected: FAIL because the module is absent.

- [ ] **Step 2: Implement deterministic material-to-form mapping**

The form stores `materialRequirementId` on each point and immutable material tests separately. `toCppProfileV3` may only select tests and starter source from the current server bundle. It maps the teacher text into `knowledge_points[].description`, `dimensions[].question`, and the single criterion statement; it never creates an ID not derived from the stable material requirement ID.

- [ ] **Step 3: Add RED recovery/autosave tests**

Use fake timers to assert: existing server draft restores on mount; no draft creates once; edits save after 800 ms; saves serialize by revision; 409 reloads the server draft and shows conflict rather than overwriting; route leave flushes the last queued save; refresh never requests a second AI suggestion.

- [ ] **Step 4: Implement `usePlanAuthoring`**

Sequence on mount: get current/open session → get materials → restore draft or create one → expose immutable `publicationGate` and suggestion state. Serialize saves through one promise chain; never fire concurrent PUTs. On unmount, cancel polling and attempt only an already-queued save—do not use `sendBeacon` with bearer tokens.

- [ ] **Step 5: Run tests/type check and commit**

```bash
npm test -- --run src/modules/classroom-monitoring/__tests__/cpp-plan-draft.test.ts src/modules/classroom-monitoring/__tests__/use-plan-authoring.test.ts
npm run type-check
git add src/modules/classroom-monitoring/cpp-plan-draft.ts src/modules/classroom-monitoring/use-plan-authoring.ts src/modules/classroom-monitoring/__tests__/cpp-plan-draft.test.ts src/modules/classroom-monitoring/__tests__/use-plan-authoring.test.ts
git commit -m "feat: recover server-backed C++ plan drafts"
```

### Task 14: Implement the four-step teacher configuration experience

**Files:**

- Modify: `src/modules/classroom-monitoring/components/CppPlanWizard.vue`
- Create: `src/modules/classroom-monitoring/components/AssessmentDimensionStep.vue`
- Create: `src/modules/classroom-monitoring/components/MaterialValidationPanel.vue`
- Create: `src/modules/classroom-monitoring/__tests__/CppPlanWizard.test.ts`
- Create: `src/modules/classroom-monitoring/__tests__/AssessmentDimensionStep.test.ts`

- [ ] **Step 1: Add RED component tests for the approved interaction**

Cover four step names; one page-level AI area; no row-level/re-generate/upload/download/import/test-generation buttons; left knowledge navigation; one textarea at a time; edit retention; completion count; real test count; sequence blocker display; linked-list traversal/delete/missing-reverse display; blocked next button; corrected linked profile advancing; stale suggestion warning and whole-replacement confirmation; terminal AI failure allowing manual edit.

Run:

```bash
npm test -- --run src/modules/classroom-monitoring/__tests__/CppPlanWizard.test.ts src/modules/classroom-monitoring/__tests__/AssessmentDimensionStep.test.ts
```

Expected: FAIL because the components are incomplete/absent.

- [ ] **Step 2: Implement step 1 and the once-only AI state strip**

Use the server session state as the single source of truth. `not_requested` shows one button; `pending` polls; `ready` shows “使用本次建议/继续手动编辑”; applied collapses; `failed` shows manual-only. Once status leaves `not_requested`, no code path renders or calls generation again.

When current form hash differs from returned `inputHash`, show “建议基于较早版本的题目”. Applying opens an explicit confirmation and replaces title plus all knowledge points/dimensions in one operation.

- [ ] **Step 3: Implement the selected master-detail dimension step**

Match the approved prototype structure:

- fixed trust explanation at top;
- `N 个知识点 · 已填写 X · 待完善 Y`;
- approximately 280 px left navigation on desktop;
- current item uses text, border and background plus `aria-current`;
- right-side 500-character textarea with `label` and `aria-describedby`;
- read-only real tests, source, language/toolchain and gate status;
- classroom-level material issues above content;
- bottom rule explanation distinguishing evidence insufficiency from not mastered.

- [ ] **Step 4: Implement steps 3 and 4 plus publish behavior**

Step 3 contains only schedule, collection explanation and existing AI policy. Step 4 shows immutable material/source/test/confirmation summary and the latest server gate. Publish first flushes draft save, then calls publish; on 409 it preserves all form state and focuses the gate summary. Do not sync assignments in phase one; after success show “方案已发布，学生受控测试将在第二阶段启用” and return to experiment management.

- [ ] **Step 5: Implement responsive/accessibility rules**

At medium width, stack navigation above editor. Below 640 px, stack the four steps vertically. Use `role="alert"` for focused error summary, visible keyboard focus, non-color status labels, and layout that remains usable at 200% zoom.

- [ ] **Step 6: Run components, type check and commit**

```bash
npm test -- --run src/modules/classroom-monitoring/__tests__/CppPlanWizard.test.ts src/modules/classroom-monitoring/__tests__/AssessmentDimensionStep.test.ts src/modules/classroom-monitoring/__tests__/PlanWizard.test.ts
npm run type-check
git add src/modules/classroom-monitoring/components/CppPlanWizard.vue src/modules/classroom-monitoring/components/AssessmentDimensionStep.vue src/modules/classroom-monitoring/components/MaterialValidationPanel.vue src/modules/classroom-monitoring/__tests__/CppPlanWizard.test.ts src/modules/classroom-monitoring/__tests__/AssessmentDimensionStep.test.ts
git commit -m "feat: configure C++ assessment dimensions from real materials"
```

### Task 15: Verify both repositories and the local phase-one stop point

**Files:**

- Modify only if commands are inaccurate: `deploy/classroom/local-demo/README.md`
- Create: `docs/2026-08-27-cpp-classroom-phase1-verification.md`
- Modify only if commands are inaccurate: frontend `README.md` or local-demo launcher docs

- [ ] **Step 1: Run backend/plugin full quality commands**

```bash
uv run --extra test pytest -q myextension/tests
cd services/classroom-sync
uv run --extra dev pytest -q
uv run --extra dev ruff check src tests
uv run --extra dev mypy src
cd ../..
git diff --check
```

Expected: all PASS. If pytest-jupyter requires loopback permission, rerun only the same test command with the required approval and record it.

- [ ] **Step 2: Run frontend full quality commands**

From the frontend root:

```bash
npm test -- --run
npm run type-check
npm run build -- --mode local-demo
git diff --check
```

Expected: all PASS. `npm run lint` is fix-writing; first run `./node_modules/.bin/eslint .` and `./node_modules/.bin/oxlint .` read-only. Run the project fix-writing lint command only after reviewing and restricting any resulting changes to in-scope files.

- [ ] **Step 3: Start only the isolated local demo**

From the backend root:

```bash
scripts/start_local_classroom_demo.sh
```

From the frontend root in a separate foreground terminal:

```bash
scripts/start-local-classroom-frontend.sh
```

Verify exact URLs:

```text
Vue: http://127.0.0.1:5175
FinColab façade: http://127.0.0.1:18082
classroom-sync: http://127.0.0.1:18080
```

Do not terminate unknown port owners. If 5175 or a demo port is occupied, inspect and stop only the known prior demo process.

- [ ] **Step 4: Run the phase-one smoke**

```bash
uv run --extra test python scripts/cpp_classroom_phase1_smoke.py
```

Expected summary contains only safe IDs/statuses:

```text
sequence-list: blocked
linked-list-raw: blocked
linked-list-corrected: published
student-execution: not-started
```

- [ ] **Step 5: Perform browser acceptance at 5175**

Log in as the local teacher and verify:

1. sequence exercise shows encoding, protected `value`, and sanitizer blockers;
2. linked exercise shows traversal provided, deletion outside task, missing reverse;
3. after selecting tail insertion + reverse and filling dimensions, real test counts are 2 and publication becomes ready;
4. refresh preserves session, draft and AI state;
5. a second browser tab cannot create a second AI result;
6. 200% zoom and 640 px layout remain operable;
7. no student mastery or score is shown.

Capture screenshots under `docs/superpowers/specs/assets/phase1-verification/`; never capture tokens or raw source.

- [ ] **Step 6: Write evidence-based verification notes**

Record exact commands, exit codes, test counts, browser paths, screenshots, hashes, unresolved production dependency for the upstream materials endpoint, and the explicit statement that student execution/mastery remain unimplemented by design.

- [ ] **Step 7: Commit verification in each repository**

Backend:

```bash
git add docs/2026-08-27-cpp-classroom-phase1-verification.md docs/superpowers/specs/assets/phase1-verification deploy/classroom/local-demo/README.md
git commit -m "docs: verify C++ classroom phase one"
```

Frontend: add only documentation files actually changed, then commit separately with `docs: verify trusted C++ authoring UI`.

- [ ] **Step 8: Stop at the stage gate**

Report Phase 1 as complete only if linked-list corrected publication succeeds and all quality commands pass. Do not start Phase 2. The next plan must separately cover the isolated C++ runner, sanitizer, `assessment_run` v2 event and evidence hash chain.

---

## Final Acceptance Matrix

| Scenario | Expected result | Proof source |
| --- | --- | --- |
| Sequence original source | Blocked | exact source hash, GB18030 status, protected `value` diagnostic |
| Sequence space release | Blocked in Phase 1 | `address_undefined_leak_v1` unavailable |
| Linked raw TXT dimensions | Blocked | traversal is protected, deletion outside task, reverse missing |
| Linked corrected dimensions | Ready/publishable | tail insertion + reverse mapped to both real tests |
| Missing materials/upload | Blocked, never “student not mastered” | material/gate stable code |
| Duplicate AI POST in same session | Original job/result returned | DB uniqueness + route test |
| AI terminal failure | Manual editing only | persisted failed state; no regenerate control |
| Refresh/new tab | Same session, draft and suggestion | server recovery tests/browser acceptance |
| Profile v2 classroom | Unchanged | full v2 schema/publish regression |
| Student assignment/execution | Not started | smoke asserts no assignment sync/run |

## Rollback

1. Set `VITE_CPP_TRUSTED_ASSESSMENT_ENABLED=false` and rebuild the frontend; the legacy three-step v2 wizard remains available.
2. Do not downgrade production database as a first response. New tables/nullable columns are backward compatible and preserve audit history.
3. If a local migration rollback is required for verification, downgrade only the isolated test/local-demo database from 0008 to 0007 and rerun migration tests.
4. If the upstream material endpoint is unavailable, keep the C++ flag disabled. Never fall back to browser parsing, hardcoded production fixtures, or an unverified source reference.
