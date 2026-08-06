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
    assert not (
        workspace / ".behavior-audit" / transport.AI_CONFIG_FILENAME
    ).exists()


def test_bluedot_workspace_is_used_without_path_configuration(
    monkeypatch,
    tmp_path,
):
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
