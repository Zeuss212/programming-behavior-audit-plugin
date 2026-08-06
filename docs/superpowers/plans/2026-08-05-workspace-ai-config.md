# Current Workspace AI Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the server save AI configuration in the current BLUEDOT `/workspace/code` directory when no explicit configuration location exists, while preserving existing deployments and local defaults.

**Architecture:** Keep AI configuration path selection inside `myextension.llm_transport`, separate from behavior-log storage. Add one explicit file-path override, retain the existing configured log-root behavior, then conditionally select a BLUEDOT workspace directory before falling back to the existing home-directory location.

**Tech Stack:** Python 3.10+, `pathlib`, `pytest`, existing atomic JSON persistence, Markdown documentation.

## Global Constraints

- Do not move operation, process, or analysis logs.
- Do not embed a real API key in source code, tests, wheel artifacts, documentation, or command history.
- Preserve `0600` configuration-file permissions, `0700` parent-directory creation, atomic replacement, and existing symlink rejection.
- Do not operate the online BLUEDOT workspace, restart Jupyter, build or push a BLUEDOT image, or call a real/paid AI service.
- `/workspace/code` fallback applies only when that path exists and is a directory.
- The working directory is not a Git repository; do not create commits or initialize Git. Use passing test output as each task checkpoint.

---

### Task 1: Lock the AI configuration path contract with tests

**Files:**
- Create: `myextension/tests/test_ai_config_path.py`
- Test: `myextension/tests/test_ai_config_path.py`

**Interfaces:**
- Consumes: `save_ai_config(config: Mapping[str, object]) -> None` from `myextension.llm_transport`.
- Produces: executable contract for `AI_CONFIG_PATH_ENV_VAR`, `BLUEDOT_WORKSPACE_CODE_DIR`, and the four-level path precedence.

- [ ] **Step 1: Write the failing path-precedence tests**

```python
from __future__ import annotations

import json
import stat

import pytest

import myextension.llm_transport as transport
from myextension.behavior_log_store import LOG_DIR_ENV_VAR


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    for name in (
        transport.AI_CONFIG_PATH_ENV_VAR,
        LOG_DIR_ENV_VAR,
        transport.ARK_API_KEY_ENV_VAR,
        transport.ARK_BASE_URL_ENV_VAR,
        transport.ARK_MODEL_ENV_VAR,
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _save() -> None:
    transport.save_ai_config(
        {
            "base_url": "https://provider.invalid/v1",
            "model": "synthetic-model",
            "api_key": "synthetic-test-key",
        }
    )


def test_explicit_ai_config_path_has_highest_priority(monkeypatch, tmp_path):
    explicit_path = tmp_path / "explicit" / "config.json"
    log_root = tmp_path / "logs"
    workspace = tmp_path / "workspace" / "code"
    workspace.mkdir(parents=True)
    monkeypatch.setenv(transport.AI_CONFIG_PATH_ENV_VAR, str(explicit_path))
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(log_root))
    monkeypatch.setattr(transport, "BLUEDOT_WORKSPACE_CODE_DIR", workspace)

    _save()

    assert explicit_path.is_file()
    assert not (log_root / transport.AI_CONFIG_FILENAME).exists()


def test_configured_log_root_precedes_bluedot_workspace(monkeypatch, tmp_path):
    log_root = tmp_path / "logs"
    workspace = tmp_path / "workspace" / "code"
    workspace.mkdir(parents=True)
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(log_root))
    monkeypatch.setattr(transport, "BLUEDOT_WORKSPACE_CODE_DIR", workspace)

    _save()

    assert (log_root / transport.AI_CONFIG_FILENAME).is_file()
    assert not (workspace / ".behavior-audit" / transport.AI_CONFIG_FILENAME).exists()


def test_bluedot_workspace_is_used_without_path_configuration(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace" / "code"
    workspace.mkdir(parents=True)
    monkeypatch.setattr(transport, "BLUEDOT_WORKSPACE_CODE_DIR", workspace)

    _save()

    path = workspace / ".behavior-audit" / transport.AI_CONFIG_FILENAME
    assert json.loads(path.read_text(encoding="utf-8"))[
        transport.ARK_MODEL_ENV_VAR
    ] == "synthetic-model"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_missing_bluedot_workspace_keeps_local_default(monkeypatch, tmp_path):
    monkeypatch.setattr(
        transport,
        "BLUEDOT_WORKSPACE_CODE_DIR",
        tmp_path / "missing-workspace",
    )

    _save()

    expected = (
        tmp_path
        / "home"
        / ".jupyterlab-behavior-audit"
        / "logs"
        / transport.AI_CONFIG_FILENAME
    )
    assert expected.is_file()
```

- [ ] **Step 2: Run the new test module and verify RED**

Run:

```bash
.venv/bin/python -m pytest -q myextension/tests/test_ai_config_path.py
```

Expected: collection fails because `AI_CONFIG_PATH_ENV_VAR` and `BLUEDOT_WORKSPACE_CODE_DIR` do not yet exist, proving the new contract is not implemented.

- [ ] **Step 3: Record the RED checkpoint**

Keep the exact failing pytest output in the task transcript. Do not run Git commands because this delivery directory has no repository metadata.

### Task 2: Implement the minimal path resolver

**Files:**
- Modify: `myextension/llm_transport.py:16-29`
- Modify: `myextension/llm_transport.py:408-409`
- Test: `myextension/tests/test_ai_config_path.py`
- Test: `myextension/tests/test_dimension_analyzer.py:473-943`

