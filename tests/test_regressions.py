import json
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from risk_assessment import assess
from web_capture import classify_page
from run_pipeline import main

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "regression_cases.json").read_text(encoding="utf-8"))


class RegressionFixtures(unittest.TestCase):
    def test_unusable_capture_never_starts_model(self):
        with tempfile.TemporaryDirectory() as output:
            args = ["run_pipeline.py", "--url", "https://www.instagram.com/example/", "--output-dir", output]
            browser_result = {"status": "unusable", "unusable_reason": "load_error", "navigation_checks": []}
            with patch.object(sys, "argv", args), patch("web_capture.capture", return_value=browser_result), redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 2)
            report = json.loads((Path(output) / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "unusable")
            self.assertEqual(report["assessment"]["risk_level"], "Unknown")
            self.assertIsNone(report["image_analysis"])

    def test_page_cases(self):
        for case in FIXTURES["page_cases"]:
            with self.subTest(case=case["id"]):
                self.assertEqual(
                    classify_page(case["title"], case["body"], case["url"], case["http_status"], case.get("has_password", False)),
                    case["expected"],
                )

    def test_risk_cases(self):
        for case in FIXTURES["risk_cases"]:
            with self.subTest(case=case["id"]):
                domain = {
                    "risk_score": case["domain_score"],
                    "risk_level": "High" if case["domain_score"] >= 60 else "Low",
                    "capture_allowed": case["capture_allowed"],
                    "normalized_url": "https://example.com/",
                }
                browser = {"status": case["browser_status"]}
                model = ({"status": case["model_status"], "prediction": case["prediction"]}
                         if case["model_status"] else None)
                self.assertEqual(assess(domain, browser, model)["risk_level"], case["expected"])

    def test_heuristics_do_not_overwrite_model(self):
        import ast
        tree = ast.parse((ROOT / "integrated_app.py").read_text(encoding="utf-8"))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_rule_signals")
        namespace = {}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "integrated_app.py", "exec"), namespace)
        self.assertEqual(namespace["_rule_signals"]("本店飲料50元，限時折扣"), [])
        self.assertIn("guaranteed_return_claim", namespace["_rule_signals"]("保證獲利"))


if __name__ == "__main__":
    unittest.main()
