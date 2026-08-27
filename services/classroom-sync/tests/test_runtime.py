"""Runtime composition checks for the containerized sync service."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest

from classroom_sync.application import ClassroomIdentityGateway, ClassroomServices
from classroom_sync.config import Settings
from classroom_sync.domain.schemas import ClassroomSchemaRegistry
from classroom_sync.errors import AiSuggestionUnavailableError
from classroom_sync.main import create_app
from classroom_sync.runtime import contract_directory, fincolab_http_client, s3_client_config
from classroom_sync.services.assignments import AssignmentService
from classroom_sync.services.plan_suggestions import AiProviderSettings, AiSuggestionSettings
from classroom_sync.services.plans import PlanService


def test_runtime_configuration_requires_all_trusted_dependencies():
    """A database URL alone is enough for health tests, never for a live classroom API."""
    settings = Settings(database_url="postgresql+psycopg://classroom:test@postgres/classroom")

    with pytest.raises(RuntimeError, match="CLASSROOM_S3_ENDPOINT_URL"):
        settings.require_runtime_dependencies()


def test_runtime_configuration_reads_explicit_test_dependencies_from_environment():
    settings = Settings.from_env(
        {
            "CLASSROOM_DATABASE_URL": "postgresql+psycopg://classroom:test@postgres/classroom",
            "CLASSROOM_S3_ENDPOINT_URL": "http://minio:9000",
            "CLASSROOM_S3_BUCKET": "classroom-evidence",
            "CLASSROOM_S3_ACCESS_KEY": "local-access-key",
            "CLASSROOM_S3_SECRET_KEY": "local-secret-key",
            "CLASSROOM_FINCOLAB_BASE_URL": "http://mock-fincolab:8080",
            "CLASSROOM_FINCOLAB_ORGANIZATION_ID": "local-org",
            "CLASSROOM_PLUGIN_JWT_SECRET": "local-plugin-secret-at-least-32-chars",
            "CLASSROOM_AI_BASE_URL": "https://ai.example/v1",
            "CLASSROOM_AI_MODEL": "classroom-model",
            "CLASSROOM_AI_API_KEY": "server-only-secret",
            "CLASSROOM_AI_TIMEOUT_SECONDS": "20",
            "CLASSROOM_AI_MAX_ATTEMPTS": "1",
        }
    )

    settings.require_runtime_dependencies()

    assert settings.s3_bucket == "classroom-evidence"
    assert settings.fincolab_organization_id == "local-org"
    assert settings.ai_base_url == "https://ai.example/v1"
    assert settings.ai_timeout_seconds == 20
    assert settings.ai_max_attempts == 1


def test_ai_settings_require_all_three_server_values() -> None:
    """AI suggestions are optional, but partial provider credentials are never usable."""
    settings = Settings(
        database_url="sqlite://",
        ai_base_url="https://ai.example/v1",
        ai_model=None,
        ai_api_key="secret",
    )

    with pytest.raises(AiSuggestionUnavailableError, match="ai_suggestion_not_configured"):
        AiSuggestionSettings.from_settings(settings)


def test_ai_provider_settings_accept_coding_plan_url_and_bound_timeout() -> None:
    settings = Settings(
        database_url="sqlite://",
        ai_base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        ai_model="glm-5.2",
        ai_api_key="server-only-secret",
        ai_timeout_seconds=180,
    )

    provider = AiProviderSettings.from_settings(settings)

    assert provider is not None
    assert provider.base_url == "https://open.bigmodel.cn/api/coding/paas/v4"
    assert provider.timeout_seconds == 180

    with pytest.raises(AiSuggestionUnavailableError, match="ai_suggestion_not_configured"):
        AiProviderSettings.from_settings(
            Settings(
                database_url="sqlite://",
                ai_base_url="https://open.bigmodel.cn/api/coding/paas/v4",
                ai_model="glm-5.2",
                ai_api_key="server-only-secret",
                ai_timeout_seconds=181,
            )
        )


def test_runtime_uses_bounded_s3_timeouts_for_retryable_storage_outages():
    """A stopped object store must become a prompt retryable API failure, not a hung request."""
    config = s3_client_config()

    assert config.connect_timeout == 2
    assert config.read_timeout == 5
    assert config.retries == {"mode": "standard", "total_max_attempts": 1}


def test_runtime_uses_the_existing_ten_second_fincolab_timeout() -> None:
    """Identity, ownership, and material reads share one bounded upstream policy."""
    client = fincolab_http_client()
    try:
        assert client.timeout.connect == 10.0
        assert client.timeout.read == 10.0
        assert client.timeout.write == 10.0
        assert client.timeout.pool == 10.0
    finally:
        client.close()


def test_app_shutdown_closes_shared_runtime_client_once() -> None:
    close_calls = 0

    def close_shared_client() -> None:
        nonlocal close_calls
        close_calls += 1

    app = create_app(
        Settings(database_url="sqlite://"),
        classroom_services=ClassroomServices(
            identity_gateway=cast(ClassroomIdentityGateway, object()),
            plan_service=cast(PlanService, object()),
            assignment_service=cast(AssignmentService, object()),
            shutdown=close_shared_client,
        ),
    )

    async def exercise_lifespan_twice() -> None:
        async with app.router.lifespan_context(app):
            pass
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(exercise_lifespan_twice())

    assert close_calls == 1


def test_schema_registry_can_use_an_explicit_plugin_schema_directory(tmp_path):
    contract_directory = tmp_path / "contracts" / "classroom" / "v1"
    plugin_directory = tmp_path / "plugin-schemas"
    contract_directory.mkdir(parents=True)
    plugin_directory.mkdir()
    source_root = Path(__file__).resolve().parents[3]
    for source in (source_root / "contracts" / "classroom" / "v1").glob("*.schema.json"):
        (contract_directory / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    for name in (
        "profile-draft-v2.json",
        "profile-version-v2.json",
        "profile-draft-v3.json",
        "profile-version-v3.json",
    ):
        source = source_root / "myextension" / "api_schemas" / name
        (plugin_directory / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    registry = ClassroomSchemaRegistry(contract_directory, plugin_schema_directory=plugin_directory)

    assert registry is not None


def test_runtime_contract_directory_honors_an_explicit_container_root(monkeypatch, tmp_path):
    configured = tmp_path / "contracts" / "classroom" / "v1"
    monkeypatch.setenv("CLASSROOM_CONTRACTS_ROOT", str(configured))

    assert contract_directory() == configured
