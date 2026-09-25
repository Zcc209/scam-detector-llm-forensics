"""Conservative fusion of independent DOM and screenshot OCR model signals."""


def valid_prediction(result):
    return bool(result and result.get("status") == "SUCCESS" and result.get("prediction") in ("Fraud", "Normal"))


def fuse(dom_result=None, ocr_result=None):
    sources = {name: result for name, result in (("dom", dom_result), ("screenshot_ocr", ocr_result))
               if valid_prediction(result)}
    predictions = {result["prediction"] for result in sources.values()}
    if not sources:
        prediction, basis = "Unknown", "no_usable_model_evidence"
    elif len(predictions) > 1:
        prediction, basis = "Unknown", "modality_conflict"
    elif len(sources) == 1:
        prediction, basis = next(iter(predictions)), "single_modality"
    else:
        prediction, basis = next(iter(predictions)), "modalities_agree"
    return {
        "status": "SUCCESS" if sources else "INSUFFICIENT_EVIDENCE",
        "prediction": prediction,
        "basis": basis,
        "evidence_refs": [f"content:{name}" for name in sources],
        "rule_signals": sorted({signal for result in sources.values() for signal in result.get("rule_signals", [])}),
        "confidence_type": "uncalibrated_softmax_by_source",
        "note": "No combined fraud probability is calculated; conflicting sources remain Unknown.",
    }