**Interfaces:**
- Consumes: `LOG_DIR_ENV_VAR: str` and `resolve_log_root() -> Path` from `myextension.behavior_log_store`.
- Produces: `AI_CONFIG_PATH_ENV_VAR: str`, `BLUEDOT_WORKSPACE_CODE_DIR: Path`, and `_ai_config_path() -> Path`.

- [ ] **Step 1: Add path-selection constants and import the existing log variable**

Change the import and constants to:

```python
from .behavior_log_store import LOG_DIR_ENV_VAR, resolve_log_root

AI_CONFIG_FILENAME = ".ark_ai_config.json"
AI_CONFIG_PATH_ENV_VAR = "JUPYTERLAB_BEHAVIOR_AUDIT_AI_CONFIG_PATH"
BLUEDOT_WORKSPACE_CODE_DIR = Path("/workspace/code")
BLUEDOT_AI_CONFIG_DIRNAME = ".behavior-audit"
```

- [ ] **Step 2: Implement the precedence contract**

Replace `_ai_config_path()` with:

```python
def _ai_config_path() -> Path:
    configured_path = os.environ.get(AI_CONFIG_PATH_ENV_VAR)
    if configured_path:
        return Path(configured_path).expanduser()
    if os.environ.get(LOG_DIR_ENV_VAR):
        return resolve_log_root() / AI_CONFIG_FILENAME
    if BLUEDOT_WORKSPACE_CODE_DIR.is_dir():
        return (
            BLUEDOT_WORKSPACE_CODE_DIR
            / BLUEDOT_AI_CONFIG_DIRNAME
            / AI_CONFIG_FILENAME
        )
    return resolve_log_root() / AI_CONFIG_FILENAME
```

- [ ] **Step 3: Run the new tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q myextension/tests/test_ai_config_path.py
```

Expected: `4 passed`.

- [ ] **Step 4: Run the existing AI configuration security regressions**

Run:

```bash
.venv/bin/python -m pytest -q myextension/tests/test_dimension_analyzer.py -k "ai_config or saved_ai_config"
```

Expected: all selected tests pass, including private permissions, clear-key behavior, invalid URL rejection, symlink rejection, and failed-write preservation.

- [ ] **Step 5: Record the implementation checkpoint**

Keep the exact passing pytest output in the task transcript. Do not create a commit in this non-Git delivery directory.

### Task 3: Document current-folder behavior and run full verification

**Files:**
- Modify: `README.md:70-72`
- Modify: `README.md:105-118`
- Modify: `docs/2026-08-04-bluedot-platform-integration.md:71-80`
- Modify: `项目交接文档.md:175-178`
- Modify: `项目交接文档.md:310-324`

**Interfaces:**
- Consumes: the Task 2 path-precedence contract.
- Produces: operator-facing instructions that distinguish current-workspace persistence from embedding a key in the plugin.

- [ ] **Step 1: Update the README user flow and privacy boundary**

Document these exact operational facts:

```text
在 BLUEDOT 单用户试点工作台中，如果没有设置专用配置路径或日志目录，插件会把页面保存的 AI 配置写入 /workspace/code/.behavior-audit/.ark_ai_config.json。普通本地环境继续使用原路径。不要提交或共享该文件。
```

- [ ] **Step 2: Update the BLUEDOT deployment guide**

Add the explicit override and fallback:

```text
JUPYTERLAB_BEHAVIOR_AUDIT_AI_CONFIG_PATH=/受控私有目录/.ark_ai_config.json
```

State that the page can save to `/workspace/code/.behavior-audit/.ark_ai_config.json` for the current single-user trial when no override or log root is configured, while formal multi-user deployment must use platform secret injection.

- [ ] **Step 3: Update the handoff security and verification notes**

Record the precedence, the fact that workspace persistence depends on BLUEDOT volume policy, and that the config file remains prohibited from delivery or sharing.

- [ ] **Step 4: Run focused backend tests**

Run:

```bash
.venv/bin/python -m pytest -q myextension/tests/test_ai_config_path.py myextension/tests/test_dimension_analyzer.py -k "ai_config or saved_ai_config"
```

Expected: all selected tests pass.

- [ ] **Step 5: Run the full backend regression suite**

Run:

```bash
.venv/bin/python -m pytest -q myextension/tests
```

Expected: the current backend baseline plus the four new tests passes with zero failures. If the sandbox blocks pytest-jupyter from binding a temporary local port, rerun the same command with the required execution permission and record that constraint.

- [ ] **Step 6: Run frontend and formatting regressions**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm lint:check
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm test --runInBand
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm build:lib:prod
```

Expected: lint, Jest, and the production TypeScript build all exit zero. These commands ensure the backend-only change did not disturb the existing packaged frontend.

- [ ] **Step 7: Inspect the final diff without Git**

Run:

```bash
rg -n "AI_CONFIG_PATH_ENV_VAR|BLUEDOT_WORKSPACE_CODE_DIR|workspace/code/.behavior-audit" myextension README.md docs 项目交接文档.md
```

Expected: matches appear only in the resolver, focused tests, approved design/plan, and the three operator documents; no real key value appears.

- [ ] **Step 8: Stop at the approved boundary**

Report exact commands, pass counts, changed file paths, and remaining platform limitation. Do not install the rebuilt code into BLUEDOT, restart its server, enter a real key, or call the provider.
