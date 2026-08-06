from unittest.mock import Mock

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
