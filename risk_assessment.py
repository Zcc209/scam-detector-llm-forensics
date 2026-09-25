"""Keep URL heuristics, content predictions, and capture failures distinct."""


def assess(domain, browser=None, image_result=None):
    checks = [domain] if domain else []
    if browser:
        checks.extend(browser.get("navigation_checks") or [])
        if browser.get("final_domain_analysis"):
            checks.append(browser["final_domain_analysis"])

    strongest = max(checks, key=lambda item: item["risk_score"], default=None)
    domain_blocked = any(not item["capture_allowed"] for item in checks)
    summary = {
        "risk_level": "Unknown",
        "basis": "insufficient_evidence",
        "domain_risk_score": strongest["risk_score"] if strongest else None,
        "domain_risk_level": strongest["risk_level"] if strongest else "Unknown",
        "domain_risk_source": strongest["normalized_url"] if strongest else None,
        "domain_check_passed": bool(checks) and not domain_blocked,
        "content_prediction": "Unknown",
        "rule_signals": [],
        "note": "Heuristic URL scores and model softmax are not fraud probabilities or proof of account authenticity.",
    }
    if domain_blocked or (strongest and strongest["risk_score"] >= 60):
        summary.update(risk_level="High", basis="domain_rule")
        return summary
    if browser and browser.get("status") != "success":
        summary["basis"] = "page_unusable"
        return summary
    if not image_result or image_result.get("status") != "SUCCESS":
        return summary
    prediction = image_result.get("prediction")
    summary["content_prediction"] = prediction if prediction in ("Fraud", "Normal") else "Unknown"
    summary["rule_signals"] = image_result.get("rule_signals") or []
    if prediction == "Fraud":
        summary.update(risk_level="Medium", basis="content_model_signal")
    elif prediction == "Normal":
        summary.update(risk_level="Low", basis="limited_content_evidence")
    return summary
