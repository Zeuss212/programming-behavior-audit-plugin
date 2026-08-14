# BAMS 课堂同步 HTTPS 接入与候选发布实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 BAMS 学生插件通过 `/classroom-api` HTTPS 入口访问课堂同步服务，并在构建前阻止非 Linux AMD64 候选镜像发布。

**Architecture:** BAMS ingress 对外终止 TLS，只代理 `/classroom-api/` 并剥离此前缀；Jupyter Server 使用该 HTTPS 基地址调用既有 `/v1/classroom/plugin/...` API。前端候选镜像通过受测的 buildx 脚本固定构建为 `linux/amd64`；代码阶段只产生配置模板、候选制品与文档，不部署到 BAMS 或替换 `5179`。

**Tech Stack:** Python 3.10+、Pytest、Tornado/Jupyter Server、Nginx、POSIX shell、Docker Buildx、Vue 3、Vite、Vitest。

## Global Constraints

- 所有课堂服务和插件请求都使用 HTTPS；学生模式拒绝所有 loopback HTTPS/HTTP 上游，测试用 HTTP loopback 仅在 `JUPYTERLAB_BEHAVIOR_AUDIT_ALLOW_INSECURE_LOOPBACK=true` 时允许。
- BAMS ingress 只能代理 `/classroom-api/`；不得代理 `40002`、BAMS 工作台、MinIO 或对象存储端口。
- 入口不能记录 Authorization、query、ticket、plugin token、请求体或任何 Secret。
- 所有学生镜像和前端候选镜像必须为 `linux/amd64`；构建脚本的 `--push` 只在新的发布授权后运行。
- 本轮不执行 SSH 写操作、镜像推送、模板替换、容器替换、迁移、数据删除或正式 `5179` 发布。
- 当前工作在两个已隔离 worktree 完成：课堂插件/服务使用 `codex/classroom-platform`；前端候选使用 `codex/classroom-ui`。根目录的用户改动不修改、不暂存、不提交。
- 前端干净基线的全量 Oxlint 有 7 个既存错误、ESLint 有 5 个既存错误；它们位于本计划不修改的文件且由 `681faa08`、`20f0ad2f`、`dfb3c515` 引入。前端任务仅对新增测试文件执行 Oxlint/ESLint，并在交付记录中保留该全量 lint 基线失败；不得将全量 lint 表述为通过。
- 每个任务先执行指定失败测试，再写最小实现；一个任务通过完整指定验证后独立提交。

---

## File Map

```text
classroom-platform/
  myextension/platform_config.py                          # 学生模式同步 URL 校验
  myextension/tests/test_platform_config.py               # URL 回归
  myextension/tests/test_classroom_release_040.py         # 0.4.0 运行配置与发布门禁
  deploy/classroom/nginx/bams-classroom-api-http.conf.template # BAMS HTTP 日志格式
  deploy/classroom/nginx/bams-classroom-api-location.conf.template # BAMS TLS server 入口模板
  scripts/__tests__/test_bams_classroom_nginx_config.py   # 模板安全契约
  deploy/bluedot/release-0.4.0/runtime.env.example        # 学生运行变量示例
  deploy/bluedot/release-0.4.0/{README.md,INSTALL.md}     # 学生镜像安装边界
  docs/runbooks/bams-classroom-api-ingress.md              # 运维预检、回滚与验收记录
  releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz
  releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz.sha256

lab-platform-frontend-classroom-ui/
  scripts/build-classroom-candidate.sh                     # AMD64 前端候选构建入口
  src/__tests__/build-classroom-candidate.test.ts          # 构建命令参数回归
  README.md                                                 # 候选构建与平台校验说明
```

### Task 1: 固化学生同步 HTTPS URL 的安全校验

