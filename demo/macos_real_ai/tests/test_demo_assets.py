from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DEMO_DIR = ROOT / "demo" / "macos_real_ai"


class DemoAssetTests(unittest.TestCase):
    def test_release_instructions_include_the_three_session_logs(self) -> None:
        readme = (DEMO_DIR / "README.md").read_text(encoding="utf-8")

        for filename in (
            "operation_log.json",
            "process_log.md",
            "analysis_log.json",
        ):
            self.assertIn(filename, readme)

    def test_notebook_is_a_clean_manual_demo_fixture(self) -> None:
        notebook = json.loads(
            (DEMO_DIR / "demo_notebook.ipynb").read_text(encoding="utf-8")
        )
        cells = notebook["cells"]
        self.assertEqual([cell["cell_type"] for cell in cells], [
            "markdown",
            "code",
            "code",
            "code",
        ])
        code_cells = cells[1:]
        for cell in code_cells:
            self.assertEqual(cell.get("outputs"), [])
            self.assertIsNone(cell.get("execution_count"))

        serialized = json.dumps(notebook, ensure_ascii=False).lower()
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("token=", serialized)
        self.assertNotIn("真实学生", serialized)

        implementation = "".join(code_cells[0]["source"])
        failing_call = "".join(code_cells[1]["source"])
        final_tests = "".join(code_cells[2]["source"])
        self.assertIn("def analyze_scores", implementation)
        self.assertNotIn("raise ValueError", implementation)
        self.assertNotIn("if not scores", implementation)
        self.assertIn("analyze_scores([])", failing_call)
        self.assertIn("analyze_scores([100, 80, 60, 40])", final_tests)
        self.assertIn("analyze_scores([])", final_tests)
        self.assertIn("analyze_scores([-1])", final_tests)
        self.assertIn("Demo tests passed", final_tests)

        self.assertEqual(
            notebook["metadata"]["kernelspec"]["name"],
            "python3",
        )

    def test_readme_contains_the_complete_manual_demo_sequence(self) -> None:
        readme = (DEMO_DIR / "README.md").read_text(encoding="utf-8")
        required = (
            "成绩统计真实 AI 演示",
            "demo-analyze-scores",
            "analyze_scores",
            "https://ark.cn-beijing.volces.com/api/coding/v3",
            "glm-5-2-260617",
            "./deploy_demo.sh --preflight",
            "./deploy_demo.sh",
            "创建题目考核方案",
            "确认测试并发布",
            "30 秒",
            "analysis_status == ready",
            "./export_latest_demo.sh",
            "清除已保存 Key",
            "./stop_demo.sh",
            "付费",
            "禁止将真实学生日志直接对外分享",
        )
        for expected in required:
            with self.subTest(expected=expected):
                self.assertIn(expected, readme)


if __name__ == "__main__":
    unittest.main()
