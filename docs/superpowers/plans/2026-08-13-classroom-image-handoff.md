# 0.4.0 课堂镜像构建安装包 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 生成可校验的 Linux AMD64 课堂插件镜像构建安装包，使 BAMS 运维能用兼容基础镜像构建、验收并导出最终 Docker 镜像 tar。

**Architecture:** 保留 `deploy/bluedot/release-0.4.0/` 作为构建上下文，wheel 是唯一插件二进制输入。构建脚本固定 Linux AMD64，导出脚本先确认目标镜像架构再保存 tar；Python 打包脚本复制白名单文件、产生归档内清单和归档外 SHA-256，从而不依赖 Docker 或真实基础镜像就能验证交付包。

**Tech Stack:** POSIX shell、Docker CLI、Python 标准库 `tarfile` / `hashlib` / `pathlib`、pytest、SHA-256。

## Global Constraints

- 当前 `dap_pytorch_1.10.0:cpu@sha256:c7c2…4632` 是 JupyterLab 3.2.9 / Jupyter Server 1.13.5，严禁用它构建 0.4.0。
- 运维提供的基础镜像必须为 `linux/amd64`，且含 Python 3.10+、JupyterLab 4.x、Jupyter Server 2.x 与 `jsonschema`。
- 构建输入必须是完整的 `repository@sha256:实际摘要值`，不得将可变 tag 当作生产输入。
- 交付包、脚本、镜像历史、测试输出和文档不得包含 API key、JWT、S3 凭据或课堂票据。
- 本任务不执行 Docker push、SSH/SCP、BAMS 模板替换、容器重启或数据库迁移。
- 学生入口保持 `https://14.103.139.131:40037`；不将 40002 写入安装或验收步骤。

---

## File Map

```text
deploy/bluedot/release-0.4.0/build_image.sh                 # Linux AMD64 镜像构建
deploy/bluedot/release-0.4.0/export_image.sh                # 已验收镜像安全导出
deploy/bluedot/release-0.4.0/INSTALL.md                     # 运维逐步安装/回滚说明
scripts/package_classroom_image_handoff.py                  # 白名单归档与 SHA-256 生成
myextension/tests/test_classroom_release_040.py             # 脚本与归档行为回归
releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz
releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz.sha256
```

### Task 1: 固定 Linux AMD64 构建并安全导出镜像

**Files:**
- Modify: `deploy/bluedot/release-0.4.0/build_image.sh`
- Create: `deploy/bluedot/release-0.4.0/export_image.sh`
- Modify: `myextension/tests/test_classroom_release_040.py`

**Interfaces:**
- `build_image.sh base_image_digest target_image` checks `SHA256SUMS`, then invokes `docker build --platform linux/amd64`.
- `export_image.sh target_image output_tar` invokes `verify_image.sh`, requires `docker image inspect` to return `linux/amd64`, writes `output_tar`, then writes `output_tar.sha256` in `sha256  filename` format.
- Both scripts exit nonzero before `docker build`/`docker save` for invalid arguments, failed verification or non-amd64 architecture.

- [x] **Step 1: Write failing shell-contract tests**

Add tests that fake the `docker` executable and assert exact observable behavior:

```python
def test_build_script_uses_linux_amd64_after_wheel_checksum(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    artifacts = bundle / "artifacts"
    fake_bin = tmp_path / "bin"
    artifacts.mkdir(parents=True)
    fake_bin.mkdir()
    shutil.copy2(_script("build_image.sh"), bundle / "build_image.sh")
    wheel = artifacts / WHEEL_NAME
    wheel.write_bytes(b"synthetic-wheel")
    (bundle / "SHA256SUMS").write_text(
        f"{hashlib.sha256(wheel.read_bytes()).hexdigest()}  artifacts/{WHEEL_NAME}\\n",
        encoding="utf-8",
    )
    docker_args = tmp_path / "docker-args"
    _write_executable(fake_bin / "docker", '#!/bin/sh\\nprintf "%s\\n" "$@" > "$DOCKER_ARGS_FILE"\\n')
    completed = subprocess.run(
        ["sh", str(bundle / "build_image.sh"), "registry.invalid/base@sha256:abc", "audit:0.4"],
        env={**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "DOCKER_ARGS_FILE": str(docker_args)},
        check=False,
    )
    assert completed.returncode == 0
    assert docker_args.read_text(encoding="utf-8").splitlines()[:4] == ["build", "--platform", "linux/amd64", "--build-arg"]


def test_export_script_refuses_non_amd64_before_docker_save(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    fake_bin = tmp_path / "bin"
    bundle.mkdir()
    fake_bin.mkdir()
    shutil.copy2(_script("export_image.sh"), bundle / "export_image.sh")
    _write_executable(bundle / "verify_image.sh", "#!/bin/sh\\nexit 0\\n")
    docker_args = tmp_path / "docker-args"
    _write_executable(
        fake_bin / "docker",
        '#!/bin/sh\\nprintf "%s\\n" "$@" >> "$DOCKER_ARGS_FILE"\\n[ "$1" = image ] && printf "linux/arm64\\n"\\n',
    )
    completed = subprocess.run(
        ["sh", str(bundle / "export_image.sh"), "audit:0.4", str(tmp_path / "audit.tar")],
        env={**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "DOCKER_ARGS_FILE": str(docker_args)},
        check=False,
    )
    assert completed.returncode != 0
    assert "save" not in docker_args.read_text(encoding="utf-8")
```

