# myextension 0.2.1 Self-Contained Delivery Folder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create one top-level, self-contained `myextension-0.2.1-BLUEDOT-完整交付包/` directory that a human administrator or Codex can understand and use without reading the repository.

**Architecture:** Copy the already verified BLUEDOT release assets byte-for-byte into a new top-level directory, preserving the Docker build context and script permissions. Add a short human entrypoint, scoped Codex instructions, and a machine-readable manifest; then verify file identity, SHA-256, JSON validity, safety, and Git provenance without rebuilding or deploying anything.

**Tech Stack:** Markdown, JSON, POSIX shell, Git, SHA-256, existing Python 3.12 verification environment.

## Global Constraints

- Target directory is exactly `myextension-0.2.1-BLUEDOT-完整交付包/` at repository root.
- Source release is exactly `deploy/bluedot/release-0.2.1/` from fixed-delivery commit `2a3dc75` and tag `bluedot-delivery-0.2.1-fixed`.
- Package remains `myextension 0.2.1`.
- Wheel path is `artifacts/myextension-0.2.1-py3-none-any.whl`.
- Wheel SHA-256 is `8436b8e69f9e25c58df68c0024723c660e9fe8751c52a60b320c1e97f28ea16e`.
- Do not rebuild the wheel, modify plugin code, call real AI, build/push an image, log into a registry, or operate BLUEDOT.
- Do not copy `.DS_Store`, logs, notebooks, source code, `.env`, `.ark_ai_config.json`, credentials, or unrelated repository files.
- Preserve `build_image.sh` and `verify_image.sh` executable permissions.

---

## File Map

**Copy byte-for-byte from `deploy/bluedot/release-0.2.1/`**

- `.dockerignore`: restricted Docker build context.
- `Dockerfile`: BLUEDOT base-image installation recipe.
- `README.md`: complete Chinese installation, deployment, acceptance, troubleshooting, and rollback guide.
- `SHA256SUMS`: authoritative wheel checksum.
- `build_image.sh`: checksum-gated image build.
- `runtime.env.example`: non-secret runtime configuration.
- `verify_image.sh`: non-interactive image verification.
- `artifacts/myextension-0.2.1-py3-none-any.whl`: verified plugin artifact.

**Create in the target directory**

- `00_从这里开始.md`: concise human entrypoint.
- `AGENTS.md`: scoped instructions automatically readable by Codex.
- `MANIFEST.json`: machine-readable identity, validation, and file roles.

---

### Task 1: Assemble the exact delivery directory

**Files:**
- Create: `myextension-0.2.1-BLUEDOT-完整交付包/`
- Copy: `deploy/bluedot/release-0.2.1/{.dockerignore,Dockerfile,README.md,SHA256SUMS,build_image.sh,runtime.env.example,verify_image.sh}`
- Copy: `deploy/bluedot/release-0.2.1/artifacts/myextension-0.2.1-py3-none-any.whl`

**Interfaces:**
- Consumes: fixed source release directory at commit `2a3dc75`.
- Produces: identical Docker build context at the repository root.

- [ ] **Step 1: Confirm the source release identity**

Run:

```bash
git status --short --branch
git rev-parse --short bluedot-delivery-0.2.1-fixed^{}
shasum -a 256 deploy/bluedot/release-0.2.1/artifacts/myextension-0.2.1-py3-none-any.whl
```

Expected: clean `main`, tag resolves to `2a3dc75`, and the wheel digest is the exact global-constraint value.

- [ ] **Step 2: Create the target directory and copy verified assets**

Run:

```bash
mkdir -p myextension-0.2.1-BLUEDOT-完整交付包/artifacts
cp -p deploy/bluedot/release-0.2.1/.dockerignore myextension-0.2.1-BLUEDOT-完整交付包/
cp -p deploy/bluedot/release-0.2.1/Dockerfile myextension-0.2.1-BLUEDOT-完整交付包/
cp -p deploy/bluedot/release-0.2.1/README.md myextension-0.2.1-BLUEDOT-完整交付包/
cp -p deploy/bluedot/release-0.2.1/SHA256SUMS myextension-0.2.1-BLUEDOT-完整交付包/
cp -p deploy/bluedot/release-0.2.1/build_image.sh myextension-0.2.1-BLUEDOT-完整交付包/
cp -p deploy/bluedot/release-0.2.1/runtime.env.example myextension-0.2.1-BLUEDOT-完整交付包/
cp -p deploy/bluedot/release-0.2.1/verify_image.sh myextension-0.2.1-BLUEDOT-完整交付包/
cp -p deploy/bluedot/release-0.2.1/artifacts/myextension-0.2.1-py3-none-any.whl myextension-0.2.1-BLUEDOT-完整交付包/artifacts/
```

