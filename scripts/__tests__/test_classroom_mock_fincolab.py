"""Compatibility checks for the local FinColab test double."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_mock_module():
    path = Path(__file__).resolve().parents[2] / "deploy/classroom/mock-fincolab/app.py"
    spec = importlib.util.spec_from_file_location("classroom_mock_fincolab", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dynamic_mock_keeps_the_legacy_single_student_token(monkeypatch):
    monkeypatch.setenv("CLASSROOM_MOCK_STUDENT_COUNT", "30")
    mock = _load_mock_module()

    roster = mock.users()

    assert roster["student-token"]["id"] == "student001"
    assert roster["student001-token"]["id"] == "student001"
    assert roster["student030-token"]["id"] == "student030"
    assert len(mock.space_members()) == 31
    assert len(mock.student_children()) == 30
