# Local Demo Experiment Creation Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing teacher three-step create-experiment dialog complete successfully against the local FinColab façade and show the new parent and student projects after refresh.

**Architecture:** Add a server-scoped, lock-protected in-memory state to the standard-library HTTP façade. Serve one fixed default dataset, store dynamic algorithm/workbench records from the real frontend payloads, merge them into existing reads, and support teacher-only rollback deletion; all state resets with the façade process.

**Tech Stack:** Python 3.12 standard library (`ThreadingHTTPServer`, dataclasses, locks), pytest HTTP contract tests, Docker Compose local demo, Vue/Vite browser acceptance at `127.0.0.1:5176`.

## Global Constraints

- Modify only the local demo façade, its Python contract tests, and the local demo README.
- Do not change the frontend UI or production BAMS contracts.
- Keep dynamic data process-local and reset it when the façade container restarts.
- Preserve fixed C++ parents, sealed materials, student visibility, and cross-course denials.
- Stop after local tests, browser acceptance, and local Git commits; do not push or deploy.

---

### Task 1: Server-scoped state and default dataset contract

**Files:**
- Modify: `deploy/classroom/local-demo/fincolab_demo.py`
- Test: `scripts/__tests__/test_local_classroom_demo_facade.py`

**Interfaces:**
- Produces: `DemoState` with `algorithms`, `workbenches`, `next_algorithm_id`, and a lock.
- Produces: `DemoFincolabServer(server_address, handler_class)` with a fresh `demo_state` per server.
- Produces: `GET /v1/spaces/course-001/datasets` returning a `DatasetListResponse`-compatible fixture.

- [ ] **Step 1: Write the failing dataset contract test**

```python
def test_teacher_can_resolve_the_course_default_dataset_for_creation() -> None:
    with demo_client() as client:
        status, payload = client.request(
            "GET",
            "/v1/spaces/course-001/datasets?name=&search_mode=like&page=1&limit=100",
            token="teacher-token",
        )

    assert status == HTTPStatus.OK
    assert payload == {
        "data": [{
            "dataset_name": "default_dataset",
            "version_datas": [{
                "id": "dataset-default",
                "name": "default_dataset",
                "version": "v1",
                "dataset_file_path": "algorithm_data",
                "data_type": "image",
                "annotation_type": "img_classification",
                "label_format": "ImageFolder",
                "description": "课程默认数据集",
            }],
        }],
    }
```

- [ ] **Step 2: Run the test and verify RED**

```bash
uv run --no-project --with pytest pytest -q -c /dev/null --confcutdir=scripts/__tests__ scripts/__tests__/test_local_classroom_demo_facade.py::test_teacher_can_resolve_the_course_default_dataset_for_creation
```

Expected: FAIL with HTTP 403 and `demo_course_access_denied`.

- [ ] **Step 3: Add fresh server state and the dataset route**

Implement these shapes and switch both `main()` and `demo_client()` to `DemoFincolabServer`:

```python
@dataclass
class DemoState:
    algorithms: dict[str, dict[str, object]] = field(default_factory=dict)
    workbenches: dict[str, dict[str, object]] = field(default_factory=dict)
    next_algorithm_id: int = 1
    lock: Lock = field(default_factory=Lock)


class DemoFincolabServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler]):
        super().__init__(server_address, handler_class)
        self.demo_state = DemoState()
```

Register the exact dataset route before the generic course denial branch and retain `_require_course_access(user)`.

- [ ] **Step 4: Run the focused test and the full façade file**

Run Step 2, then:

```bash
uv run --no-project --with pytest pytest -q -c /dev/null --confcutdir=scripts/__tests__ scripts/__tests__/test_local_classroom_demo_facade.py
```

