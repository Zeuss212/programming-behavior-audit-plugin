# Local Classroom Interactive Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Build a repeatable, entirely local browser demonstration: teacher001 publishes a plan, student001 accepts it and enters a real JupyterLab monitoring session, then the teacher reads the submitted brief.

**Architecture:** The existing deploy/classroom/docker-compose.test.yml remains the contract and fault-test environment. A new independent local-demo Compose project runs PostgreSQL, private MinIO, the existing sync API/worker and Nginx, plus a deliberately small local FinColab façade. The real Vue app runs from its isolated frontend worktree in Vite local-demo mode; the real candidate JupyterLab plugin runs from a loopback-only launcher script.

**Tech Stack:** Docker Compose, PostgreSQL 16, MinIO, Nginx 1.27, Python 3.12 standard-library HTTP server, FastAPI, Vue 3/Vite/Vitest, JupyterLab 4, uv, pytest.

## Global Constraints

- Do not modify, merge, stage, reset, push, or deploy the root checkout main. Service changes stay on codex/classroom-main-integration and Vue changes stay on codex/classroom-ui.
- Do not connect to remote FinColab, BAMS, 5179, 5180, a registry, a real AI endpoint, or production data during this demo.
- Do not change deploy/classroom/docker-compose.test.yml, deploy/classroom/mock-fincolab, candidate containers, BAMS templates, release artifacts, or plugin defaults.
- Exact loopback addresses: Vue http://127.0.0.1:5175; façade http://127.0.0.1:18082; direct sync http://127.0.0.1:18080; Nginx proxy http://127.0.0.1:18081/classroom-api; JupyterLab http://127.0.0.1:8888/lab.
- Demo identities: teacher001, student001, and negative-case student002. They use only documented local fixture credentials.
- student001 alone belongs to local-org/course-001. student002 alone belongs to local-org-negative/course-002 and must receive HTTP 403 for every course-001 façade resource.
- The local Jupyter launcher alone may set LOCAL_CLASSROOM_DEMO=true, JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL=http://127.0.0.1:18081/classroom-api, and JUPYTERLAB_BEHAVIOR_AUDIT_ALLOW_INSECURE_LOOPBACK=true.
- No image, release handoff, default environment file, BAMS template, or normal development command may contain the two local exception flags.
- Compose project name is classroom-local-demo. Its only data volumes are classroom-local-demo-postgres and classroom-local-demo-minio. Stop preserves them; reset removes only them after an explicit confirmation argument.
- All host bindings are 127.0.0.1. Every launcher fails before startup when its own fixed port already has a listener.
- Raw evidence stays in the private local classroom-evidence MinIO bucket. The Vue teacher UI receives only classroom read models and briefs.
- Production ticket behavior stays unchanged: BAMS HTTPS remains the ticket origin unless VITE_LOCAL_CLASSROOM_DEMO=true is explicit.

---

## File Structure

### Integration worktree: codex/classroom-main-integration

- Create: deploy/classroom/local-demo/Dockerfile — packages just the façade Python script.
- Create: deploy/classroom/local-demo/fincolab_demo.py — fixed users, access checks, façade routes, silent request logging.
- Create: deploy/classroom/local-demo/docker-compose.yml — separate local topology, health checks and volumes.
- Create: deploy/classroom/local-demo/README.md — credentials, topology, safe commands, manual presentation runbook.
- Create: scripts/start_local_classroom_demo.sh — starts and waits for only the local Compose project.
- Create: scripts/stop_local_classroom_demo.sh — stops that project and keeps volumes.
- Create: scripts/reset_local_classroom_demo.sh — guarded removal of only local-demo data.
- Create: scripts/start_local_classroom_jupyter.sh — foreground, loopback-only JupyterLab launcher.
- Create: scripts/local_classroom_demo_smoke.py — loopback façade/sync/authorization/brief smoke runner.
- Create: scripts/__tests__/test_local_classroom_demo_facade.py — login, visibility, workbench, denial tests.
- Create: scripts/__tests__/test_local_classroom_demo_compose.py — topology and lifecycle static contract tests.
- Create: scripts/__tests__/test_local_classroom_demo_jupyter.py — local plaintext exception containment tests.

