from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from myextension.session_store import SessionIntegrityError, SessionStore
from myextension.dimension_profile_store import DimensionProfileStore
from myextension.platform_context_store import PlatformContextStore
from myextension.tests.test_assessment_profile import make_assessment_profile
from myextension.tests.test_platform_registration import context
from myextension.tests.test_session_store import published_profile


ASSIGNMENT_ID = "d7647a1a-89c3-4c6d-9b5f-7e803918aa9d"
PLAN_ID = "2b16b5c0-4e58-48f9-9448-9067de005e4a"
SESSION_ID = "23d7d803-524a-4d9f-b8bd-152a540dba12"
NOW = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)


def bootstrap(store: SessionStore, *, now: datetime = NOW):
    return store.bootstrap_platform_session(
        assignment_id=ASSIGNMENT_ID,
        plan_id=PLAN_ID,
        plan_version=1,
        monitor_session_id=SESSION_ID,
        profile=published_profile(),
        scheduled_end_at=(NOW + timedelta(minutes=30)).isoformat(),
        evidence_cutoff_at=(NOW + timedelta(minutes=45)).isoformat(),
        now=now,
    )


def test_platform_bootstrap_recovers_the_same_persisted_session(tmp_path):
    store = SessionStore(tmp_path)

    created, first = bootstrap(store)
    resumed, second = bootstrap(store, now=NOW + timedelta(minutes=2))

    assert created == "created"
    assert resumed == "resumed"
    assert first["session_id"] == SESSION_ID
    assert second["session_id"] == SESSION_ID
    assert second["platform_assignment_id"] == ASSIGNMENT_ID
    assert second["platform_plan_id"] == PLAN_ID
    assert second["platform_plan_version"] == 1


def test_platform_bootstrap_refuses_a_changed_assignment_or_plan(tmp_path):
    store = SessionStore(tmp_path)
    bootstrap(store)

    with pytest.raises(SessionIntegrityError, match="platform assignment"):
        store.bootstrap_platform_session(
            assignment_id="e7647a1a-89c3-4c6d-9b5f-7e803918aa9d",
            plan_id=PLAN_ID,
            plan_version=1,
            monitor_session_id=SESSION_ID,
            profile=published_profile(),
            scheduled_end_at=(NOW + timedelta(minutes=30)).isoformat(),
            evidence_cutoff_at=(NOW + timedelta(minutes=45)).isoformat(),
            now=NOW + timedelta(minutes=2),
        )


def test_platform_bootstrap_returns_terminal_without_creating_after_cutoff(tmp_path):
    store = SessionStore(tmp_path)

    outcome, session = bootstrap(store, now=NOW + timedelta(minutes=46))

    assert outcome == "terminal"
    assert session is None
    assert store.list_session_ids() == []


async def test_platform_capture_bootstrap_route_reuses_the_persisted_session(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv("JUPYTERLAB_BEHAVIOR_AUDIT_PLATFORM_MODE", "student")
    monkeypatch.setenv(
        "JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL", "https://sync.example"
    )
    monkeypatch.setenv("JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR", str(tmp_path))
    current = datetime.now(UTC)
    profile_store = DimensionProfileStore(tmp_path)
    draft = profile_store.create_draft(make_assessment_profile())
    profile = profile_store.publish(str(draft["profile_id"]))
    classroom_context = replace(
        context(),
        profile=profile,
        scheduled_end_at=(current + timedelta(minutes=30)).isoformat(),
        evidence_cutoff_at=(current + timedelta(minutes=45)).isoformat(),
    )
    PlatformContextStore(tmp_path).save_registered_context(classroom_context)

    created = await jp_fetch(
        "myextension",
        "platform",
        "capture",
        "bootstrap",
        method="POST",
        body="",
        raise_error=False,
    )
    resumed = await jp_fetch(
        "myextension",
        "platform",
        "capture",
        "bootstrap",
        method="POST",
        body="",
        raise_error=False,
    )

    assert created.code == 200
    assert resumed.code == 200
    assert created.body and resumed.body
    assert created.headers["Content-Type"].startswith("application/json")
    import json

    created_payload = json.loads(created.body)
    resumed_payload = json.loads(resumed.body)
    assert created_payload["outcome"] == "created"
    assert resumed_payload["outcome"] == "resumed"
    assert created_payload["session"]["session_id"] == classroom_context.session_id
    assert resumed_payload["session"]["last_contiguous_sequence"] == 0
