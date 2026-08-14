# macOS 演示包同步至 0.4.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the macOS real-AI demo preflight reproducibly consume the checked-in, checksum-verified `myextension 0.4.0` candidate wheel.

**Architecture:** Keep the existing shell preflight and its exact SHA-256 comparison. Change only its default artifact coordinate from ignored `dist/` output to the tracked `deploy/bluedot/release-0.4.0` artifact, then align the user-facing override documentation and contract tests.

**Tech Stack:** Bash, Python `unittest`/pytest, macOS `shasum`.

## Global Constraints

- Do not rebuild or alter `myextension-0.4.0-py3-none-any.whl`.
- Default wheel path: `deploy/bluedot/release-0.4.0/artifacts/myextension-0.4.0-py3-none-any.whl`.
- Default SHA-256: `bc9cb1cdd3e95056f5ed9eed1aff19e1cf36e112966772b9fbdc86cd3b10804c`.
- Preserve explicit `DEMO_WHEEL` and `DEMO_EXPECTED_WHEEL_SHA256` overrides, strict allowlisting, and the prohibition on API keys in files.
- Do not call real AI, build/push images, modify BAMS, modify the running frontend preview, or deploy.

---

### Task 1: Lock the demo release contract in tests

**Files:**
- Modify: `demo/macos_real_ai/tests/test_demo_assets.py:12-31`
- Test: `demo/macos_real_ai/tests/test_demo_assets.py::DemoAssetTests::test_release_assets_target_0_4_0_and_the_three_session_logs`

**Interfaces:**
- Consumes: current `package.json`, `demo/macos_real_ai/deploy_demo.sh`, and the published `0.4.0` checksum.
- Produces: a source-level regression test that fails if the demo defaults back to an ignored local wheel or stale hash.

- [ ] **Step 1: Write the failing test**

```python
def test_release_assets_target_0_4_0_and_the_three_session_logs(self) -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    deploy = (DEMO_DIR / "deploy_demo.sh").read_text(encoding="utf-8")

    self.assertEqual(package["version"], "0.4.0")
    self.assertIn(
        "deploy/bluedot/release-0.4.0/artifacts/myextension-0.4.0-py3-none-any.whl",
        deploy,
    )
    self.assertIn(
        "bc9cb1cdd3e95056f5ed9eed1aff19e1cf36e112966772b9fbdc86cd3b10804c",
        deploy,
    )
    self.assertNotIn("$PROJECT_ROOT/dist/", deploy)
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run:

```bash
UV_CACHE_DIR=/private/tmp/classroom-platform-uv-cache \
uv run --no-project --with 'pytest>=8,<9' \
python -m pytest -q \
demo/macos_real_ai/tests/test_demo_assets.py::DemoAssetTests::test_release_assets_target_0_4_0_and_the_three_session_logs
```

Expected: FAIL because the script still contains the `dist/myextension-0.3.0` path and old SHA-256.

### Task 2: Sync defaults and human-facing guidance

**Files:**
- Modify: `demo/macos_real_ai/deploy_demo.sh:10-11`
- Modify: `demo/macos_real_ai/.env.example:6-7`
- Modify: `demo/macos_real_ai/README.md:82-87`
- Test: `demo/macos_real_ai/tests/test_demo_assets.py`
- Test: `demo/macos_real_ai/tests/test_shell_safety.py`

**Interfaces:**
- Consumes: the checked-in 0.4.0 wheel path and its fixed SHA-256 from Task 1.
- Produces: a reproducible default preflight with existing optional local-wheel overrides unchanged.

- [ ] **Step 1: Implement the minimal release-coordinate update**

```bash
DEMO_WHEEL="$PROJECT_ROOT/deploy/bluedot/release-0.4.0/artifacts/myextension-0.4.0-py3-none-any.whl"
EXPECTED_WHEEL_SHA256=${DEMO_EXPECTED_WHEEL_SHA256:-"bc9cb1cdd3e95056f5ed9eed1aff19e1cf36e112966772b9fbdc86cd3b10804c"}
```

Keep the existing override parser and hash comparison untouched. Update only the comments and troubleshooting text that name the old local 0.3.0 wheel.

- [ ] **Step 2: Run focused regression tests**

Run:

```bash
UV_CACHE_DIR=/private/tmp/classroom-platform-uv-cache \
uv run --no-project --with 'pytest>=8,<9' \
python -m pytest -q demo/macos_real_ai/tests
```

Expected: all demo asset, shell-safety, and demo verification tests pass.

- [ ] **Step 3: Verify the checked-in delivery artifact**

Run:

```bash
(cd deploy/bluedot/release-0.4.0 && shasum -a 256 -c SHA256SUMS)
```

Expected: `artifacts/myextension-0.4.0-py3-none-any.whl: OK`.

- [ ] **Step 4: Commit the release synchronization**

```bash
git add demo/macos_real_ai/deploy_demo.sh demo/macos_real_ai/.env.example \
  demo/macos_real_ai/README.md demo/macos_real_ai/tests/test_demo_assets.py
git commit -m "fix: sync macOS demo to classroom release"
```

### Task 3: Run the release-integrated suite

**Files:**
- Verify only.

**Interfaces:**
- Consumes: the synchronized demo contract and existing classroom service tests.
- Produces: fresh whole-branch evidence before integration.

- [ ] **Step 1: Run the complete suite in the controlled local test environment**

Run:

```bash
CLASSROOM_UV_CACHE=/private/tmp/classroom-platform-uv-cache \
PYTHONPATH=services/classroom-sync:services/classroom-sync/src:. \
UV_CACHE_DIR=$CLASSROOM_UV_CACHE \
uv run --no-project \
  --with 'pytest>=8,<9' --with pytest-asyncio \
  --with 'pytest-jupyter[server]>=0.6.0' \
  --with 'jupyter_server>=2.4.0,<3' \
  --with 'hatch-jupyter-builder>=0.5' \
  --with alembic --with boto3 --with httpx --with fastapi \
  --with 'jsonschema>=4.18,<5' --with pydantic \
  --with 'psycopg[binary]' --with pyjwt --with sqlalchemy --with uvicorn \
  python -m pytest -q
```

Expected: exit 0 with no failures. The command needs the controlled local environment because tests bind temporary loopback ports and validate an ephemeral Nginx container.

- [ ] **Step 2: Check repository hygiene**

Run:

```bash
git diff --check
git status --short --branch
```

Expected: no whitespace errors and only intended committed changes.
