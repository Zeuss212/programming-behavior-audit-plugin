import asyncio

import httpx
import pytest

from classroom_sync.config import Settings
from classroom_sync.main import create_app


def request(app, path: str) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    return asyncio.run(send())


def test_settings_from_env_rejects_an_empty_database_url():
    """An omitted database URL must fail before the service starts."""
    with pytest.raises(
        RuntimeError,
        match="CLASSROOM_DATABASE_URL must be configured.",
    ):
        Settings.from_env({})


def test_settings_from_env_trims_a_configured_database_url():
    """Deployment whitespace must not become part of the database DSN."""
    settings = Settings.from_env(
        {"CLASSROOM_DATABASE_URL": " postgresql://classroom:test@db/classroom "},
    )

    assert settings.database_url == "postgresql://classroom:test@db/classroom"


def test_live_reports_process_health_without_database_configuration():
    """Orchestrators can distinguish a running process from ready dependencies."""
    response = request(create_app(Settings(database_url=None)), "/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "live"}


def test_ready_reports_dependency_unavailable_when_database_is_not_configured():
    """A release must not receive traffic before its database is configured."""
    response = request(create_app(Settings(database_url=None)), "/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "dependency_unavailable",
            "message": "同步服务依赖尚未就绪。",
        }
    }


def test_ready_reports_ready_when_database_is_configured():
    """A configured service can be admitted before business routes exist."""
    response = request(
        create_app(Settings(database_url="postgresql://classroom:test@db/classroom")),
        "/health/ready",
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
