from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DEMO_DIR = ROOT / "demo" / "macos_real_ai"


class ShellSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.temp = Path(self.temporary.name).resolve()
        self.state_file = self.temp / "current-state"
        self.environment = {
            **os.environ,
            "TMPDIR": f"{self.temp}/",
            "DEMO_STATE_FILE": str(self.state_file),
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_script(self, name: str, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(DEMO_DIR / name), *arguments],
            cwd=ROOT,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_preflight_uses_checked_in_classroom_release_by_default(self) -> None:
        allowed = self.temp / "allowed.env"
        allowed.write_text(
            "DEMO_PORT=18995\n"
            "DEMO_BASE_URL=https://ark.cn-beijing.volces.com/api/coding/v3\n"
            "DEMO_MODEL=glm-5-2-260617\n",
            encoding="utf-8",
        )

        result = self.run_script(
            "deploy_demo.sh", "--preflight", "--env-file", str(allowed)
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "Delivery wheel: "
            + str(
                ROOT
                / "deploy/bluedot/release-0.4.0/artifacts"
                / "myextension-0.4.0-py3-none-any.whl"
            ),
            result.stdout,
        )
        self.assertIn("Preflight passed", result.stdout)

    def test_preflight_rejects_secret_or_shell_code_without_executing_it(self) -> None:
        marker = self.temp / "must-not-exist"
        unsafe = self.temp / "unsafe.env"
        unsafe.write_text(
            "DEMO_API_KEY=secret\n"
            f"MALICIOUS=$(touch {marker})\n",
            encoding="utf-8",
        )

        result = self.run_script(
            "deploy_demo.sh", "--preflight", "--env-file", str(unsafe)
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())
        self.assertNotIn("secret", result.stdout + result.stderr)

    def test_preflight_rejects_a_wheel_with_the_wrong_hash(self) -> None:
        wrong_wheel = self.temp / "myextension.whl"
        wrong_wheel.write_bytes(b"not the delivery wheel")
        settings = self.temp / "wrong-wheel.env"
        settings.write_text(f"DEMO_WHEEL={wrong_wheel}\n", encoding="utf-8")

        result = self.run_script(
            "deploy_demo.sh", "--preflight", "--env-file", str(settings)
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("wheel SHA-256 mismatch", result.stderr)

    def test_stop_and_export_reject_state_outside_demo_runtime(self) -> None:
        outside = self.temp / "ordinary-directory"
        outside.mkdir()
        self.state_file.write_text(f"{outside}\n", encoding="utf-8")

        stopped = self.run_script("stop_demo.sh")
        exported = self.run_script("export_latest_demo.sh")

        self.assertNotEqual(stopped.returncode, 0)
        self.assertNotEqual(exported.returncode, 0)
        self.assertIn("unsafe Demo runtime", stopped.stderr)
        self.assertIn("unsafe Demo runtime", exported.stderr)

    def test_stop_refuses_a_pid_whose_command_is_not_the_demo_server(self) -> None:
        runtime = self.temp / "myextension-real-ai-demo.ABC123"
        runtime.mkdir()
        (runtime / "server.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
        (runtime / "server.port").write_text("18994\n", encoding="utf-8")
        self.state_file.write_text(f"{runtime}\n", encoding="utf-8")

        result = self.run_script("stop_demo.sh")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to signal unverified PID", result.stderr)


if __name__ == "__main__":
    unittest.main()
