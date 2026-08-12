from __future__ import annotations

import json
from pathlib import Path

import pytest

from myextension.platform_context_store import PlatformContextStore
from myextension.schema_registry import validate_schema
from myextension.tests.test_platform_registration import context


def _enable_student_mode(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setenv("JUPYTERLAB_BEHAVIOR_AUDIT_PLATFORM_MODE", "student")
    monkeypatch.setenv("JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL", "https://sync.example")
    monkeypatch.setenv("JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR", str(root))


async def test_student_mode_returns_private_context_without_exposing_plugin_token(
    jp_fetch, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _enable_student_mode(monkeypatch, tmp_path)
    PlatformContextStore(tmp_path).save_registered_context(context())

    response = await jp_fetch(
        "myextension", "platform", "context", method="GET", raise_error=False
    )

    assert response.code == 200
    payload = json.loads(response.body)
    validate_schema("platform-context-response-v1", payload)
    assert payload["mode"] == "student"
    assert payload["capabilities"] == {
        "canAuthorPlan": False,
        "canPublishPlan": False,
        "canConfigureAi": False,
        "canUseAssessmentAssist": False,
        "canCapture": True,
        "canSubmit": True,
    }
    assert payload["classroom_session"]["profile"] == context().profile
    assert "access_token" not in payload["classroom_session"]
    assert "short-lived-plugin-token" not in response.body.decode("utf-8")


@pytest.mark.parametrize(
    ("parts", "method", "body"),
    [
        (("myextension", "ai-config"), "GET", None),
        (("myextension", "ai-config"), "POST", "{}"),
        (("myextension", "dimension-profiles"), "POST", "{}"),
        (
            (
                "myextension",
                "dimension-profiles",
                "5c0a7494-7f0e-41c3-a7a2-0c1bc19ed7b3",
                "draft",
            ),
            "PUT",
            "{}",
        ),
        (
            (
                "myextension",
                "dimension-profiles",
                "5c0a7494-7f0e-41c3-a7a2-0c1bc19ed7b3",
                "publish",
            ),
            "POST",
            "{}",
        ),
        (("myextension", "assessment-assist", "knowledge-points"), "POST", "{}"),
        (("myextension", "assessment-assist", "tests"), "POST", "{}"),
    ],
)
async def test_student_mode_rejects_teacher_configuration_mutations(
    jp_fetch,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    parts: tuple[str, ...],
    method: str,
    body: str | None,
):
    _enable_student_mode(monkeypatch, tmp_path)
    kwargs: dict[str, object] = {"method": method, "raise_error": False}
    if body is not None:
        kwargs["body"] = body
        kwargs["headers"] = {"Content-Type": "application/json"}

    response = await jp_fetch(*parts, **kwargs)

    assert response.code == 403
    payload = json.loads(response.body)
    assert payload["code"] == "student_capability_forbidden"