Expected: eight copied files, no `.DS_Store`, and both shell scripts remain executable.

- [ ] **Step 3: Verify byte identity immediately**

Run `cmp -s` for each source/target pair and inspect each exit code. Run:

```bash
shasum -a 256 myextension-0.2.1-BLUEDOT-完整交付包/artifacts/myextension-0.2.1-py3-none-any.whl
stat -f '%Sp %N' myextension-0.2.1-BLUEDOT-完整交付包/build_image.sh myextension-0.2.1-BLUEDOT-完整交付包/verify_image.sh
```

Expected: all pairs match, digest matches, and both scripts show executable modes.

---

### Task 2: Add human and Codex entrypoints

**Files:**
- Create: `myextension-0.2.1-BLUEDOT-完整交付包/00_从这里开始.md`
- Create: `myextension-0.2.1-BLUEDOT-完整交付包/AGENTS.md`
- Create: `myextension-0.2.1-BLUEDOT-完整交付包/MANIFEST.json`

**Interfaces:**
- Consumes: copied release assets from Task 1.
- Produces: one short human workflow, Codex operating constraints, and exact machine-readable package metadata.

- [ ] **Step 1: Write `00_从这里开始.md`**

Use these exact sections and values:

```markdown
# 从这里开始

这是 `myextension 0.2.1` 修复版的完整 BLUEDOT 交付文件夹。

## 先校验

macOS：`shasum -a 256 -c SHA256SUMS`

Linux：`sha256sum -c SHA256SUMS`

必须显示 wheel 为 `OK`。

## 选择一种安装方式

1. 已有 JupyterLab 4：按 `README.md` 第 4 节直接安装 wheel。
2. BLUEDOT 镜像：按 `README.md` 第 5–10 节构建、验证、推送、注册和验收。
3. 交给 Codex：让 Codex 先读取本目录 `AGENTS.md` 和 `MANIFEST.json`，再执行 `README.md`；未经授权不要让它推送或部署。

## 最重要的文件

- 插件：`artifacts/myextension-0.2.1-py3-none-any.whl`
- 完整步骤：`README.md`
- 制品身份：`MANIFEST.json` 和 `SHA256SUMS`
- 镜像构建：`Dockerfile` 和 `build_image.sh`
- 镜像验收：`verify_image.sh`

当前文件夹没有包含 API Key。真实 AI 和真实 BLUEDOT 环境尚未验收。
```

- [ ] **Step 2: Write scoped `AGENTS.md`**

Use these exact rules:

```markdown
# Codex 交付包操作规则

1. 开始前依次完整读取 `00_从这里开始.md`、`MANIFEST.json`、`README.md`。
2. 任何安装或镜像操作前先运行 `SHA256SUMS` 校验；失败立即停止。
3. 本目录 wheel 是唯一交付制品。不要改用仓库旧 `dist/` wheel，不要重新构建、改名或修改 SHA。
4. 未经用户明确授权，不得登录镜像仓库、推送、部署、创建 BLUEDOT 工作台、调用真实 AI 或写入密钥。
5. `ARK_API_KEY` 只能由平台 Secret Manager 在运行时注入，不得写入任何文件、镜像层、命令参数或日志。
6. 仅说明部署时，直接引用 `README.md` 的现有命令和相对路径，不扩展产品功能。
7. Docker daemon、真实基础镜像、镜像推送和 BLUEDOT 验收均是待管理员执行项，不得宣称已完成。
8. 任何修改后重新验证 JSON、脚本语法、wheel SHA、文件清单，并准确报告未验证项。
```

- [ ] **Step 3: Write `MANIFEST.json`**