### Frontend worktree: codex/classroom-ui

- Create: .env.local-demo — all explicit Vite demo values.
- Create: scripts/start-local-classroom-frontend.sh — foreground Vite local-demo launcher.
- Create: src/config/__tests__/app-config.local-demo.test.ts — false-by-default local configuration test.
- Create: src/modules/classroom-monitoring/__tests__/workbench-ticket.local-demo.test.ts — ticket origin policy test.
- Create: vite.config.local-demo.test.ts — port, host and both proxy tests.
- Modify: vite.config.ts — reusable config factory, demo port/host policy and classroom proxy.
- Modify: src/config/app-config.ts — typed local demo and local Jupyter origin values.
- Modify: src/modules/classroom-monitoring/workbench-ticket.ts — fail-closed origin resolver.

## Fixture and Façade Contract

The façade is not a BAMS replacement; it only provides the browser reads and classroom-sync upstream reads needed for this demo.

| Route | Method | Required behavior |
| --- | --- | --- |
| /health/live | GET | Unauthenticated { "status": "live" } |
| /v1/login | POST | Maps the three local credentials to fixed bearer tokens |
| /v1/logout | POST | Authenticated no-op {} |
| /v1/user/info | GET | Fixed UI-required user fields |
| /v1/organizations/spaces | GET | course-001 for teacher/student001; course-002 for student002 |
| /v1/organizations/{org}/spaces/{space}/users | GET | One-page roster only for a member of that course |
| /v1/quota/spec/all | GET | One CPU resource fixture |
| /v1/spaces/{space}/algorithm_development | GET | Teacher gets parent+child; student001 gets own child |
| /v1/spaces/{space}/algorithm_development/{algorithm} | GET | Membership/ownership constrained detail |
| /v1/spaces/{space}/algorithm_development/{algorithm}/workbench/{workbench} | GET | RUNNING local Jupyter workbench for student001 |

Credentials are teacher001 / local-demo-teacher, student001 / local-demo-student, and student002 / local-demo-student2. Bearers are teacher-token, student001-token, student002-token, plus the student001 alias student-token retained for scripts/classroom_contract_smoke.py.

Use local-org, course-001, parent-experiment-001, child-experiment-001, and workbench-student001. Child metadata begins exactly [FINCOLAB_PARENT_PROJECT_ID:parent-experiment-001], as required by FincolabIdentityGateway.

### Task 1: Implement and test the restricted façade

**Files:**
- Create: scripts/__tests__/test_local_classroom_demo_facade.py
- Create: deploy/classroom/local-demo/fincolab_demo.py
- Create: deploy/classroom/local-demo/Dockerfile

**Interfaces:**
- Produces DemoUser, authenticate_bearer(token: str) -> DemoUser | None, visible_spaces(user: DemoUser) -> list[dict[str, object]], and DemoFincolabHandler.
- Serves demo-fincolab:8080 to the Compose network.
- Consumes existing sync expectations for /v1/user/info, paginated roster rows, parent ownership and child project metadata.

- [ ] **Step 1: Write the failing route tests**

~~~python
def test_teacher_login_and_roster_expose_only_the_local_course() -> None:
    with run_demo_server() as client:
        login = client.json("POST", "/v1/login", {"username": "teacher001", "password": "local-demo-teacher"})
        assert login["token"] == "teacher-token"
        roster = client.json("GET", "/v1/organizations/local-org/spaces/course-001/users", token="teacher-token")
        assert [row["username"] for row in roster["data"]] == ["teacher001", "student001"]

def test_student_two_cannot_read_course_one() -> None:
    with run_demo_server() as client:
        response = client.response("GET", "/v1/spaces/course-001/algorithm_development", token="student002-token")
        assert response.status == 403
        assert response.json() == {"detail": "demo_course_access_denied"}
~~~

Add assertions for invalid login 401, unknown bearer 401, student001 child/workbench data, teacher parent ownership data, and 405 responses without request headers.

- [ ] **Step 2: Prove the test is red**

Run: PYTHONPATH=. uv run --no-project --with 'pytest>=8,<9' python -m pytest scripts/__tests__/test_local_classroom_demo_facade.py -q

