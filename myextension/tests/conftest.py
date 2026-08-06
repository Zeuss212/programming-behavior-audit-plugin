import pytest

from myextension.behavior_log_store import LOG_DIR_ENV_VAR

@pytest.fixture
def jp_server_config(tmp_path, monkeypatch):
    # Server lifecycle services must never inspect a developer's real log root.
    monkeypatch.setenv(
        LOG_DIR_ENV_VAR,
        str(tmp_path / "synthetic-server-logs"),
    )
    return {
        "ServerApp": {
            "jpserver_extensions": {
                "myextension": True,
            },
        },
    }
