"""Conservative fusion of independent DOM and screenshot OCR model signals."""
import math

def source_decision(result):
    if not valid_prediction(result):
        return "Unavailable"
    probability = (result.get('calibrated_fraud_score') if result.get('calibration', {}).get('status') == 'candidate_applied'
                   else result.get("fraud_confidence"))
    if not isinstance(probability, (int, float)) or not math.isfinite(probability) or not 0 <= probability <= 1:
        return "Uncertain"
    if probability < 0.5:
        return "Normal"
    if probability > 0.5:
        return "Fraud"
    return "Uncertain"


def valid_prediction(result):
    return bool(result and result.get("status") == "SUCCESS" and result.get("prediction") in ("Fraud", "Normal"))


def fuse(dom_result=None, ocr_result=None):
    sources = {name: result for name, result in (("dom", dom_result), ("screenshot_ocr", ocr_result))
               if valid_prediction(result)}
    decisions = {name: source_decision(result) for name, result in sources.items()}
    decisive = {name: result for name, result in sources.items() if decisions[name] in ("Normal", "Fraud")}
    predictions = {decisions[name] for name in decisive}
    if not sources:
        prediction, basis = "Unknown", "no_usable_model_evidence"
    elif not decisive:
        prediction, basis = "Unknown", "tied_or_invalid_scores"
    elif len(predictions) > 1:
        prediction, basis = "Unknown", "modality_conflict"
    elif len(decisive) == 1 and len(sources) == 2:
        prediction, basis = "Unknown", "tied_or_invalid_source"
    elif len(sources) == 1:
        prediction, basis = next(iter(predictions)), "single_modality"
    else:
        prediction, basis = next(iter(predictions)), "modalities_agree"
    return {
        "status": "SUCCESS" if sources else "INSUFFICIENT_EVIDENCE",
        "prediction": prediction,
        "basis": basis,
        "source_decisions": decisions,
        "decision_policy": {"type": "binary_argmax_with_source_agreement", "validated": False,
                            "note": "0.5 follows equal two-class scores, not an empirically selected risk threshold."},
        "evidence_refs": [f"content:{name}" for name in sources],
        "rule_signals": sorted({signal for result in sources.values() for signal in result.get("rule_signals", [])}),
        "confidence_type": ('source_specific_candidate_or_raw' if any(item.get('calibration', {}).get('status') == 'candidate_applied' for item in sources.values())
                            else 'uncalibrated_softmax_by_source'),
        "source_score_types": {name: 'calibration_candidate' if item.get('calibration', {}).get('status') == 'candidate_applied'
                               else 'raw_softmax' for name, item in sources.items()},
        "note": "No combined fraud probability is calculated; conflicting sources remain Unknown.",
    }