Expected: FAIL because deploy/classroom/local-demo/fincolab_demo.py is absent.

- [ ] **Step 3: Add only the fixed domain and access layer**

~~~python
@dataclass(frozen=True)
class DemoUser:
    user_id: str
    username: str
    password: str
    token: str
    role_name: str
    organization_id: str
    space_id: str

USERS = {
    "teacher001": DemoUser("teacher001", "teacher001", "local-demo-teacher", "teacher-token", "teacher", "local-org", "course-001"),
    "student001": DemoUser("student001", "student001", "local-demo-student", "student001-token", "student", "local-org", "course-001"),
    "student002": DemoUser("student002", "student002", "local-demo-student2", "student002-token", "student", "local-org-negative", "course-002"),
}
TOKEN_ALIASES = {"student-token": USERS["student001"]}

def authenticate_bearer(token: str) -> DemoUser | None:
    return next((user for user in USERS.values() if user.token == token), TOKEN_ALIASES.get(token))
~~~

Implement one BaseHTTPRequestHandler dispatcher. Decode JSON only for POST /v1/login. All protected routes call require_bearer then require_space_access before returning data. Paginated responses always contain data, current_page: 1, total_page: 1, total_count. The workbench jupyter_url is exactly http://127.0.0.1:8888/lab. Override log_message with an empty method so tokens and passwords never enter logs.

- [ ] **Step 4: Package the local script only**

~~~dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY deploy/classroom/local-demo/fincolab_demo.py /app/fincolab_demo.py
USER 65532:65532
EXPOSE 8080
CMD ["python", "/app/fincolab_demo.py"]
~~~

The process may bind 0.0.0.0 only inside Compose; Task 2 restricts its published host port.

- [ ] **Step 5: Verify and commit the vertical slice**

Run: PYTHONPATH=. uv run --no-project --with 'pytest>=8,<9' python -m pytest scripts/__tests__/test_local_classroom_demo_facade.py -q

Expected: PASS.

~~~bash
git add deploy/classroom/local-demo/Dockerfile deploy/classroom/local-demo/fincolab_demo.py scripts/__tests__/test_local_classroom_demo_facade.py
git commit -m "feat: add local classroom demo facade"
~~~

### Task 2: Build isolated Compose lifecycle and readiness controls

**Files:**
- Create: scripts/__tests__/test_local_classroom_demo_compose.py
- Create: deploy/classroom/local-demo/docker-compose.yml
- Create: scripts/start_local_classroom_demo.sh
- Create: scripts/stop_local_classroom_demo.sh
- Create: scripts/reset_local_classroom_demo.sh

**Interfaces:**
- Consumes demo-fincolab:8080 from Task 1, existing classroom sync Dockerfile and existing Nginx config.
- Keeps the service name sync-api because deploy/classroom/nginx/classroom.conf has upstream classroom_sync { server sync-api:8080; }.
- Produces project classroom-local-demo and endpoints 18080, 18081 and 18082.

- [ ] **Step 1: Write the failing topology/safety test**

~~~python
def test_local_demo_isolated_from_test_compose_and_remote_targets() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert "name: classroom-local-demo" in text
    assert '"127.0.0.1:18080:8080"' in text
    assert '"127.0.0.1:18081:8080"' in text
    assert '"127.0.0.1:18082:8080"' in text
    assert "classroom-local-demo-postgres" in text
    assert "classroom-local-demo-minio" in text
    assert "14.103." not in text
    assert "https://" not in text

def test_reset_requires_the_exact_confirmation_flag() -> None:
    assert '"$1" = "--yes-reset-local-demo"' in RESET.read_text(encoding="utf-8")
    assert "down --volumes" not in STOP.read_text(encoding="utf-8")
~~~

Also assert private MinIO has no host port, sync-api waits for PostgreSQL and bucket init, Nginx waits for healthy sync-api, and the local launcher does not name docker-compose.test.yml.

- [ ] **Step 2: Prove the test is red**

Run: PYTHONPATH=. uv run --no-project --with 'pytest>=8,<9' python -m pytest scripts/__tests__/test_local_classroom_demo_compose.py -q