Expected: focused PASS and full file PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add deploy/classroom/local-demo/fincolab_demo.py scripts/__tests__/test_local_classroom_demo_facade.py
git commit -m "feat: add local demo creation state"
```

---

### Task 2: Project, workbench, visibility, and rollback lifecycle

**Files:**
- Modify: `deploy/classroom/local-demo/fincolab_demo.py`
- Test: `scripts/__tests__/test_local_classroom_demo_facade.py`

**Interfaces:**
- Consumes: `DemoFincolabServer.demo_state` and `dataset-default` from Task 1.
- Produces: algorithm POST, workbench POST, dynamic-aware list/detail, and teacher-only dynamic DELETE routes.

- [ ] **Step 1: Write one failing real lifecycle test**

Add `test_teacher_can_create_list_and_rollback_a_parent_and_student_experiment`. Use literal request payloads:

```python
parent_payload = {
    "name": "本地闭环实验",
    "description": "教师创建验收",
    "framework_id": "framework-behavior",
    "project_type": "notebook",
    "dataset_id": "dataset-default",
    "dataset_name": "default_dataset",
    "template_id": "template-behavior",
    "upload_id": "",
}
student_payload = {
    **parent_payload,
    "name": "exp-student001-abcd",
    "description": f"[FINCOLAB_PARENT_PROJECT_ID:{parent_id}]\n实验名称：本地闭环实验",
}
```

Assert parent owner `teacher001`; student owner `student001`; workbench preserves `{cpu: 2, memory: 4, gpu: 0}` and returns `RUNNING`; the teacher list contains both; the student list contains its child and the referenced parent metadata while direct parent detail remains forbidden; deleting child and parent removes them and the child workbench.

- [ ] **Step 2: Run the lifecycle test and verify RED**

```bash
uv run --no-project --with pytest pytest -q -c /dev/null --confcutdir=scripts/__tests__ scripts/__tests__/test_local_classroom_demo_facade.py::test_teacher_can_create_list_and_rollback_a_parent_and_student_experiment
```

Expected: FAIL because algorithm POST returns HTTP 405.

- [ ] **Step 3: Implement minimal JSON parsing and route dispatch**

Add a request-body helper accepting only a JSON object and returning HTTP 400 `demo_payload_invalid` otherwise. In `do_POST`, authenticate, enforce the exact course, then dispatch only algorithm and workbench creation. In `do_DELETE`, authenticate and dispatch only an exact dynamic algorithm ID.

- [ ] **Step 4: Implement algorithm ownership and storage**

Within `DemoState.lock`, allocate `demo-algorithm-{next_algorithm_id:04d}`. Derive a student owner only when the name matches `exp-<username>-<four-character-code>` and that username is an in-course student; otherwise use `teacher001`. Store all request fields plus:

```python
{
    "id": algorithm_id,
    "username": owner,
    "workbench_id": "",
    "workbench_status": "NOT_STARTED",
    "created_at": str(int(time.time())),
    "updated_at": str(int(time.time())),
}
```

Reject student writes with HTTP 403 `demo_resource_access_denied`. Reject missing `name`, `framework_id`, `project_type`, or `dataset_id` with HTTP 400 `demo_payload_invalid`.

- [ ] **Step 5: Implement workbench creation and dynamic reads/deletion**

Create `workbench-{algorithm_id}` with `project_id`, `space_id`, owner, `RUNNING`, the local Jupyter URL, and exact `container_resource_json`. Update the algorithm workbench fields. Teachers see all dynamic records; students see their own records plus only the parent metadata referenced by those records, while dynamic parent detail stays teacher-only. Delete only dynamic records and their workbench; fixed projects remain protected.

- [ ] **Step 6: Add denial and state-isolation tests**

Assert student writes return `(403, {"detail": "demo_resource_access_denied"})`; cross-course writes return `(403, {"detail": "demo_course_access_denied"})`. Create a dynamic project in one `demo_client()` context and verify a second context does not contain its ID.

- [ ] **Step 7: Run the full façade file and commit Task 2**

```bash
uv run --no-project --with pytest pytest -q -c /dev/null --confcutdir=scripts/__tests__ scripts/__tests__/test_local_classroom_demo_facade.py
git add deploy/classroom/local-demo/fincolab_demo.py scripts/__tests__/test_local_classroom_demo_facade.py
git commit -m "feat: close local experiment creation lifecycle"
```

---

### Task 3: Runtime documentation and browser acceptance

**Files:**
- Modify: `deploy/classroom/local-demo/README.md`
- Test: `scripts/__tests__/test_local_classroom_demo_facade.py`

**Interfaces:**
- Consumes: all Task 1–2 HTTP contracts.
- Produces: reproducible documentation for the process-local reset boundary.

- [ ] **Step 1: Document the lifecycle boundary**

State beside the teacher flow that the three-step create dialog is supported locally; new experiments are in-memory demo data; restarting `demo-fincolab` restores fixed fixtures.

- [ ] **Step 2: Run relevant tests and checks**

```bash
uv run --no-project --with pytest pytest -q -c /dev/null --confcutdir=scripts/__tests__ scripts/__tests__/test_local_classroom_demo_facade.py scripts/__tests__/test_local_classroom_demo_smoke.py scripts/__tests__/test_cpp_classroom_phase1_smoke.py
uv run --no-project --with ruff ruff check --select E4,E7,E9,F deploy/classroom/local-demo/fincolab_demo.py scripts/__tests__/test_local_classroom_demo_facade.py
git diff --check
```

Expected: all tests and checks pass.

- [ ] **Step 3: Rebuild only the façade and verify proxy contracts**

```bash
docker compose -p classroom-local-demo -f deploy/classroom/local-demo/docker-compose.yml up --build -d demo-fincolab
```

Verify login, datasets, algorithm creation, workbench creation, list refresh, and deletion through `http://127.0.0.1:5176/api`. Restart the Vite launch agent only if the rebuilt container causes a transient proxy 502.

- [ ] **Step 4: Complete one browser create flow**

Use teacher `1 / 1`, open the existing wizard, prepare a uniquely named all-students experiment, and verify the summary and new parent row. Request action-time confirmation immediately before the final `创建实验` click because it creates local application state.

- [ ] **Step 5: Commit documentation and stop locally**

```bash
git add deploy/classroom/local-demo/README.md
git commit -m "docs: describe local experiment creation reset"
git status --short
```

Expected: only pre-existing `.codegraph/` remains; do not push.
