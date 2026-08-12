from unittest.mock import Mock

from myextension.classroom_brief_automation import ClassroomBriefRefresher
from myextension.tests.test_session_store import started_session
from myextension.training_record_automation import TrainingRecordRefresher


SESSION_ID = "10000000-0000-4000-8000-000000000001"


def test_refresher_exports_training_record_once():
    calls: list[str] = []

    class Service:
        def export_training_record(self, session_id):
            calls.append(session_id)
            return {"session_id": session_id}

    refresher = TrainingRecordRefresher(Service(), logger=Mock())

    assert refresher.refresh(SESSION_ID) is True
    assert calls == [SESSION_ID]


def test_refresher_returns_false_without_exposing_service_exception():
    class Service:
        def export_training_record(self, session_id):
            raise OSError("/private/synthetic-secret-path")

    logger = Mock()
    refresher = TrainingRecordRefresher(Service(), logger=logger)

    assert refresher.refresh(SESSION_ID) is False
    logger.warning.assert_called_once_with("training_record_refresh_failed")
    assert "/private/synthetic-secret-path" not in str(logger.mock_calls)


def test_classroom_brief_refresher_exports_once():
    calls: list[str] = []

    class Service:
        def export_classroom_brief(self, session_id):
            calls.append(session_id)
            return {"session_id": session_id}

    refresher = ClassroomBriefRefresher(Service(), logger=Mock())

    assert refresher.refresh(SESSION_ID) is True
    assert calls == [SESSION_ID]


def test_classroom_brief_refresher_failure_preserves_abandoned_state(
    tmp_path,
):
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    store.abandon(session_id, reason="stale_session")

    class Service:
        def export_classroom_brief(self, requested_session_id):
            assert requested_session_id == session_id
            raise OSError("/private/synthetic-secret-path")

    logger = Mock()
    refresher = ClassroomBriefRefresher(Service(), logger=logger)

    assert refresher.refresh(session_id) is False
    assert store.read(session_id)["status"] == "abandoned"
    logger.warning.assert_called_once_with("classroom_brief_refresh_failed")
    assert "/private/synthetic-secret-path" not in str(logger.mock_calls)