**Files:**
- Modify: `myextension/platform_config.py:14-85`
- Modify: `myextension/tests/test_platform_config.py`
- Modify: `myextension/tests/test_classroom_release_040.py:25-61,200-225`
- Modify: `deploy/bluedot/release-0.4.0/runtime.env.example`
- Modify: `deploy/bluedot/release-0.4.0/README.md`
- Modify: `deploy/bluedot/release-0.4.0/INSTALL.md`

**Interfaces:**
- `PlatformConfig._validate_sync_base_url(value: str, allow_insecure_loopback: bool) -> None` accepts a non-loopback HTTPS host, including a `/classroom-api` path.
- It permits `http://127.0.0.1` only with `JUPYTERLAB_BEHAVIOR_AUDIT_ALLOW_INSECURE_LOOPBACK=true`; it rejects `https://127.0.0.1`, `https://localhost`, and `https://[::1]` in student mode.
- The packaged example is `https://classroom-sync.example.invalid/classroom-api`; it contains no token or secret.

- [ ] **Step 1: Write the failing URL regression test**

Add to `myextension/tests/test_platform_config.py`:

```python
@pytest.mark.parametrize(
    "value",
    [
        "https://127.0.0.1:18080/classroom-api",
        "https://localhost/classroom-api",
        "https://[::1]/classroom-api",
    ],
)
def test_student_mode_rejects_loopback_https_sync_service_urls(value: str):
    with pytest.raises(RuntimeError, match="loopback"):
        PlatformConfig.from_env(
            {
                "JUPYTERLAB_BEHAVIOR_AUDIT_PLATFORM_MODE": "student",
                "JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL": value,
            }
        )


def test_student_mode_accepts_bams_https_classroom_api_prefix(tmp_path):
    config = PlatformConfig.from_env(
        {
            "JUPYTERLAB_BEHAVIOR_AUDIT_PLATFORM_MODE": "student",
            "JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL": "https://bams.example.invalid/classroom-api",
            "JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR": str(tmp_path),
        }
    )
    assert config.sync_base_url == "https://bams.example.invalid/classroom-api"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest myextension/tests/test_platform_config.py::test_student_mode_rejects_loopback_https_sync_service_urls -q`

Expected: FAIL because the existing HTTPS branch accepts loopback hosts.

- [ ] **Step 3: Write the minimal validation implementation**

In `myextension/platform_config.py`, add `LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}` beside the environment constants. Replace `_validate_sync_base_url` with:

```python
@staticmethod
def _validate_sync_base_url(value: str, allow_insecure_loopback: bool) -> None:
    parsed = urlparse(value)
    if parsed.hostname in LOOPBACK_HOSTS:
        if allow_insecure_loopback and parsed.scheme == "http":
            return
        raise RuntimeError("student mode sync_base_url must not target a loopback host.")
    if parsed.scheme == "https" and parsed.hostname:
        return
    raise RuntimeError("student mode sync_base_url must use HTTPS.")
```

Keep `PlatformSyncClient` unchanged: its existing `rstrip("/")` produces `/classroom-api/v1/classroom/plugin/...`.

- [ ] **Step 4: Synchronize candidate runtime configuration and release tests**

Set `JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL=https://classroom-sync.example.invalid/classroom-api` in `runtime.env.example`. Update the identical expected value and fake Docker assertion in `test_classroom_release_040.py`. In both 0.4.0 release documents, state that the value is the BAMS HTTPS ingress base plus `/classroom-api`, never the classroom host loopback address or port `40037`.

- [ ] **Step 5: Run verification**

Run:

```bash
python -m pytest myextension/tests/test_platform_config.py myextension/tests/test_platform_registration.py myextension/tests/test_classroom_release_040.py -q
sh -n deploy/bluedot/release-0.4.0/build_image.sh
sh -n deploy/bluedot/release-0.4.0/export_image.sh
sh -n deploy/bluedot/release-0.4.0/verify_image.sh
```

Expected: all selected tests pass and all three scripts have valid POSIX shell syntax.

