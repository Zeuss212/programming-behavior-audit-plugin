"""Runtime composition checks for the containerized sync service."""

from __future__ import annotations

from pathlib import Path

import pytest

from classroom_sync.config import Settings
from classroom_sync.domain.schemas import ClassroomSchemaRegistry
from classroom_sync.runtime import contract_directory


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
        }
    )

    settings.require_runtime_dependencies()

    assert settings.s3_bucket == "classroom-evidence"
    assert settings.fincolab_organization_id == "local-org"


def test_schema_registry_can_use_an_explicit_plugin_schema_directory(tmp_path):
    contract_directory = tmp_path / "contracts" / "classroom" / "v1"
    plugin_directory = tmp_path / "plugin-schemas"
    contract_directory.mkdir(parents=True)
    plugin_directory.mkdir()
    source_root = Path(__file__).resolve().parents[3]
    for source in (source_root / "contracts" / "classroom" / "v1").glob("*.schema.json"):
        (contract_directory / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    for name in ("profile-draft-v2.json", "profile-version-v2.json"):
        source = source_root / "myextension" / "api_schemas" / name
        (plugin_directory / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    registry = ClassroomSchemaRegistry(contract_directory, plugin_schema_directory=plugin_directory)

    assert registry is not None


def test_runtime_contract_directory_honors_an_explicit_container_root(monkeypatch, tmp_path):
    configured = tmp_path / "contracts" / "classroom" / "v1"
    monkeypatch.setenv("CLASSROOM_CONTRACTS_ROOT", str(configured))

    assert contract_directory() == configured
