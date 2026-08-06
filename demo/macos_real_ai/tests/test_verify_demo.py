from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from demo.macos_real_ai.verify_demo import (
    DemoVerificationError,
    export_verified_demo,
    verify_latest_demo,
)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def ready_record(session_id: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "session": {
            "session_id": session_id,
            "status": "finalized",
            "analysis_status": "ready",
            "event_count": 3,
        },
        "integrity": {
            "complete": True,
            "missing_artifacts": [],
            "warnings": [],
        },
        "behavior_events": [
            {"segment_type": "code_writing"},
            {
                "segment_type": "code_execution",
                "execution_result": "failure",
            },
            {
                "segment_type": "code_execution",
                "execution_result": "success",
            },
        ],
        "ai_analysis": {
            "status": "ready",
            "dimension_results": [{"dimension_code": "DEMO"}],
            "provenance": {
                "model_name": "real-demo-model",
                "prompt_version": "teacher-dimensions-pilot-v1",
                "input_snapshot_hash": "a" * 64,
            },
        },
    }


def create_session(
    log_root: Path,
    session_id: str,
    ended_at: str,
    *,
    session_status: str = "finalized",
    analysis_status: str = "ready",
) -> Path:
    session_dir = log_root / "sessions" / session_id
    legacy_relative = f"2026-08-04/{session_id}.md"
    write_json(
        session_dir / "session.json",
        {
            "schema_version": 1,
            "session_id": session_id,
            "status": session_status,
            "ended_at": ended_at,
            "legacy_projection_path": legacy_relative,
        },
    )
    record = ready_record(session_id)
    record["session"]["analysis_status"] = analysis_status  # type: ignore[index]
    record["ai_analysis"]["status"] = analysis_status  # type: ignore[index]
    write_json(session_dir / "training_record.json", record)
    write_json(
        session_dir / "logs" / "operation_log.json",
        {
            "schema_version": 1,
            "session": record["session"],
            "events": record["behavior_events"],
            "integrity": record["integrity"],
        },
    )
    (session_dir / "logs" / "process_log.md").write_text(
        (
            "# 编程行为过程日志\n\n"
            "## 会话摘要\n\n"
            f"- 会话 ID：`{session_id}`\n\n"
            "## 时间线\n\n- 合成事件\n\n"
            "## 行为明细\n\n停顿（可能包含思考）\n"
        ),
        encoding="utf-8",
    )
    write_json(
        session_dir / "logs" / "analysis_log.json",
        {
            "schema_version": 1,
            "session": record["session"],
            "ai_analysis": record["ai_analysis"],
            "teacher_reviews": [],
            "integrity": record["integrity"],
        },
    )
    write_json(session_dir / "profile.json", {"profile_id": "demo-profile"})
    write_json(
        session_dir / "signal_dictionary.json",
        {"signal_dictionary_version": "pilot-v1"},
    )
    (session_dir / "raw_events.jsonl").write_text(
        "\n".join(
            json.dumps(event, ensure_ascii=False)
            for event in record["behavior_events"]  # type: ignore[index]
        )
        + "\n",
        encoding="utf-8",
    )
    legacy_path = log_root / legacy_relative
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("# Synthetic demo session\n", encoding="utf-8")
    return session_dir


class VerifyLatestDemoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.log_root = Path(self.temporary.name) / "log"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_selects_the_newest_finalized_session_by_ended_at(self) -> None:
        create_session(
            self.log_root,
            "11111111-1111-4111-8111-111111111111",
            "2026-08-04T01:00:00+00:00",
        )
        newest = create_session(
            self.log_root,
            "22222222-2222-4222-8222-222222222222",
            "2026-08-04T02:00:00+00:00",
        )

        result = verify_latest_demo(self.log_root)

        self.assertEqual(result.session_id, newest.name)
        self.assertEqual(result.session_dir, newest.resolve())
        self.assertEqual(result.analysis_status, "ready")
        self.assertEqual(result.event_count, 3)
        self.assertEqual(result.model_name, "real-demo-model")

    def test_does_not_fall_back_when_newest_finalized_analysis_is_partial(
        self,
    ) -> None:
        create_session(
            self.log_root,
            "11111111-1111-4111-8111-111111111111",
            "2026-08-04T01:00:00+00:00",
        )
        create_session(
            self.log_root,
            "22222222-2222-4222-8222-222222222222",
            "2026-08-04T02:00:00+00:00",
            analysis_status="partial",
        )

        with self.assertRaisesRegex(
            DemoVerificationError,
            "AI analysis is not ready: partial",
        ):
            verify_latest_demo(self.log_root)

    def test_ignores_collecting_session_when_selecting_latest_finalized(
        self,
    ) -> None:
        finalized = create_session(
            self.log_root,
            "11111111-1111-4111-8111-111111111111",
            "2026-08-04T01:00:00+00:00",
        )
        create_session(
            self.log_root,
            "22222222-2222-4222-8222-222222222222",
            "2026-08-04T02:00:00+00:00",
            session_status="collecting",
        )

        result = verify_latest_demo(self.log_root)

        self.assertEqual(result.session_dir, finalized.resolve())

    def test_rejects_incomplete_or_unverifiable_ready_records(self) -> None:
        cases = {
            "incomplete integrity": (
                lambda record: record["integrity"].update(complete=False),
                "training record integrity is incomplete",
            ),
            "event count mismatch": (
                lambda record: record["session"].update(event_count=4),
                "event count mismatch",
            ),
            "missing execution error": (
                lambda record: record.update(
                    behavior_events=[
                        {"segment_type": "code_writing"},
                        {
                            "segment_type": "code_execution",
                            "execution_result": "success",
                        },
                        {
                            "segment_type": "code_execution",
                            "execution_result": "success",
                        },
                    ]
                ),
                "missing required behavior event: execution error",
            ),
            "missing provenance": (
                lambda record: record["ai_analysis"].update(provenance={}),
                "missing analysis provenance: model_name",
            ),
        }
        for name, (mutate, message) in cases.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as temporary:
                    log_root = Path(temporary) / "log"
                    session_dir = create_session(
                        log_root,
                        "33333333-3333-4333-8333-333333333333",
                        "2026-08-04T03:00:00+00:00",
                    )
                    record = json.loads(
                        (session_dir / "training_record.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    mutate(record)
                    write_json(session_dir / "training_record.json", record)

                    with self.assertRaisesRegex(
                        DemoVerificationError,
                        message,
                    ):
                        verify_latest_demo(log_root)

    def test_rejects_a_ready_demo_when_any_human_facing_log_is_missing(self) -> None:
        session_dir = create_session(
            self.log_root,
            "55555555-5555-4555-8555-555555555555",
            "2026-08-04T05:00:00+00:00",
        )
        (session_dir / "logs" / "process_log.md").unlink()

        with self.assertRaisesRegex(
            DemoVerificationError,
            "missing required file: process_log.md",
        ):
            verify_latest_demo(self.log_root)

    def test_rejects_mismatched_or_placeholder_human_facing_logs(self) -> None:
        cases = {
            "operation belongs to another session": (
                "operation_log.json",
                lambda payload: payload["session"].update(
                    session_id="99999999-9999-4999-8999-999999999999"
                ),
                "operation log belongs to another session",
            ),
            "operation event mismatch": (
                "operation_log.json",
                lambda payload: payload.update(events=[]),
                "operation log events do not match training record",
            ),
            "analysis is only a placeholder": (
                "analysis_log.json",
                lambda payload: payload.update(ai_analysis={}),
                "analysis log does not match training record",
            ),
        }
        for name, (filename, mutate, message) in cases.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as temporary:
                    log_root = Path(temporary) / "log"
                    session_dir = create_session(
                        log_root,
                        "66666666-6666-4666-8666-666666666666",
                        "2026-08-04T06:00:00+00:00",
                    )
                    path = session_dir / "logs" / filename
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    mutate(payload)
                    write_json(path, payload)

                    with self.assertRaisesRegex(DemoVerificationError, message):
                        verify_latest_demo(log_root)

    def test_rejects_process_log_without_expected_session_structure(self) -> None:
        session_dir = create_session(
            self.log_root,
            "77777777-7777-4777-8777-777777777777",
            "2026-08-04T07:00:00+00:00",
        )
        (session_dir / "logs" / "process_log.md").write_text(
            "# 分析中\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            DemoVerificationError,
            "process log is incomplete or belongs to another session",
        ):
            verify_latest_demo(self.log_root)


class ExportVerifiedDemoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.log_root = self.root / "log"
        self.session_dir = create_session(
            self.log_root,
            "44444444-4444-4444-8444-444444444444",
            "2026-08-04T04:00:00+00:00",
        )
        (self.log_root / ".ark_ai_config.json").write_text(
            '{"ARK_API_KEY":"synthetic-secret-must-not-export"}',
            encoding="utf-8",
        )
        sensitive_files = {
            self.log_root / "jobs" / "job" / "raw_response.json": "raw",
            self.session_dir / "batches" / "batch.json": "batch",
            self.session_dir / "receipts" / "receipt.json": "receipt",
            self.root / "jupyter_cookie_secret": "cookie",
        }
        for path, contents in sensitive_files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_exports_only_the_verified_session_whitelist_with_valid_hashes(
        self,
    ) -> None:
        archive = export_verified_demo(self.log_root, self.root / "exports")

        self.assertTrue(archive.is_file())
        with zipfile.ZipFile(archive) as exported:
            names = set(exported.namelist())
            self.assertEqual(
                names,
                {
                    "manifest.json",
                    "session/training_record.json",
                    "session/session.json",
                    "session/profile.json",
                    "session/signal_dictionary.json",
                    "session/raw_events.jsonl",
                    "session/logs/operation_log.json",
                    "session/logs/process_log.md",
                    "session/logs/analysis_log.json",
                    "legacy/session.md",
                },
            )
            manifest = json.loads(exported.read("manifest.json"))
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(
                manifest["session_id"],
                "44444444-4444-4444-8444-444444444444",
            )
            manifest_entries = {
                entry["path"]: entry["sha256"]
                for entry in manifest["files"]
            }
            self.assertEqual(set(manifest_entries), names - {"manifest.json"})
            for name, expected_hash in manifest_entries.items():
                self.assertEqual(
                    hashlib.sha256(exported.read(name)).hexdigest(),
                    expected_hash,
                )

            lowered_names = "\n".join(names).lower()
            for forbidden in (
                "config",
                "key",
                "token",
                "cookie",
                "job",
                "batch",
                "receipt",
                "raw_response",
            ):
                self.assertNotIn(forbidden, lowered_names)
            self.assertNotIn(
                b"synthetic-secret-must-not-export",
                b"".join(exported.read(name) for name in names),
            )


if __name__ == "__main__":
    unittest.main()