- [ ] **Step 6: Commit**

```bash
git add myextension/platform_config.py myextension/tests/test_platform_config.py myextension/tests/test_classroom_release_040.py deploy/bluedot/release-0.4.0
git commit -m "fix: require non-loopback classroom sync HTTPS"
```

### Task 2: 添加 BAMS 入口代理模板及安全契约

**Files:**
- Create: `deploy/classroom/nginx/bams-classroom-api-http.conf.template`
- Create: `deploy/classroom/nginx/bams-classroom-api-location.conf.template`
- Create: `scripts/__tests__/test_bams_classroom_nginx_config.py`
- Create: `docs/runbooks/bams-classroom-api-ingress.md`

**Interfaces:**
- The BAMS Nginx renderer supplies the private `CLASSROOM_SYNC_UPSTREAM` value; the location template contains only `${CLASSROOM_SYNC_UPSTREAM}`, not a host, port, credential, or public loopback address.
- The HTTP-context template defines `classroom_safe` as a log format that uses `$uri` rather than query-bearing `$request_uri`; the location template is included only inside BAMS's TLS `server` block.
- `location /classroom-api/` rewrites `/classroom-api/v1/...` to `/v1/...`, forwards Authorization and request correlation headers, and limits the body to `2m`.
- `location ~ ^/classroom-api/v1/classroom/classrooms/[^/]+/events$` is evaluated first and has `proxy_buffering off`, `proxy_cache off`, and one-hour stream timeouts.

- [ ] **Step 1: Write the failing static ingress contract test**

Create `scripts/__tests__/test_bams_classroom_nginx_config.py`:

```python
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HTTP_CONFIG = ROOT / "deploy/classroom/nginx/bams-classroom-api-http.conf.template"
LOCATION_CONFIG = ROOT / "deploy/classroom/nginx/bams-classroom-api-location.conf.template"


def test_bams_ingress_renders_a_syntax_valid_proxy_without_sensitive_logging(tmp_path: Path):
    http_config = HTTP_CONFIG.read_text(encoding="utf-8")
    config = LOCATION_CONFIG.read_text(encoding="utf-8")
    assert "location = /classroom-api" in config
    assert "location ~ ^/classroom-api/v1/classroom/classrooms/[^/]+/events$" in config
    assert "location /classroom-api/" in config
    assert "proxy_pass ${CLASSROOM_SYNC_UPSTREAM};" in config
    assert "proxy_set_header Authorization $http_authorization;" in config
    assert "client_max_body_size 2m;" in config
    assert "proxy_buffering off;" in config
    assert "proxy_cache off;" in config
    assert "log_format classroom_safe" in http_config
    assert "$uri" in http_config
    assert "$request_uri" not in http_config
    assert "$http_authorization" not in http_config
    assert "40002" not in config
    assert "40037" not in config
    assert "127.0.0.1" not in config

    rendered_location = config.replace(
        "${CLASSROOM_SYNC_UPSTREAM}", "http://sync-api:8080"
    )
    rendered = tmp_path / "nginx.conf"
    rendered.write_text(
        f"events {{}}\nhttp {{\n{http_config}\nserver {{\nlisten 8080;\n"
        f"{rendered_location}\n}}\n}}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "docker", "run", "--rm",
            "-v", f"{rendered}:/etc/nginx/nginx.conf:ro",
            "nginx:1.27-alpine", "nginx", "-t",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest scripts/__tests__/test_bams_classroom_nginx_config.py -q`

Expected: FAIL with `FileNotFoundError` for the BAMS templates before Docker is invoked.

- [ ] **Step 3: Write the Nginx location template**

Create `deploy/classroom/nginx/bams-classroom-api-http.conf.template` with:

```nginx
log_format classroom_safe '$remote_addr [$time_local] "$request_method $uri $server_protocol" $status $body_bytes_sent';
```