Expected: FAIL because the local Compose and scripts are absent.

- [ ] **Step 3: Implement the independent topology**

~~~yaml
name: classroom-local-demo
services:
  demo-fincolab:
    build:
      context: ../../..
      dockerfile: deploy/classroom/local-demo/Dockerfile
    ports: ["127.0.0.1:18082:8080"]
    networks: [classroom-local-demo]
  sync-api:
    environment:
      CLASSROOM_FINCOLAB_BASE_URL: http://demo-fincolab:8080
      CLASSROOM_FINCOLAB_ORGANIZATION_ID: local-org
    ports: ["127.0.0.1:18080:8080"]
  classroom-nginx:
    ports: ["127.0.0.1:18081:8080"]
    volumes:
      - ../nginx/classroom.conf:/etc/nginx/nginx.conf:ro
~~~

Complete PostgreSQL, MinIO, minio-init, sync-api and deadline-worker from the functional portions of the existing contract Compose, but give them only the new project network and volumes. Use demo-only database/S3/plugin-JWT values. Do not add the test Compose placeholder frontend or Jupyter services.

- [ ] **Step 4: Implement conservative lifecycle scripts**

~~~sh
#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
compose="$root/deploy/classroom/local-demo/docker-compose.yml"
project=classroom-local-demo
lsof -nP -iTCP:18080 -iTCP:18081 -iTCP:18082 -sTCP:LISTEN && { echo "local demo port already in use" >&2; exit 1; }
docker compose -p "$project" -f "$compose" up --build -d
python - <<'PY'
from urllib.request import urlopen
for url in ("http://127.0.0.1:18082/health/live", "http://127.0.0.1:18080/health/ready", "http://127.0.0.1:18081/classroom-api/health/ready"):
    with urlopen(url, timeout=5) as response:
        assert response.status == 200, url
PY
~~~

Replace the one-shot health block with a bounded retry loop (60 seconds, one second per retry), then print all local URLs. Stop uses only docker compose -p classroom-local-demo -f "$compose" stop. Reset accepts only --yes-reset-local-demo, runs down --remove-orphans, then docker volume rm classroom-local-demo-postgres classroom-local-demo-minio; it accepts no target from env or command line.

- [ ] **Step 5: Verify and commit**

Run: PYTHONPATH=. uv run --no-project --with 'pytest>=8,<9' python -m pytest scripts/__tests__/test_local_classroom_demo_compose.py -q && scripts/start_local_classroom_demo.sh

Expected: tests pass and the three loopback health URLs return 200.

~~~bash
git add deploy/classroom/local-demo/docker-compose.yml scripts/__tests__/test_local_classroom_demo_compose.py scripts/start_local_classroom_demo.sh scripts/stop_local_classroom_demo.sh scripts/reset_local_classroom_demo.sh
git commit -m "feat: add isolated local classroom demo stack"
~~~

### Task 3: Enable real Vue teacher/student local-demo mode

**Files:**
- Create: ../lab-platform-frontend-classroom-ui/.env.local-demo
- Create: ../lab-platform-frontend-classroom-ui/scripts/start-local-classroom-frontend.sh
- Create: ../lab-platform-frontend-classroom-ui/src/config/__tests__/app-config.local-demo.test.ts
- Create: ../lab-platform-frontend-classroom-ui/src/modules/classroom-monitoring/__tests__/workbench-ticket.local-demo.test.ts
- Create: ../lab-platform-frontend-classroom-ui/vite.config.local-demo.test.ts
- Modify: ../lab-platform-frontend-classroom-ui/vite.config.ts
- Modify: ../lab-platform-frontend-classroom-ui/src/config/app-config.ts
- Modify: ../lab-platform-frontend-classroom-ui/src/modules/classroom-monitoring/workbench-ticket.ts

