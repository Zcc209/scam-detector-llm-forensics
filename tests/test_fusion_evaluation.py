import csv
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluate import evaluate, load_cases, metrics, predictions, fit_temperature, calibrate
from fusion import fuse
from prepare_165 import extract_candidates
from risk_assessment import assess
from run_pipeline import main


def model_result(prediction, probability):
    return {"status": "SUCCESS", "prediction": prediction, "fraud_confidence": probability,
            "normal_confidence": 1 - probability, "confidence": max(probability, 1 - probability),
            "rule_signals": []}


class FusionTests(unittest.TestCase):
    def test_agreement_disagreement_and_missing_modality(self):
        fraud = model_result("Fraud", 0.8)
        normal = model_result("Normal", 0.1)
        self.assertEqual(fuse(fraud, fraud)["basis"], "modalities_agree")
        self.assertEqual(fuse(fraud, normal)["prediction"], "Unknown")
        self.assertEqual(fuse(normal, None)["basis"], "single_modality")
        self.assertEqual(fuse(None, None)["status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(assess(None, None, fuse(normal, None))["risk_level"], "Unknown")
        self.assertEqual(assess(None, None, fuse(normal, normal))["risk_level"], "Low")

    def test_redirect_evidence_refers_to_risky_hop(self):
        safe = {"risk_score": 0, "risk_level": "Low", "capture_allowed": True,
                "normalized_url": "https://example.com/"}
        risky = {"risk_score": 80, "risk_level": "High", "capture_allowed": False,
                 "normalized_url": "https://www.intagram.com/"}
        result = assess(safe, {"navigation_checks": [risky], "status": "blocked"})
        self.assertEqual(result["risk_level"], "High")
        self.assertEqual(result["evidence_refs"], ["domain:navigation:0"])

    def test_url_pipeline_uses_both_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "model").mkdir()
            (root / "model" / "config.json").write_text("{}", encoding="utf-8")
            screenshot = root / "page.png"
            screenshot.write_bytes(b"fixture")
            calls = []

            class FakePipeline:
                def __init__(self, model_path):
                    pass

                def process_text(self, text):
                    calls.append("dom")
                    return model_result("Normal", 0.1)

                def process_image(self, path):
                    calls.append("ocr")
                    return {**model_result("Fraud", 0.8), "ocr_texts": ["保證獲利"]}

            browser = {"status": "success", "screenshot_path": str(screenshot),
                       "page_text": "官方活動", "navigation_checks": [], "final_domain_analysis": None}
            argv = ["run_pipeline.py", "--url", "https://www.instagram.com/example/",
                    "--model-path", str(root / "model"), "--output-dir", str(root / "output")]
            with patch.object(sys, "argv", argv), patch("web_capture.capture", return_value=browser), \
                    patch.dict(sys.modules, {"integrated_app": types.SimpleNamespace(ScamDetectionPipeline=FakePipeline)}), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 2)
            report = json.loads((root / "output" / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(calls, ["dom", "ocr"])
            self.assertEqual(report["content_analysis"]["basis"], "modality_conflict")
            self.assertEqual(report["assessment"]["risk_level"], "Unknown")
            self.assertEqual(report["evidence"]["dom"]["id"], "content:dom")
            self.assertEqual(report["evidence"]["screenshot_ocr"]["id"], "content:screenshot_ocr")


class EvaluationTests(unittest.TestCase):
    def test_165_candidates_are_domain_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "official.csv"
            path.write_text("民國年月,網域,網站性質\n11412,Bad.Example,電子商務\n11412,bad.example,電子商務\n",
                            encoding="cp950")
            candidates = extract_candidates(path)
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["label_scope"], "listed_domain_only")

    def test_group_leakage_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "report.json"
            report.write_text("{}", encoding="utf-8")
            manifest = root / "cases.csv"
            with manifest.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=("case_id", "group_id", "split", "label", "label_source", "reviewer", "source_url", "report_path"))
                writer.writeheader()
                for case_id, split in (("one", "calibration"), ("two", "test")):
                    writer.writerow({"case_id": case_id, "group_id": "same-account", "split": split,
                                     "label": "Fraud", "label_source": "review", "reviewer": "tester",
                                     "source_url": "https://example.com", "report_path": "report.json"})
            with self.assertRaisesRegex(ValueError, "Group leakage"):
                load_cases(manifest)

    def test_metrics_and_small_data_warning(self):
        fraud_report = {"domain_analysis": {"risk_score": 80, "capture_allowed": False},
                        "content_analysis": {"prediction": "Unknown"}}
        normal_report = {"domain_analysis": {"risk_score": 0, "capture_allowed": True},
                         "browser_capture": {"status": "success"},
                         "evidence": {"dom": {"model": model_result("Normal", 0.1)}},
                         "assessment": {"risk_level": "Low"},
                         "content_analysis": {"prediction": "Normal"}}
        cases = [{"case_id": "a", "group_id": "a", "split": "test", "label": "Fraud",
                  "source_url": "https://bad.example", "report": fraud_report},
                 {"case_id": "b", "group_id": "b", "split": "test", "label": "Normal",
                  "source_url": "https://good.example", "report": normal_report}]
        result = evaluate(cases)
        self.assertEqual(result["methods"]["fusion"]["f1"], 1.0)
        self.assertEqual(result["methods"]["domain_only"]["coverage"], 0.5)
        self.assertFalse(result["minimum_sample_count_met"])
        self.assertEqual(result["calibration"]["status"], "insufficient_labeled_cases")

    def test_temperature_math(self):
        samples = [(0.9, "Fraud"), (0.1, "Normal")]
        temperature = fit_temperature(samples)
        self.assertGreater(temperature, 0)
        self.assertAlmostEqual(calibrate(0.5, temperature), 0.5)


if __name__ == "__main__":
    unittest.main()
