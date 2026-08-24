"""Static contract checks for the local classroom API reverse proxy."""

from __future__ import annotations

from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPOSITORY_ROOT / "deploy/classroom/nginx/classroom.conf"
COMPOSE_PATH = REPOSITORY_ROOT / "deploy/classroom/docker-compose.test.yml"


def test_classroom_nginx_proxies_only_api_and_keeps_sse_unbuffered():
    config = CONFIG_PATH.read_text(encoding="utf-8")

    assert "client_max_body_size 2m;" in config
    assert "proxy_set_header Authorization $http_authorization;" in config
    assert "proxy_read_timeout 60s;" in config
    assert "location = /classroom-api/v1/classroom/plan-suggestions" in config
    assert "proxy_read_timeout 75s;" in config
    assert "location ~ ^/classroom-api/v1/classroom/classrooms/[^/]+/events$" in config
    assert "proxy_buffering off;" in config
    assert "proxy_cache off;" in config
    assert "proxy_read_timeout 3600s;" in config
    assert config.index("location ~ ^/classroom-api/v1/classroom/classrooms/") < config.index(
        "location = /classroom-api/v1/classroom/plan-suggestions"
    ) < config.index(
        "location /classroom-api/"
    )
    assert "proxy_pass http://classroom_sync;" in config


def test_classroom_nginx_uses_safe_logging_and_never_owns_workbench_or_minio():
    config = CONFIG_PATH.read_text(encoding="utf-8")
    compose = COMPOSE_PATH.read_text(encoding="utf-8")

    assert "log_format classroom_safe" in config
    assert "error_log /dev/stderr crit;" in config
    assert "$uri" in config
    assert "$request_uri" not in config
    assert "$http_authorization" not in config.split("log_format classroom_safe", 1)[1].split(";", 1)[0]
    assert "40002" not in config
    assert "40037" not in config
    assert "classroom-nginx:" in compose
    assert '"127.0.0.1:18081:8080"' in compose
    assert '"9000:' not in compose
    assert '"9001:' not in compose