Create `deploy/classroom/nginx/bams-classroom-api-location.conf.template` with:

```nginx

location = /classroom-api { return 308 /classroom-api/; }

location ~ ^/classroom-api/v1/classroom/classrooms/[^/]+/events$ {
    rewrite ^/classroom-api/(.*)$ /$1 break;
    proxy_pass ${CLASSROOM_SYNC_UPSTREAM};
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Request-ID $request_id;
    proxy_set_header Authorization $http_authorization;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}

location /classroom-api/ {
    rewrite ^/classroom-api/(.*)$ /$1 break;
    proxy_pass ${CLASSROOM_SYNC_UPSTREAM};
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Request-ID $request_id;
    proxy_set_header Authorization $http_authorization;
    proxy_read_timeout 60s;
    proxy_send_timeout 60s;
    client_max_body_size 2m;
}
```

- [ ] **Step 4: Write the BAMS ingress runbook**

In `docs/runbooks/bams-classroom-api-ingress.md`, require the BAMS operator to record, before a deployment: HTTPS DNS/certificate validation, private upstream reachability from the ingress, the current test-template digest, candidate/old frontend digests, a database backup identifier, and an exact rollback action. State that its renderer installs the HTTP template at Nginx `http` scope and injects a private upstream into the location template inside the existing BAMS TLS `server` block. Record read-only acceptance requests: `GET /classroom-api/health/ready`, plugin ticket registration, one gzip evidence upload, one brief submit, and teacher brief read. Any failure reverts only the test-template or candidate frontend digest and retains PostgreSQL/MinIO data.

- [ ] **Step 5: Run verification**

Run:

```bash
python -m pytest scripts/__tests__/test_bams_classroom_nginx_config.py scripts/__tests__/test_classroom_nginx_config.py -q
git diff --check
```

Expected: both proxy contract suites pass, the rendered template passes `nginx -t` in the exact Nginx 1.27 image, and Git reports no whitespace errors. If that image is unavailable locally, stop and report the environment blocker rather than skipping the syntax test.

- [ ] **Step 6: Commit**

```bash
git add deploy/classroom/nginx/bams-classroom-api-http.conf.template deploy/classroom/nginx/bams-classroom-api-location.conf.template scripts/__tests__/test_bams_classroom_nginx_config.py docs/runbooks/bams-classroom-api-ingress.md
git commit -m "ops: define BAMS classroom API ingress contract"
```

### Task 3: 阻止前端候选发布生成非 AMD64 镜像

**Files:**
- Create: `scripts/build-classroom-candidate.sh`
- Create: `src/__tests__/build-classroom-candidate.test.ts`
- Modify: `README.md:47-59`

**Interfaces:**
- `sh scripts/build-classroom-candidate.sh registry.example.invalid/fincolab:classroom-candidate` accepts exactly one non-empty target reference.
- The script invokes `docker buildx build --platform linux/amd64 --build-arg VITE_CLASSROOM_MONITORING_ENABLED=true --tag TARGET --push .` in the frontend worktree.
- The script does not run from `npm run build`; it is an explicit post-verification release command and needs separate push/deployment authorization.

- [ ] **Step 1: Write the failing Buildx argument test**

Create `src/__tests__/build-classroom-candidate.test.ts`:

