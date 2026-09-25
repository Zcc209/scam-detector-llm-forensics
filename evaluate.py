"""Evaluate saved reports against independently reviewed, group-separated labels."""

import argparse
import csv
import json
import math
from pathlib import Path

LABELS = {"Fraud", "Normal"}
SPLITS = {"train", "calibration", "test"}
REQUIRED = {"case_id", "group_id", "split", "label", "label_source", "reviewer", "source_url", "report_path"}


def load_cases(manifest):
    manifest = Path(manifest).resolve()
    with manifest.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not REQUIRED.issubset(reader.fieldnames):
            raise ValueError(f"Manifest requires columns: {', '.join(sorted(REQUIRED))}")
        cases = list(reader)
    if not cases:
        raise ValueError("Manifest has no labeled cases")
    ids, groups = set(), {}
    for case in cases:
        if any(not case.get(field, "").strip() for field in REQUIRED):
            raise ValueError(f"Case has missing required fields: {case.get('case_id', '<empty>')}")
        if case["case_id"] in ids:
            raise ValueError(f"Duplicate case_id: {case['case_id']}")
        ids.add(case["case_id"])
        if case["label"] not in LABELS or case["split"] not in SPLITS:
            raise ValueError(f"Invalid label or split: {case['case_id']}")
        group = case["group_id"]
        if group in groups and groups[group] != (case["split"], case["label"]):
            raise ValueError(f"Group leakage or contradictory label: {group}")
        groups[group] = (case["split"], case["label"])
        path = Path(case["report_path"])
        if not path.is_absolute():
            path = manifest.parent / path
        case["report"] = json.loads(path.read_text(encoding="utf-8"))
    return cases


def predictions(report):
    domain = report.get("domain_analysis") or {}
    browser = report.get("browser_capture") or {}
    all_domains = [domain, *(browser.get("navigation_checks") or []), browser.get("final_domain_analysis") or {}]
    domain_positive = any(item.get("risk_score", 0) >= 60 or item.get("capture_allowed") is False
                          for item in all_domains)
    domain_pred = "Fraud" if domain_positive else "Unknown"

    evidence = report.get("evidence") or {}
    dom = (evidence.get("dom") or {}).get("model") or {}
    ocr = (evidence.get("screenshot_ocr") or {}).get("model") or report.get("image_analysis") or {}
    single = next((item for item in (dom, ocr)
                   if item.get("status") == "SUCCESS" and item.get("prediction") in LABELS), None)
    model_pred = single["prediction"] if single else "Unknown"
    probability = single.get("fraud_confidence") if single else None
    if browser and browser.get("status") not in ("success", "skipped"):
        model_pred, probability = "Unknown", None

    risk_level = (report.get("assessment") or {}).get("risk_level")
    fused_pred = ("Fraud" if domain_positive or risk_level in ("High", "Medium") else
                  "Normal" if risk_level == "Low" else "Unknown")
    if not domain_positive and browser and browser.get("status") != "success":
        fused_pred = "Unknown"
    if fused_pred not in LABELS:
        fused_pred = "Unknown"
    return {"domain_only": domain_pred, "macbert_only": model_pred, "fusion": fused_pred}, probability


def metrics(cases, method):
    confusion = {label: {pred: 0 for pred in ("Fraud", "Normal", "Unknown")} for label in LABELS}
    errors = []
    for case in cases:
        prediction = case["predictions"][method]
        confusion[case["label"]][prediction] += 1
        if prediction != case["label"]:
            errors.append({"case_id": case["case_id"], "group_id": case["group_id"],
                           "label": case["label"], "prediction": prediction,
                           "source_url": case["source_url"]})
    tp = confusion["Fraud"]["Fraud"]
    fp = confusion["Normal"]["Fraud"]
    fn = confusion["Fraud"]["Normal"] + confusion["Fraud"]["Unknown"]
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision is not None and recall is not None and precision + recall else None
    coverage = sum(case["predictions"][method] != "Unknown" for case in cases) / len(cases)
    return {"precision": precision, "recall": recall, "f1": f1, "coverage": coverage,
            "confusion_matrix": confusion, "error_cases": errors[:20]}


def calibrate(probability, temperature):
    probability = min(max(float(probability), 1e-6), 1 - 1e-6)
    log_odds = math.log(probability / (1 - probability)) / temperature
    return 1 / (1 + math.exp(-log_odds))


def brier(cases, temperature=1.0):
    return sum((calibrate(prob, temperature) - int(label == "Fraud")) ** 2 for prob, label in cases) / len(cases)


def ece(cases, temperature=1.0, bins=10):
    groups = [[] for _ in range(bins)]
    for prob, label in cases:
        calibrated = calibrate(prob, temperature)
        groups[min(int(calibrated * bins), bins - 1)].append((calibrated, int(label == "Fraud")))
    return sum(len(group) / len(cases) * abs(sum(p for p, _ in group) / len(group) -
               sum(y for _, y in group) / len(group)) for group in groups if group)


def fit_temperature(cases):
    def nll(temperature):
        return sum(-math.log(calibrate(prob, temperature) if label == "Fraud"
                             else 1 - calibrate(prob, temperature)) for prob, label in cases)
    return min((0.25 + step * 0.05 for step in range(156)), key=nll)


def evaluate(cases):
    for case in cases:
        case["predictions"], case["probability"] = predictions(case["report"])
    test = [case for case in cases if case["split"] == "test"]
    calibration = [case for case in cases if case["split"] == "calibration"]
    if not test:
        raise ValueError("At least one test case is required")
    counts = {label: sum(case["label"] == label for case in test) for label in LABELS}
    output = {
        "status": "evaluation_only",
        "test_count": len(test),
        "test_label_counts": counts,
        "test_group_count": len({case["group_id"] for case in test}),
        "methods": {method: metrics(test, method) for method in ("domain_only", "macbert_only", "fusion")},
        "minimum_sample_count_met": all(counts[label] >= 30 for label in LABELS),
        "requires_manual_provenance_audit": True,
        "limitations": ["Unknown predictions count as errors for recall and reduce coverage.",
                        "Group separation in this manifest cannot prove separation from the original MacBERT training set."],
    }
    calibration_pairs = [(case["probability"], case["label"]) for case in calibration
                         if case["probability"] is not None]
    test_pairs = [(case["probability"], case["label"]) for case in test if case["probability"] is not None]
    if (all(sum(label == target for _, label in calibration_pairs) >= 10 for target in LABELS)
            and all(sum(label == target for _, label in test_pairs) >= 10 for target in LABELS)):
        temperature = fit_temperature(calibration_pairs)
        output["calibration"] = {
            "status": "candidate_not_deployed",
            "temperature": temperature,
            "validation_count": len(calibration_pairs),
            "test_count_with_probability": len(test_pairs),
            "test_brier_raw": brier(test_pairs),
            "test_brier_calibrated": brier(test_pairs, temperature),
            "test_ece_raw": ece(test_pairs),
            "test_ece_calibrated": ece(test_pairs, temperature),
        }
    else:
        output["calibration"] = {"status": "insufficient_labeled_cases",
                                 "requirement": "At least 10 scored cases of each label in both calibration and test splits"}
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate(load_cases(args.manifest))
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
