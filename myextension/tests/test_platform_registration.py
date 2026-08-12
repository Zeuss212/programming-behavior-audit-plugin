from __future__ import annotations

import json
import stat
from pathlib import Path
from urllib.error import HTTPError

import pytest

from myextension.platform_client import PlatformClientError, PlatformSyncClient
from myextension.platform_context_store import (
    PlatformContextStore,
    RegisteredPlatformContext,
)


def context() -> RegisteredPlatformContext:
    return RegisteredPlatformContext(
        assignment_id="d7647a1a-89c3-4c6d-9b5f-7e803918aa9d",
        plan_id="2b16b5c0-4e58-48f9-9448-9067de005e4a",
        plan_version=1,
        session_id="23d7d803-524a-4d9f-b8bd-152a540dba12",
        access_token="short-lived-plugin-token",
        access_token_expires_at="2026-08-12T09:00:00Z",
        evidence_cutoff_at="2026-08-12T08:45:00Z",
    )


def test_registered_context_is_private_and_never_contains_the_one_time_ticket(tmp_path: Path):
    store = PlatformContextStore(tmp_path)
    saved = store.save_registered_context(context())
    path = tmp_path / "platform-context.json"

    assert saved == context()
    assert store.read_registered_context() == context()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "short-lived-plugin-token" in path.read_text(encoding="utf-8")
    assert "one-time-ticket" not in path.read_text(encoding="utf-8")


def test_context_store_rejects_ticket_fields_instead_of_persisting_them(tmp_path: Path):
    store = PlatformContextStore(tmp_path)
    (tmp_path / "platform-context.json").write_text(
        json.dumps({**context().to_dict(), "ticket": "one-time-ticket"}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="invalid keys"):
        store.read_registered_context()


@pytest.mark.parametrize(
    "field,value",
    [
        ("plan_version", True),
        ("access_token_expires_at", "not-a-timestamp"),
        ("evidence_cutoff_at", "2026-08-12T08:45:00"),
    ],
)
def test_context_store_rejects_noncanonical_platform_response_fields(
    tmp_path: Path, field: str, value: object
):
    store = PlatformContextStore(tmp_path)
    (tmp_path / "platform-context.json").write_text(
        json.dumps({**context().to_dict(), field: value}), encoding="utf-8"
    )

    with pytest.raises(ValueError):
        store.read_registered_context()


def test_platform_client_errors_do_not_echo_the_plaintext_ticket():
    def failing_transport(_request, *, timeout: float):
        assert timeout > 0
        raise OSError("connection refused")

    client = PlatformSyncClient("https://classroom.example", transport=failing_transport)

    with pytest.raises(PlatformClientError) as error:
        client.register("one-time-ticket", plugin_instance_id="plugin-instance-a")

    assert "one-time-ticket" not in str(error.value)


def test_platform_client_treats_forbidden_ticket_exchange_as_nonretryable():
    def forbidden_transport(_request, *, timeout: float):
        assert timeout > 0
        raise HTTPError("https://sync.example/register", 403, "forbidden", {}, None)

    client = PlatformSyncClient("https://classroom.example", transport=forbidden_transport)

    with pytest.raises(PlatformClientError) as error:
        client.register("one-time-ticket", plugin_instance_id="plugin-instance-a")

    assert error.value.args == ("platform_registration_unauthorized",)


async def test_jupyter_registration_route_exchanges_ticket_without_returning_credentials(
    jp_fetch, monkeypatch, tmp_path: Path
):
    """The browser may hand over one ticket but never receives the stored token."""

    captured: list[tuple[str, str]] = []

    def register(_client, ticket: str, *, plugin_instance_id: str):
        captured.append((ticket, plugin_instance_id))
        return context()

    monkeypatch.setenv("JUPYTERLAB_BEHAVIOR_AUDIT_PLATFORM_MODE", "student")
    monkeypatch.setenv("JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL", "https://sync.example")
    monkeypatch.setenv("JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR", str(tmp_path))
    monkeypatch.setattr("myextension.routes.PlatformSyncClient.register", register)

    response = await jp_fetch(
        "myextension",
        "platform",
        "register",
        method="POST",
        body=json.dumps(
            {
                "schema_version": 1,
                "ticket": "one-time-ticket",
                "plugin_instance_id": "plugin-instance-a",
            }
        ),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert response.code == 201
    payload = json.loads(response.body)
    assert captured == [("one-time-ticket", "plugin-instance-a")]
    assert payload["session_id"] == context().session_id
    assert payload["assignment_id"] == context().assignment_id
    assert payload["plan_version"] == 1
    assert "access_token" not in payload
    assert "one-time-ticket" not in response.body.decode("utf-8")
    assert (tmp_path / "platform-context.json").is_file()


async def test_jupyter_registration_route_is_disabled_outside_student_mode(jp_fetch):
    response = await jp_fetch(
        "myextension",
        "platform",
        "register",
        method="POST",
        body=json.dumps(
            {
                "schema_version": 1,
                "ticket": "one-time-ticket",
                "plugin_instance_id": "plugin-instance-a",
            }
        ),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert response.code == 404
    payload = json.loads(response.body)
    assert payload["code"] == "platform_registration_disabled"


async def test_jupyter_registration_route_rejects_noninteger_schema_versions(
    jp_fetch, monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("JUPYTERLAB_BEHAVIOR_AUDIT_PLATFORM_MODE", "student")
    monkeypatch.setenv("JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL", "https://sync.example")
    monkeypatch.setenv("JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR", str(tmp_path))

    response = await jp_fetch(
        "myextension",
        "platform",
        "register",
        method="POST",
        body=json.dumps(
            {
                "schema_version": True,
                "ticket": "one-time-ticket",
                "plugin_instance_id": "plugin-instance-a",
            }
        ),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert response.code == 422
    payload = json.loads(response.body)
    assert payload["code"] == "platform_registration_validation_failed"
    assert "one-time-ticket" not in response.body.decode("utf-8")