```ts
import { execFileSync } from 'node:child_process'
import { chmodSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, describe, expect, it } from 'vitest'

const root = fileURLToPath(new URL('../..', import.meta.url))
const script = join(root, 'scripts/build-classroom-candidate.sh')
const temporaryDirectories: string[] = []

afterEach(() => temporaryDirectories.splice(0).forEach(path => rmSync(path, { recursive: true, force: true })))

describe('classroom candidate image build', () => {
  it('builds and pushes only a linux amd64 classroom-enabled candidate', () => {
    const temporary = mkdtempSync(join(tmpdir(), 'classroom-build-'))
    temporaryDirectories.push(temporary)
    const bin = join(temporary, 'bin')
    const capture = join(temporary, 'docker-args')
    mkdirSync(bin)
    const docker = join(bin, 'docker')
    writeFileSync(docker, '#!/bin/sh\nprintf "%s\\n" "$@" > "$DOCKER_ARGS_FILE"\n')
    chmodSync(docker, 0o755)

    execFileSync('sh', [script, 'registry.example.invalid/fincolab:classroom-candidate'], {
      cwd: root,
      env: { ...process.env, PATH: `${bin}:${process.env.PATH}`, DOCKER_ARGS_FILE: capture },
    })

    expect(readFileSync(capture, 'utf8').trim().split('\n')).toEqual([
      'buildx', 'build', '--platform', 'linux/amd64', '--build-arg',
      'VITE_CLASSROOM_MONITORING_ENABLED=true', '--tag',
      'registry.example.invalid/fincolab:classroom-candidate', '--push', '.',
    ])
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- --run src/__tests__/build-classroom-candidate.test.ts`

Expected: FAIL because `scripts/build-classroom-candidate.sh` does not exist.

- [ ] **Step 3: Write the single-purpose candidate build script**

Create `scripts/build-classroom-candidate.sh` with:

```sh
#!/bin/sh
set -eu

if [ "$#" -ne 1 ] || [ -z "$1" ]; then
  echo "Usage: $0 <target-image>" >&2
  exit 64
fi

docker buildx build \
  --platform linux/amd64 \
  --build-arg VITE_CLASSROOM_MONITORING_ENABLED=true \
  --tag "$1" \
  --push \
  .
```

Mark it executable. Do not add credentials, registry login, SSH commands, or a default image tag.

- [ ] **Step 4: Document the release gate**

In the frontend `README.md`, add a subsection after “上线前检查” that requires the full frontend quality suite before calling the script, repeats `linux/amd64`, identifies `--push` as an external action requiring approval, and requires server-side read-only `nerdctl image inspect` platform confirmation before candidate replacement. State that `no match for platform in manifest` blocks deployment: rebuild with this script rather than changing the server architecture.

- [ ] **Step 5: Run verification**

Run:

```bash
npm test -- --run src/__tests__/build-classroom-candidate.test.ts src/__tests__/nginx-classroom-proxy.test.ts
npm run type-check
npm run build
npx --no-install oxlint src/__tests__/build-classroom-candidate.test.ts
npx --no-install eslint src/__tests__/build-classroom-candidate.test.ts
sh -n scripts/build-classroom-candidate.sh
git diff --check
```

Expected: both focused tests, type check, build, changed-test linters, shell syntax, and whitespace check pass. The known full-tree lint baseline remains recorded as failing outside this task's files.

- [ ] **Step 6: Commit**

```bash
git add scripts/build-classroom-candidate.sh src/__tests__/build-classroom-candidate.test.ts README.md
git commit -m "build: enforce amd64 classroom candidate images"
```

### Task 4: 重新生成并验证学生候选离线构建包

**Files:**
- Modify: `releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz`
- Modify: `releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz.sha256`
- Modify: `myextension/tests/test_classroom_release_040.py`

**Interfaces:**
- `scripts/package_classroom_image_handoff.py --source deploy/bluedot/release-0.4.0 --output releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz` creates the deterministic handoff archive and matching `.sha256` file.
- The archive contains the `/classroom-api` runtime example and all checksummed release payloads; it is an AMD64 build kit, not an already runnable BAMS image.

- [ ] **Step 1: Write the failing archived-runtime regression test**

Add to `myextension/tests/test_classroom_release_040.py`:

```python
def test_checked_in_handoff_archive_uses_the_classroom_api_runtime_prefix() -> None:
    archive = ROOT / "releases" / "behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz"
    with tarfile.open(archive, "r:gz") as bundle:
        member = bundle.getmember("behavior-audit-classroom-0.4.0/runtime.env.example")
        runtime = bundle.extractfile(member)
        assert runtime is not None
        assert b"JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL=https://classroom-sync.example.invalid/classroom-api\n" in runtime.read()
```