**Interfaces:**
- Produces /api/* -> façade /*, preserving the existing rewrite, and /classroom-api/* -> Nginx /classroom-api/* without a rewrite.
- Produces resolveTicketedWorkbenchOrigin(localDemo: boolean, configuredOrigin: string) -> URL.
- Defaults remain port 5174, fincolab.lixin.edu.cn host policy, disabled classroom feature, and BAMS workbench origin.

- [ ] **Step 1: Write the failing config/ticket tests**

~~~ts
it("keeps BAMS unless local demo is explicit", () => {
  expect(resolveTicketedWorkbenchOrigin(false, "http://127.0.0.1:8888").origin)
    .toBe("https://14.103.139.131:40037")
})

it("allows only the documented loopback Jupyter origin in demo mode", () => {
  expect(resolveTicketedWorkbenchOrigin(true, "http://127.0.0.1:8888").origin)
    .toBe("http://127.0.0.1:8888")
  expect(() => resolveTicketedWorkbenchOrigin(true, "https://bams.example.invalid")).toThrow("local demo")
})
~~~

The Vite test invokes exported createViteConfig("local-demo", env) and asserts port 5175, /api rewrite of /api/v1/login to /v1/login, and a no-rewrite /classroom-api target http://127.0.0.1:18081. The config test asserts unset values yield localClassroomDemo === false and localWorkbenchOrigin === "".

- [ ] **Step 2: Prove the tests are red**

Run: npm test -- --run src/config/__tests__/app-config.local-demo.test.ts src/modules/classroom-monitoring/__tests__/workbench-ticket.local-demo.test.ts vite.config.local-demo.test.ts

Expected: FAIL because local config fields, resolver and factory do not exist.

- [ ] **Step 3: Add explicit environment and safe proxies**

~~~dotenv
VITE_CLASSROOM_MONITORING_ENABLED=true
VITE_LOCAL_CLASSROOM_DEMO=true
VITE_LOCAL_WORKBENCH_ORIGIN=http://127.0.0.1:8888
VITE_API_PREFIX=/api
VITE_PROXY_TARGET=http://127.0.0.1:18082
VITE_CLASSROOM_PROXY_TARGET=http://127.0.0.1:18081
VITE_DEV_PORT=5175
VITE_DEFAULT_ORG_ID=local-org
VITE_DEFAULT_SPACE_ID=course-001
VITE_FORCE_CONTEXT=true
~~~

Add to appConfig:

~~~ts
localClassroomDemo: String(import.meta.env.VITE_LOCAL_CLASSROOM_DEMO || "false") === "true",
localWorkbenchOrigin: import.meta.env.VITE_LOCAL_WORKBENCH_ORIGIN || "",
~~~

Refactor Vite to export createViteConfig. In local-demo only, add localhost and 127.0.0.1 to allowedHosts and use VITE_DEV_PORT; normal mode preserves its current values. Add:

~~~ts
"/classroom-api": {
  target: env.VITE_CLASSROOM_PROXY_TARGET || "http://127.0.0.1:18081",
  changeOrigin: true,
},
~~~

- [ ] **Step 4: Make ticket origin fail closed**

~~~ts
const BAMS_WORKBENCH_ORIGIN = "https://14.103.139.131:40037"
const LOCAL_DEMO_WORKBENCH_ORIGIN = "http://127.0.0.1:8888"

export function resolveTicketedWorkbenchOrigin(localDemo: boolean, configuredOrigin: string): URL {
  if (!localDemo) return new URL(BAMS_WORKBENCH_ORIGIN)
  const parsed = new URL(configuredOrigin)
  if (parsed.origin !== LOCAL_DEMO_WORKBENCH_ORIGIN) {
    throw new Error("local demo requires the 127.0.0.1:8888 Jupyter origin")
  }
  return parsed
}
~~~

Use the resolver from buildTicketedWorkbenchUrl, retaining fragment ticket storage and query stripping. No generic environment variable may replace BAMS outside explicit local mode.

- [ ] **Step 5: Add launcher, verify and commit**

~~~sh
#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
lsof -nP -iTCP:5175 -sTCP:LISTEN && { echo "127.0.0.1:5175 is already in use" >&2; exit 1; }
grep -qx "VITE_LOCAL_CLASSROOM_DEMO=true" "$root/.env.local-demo"
exec npm run dev -- --mode local-demo --host 127.0.0.1 --port 5175
~~~

Run: npm test -- --run src/config/__tests__/app-config.local-demo.test.ts src/modules/classroom-monitoring/__tests__/workbench-ticket.local-demo.test.ts vite.config.local-demo.test.ts && npm run type-check && npm run build -- --mode local-demo

Expected: PASS. With the launcher running, curl -fsS http://127.0.0.1:5175/api/health/live and curl -fsS http://127.0.0.1:5175/classroom-api/health/ready both succeed.

~~~bash
git add .env.local-demo scripts/start-local-classroom-frontend.sh vite.config.ts vite.config.local-demo.test.ts src/config/app-config.ts src/config/__tests__/app-config.local-demo.test.ts src/modules/classroom-monitoring/workbench-ticket.ts src/modules/classroom-monitoring/__tests__/workbench-ticket.local-demo.test.ts
git commit -m "feat: add local classroom frontend demo mode"
~~~

### Task 4: Add real loopback-only JupyterLab launching

**Files:**
- Create: scripts/__tests__/test_local_classroom_demo_jupyter.py
- Create: scripts/start_local_classroom_jupyter.sh
- Modify: myextension/tests/test_platform_config.py only if its exact 18081/classroom-api positive case is absent.

**Interfaces:**
- Consumes a fresh dist/myextension-0.4.0-py3-none-any.whl and the Nginx readiness endpoint.
- Produces foreground JupyterLab at 127.0.0.1:8888 with logs under /private/tmp/classroom-local-demo-jupyter/behavior-audit.
- Does not change PlatformConfig HTTPS defaults, release artifacts, images, or runtime examples.

- [ ] **Step 1: Write failing policy tests**

~~~python
def test_jupyter_launcher_marks_and_contains_the_plaintext_exception() -> None:
    text = LAUNCHER.read_text(encoding="utf-8")
    assert "LOCAL_CLASSROOM_DEMO=true" in text
    assert "JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL=http://127.0.0.1:18081/classroom-api" in text
    assert "JUPYTERLAB_BEHAVIOR_AUDIT_ALLOW_INSECURE_LOOPBACK=true" in text
    assert "--ServerApp.ip=127.0.0.1" in text
    assert "--ServerApp.port=8888" in text

def test_local_exception_never_enters_default_or_release_files() -> None:
    for path in DEFAULT_AND_RELEASE_FILES:
        text = path.read_text(encoding="utf-8")
        assert "LOCAL_CLASSROOM_DEMO=true" not in text
        assert "JUPYTERLAB_BEHAVIOR_AUDIT_ALLOW_INSECURE_LOOPBACK=true" not in text
~~~

Retain arbitrary plaintext host rejection. Add the exact local prefix success case only when it does not exist.

- [ ] **Step 2: Prove the policy tests are red**

Run: PYTHONPATH=. uv run --no-project --with 'pytest>=8,<9' python -m pytest scripts/__tests__/test_local_classroom_demo_jupyter.py myextension/tests/test_platform_config.py -q

Expected: FAIL because the launcher is absent.

- [ ] **Step 3: Implement a foreground local launcher**

~~~sh
#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
wheel="$root/dist/myextension-0.4.0-py3-none-any.whl"
demo_root=/private/tmp/classroom-local-demo-jupyter
lsof -nP -iTCP:8888 -sTCP:LISTEN && { echo "127.0.0.1:8888 is already in use" >&2; exit 1; }
test -f "$wheel"
curl -fsS http://127.0.0.1:18081/classroom-api/health/ready >/dev/null
mkdir -p "$demo_root/notebooks" "$demo_root/behavior-audit"
export LOCAL_CLASSROOM_DEMO=true
export JUPYTERLAB_BEHAVIOR_AUDIT_PLATFORM_MODE=student
export JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL=http://127.0.0.1:18081/classroom-api
export JUPYTERLAB_BEHAVIOR_AUDIT_ALLOW_INSECURE_LOOPBACK=true
export JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR="$demo_root/behavior-audit"
exec uv run --no-project --with "$wheel" --with 'jupyterlab>=4,<5' jupyter lab --ServerApp.ip=127.0.0.1 --ServerApp.port=8888 --ServerApp.open_browser=False --ServerApp.token='' --ServerApp.password='' --ServerApp.root_dir="$demo_root/notebooks"
~~~

Build the wheel first with uv build --wheel. The foreground process ends only when its own terminal closes and never kills an existing listener.

- [ ] **Step 4: Verify and commit**

Run: PYTHONPATH=. uv run --no-project --with 'pytest>=8,<9' python -m pytest scripts/__tests__/test_local_classroom_demo_jupyter.py myextension/tests/test_platform_config.py -q && uv build --wheel

Expected: PASS and fresh candidate wheel exists.

~~~bash
git add scripts/start_local_classroom_jupyter.sh scripts/__tests__/test_local_classroom_demo_jupyter.py myextension/tests/test_platform_config.py
git commit -m "feat: add local classroom Jupyter launcher"
~~~

### Task 5: Automate the local closed-loop smoke and write the presentation runbook

**Files:**
- Create: scripts/local_classroom_demo_smoke.py
- Create: deploy/classroom/local-demo/README.md
- Modify: scripts/__tests__/test_local_classroom_demo_facade.py
- Modify: scripts/__tests__/test_local_classroom_demo_compose.py

**Interfaces:**
- Consumes facade fixture bearers, lifecycle scripts, and scripts/classroom_contract_smoke.py.
- Produces a token-safe direct service smoke using only loopback URLs.
- Documents manual browser actions and separate teacher/student profiles.

- [ ] **Step 1: Write the failing smoke source test**

~~~python
def test_smoke_is_loopback_only_and_reuses_the_contract_state_machine() -> None:
    source = SMOKE.read_text(encoding="utf-8")
    assert "http://127.0.0.1:18082" in source
    assert "http://127.0.0.1:18080" in source
    assert "classroom_contract_smoke.run_smoke" in source
    assert "student002-token" in source
    assert "14.103." not in source
~~~

Add a subprocess test with only the façade running; it must report the missing direct-sync endpoint without echoing any token.

- [ ] **Step 2: Prove the smoke test is red**

Run: PYTHONPATH=. uv run --no-project --with 'pytest>=8,<9' python -m pytest scripts/__tests__/test_local_classroom_demo_facade.py scripts/__tests__/test_local_classroom_demo_compose.py -q

Expected: FAIL until the smoke runner is added.

- [ ] **Step 3: Implement token-safe smoke sequencing**

~~~python
from classroom_contract_smoke import HttpSmokeClient, run_smoke

FACADE_BASE_URL = "http://127.0.0.1:18082"
SYNC_BASE_URL = "http://127.0.0.1:18080"

def main() -> int:
    assert_status(FACADE_BASE_URL + "/health/live", 200)
    assert_status(SYNC_BASE_URL + "/health/ready", 200)
    assert_login("teacher001", "local-demo-teacher", "teacher-token")
    assert_login("student001", "local-demo-student", "student001-token")
    assert_status_with_token(
        FACADE_BASE_URL + "/v1/spaces/course-001/algorithm_development",
        "student002-token", 403,
    )
    state_path = Path("/private/tmp/classroom-local-demo-smoke.json")
    try:
        run_smoke(HttpSmokeClient(SYNC_BASE_URL), state_file=state_path, now=datetime.now(UTC), repeat_existing=False)
        state = run_smoke(HttpSmokeClient(SYNC_BASE_URL), state_file=state_path, now=datetime.now(UTC), repeat_existing=True)
        assert state["phase"] == "submitted"
    finally:
        state_path.unlink(missing_ok=True)
    return 0
~~~

Report only local component names and HTTP status; never print a bearer, launch ticket, plugin token, raw evidence, or the state file contents.

- [ ] **Step 4: Write the operator guide**

The README must give exactly these phases:

1. Integration worktree: scripts/start_local_classroom_demo.sh, then uv build --wheel.
2. Separate terminal: scripts/start_local_classroom_jupyter.sh.
3. Frontend worktree after npm ci: scripts/start-local-classroom-frontend.sh.
4. Open teacher and student in separate browser profiles at http://127.0.0.1:5175.
5. Teacher001 opens admin/projects, selects parent-experiment-001, authors/publishes and synchronizes the plan.
6. Student001 opens the assignment, accepts, enters workbench, executes a notebook cell, and submits through the plugin.
7. Teacher refreshes monitoring and opens the brief. Student002 in a third profile is denied course-001 and the assignment.
8. Stop with scripts/stop_local_classroom_demo.sh. For a fresh demo only, run reset_local_classroom_demo.sh --yes-reset-local-demo, then restart services.

Include a troubleshooting table for occupied ports, Compose health failure, missing wheel, wrong Vite flag, and Jupyter session not registered. State why separate profiles are mandatory: Vue stores the bearer in browser local storage.

- [ ] **Step 5: Verify, commit and preserve the main boundary**

Run: scripts/start_local_classroom_demo.sh && PYTHONPATH=scripts uv run --no-project --with 'pytest>=8,<9' python -m pytest scripts/__tests__/test_local_classroom_demo_facade.py scripts/__tests__/test_local_classroom_demo_compose.py scripts/__tests__/test_local_classroom_demo_jupyter.py -q && PYTHONPATH=scripts python scripts/local_classroom_demo_smoke.py

Expected: façade permissions, Compose readiness, exception containment, student002 denial, and direct teacher/student/evidence/brief flow all pass locally.

~~~bash
git add scripts/local_classroom_demo_smoke.py scripts/__tests__/test_local_classroom_demo_facade.py scripts/__tests__/test_local_classroom_demo_compose.py deploy/classroom/local-demo/README.md
git commit -m "test: cover local classroom interactive demo"
~~~

### Task 6: Regression, manual acceptance and unmerged handoff

**Files:**
- Modify: deploy/classroom/local-demo/README.md only if verified results reveal an incorrect command or outcome.

**Interfaces:**
- Consumes both dedicated branch heads.
- Produces test evidence and an unmerged handoff; no push, deployment, BAMS action, candidate change, or root-main mutation.

- [ ] **Step 1: Run the service/plugin regression**

Run:

~~~bash
CLASSROOM_UV_CACHE=/private/tmp/classroom-platform-uv-cache \
PYTHONPATH=services/classroom-sync:services/classroom-sync/src:. \
UV_CACHE_DIR=/private/tmp/classroom-platform-uv-cache \
uv run --no-project --with 'pytest>=8,<9' --with pytest-asyncio \
--with 'pytest-jupyter[server]>=0.6.0' --with 'jupyter_server>=2.4.0,<3' \
--with 'hatch-jupyter-builder>=0.5' --with alembic --with boto3 --with httpx \
--with fastapi --with 'jsonschema>=4.18,<5' --with pydantic \
--with 'psycopg[binary]' --with pyjwt --with sqlalchemy --with uvicorn \
python -m pytest -q
~~~

Expected: all existing and local-demo Python tests pass.

- [ ] **Step 2: Run the frontend regression**

Run:

~~~bash
npm test -- --run
npm run type-check
npm run build -- --mode local-demo
~~~

Expected: existing frontend tests, TypeScript and local-demo build pass.

- [ ] **Step 3: Execute the manual three-profile acceptance**

After scripts/reset_local_classroom_demo.sh --yes-reset-local-demo and a fresh service start, perform the README flow once. Record only readiness status, published plan ID, assignment state, plugin registration/submit state, teacher brief state and student002 denial. Never record tokens, tickets, plugin JWTs, or evidence body data.

Expected: teacher publish → student accept → actual Jupyter monitoring/submit → teacher brief works; student002 is denied.

- [ ] **Step 4: Verify isolation and report**

Run:

~~~bash
git -C /Users/sxh/编程行为监控分析插件_交付版_20260727 status --short --branch
git -C /Users/sxh/编程行为监控分析插件_交付版_20260727/.worktrees/classroom-main-integration status --short --branch
git -C /Users/sxh/编程行为监控分析插件_交付版_20260727/.worktrees/lab-platform-frontend-classroom-ui status --short --branch
docker compose -p classroom-local-demo -f deploy/classroom/local-demo/docker-compose.yml ps
~~~

Expected: root main remains untouched, only the two isolated feature branches contain their commits, and no remote project or BAMS configuration was involved. Report both branch heads, actual commands/results, remaining manual browser evidence, and the explicit absence of merge, push and deployment.