- [x] **Step 2: Run the new tests to verify they fail**

Run:

```bash
UV_CACHE_DIR=/private/tmp/classroom-platform-uv-cache \
uv run --no-project --with pytest --with jupyter_server --with jsonschema \
python -m pytest myextension/tests/test_classroom_release_040.py -q
```

Expected: FAIL because the AMD64 build option and `export_image.sh` do not yet exist.

- [x] **Step 3: Implement the minimum shell behavior**

In `build_image.sh`, retain checksum validation and add the exact Docker arguments:

```sh
docker build \
  --platform linux/amd64 \
  --build-arg "BLUEDOT_BASE_IMAGE=$1" \
  --tag "$2" \
  "$script_dir"
```

Create `export_image.sh` with this ordered contract:

```sh
"$script_dir/verify_image.sh" "$1"
platform=$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$1")
test "$platform" = linux/amd64
docker save --output "$2" "$1"
sha256sum "$2" > "$2.sha256"  # use shasum -a 256 when sha256sum is unavailable
```

It must reject output paths without a `.tar` suffix and must not delete any existing image.

- [x] **Step 4: Run the targeted tests and shell syntax checks**

Run:

```bash
UV_CACHE_DIR=/private/tmp/classroom-platform-uv-cache \
uv run --no-project --with pytest --with jupyter_server --with jsonschema \
python -m pytest myextension/tests/test_classroom_release_040.py -q
sh -n deploy/bluedot/release-0.4.0/build_image.sh
sh -n deploy/bluedot/release-0.4.0/export_image.sh
```

Expected: tests pass; both shell parsers exit 0.

- [x] **Step 5: Commit the script contract**

```bash
git add deploy/bluedot/release-0.4.0/build_image.sh \
  deploy/bluedot/release-0.4.0/export_image.sh \
  myextension/tests/test_classroom_release_040.py
git commit -m "build: export classroom amd64 image safely"
```

### Task 2: 创建可复现的安装归档和运维说明

**Files:**
- Create: `scripts/package_classroom_image_handoff.py`
- Create: `deploy/bluedot/release-0.4.0/INSTALL.md`
- Modify: `myextension/tests/test_classroom_release_040.py`
- Create: `releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz`
- Create: `releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz.sha256`

**Interfaces:**
- `package_classroom_image_handoff.py --source release_directory --output archive_path` includes exactly `.dockerignore`, `Dockerfile`, `README.md`, `INSTALL.md`, `SHA256SUMS`, `runtime.env.example`, `build_image.sh`, `verify_image.sh`, `export_image.sh` and `artifacts/myextension-0.4.0-py3-none-any.whl` under the archive root `behavior-audit-classroom-0.4.0/`.
- It writes an archive-internal `SHA256SUMS` for all whitelisted payload files except itself and an adjacent archive checksum file.
- `INSTALL.md` names the required base-image versions and digest-only input, instructs `build_image.sh`, `verify_image.sh`, `export_image.sh`, test-template import and old-digest rollback; it contains no remote command or secret.

- [x] **Step 1: Write failing archive and documentation tests**

Add tests that execute the Python script in a temporary directory and inspect the produced archive:

```python
def test_handoff_archive_has_only_whitelisted_payloads_and_checksums(tmp_path: Path) -> None:
    archive = tmp_path / "handoff.tar.gz"
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/package_classroom_image_handoff.py"), "--source", str(RELEASE_ROOT), "--output", str(archive)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    with tarfile.open(archive, "r:gz") as bundle:
        names = {member.name for member in bundle.getmembers() if member.isfile()}
        assert all(name.startswith("behavior-audit-classroom-0.4.0/") for name in names)
        assert not any(".git" in name or "node_modules" in name or "secret" in name.lower() for name in names)
        manifest = bundle.extractfile("behavior-audit-classroom-0.4.0/SHA256SUMS").read().decode()
        assert "artifacts/myextension-0.4.0-py3-none-any.whl" in manifest
    assert (tmp_path / "handoff.tar.gz.sha256").is_file()


def test_install_guide_requires_jupyterlab4_digest_and_safe_rollback() -> None:
    guide = (RELEASE_ROOT / "INSTALL.md").read_text(encoding="utf-8")
    assert "JupyterLab 4" in guide
    assert "Jupyter Server 2" in guide
    assert "@sha256:" in guide
    assert "docker push" not in guide
    assert "40002" not in guide
    assert "/workspace/result" in guide
    assert "旧 digest" in guide
```

- [x] **Step 2: Run the new tests to verify they fail**