- [ ] **Step 2: Run test to verify it fails before rebuilding**

Run: `python -m pytest myextension/tests/test_classroom_release_040.py::test_checked_in_handoff_archive_uses_the_classroom_api_runtime_prefix -q`

Expected: FAIL because the checked-in archive still contains the previous runtime example.

- [ ] **Step 3: Regenerate the deterministic archive and checksum**

Run `python scripts/package_classroom_image_handoff.py --source deploy/bluedot/release-0.4.0 --output releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz`, then run `shasum -a 256 -c releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz.sha256`.

The package script writes the matching checksum; do not rename the archive, add a registry credential, or create an image from it.

- [ ] **Step 4: Run release verification**

Run `python -m pytest myextension/tests/test_classroom_release_040.py -q`, `git diff --check`, and `git status --short --branch`.

Expected: release tests pass, archive checksum verifies, Git reports no whitespace errors, and only Task 4 files remain before commit.

- [ ] **Step 5: Commit**

Run `git add myextension/tests/test_classroom_release_040.py releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz.sha256` and `git commit -m "build: refresh classroom handoff runtime configuration"`.

### Task 5: 汇总本地验证并准备部署授权信息

**Files:**
- Modify: `docs/runbooks/bams-classroom-api-ingress.md`
- Test: all Task 1–4 commands

**Interfaces:**
- The runbook ends with a dated local-evidence section recording commit SHAs, exact commands, results, unexecuted external actions, and deployment prerequisites.
- It contains no secrets, bearer tokens, real upstream addresses, or executable commands that modify BAMS/production.

- [ ] **Step 1: Record verified local evidence**

Append a dated “本地验证证据” section that records: the plugin test command/result, ingress static-test command/result, frontend test/type/build/changed-test-lint command/result, archive SHA-256 result, the two worktree commits, the 7 Oxlint and 5 ESLint baseline failures outside the task files, and the statement “真实 BAMS 配置、镜像推送、测试模板替换、候选容器替换均未执行”.

- [ ] **Step 2: Perform final read-only verification in both worktrees**

Run in `classroom-platform`: `python -m pytest myextension/tests/test_platform_config.py myextension/tests/test_platform_registration.py myextension/tests/test_classroom_release_040.py scripts/__tests__/test_bams_classroom_nginx_config.py scripts/__tests__/test_classroom_nginx_config.py -q`, then `git diff --check` and `git status --short --branch`.

Run in `lab-platform-frontend-classroom-ui`: `npm test -- --run src/__tests__/build-classroom-candidate.test.ts src/__tests__/nginx-classroom-proxy.test.ts`, `npm run type-check`, `npm run build`, `npx --no-install oxlint src/__tests__/build-classroom-candidate.test.ts`, `npx --no-install eslint src/__tests__/build-classroom-candidate.test.ts`, `sh -n scripts/build-classroom-candidate.sh`, `git diff --check`, and `git status --short --branch`.

Expected: all listed scoped quality commands pass and both worktrees are clean after their task commits. The pre-existing full-tree lint baseline remains an explicitly documented gap.

- [ ] **Step 3: Commit the local verification record**

Run `git add docs/runbooks/bams-classroom-api-ingress.md` and `git commit -m "docs: record classroom API ingress verification"`.

- [ ] **Step 4: Stop at the deployment authorization gate**

Present the BAMS operator with the candidate frontend image reference, candidate student image digest after its approved build, current test-template digest, BAMS ingress HTTPS hostname/path, private upstream reachability evidence, certificate result, database backup identifier, and the documented rollback action. Do not push, deploy, replace, migrate, or restart anything until the user explicitly authorizes that exact action set.
