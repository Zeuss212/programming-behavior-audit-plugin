from __future__ import annotations

import pytest

from myextension.platform_config import PlatformConfig


def test_student_mode_requires_an_explicit_https_sync_service_url():
    with pytest.raises(RuntimeError, match="sync_base_url"):
        PlatformConfig.from_env({"JUPYTERLAB_BEHAVIOR_AUDIT_PLATFORM_MODE": "student"})

    with pytest.raises(RuntimeError, match="HTTPS"):
        PlatformConfig.from_env(
            {
                "JUPYTERLAB_BEHAVIOR_AUDIT_PLATFORM_MODE": "student",
                "JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL": "http://classroom.example",
            }
        )


def test_student_mode_allows_loopback_http_only_when_explicitly_configured_for_tests(tmp_path):
    config = PlatformConfig.from_env(
        {
            "JUPYTERLAB_BEHAVIOR_AUDIT_PLATFORM_MODE": "student",
            "JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL": "http://127.0.0.1:8088",
            "JUPYTERLAB_BEHAVIOR_AUDIT_ALLOW_INSECURE_LOOPBACK": "true",
            "JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR": str(tmp_path),
        }
    )

    assert config.student_mode is True
    assert config.sync_base_url == "http://127.0.0.1:8088"
    assert config.deadline_poll_seconds == 30


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


def test_local_mode_does_not_require_platform_connection_configuration():
    config = PlatformConfig.from_env({})

    assert config.student_mode is False
    assert config.sync_base_url is None