Run the same targeted pytest command from Task 1.

Expected: FAIL because the packager and `INSTALL.md` do not exist.

- [x] **Step 3: Implement a deterministic whitelist packager and installation guide**

Use `tarfile.open(output, "w:gz")`, `hashlib.sha256`, sorted relative paths and a fixed archive root. Reject a source tree with a missing or extra required payload, make each archived member mode non-world-writable, and generate checksums from bytes immediately before adding them. Write the external archive checksum with the same `sha256  filename` convention.

In `INSTALL.md`, show these concrete command shapes without real registry values or credentials:

```bash
shasum -a 256 -c behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz.sha256
./build_image.sh 'repository@sha256:实际摘要值' 'behavior-audit:0.4.0-classroom'
./verify_image.sh 'behavior-audit:0.4.0-classroom'
./export_image.sh 'behavior-audit:0.4.0-classroom' behavior-audit-0.4.0-linux-amd64.tar
```

State that the BAMS test template receives the generated tar/digest, runs with `PLATFORM_MODE=student`, a real HTTPS sync URL and persistent `/workspace/result`, and rolls back by pointing only that template to its recorded old digest.

- [x] **Step 4: Build the release archive and verify it independently**

Run:

```bash
UV_CACHE_DIR=/private/tmp/classroom-platform-uv-cache \
uv run --no-project python scripts/package_classroom_image_handoff.py \
  --source deploy/bluedot/release-0.4.0 \
  --output releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz
shasum -a 256 -c releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz.sha256
UV_CACHE_DIR=/private/tmp/classroom-platform-uv-cache \
uv run --no-project --with pytest --with jupyter_server --with jsonschema \
python -m pytest myextension/tests/test_classroom_release_040.py -q
```

Expected: external checksum passes and all targeted tests pass without Docker or a real BAMS base image.

- [x] **Step 5: Commit the handoff archive**

```bash
git add deploy/bluedot/release-0.4.0/INSTALL.md \
  scripts/package_classroom_image_handoff.py \
  myextension/tests/test_classroom_release_040.py \
  releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz \
  releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz.sha256
git commit -m "build: package classroom image handoff"
```

### Task 3: 进行全量候选包回归并交接到本地联调计划

**Files:**
- Modify: `README.md`
- Modify: `项目交接文档.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Documentation links the install archive and states it is a build kit, not a final image.
- The next executable plan is `docs/superpowers/plans/2026-08-12-classroom-integration-deployment.md`, starting at Task 1 only after this handoff gate is green.

- [x] **Step 1: Review the release-documentation boundary**

Human-facing operational prose is reviewed as prose, not converted into a brittle source-text test. Read the three release documents and confirm they currently lack the build-kit artifact link and compatibility boundary; then make the minimal change in the next step.

- [x] **Step 2: Make the minimum documentation changes**

Add one concise release paragraph per document. Do not add commands that push, upload, replace a BAMS template or restart a remote container.

- [x] **Step 3: Run the complete local quality gate**

Run:

```bash
UV_CACHE_DIR=/private/tmp/classroom-platform-uv-cache \
uv run --no-project --with pytest --with jupyter_server --with jsonschema \
python -m pytest myextension/tests -q
UV_CACHE_DIR=/private/tmp/classroom-platform-uv-cache \
uv run --no-project --with jupyterlab jlpm lint:check
UV_CACHE_DIR=/private/tmp/classroom-platform-uv-cache \
uv run --no-project --with jupyterlab jlpm test --runInBand
UV_CACHE_DIR=/private/tmp/classroom-platform-uv-cache \
uv run --no-project --with jupyterlab jlpm build:prod
UV_CACHE_DIR=/private/tmp/classroom-platform-uv-cache \
uv run --no-project python scripts/package_classroom_image_handoff.py \
  --source deploy/bluedot/release-0.4.0 \
  --output releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz
shasum -a 256 -c releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz.sha256
git diff --check
```

Expected: all source checks pass, the handoff archive checksum passes, and the diff check emits no errors. Actual Docker build/run remains explicitly pending a compatible BAMS base digest.

- [x] **Step 4: Commit the completed handoff**

```bash
git add README.md 项目交接文档.md CHANGELOG.md \
  myextension/tests/test_classroom_release_040.py \
  releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz \
  releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz.sha256
git commit -m "docs: hand off classroom image build kit"
```

## Plan Self-Review

- Spec coverage: Task 1 supplies architecture-safe build/export scripts; Task 2 supplies the deterministic archive and handoff document; Task 3 verifies documentation, full local gates and records the explicit Docker limitation.
- No placeholders: no task relies on unspecified files, functions or external commands; values needing operator input are intentionally non-executable documentation text, not shell variables.
- Type consistency: `build_image.sh`, `verify_image.sh`, `export_image.sh`, the packager CLI and archive names remain identical across all tasks.
- Scope: local synchronization service/Compose is intentionally excluded and starts only after this independently releasable handoff package is verified, using the existing classroom integration plan.