Use valid UTF-8 JSON with this exact data model:

```json
{
  "schema_version": 1,
  "package": "myextension",
  "version": "0.2.1",
  "purpose": "BLUEDOT JupyterLab 4 image delivery",
  "artifact": {
    "path": "artifacts/myextension-0.2.1-py3-none-any.whl",
    "sha256": "8436b8e69f9e25c58df68c0024723c660e9fe8751c52a60b320c1e97f28ea16e"
  },
  "source": {
    "fixed_delivery_commit": "2a3dc75",
    "fixed_delivery_tag": "bluedot-delivery-0.2.1-fixed",
    "folder_design_commit": "8c53a7e"
  },
  "validated": {
    "backend": "668 passed, 23 subtests passed",
    "frontend": "20 suites, 287 passed",
    "lint": "passed",
    "production_build": "passed",
    "wheel_structure": "passed",
    "wheel_zip_integrity": "passed",
    "isolated_install": "myextension 0.2.1, analysis budget 120, provider timeout 60",
    "release_script_tests": "8 passed"
  },
  "not_validated": [
    "Docker image build and run because the local Docker daemon was unavailable",
    "Real BLUEDOT base image, registry push, framework registration, and workspace acceptance",
    "Real or paid AI provider"
  ],
  "entrypoints": {
    "human": "00_从这里开始.md",
    "codex": "AGENTS.md",
    "full_instructions": "README.md",
    "checksum": "SHA256SUMS"
  }
}
```

- [ ] **Step 4: Review the entrypoints**

Confirm that the human file fits on one screen, `AGENTS.md` contains no authorization beyond the user request, and the manifest distinguishes validated from unvalidated work.

---

### Task 3: Verify, commit, and label the folder

**Files:**
- Verify: `myextension-0.2.1-BLUEDOT-完整交付包/**`
- Modify: none after validation unless a check fails.

**Interfaces:**
- Consumes: complete Task 1–2 directory.
- Produces: one Git-tracked delivery folder and local rollback tag.

- [ ] **Step 1: Validate JSON, SHA, and scripts**

Run:

```bash
.venv/bin/python -m json.tool myextension-0.2.1-BLUEDOT-完整交付包/MANIFEST.json
cd myextension-0.2.1-BLUEDOT-完整交付包
shasum -a 256 -c SHA256SUMS
sh -n build_image.sh
sh -n verify_image.sh
```

Expected: valid JSON, wheel `OK`, and both scripts parse.

- [ ] **Step 2: Verify exact file set and safety**

Expected regular files are exactly:

```text
.dockerignore
00_从这里开始.md
AGENTS.md
Dockerfile
MANIFEST.json
README.md
SHA256SUMS
artifacts/myextension-0.2.1-py3-none-any.whl
build_image.sh
runtime.env.example
verify_image.sh
```

Run searches that fail the task if any `.DS_Store`, `.env`, `.ark_ai_config.json`, log, placeholder, or non-comment `ARK_API_KEY` assignment appears.

- [ ] **Step 3: Compare all copied files to the source release**

Run `cmp -s` for the eight source/target pairs. Expected: exit code 0 for every pair.

- [ ] **Step 4: Run the source release script tests**

Run:

```bash
.venv/bin/pytest -q myextension/tests/test_bluedot_release.py
```

Expected: `8 passed`.

- [ ] **Step 5: Inspect and commit only the intended files**

Run:

```bash
git diff --check
git status --short --untracked-files=all
git add myextension-0.2.1-BLUEDOT-完整交付包 docs/superpowers/plans/2026-08-06-self-contained-delivery-folder.md
git diff --cached --check
git diff --cached --name-status
git commit -m "build: add self-contained 0.2.1 delivery folder"
```

Expected: only the plan and eleven delivery files are committed.

- [ ] **Step 6: Add and verify the local delivery tag**

Run:

```bash
git tag -a self-contained-delivery-0.2.1 -m "Self-contained myextension 0.2.1 BLUEDOT delivery folder"
git status --short --branch
git rev-parse --short self-contained-delivery-0.2.1^{}
```

Expected: clean `main` and the tag resolves to the new delivery-folder commit.
