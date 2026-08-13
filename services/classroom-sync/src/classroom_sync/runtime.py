"""Composition root for the containerized classroom synchronization service."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import boto3  # type: ignore[import-untyped]
import httpx
from botocore.config import Config
from fastapi import FastAPI

from classroom_sync.application import ClassroomServices
from classroom_sync.auth.fincolab import FincolabIdentityGateway
from classroom_sync.config import Settings
from classroom_sync.db import create_database_engine, create_session_factory
from classroom_sync.domain.schemas import ClassroomSchemaRegistry
from classroom_sync.main import create_app
from classroom_sync.services.assignments import AssignmentService
from classroom_sync.services.briefs import BriefService
from classroom_sync.services.deadlines import DeadlineService
from classroom_sync.services.plans import PlanService
from classroom_sync.services.read_models import ClassroomReadService
from classroom_sync.services.sessions import PluginSessionService
from classroom_sync.storage import Boto3PrivateObjectStorage


def utc_now() -> datetime:
    """Return the service clock in the one canonical timezone."""

    return datetime.now(timezone.utc)


def repository_root() -> Path:
    """Locate the checked-out shared contracts when running from a container."""

    return Path(__file__).resolve().parents[4]


def plugin_schema_directory() -> Path:
    """Resolve copied plugin schemas in containers, or the checkout in tests."""

    configured = os.environ.get("CLASSROOM_PLUGIN_SCHEMA_ROOT", "").strip()
    if configured:
        return Path(configured)
    return repository_root() / "myextension" / "api_schemas"


def contract_directory() -> Path:
    """Resolve copied classroom contracts in containers, or the checkout in tests."""

    configured = os.environ.get("CLASSROOM_CONTRACTS_ROOT", "").strip()
    if configured:
        return Path(configured)
    return repository_root() / "contracts" / "classroom" / "v1"


def s3_client_config() -> Config:
    """Bound S3 outages so evidence upload requests can return a retryable 503 promptly."""

    return Config(
        connect_timeout=2,
        read_timeout=5,
        retries={"mode": "standard", "total_max_attempts": 2},
    )


def create_runtime_services(settings: Settings) -> ClassroomServices:
    """Wire only trusted, environment-derived dependencies into route services."""

    settings.require_runtime_dependencies()
    if (
        settings.database_url is None
        or settings.s3_endpoint_url is None
        or settings.s3_bucket is None
        or settings.s3_access_key is None
        or settings.s3_secret_key is None
        or settings.fincolab_base_url is None
        or settings.fincolab_organization_id is None
        or settings.plugin_jwt_secret is None
    ):
        raise RuntimeError("Classroom runtime configuration is incomplete.")

    session_factory = create_session_factory(create_database_engine(settings.database_url))
    schema_registry = ClassroomSchemaRegistry(
        contract_directory(),
        plugin_schema_directory=plugin_schema_directory(),
    )
    storage_client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name="us-east-1",
        config=s3_client_config(),
    )
    storage = Boto3PrivateObjectStorage(storage_client, settings.s3_bucket)
    identity_gateway = FincolabIdentityGateway(
        base_url=settings.fincolab_base_url,
        organization_id=settings.fincolab_organization_id,
        client=httpx.Client(timeout=10.0),
    )
    plan_service = PlanService(session_factory, schema_registry, clock=utc_now)
    assignment_service = AssignmentService(session_factory, clock=utc_now)
    brief_service = BriefService(session_factory, schema_registry, clock=utc_now)
    plugin_session_service = PluginSessionService(
        session_factory,
        storage=storage,
        plugin_jwt_secret=settings.plugin_jwt_secret,
        clock=utc_now,
        schema_registry=schema_registry,
    )
    deadline_service = DeadlineService(session_factory, brief_service, clock=utc_now)
    return ClassroomServices(
        identity_gateway=identity_gateway,
        plan_service=plan_service,
        assignment_service=assignment_service,
        plugin_session_service=plugin_session_service,
        brief_service=brief_service,
        deadline_service=deadline_service,
        read_service=ClassroomReadService(session_factory),
    )


def create_runtime_app(settings: Settings) -> FastAPI:
    """Build the ready-to-serve app after the complete dependency graph is known."""

    return create_app(settings, classroom_services=create_runtime_services(settings))
