"""URL -> domain checks -> screenshot -> existing EasyOCR/MacBERT pipeline."""
import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

from domain_check import analyze_url
from evidence import domain_records
from fusion import fuse
from risk_assessment import assess
from progress import Progress


def domain_assessment(initial, browser=None):
    return assess(initial, browser)


def image_only_model(pipeline, viewport, ocr_full):
    """Score only the OCR text that the DOM does not already contain (text inside images)."""
    from alignment import align_ocr, image_only_segments
    from content_filter import filter_lines
    alignment = align_ocr(viewport, ocr_full.get("ocr_items")) if viewport else {"available": False}
    if not alignment.get("available") or ocr_full.get("status") != "SUCCESS":
        return ocr_full, ocr_full.get("ocr_texts", []), alignment
    segments = ["\n".join(filter_lines(segment.splitlines())[0]) for segment in image_only_segments(alignment)]
    segments = [segment for segment in segments if segment.strip()]
    text = "\n".join(segments)
    if sum(char.isalnum() for char in text) < 20:
        return ({"status": "SKIPPED_NO_IMAGE_ONLY_TEXT", "prediction": "Unknown", "ocr_texts": ocr_full.get("ocr_texts", []),
                 "ocr_items": ocr_full.get("ocr_items", []),
                 "message": "OCR text is already covered by DOM text; nothing image-only to score"}, segments, alignment)
    result = pipeline.process_text(text)
    result.update(input_source="screenshot_ocr_image_only", ocr_texts=ocr_full.get("ocr_texts", []),
                  ocr_items=ocr_full.get("ocr_items", []), full_ocr_model=ocr_full)
    return result, segments, alignment


def screenshot_domains(ocr_texts):
    """Run the URL/domain check on every URL read from an uploaded screenshot."""
    from domain_check import analyze_url
    from link_tracer import candidates
    results = []
    for item in candidates(None, [], [("screenshot_text", "\n".join(ocr_texts))], limit=6):
        try:
            results.append({**analyze_url(item["url"]), "context": item["context"]})
        except ValueError as exc:
            results.append({"normalized_url": item["url"], "error": str(exc), "context": item["context"]})
    return results


def follow_links(args, page_url, anchors, page_text, ocr_full, out, pipeline):
    if args.no_follow:
        return {"status": "disabled"}
    try:
        from link_tracer import trace
        return trace(page_url, anchors, [("page_text", page_text or ""), ("screenshot_text", "\n".join(ocr_full.get("ocr_texts", [])))],
                     out, detector=getattr(pipeline, "detector", None))
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def extra_evidence(args, report, image, content_text, viewport, ocr_full, out):
    import re
    from account_signals import extract
    browser = report.get("browser_capture") or {}
    alt = "\n".join(item.get("alt", "") for item in (viewport or {}).get("images", []) if item.get("alt"))
    match = re.match(r"https?://(?:www\.)?(?:instagram\.com|threads\.(?:net|com)|x\.com|twitter\.com|tiktok\.com)/@?([A-Za-z0-9_.]+)",
                     browser.get("final_url") or "")
    # Profile counts sit in page headers, which the content filter removes, so read the unfiltered viewport text.
    account = extract("\n".join(filter(None, [browser.get("page_title"), browser.get("page_text"), content_text, alt])),
                      match.group(1) if match else None)
    llm = {"status": "disabled"}
    if not args.no_llm:
        from llm_evidence import analyze
        llm = analyze(content_text or "\n".join(ocr_full.get("ocr_texts", [])))
    try:
        from image_forensics import analyze as forensic
        dpr = ((viewport or {}).get("viewport") or {}).get("dpr") or 1.0
        domain = (browser.get("final_domain_analysis") or report.get("domain_analysis") or {}).get("hostname")
        if not browser:  # image mode: compare on-screen brands with the domains printed in the screenshot
            domain = [d["hostname"] for d in report.get("image_domains") or [] if d.get("hostname")] or None
        forensics = forensic(image, boxes=[item["box"] for item in (viewport or {}).get("images", [])], dpr=dpr,
                             texts=ocr_full.get("ocr_texts", []), domain=domain, output_dir=out)
    except Exception as exc:
        forensics = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    return account, llm, forensics


def decide_content(report, dom_result, ocr_result, fusion_path=None):
    """Learned fusion when a fitted model exists; otherwise the conservative source-agreement rule."""
    import fusion_model
    model = fusion_model.load(fusion_path) if fusion_path else fusion_model.load()
    agreement = report["source_agreement"]
    if not model:
        return agreement
    text_model = dom_result if dom_result and dom_result.get("status") == "SUCCESS" else None
    image_model = ocr_result if ocr_result and ocr_result.get("status") == "SUCCESS" else None
    if text_model is None:
        # Image mode, or a URL whose DOM was discarded (unstable viewport, e.g. a carousel): the full OCR text is then
        # the account text. Training cases always carry post text, so "OCR present, text missing" was never seen and
        # the fitted bias alone would push such pages towards Fraud (www.gov.tw scored 81% with a 0.8% OCR score).
        text_model, image_model = image_model, None
    decision = fusion_model.decide({"text_model": text_model, "ocr_model": image_model, "account": report["account_signals"],
                                    "llm": report["llm_evidence"], "image": report["image_forensics"]}, model)
    if not text_model and not image_model:
        return {**agreement, "learned_fusion": decision}
    return {**decision, "status": "SUCCESS", "source_decisions": agreement.get("source_decisions"),
            "source_agreement_basis": agreement.get("basis"), "keyword_rule_signals": agreement.get("rule_signals", []),
            "evidence_refs": agreement.get("evidence_refs", [])}


def main():
    # JSON stdout is UTF-8 even on Windows consoles whose default is CP950.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url")
    source.add_argument("--image", type=Path)
    parser.add_argument("--domain-only", action="store_true")
    parser.add_argument("--capture-only", action="store_true")
    parser.add_argument("--model-path", type=Path, default=Path(__file__).parent / "anti_fraud_E3_macbert")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--progress-file", type=Path)
    parser.add_argument("--calibration-json", type=Path, help="Explicitly apply a reviewed source-specific calibration candidate")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--explain", action="store_true", help="Run local deletion sensitivity experiments (slower)")
    parser.add_argument("--no-llm", action="store_true", help="Skip the local Ollama evidence extractor")
    parser.add_argument("--original-macbert", action="store_true", help="Do not switch to models/macbert_social")
    parser.add_argument("--no-follow", action="store_true", help="Do not follow outbound links to their final URL")
    parser.add_argument("--channel", choices=["chrome", "msedge"])
    parser.add_argument("--storage-state", help="Playwright login-state JSON (keep private)")
    args = parser.parse_args()

    if args.domain_only and not args.url:
        parser.error("--domain-only requires --url")
    if args.capture_only and not args.url:
        parser.error("--capture-only requires --url")

    out = args.output_dir or Path(__file__).parent / "artifacts" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    out.mkdir(parents=True, exist_ok=True)

    report = {
        "status": "partial",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "domain_analysis": None,
        "browser_capture": None,
        "image_analysis": None,
        "content_analysis": None,
        "evidence": {"domains": [], "dom": None, "screenshot_ocr": None},
        "assessment": {
            "risk_level": "Unknown",
            "content_prediction": "Unknown",
            "note": "Domain rules and model confidence are separate evidence; neither proves account authenticity",
        },
    }
    code = 0
    progress = Progress(args.progress_file)
    progress.set(0, 'done')

    try:
        image = args.image
        if args.url:
            progress.set(1, 'running')
            domain = report["domain_analysis"] = analyze_url(args.url)
            progress.set(1, 'done' if domain['capture_allowed'] else 'warning')
            report["assessment"] = assess(domain)
            report["evidence"]["domains"] = domain_records(domain)

            if not domain["capture_allowed"]:
                report["status"] = "blocked"
                report["browser_capture"] = {
                    "status": "skipped",
                    "reason": domain["block_reason"],
                    "navigation_checks": [],
                    "final_domain_analysis": None,
                }
                raise ValueError(domain["block_reason"])

            if not args.domain_only:
                progress.set(2, 'running')
                from web_capture import capture
                report["browser_capture"] = capture(
                    domain["normalized_url"],
                    out,
                    headed=args.headed,
                    storage_state=args.storage_state,
                    channel=args.channel,
                )
                report["evidence"]["domains"] = domain_records(domain, report["browser_capture"])
                report["assessment"] = assess(domain, report["browser_capture"])
                progress.set(2, 'done' if report['browser_capture']['status'] == 'success' else 'warning')

                if report["browser_capture"].get("status") == "blocked":
                    report["status"] = "blocked"
                elif report["browser_capture"].get("status") == "unusable":
                    report["status"] = "unusable"
                if report["browser_capture"].get("status") != "success":
                    raise ValueError(
                        report["browser_capture"].get("error")
                        or report["browser_capture"].get("unusable_reason")
                        or "Page inaccessible or login wall detected; provide a login state or original image"
                    )

                # 健全的截圖路徑相容性提取
                ss_str = report["browser_capture"].get("screenshot_path")
                if ss_str and Path(ss_str).is_file():
                    image = Path(ss_str)
                elif (out / "page.png").is_file():
                    image = out / "page.png"

        if args.domain_only or args.capture_only:
            report["status"] = "success"
            report["mode"] = "domain_only" if args.domain_only else "capture_only"
        else:
            progress.set(3, 'running')
            if not image or not image.is_file():
                raise ValueError("Input image does not exist")
            import fusion_model
            text_model_path, fusion_path = fusion_model.select(args.model_path, args.original_macbert)
            if not (text_model_path / "config.json").is_file():
                raise ValueError("MacBERT model missing: put models/macbert_social (or anti_fraud_E3_macbert) in place, or pass --model-path to the folder containing config.json")

            # Existing OCR progress belongs on stderr; stdout remains machine-readable JSON.
            with redirect_stdout(sys.stderr):
                from integrated_app import ScamDetectionPipeline
                report["models"] = {"macbert": str(text_model_path), "fusion": str(fusion_path)}
                pipeline = ScamDetectionPipeline(str(text_model_path.resolve()))

                browser_data = report.get("browser_capture")
                page_text = browser_data.get("page_text") if browser_data else None
                viewport = (browser_data or {}).get("viewport_evidence") if page_text else None
                from content_filter import filter_text, filter_viewport
                dom_filter = (filter_viewport(viewport) if viewport else filter_text(page_text)) if page_text else None
                dom_text = (dom_filter or {}).get("text") or None
                dom_result = pipeline.process_text(dom_text) if dom_text else None
                ocr_full = pipeline.process_image(str(image.resolve()))
                ocr_result, ocr_segments, alignment = image_only_model(pipeline, viewport, ocr_full)
                if not args.url:
                    report["image_domains"] = screenshot_domains(ocr_full.get("ocr_texts", []))
                    if report["image_domains"]:
                        progress.set(1, 'warning' if any(not d.get("capture_allowed", True) for d in report["image_domains"]) else 'done')
                        progress.set(2, 'running')
                        report["link_trace"] = follow_links(args, None, None, "", ocr_full, out, pipeline)
                        progress.set(2, 'done')
                    else:
                        progress.set(1, 'skipped')
                        progress.set(2, 'skipped')
                if args.calibration_json:
                    from calibration import apply_candidate
                    artifact = json.loads(args.calibration_json.read_text(encoding='utf-8'))
                    dom_result = apply_candidate(dom_result, 'dom', artifact, pipeline.detector.model_identity)
                    ocr_result = apply_candidate(ocr_result, 'screenshot_ocr', artifact, pipeline.detector.model_identity)
                report["image_analysis"] = ocr_result
                from alignment import compare_texts
                report["alignment"] = compare_texts(page_text, "\n".join(ocr_full.get("ocr_texts", [])),
                                                     (browser_data or {}).get("viewport_stable", True))
                report["region_alignment"] = alignment
                report["content_filter"] = dom_filter
                report["evidence"]["dom"] = ({"id": "content:dom", "text": page_text, "model_text": dom_text,
                                              "model": dom_result} if page_text else None)
                report["evidence"]["screenshot_ocr"] = {
                    "id": "content:screenshot_ocr",
                    "screenshot_path": str(image.resolve()),
                    "texts": ocr_full.get("ocr_texts", []),
                    "items": ocr_full.get("ocr_items", []),
                    "model_segments": ocr_segments,
                    "model_scope": "image_only_text" if alignment.get("available") else "all_ocr_text",
                    "model": ocr_result,
                }
                progress.set(3, 'done' if (dom_result and dom_result.get('status') == 'SUCCESS') or ocr_result.get('status') == 'SUCCESS' else 'warning')
                progress.set(4, 'running')
                report["source_agreement"] = fuse(dom_result, ocr_result)
                content_text = "\n".join(filter(None, [dom_text, "\n".join(ocr_segments)]))
                report["account_signals"], report["llm_evidence"], report["image_forensics"] = extra_evidence(
                    args, report, image, content_text, viewport, ocr_full, out)
                try:
                    import ood
                    reference = ood.load(ood.PATH.with_name('ood_reference_social.json') if 'social' in fusion_path.name else ood.PATH)
                    report["ood"] = ood.check(pipeline.detector, content_text or "\n".join(ocr_full.get("ocr_texts", [])), reference)
                except Exception as exc:
                    report["ood"] = {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}
                if args.url:
                    report["link_trace"] = follow_links(args, (browser_data or {}).get("final_url"),
                                                        (browser_data or {}).get("outbound_links"), page_text, ocr_full, out, pipeline)
                report.setdefault("link_trace", {"status": "no_links"})
                result = decide_content(report, dom_result, ocr_result, fusion_path)
                report["content_analysis"] = result
                report["assessment"] = assess(report["domain_analysis"], report["browser_capture"], result)
                exact = [m for m in (report["image_forensics"].get("known_scam_matches") or [])
                         if m["distance"]["phash"] <= 4 and m["distance"]["dhash"] <= 4]
                links = report.get("link_trace") or {}
                risky = [link for link in links.get("links", []) if link["risk"] in ("high", "medium")]
                if links.get("worst_risk") == "high" and report["assessment"]["risk_level"] != "High":
                    # A followed link lands on (or passes through) a 165-listed / lookalike site.
                    report["assessment"].update(risk_level="High", basis="redirect_rule",
                                                evidence_refs=[link["url"] for link in risky if link["risk"] == "high"])
                elif risky and report["assessment"]["risk_level"] in ("Low", "Unknown"):
                    report["assessment"].update(risk_level="Medium", basis="redirect_heuristic", evidence_refs=[link["url"] for link in risky])
                blocked_shots = [d for d in report.get("image_domains") or [] if not d.get("capture_allowed", True)]
                if blocked_shots and report["assessment"]["risk_level"] != "High":
                    # A URL printed in the uploaded screenshot is on the 165 list or imitates a platform.
                    report["assessment"].update(risk_level="High", basis="screenshot_domain",
                                                evidence_refs=[d["normalized_url"] for d in blocked_shots])
                mismatch = report["image_forensics"].get("brand_domain_mismatch") or []
                if mismatch and report["assessment"]["risk_level"] != "High":
                    # A brand name shown on screen next to a domain that brand does not own: impersonation.
                    report["assessment"].update(risk_level="High", basis="brand_impersonation",
                                                evidence_refs=[m["page_domain"] for m in mismatch])
                if not args.url and report["assessment"]["risk_level"] == "Medium":
                    import re
                    shown = re.sub(r"\W", "", "".join(ocr_full.get("ocr_texts", [])))
                    concrete = (report.get("image_domains") or (report["llm_evidence"].get("tactics") if report["llm_evidence"].get("status") == "SUCCESS" else None)
                                or any(s["weight"] > 0 for s in report["account_signals"]["signals"])
                                or report["image_forensics"].get("known_scam_matches") or report["image_forensics"].get("brand_domain_mismatch"))
                    if len(shown) < 30 and not concrete:
                        # A few short casual lines with no URL, tactic, account or image evidence: scam "bait" posts look exactly
                        # like this, so the text model alone cannot tell; abstain instead of calling it fraud.
                        report["assessment"].update(risk_level="Unknown", basis="too_little_text", text_characters=len(shown))
                if exact and report["assessment"]["risk_level"] != "High":
                    # Near-identical to an image the authorities confirmed as fraud: a lookup hit, like the 165 domain list.
                    report["assessment"].update(risk_level="High", basis="known_scam_image",
                                                evidence_refs=[m["source_url"] for m in exact])
                if browser_data and browser_data.get('viewport_stable'):
                    from report_explanation import compare_regions
                    try:
                        report['region_experiment'] = compare_regions(report, pipeline.detector)
                    except Exception as exc:
                        report['region_experiment'] = {'status': 'error', 'error': str(exc)}
                if args.calibration_json:
                    applied_sources = [name for name, item in [('dom', dom_result), ('screenshot_ocr', ocr_result)]
                                       if item and item.get('calibration', {}).get('status') == 'candidate_applied']
                    report['assessment']['calibration_status'] = 'candidate_applied_not_deployment_validated' if applied_sources else 'not_calibrated'
                    report['assessment']['calibrated_sources'] = applied_sources
                if args.explain:
                    from attribution import explain_report
                    try:
                        report["attribution"] = explain_report(pipeline.detector, report, callback=progress.experiment_progress)
                    except Exception as exc:
                        report["attribution"] = {"status": "error", "error": str(exc)}

                progress.set(4, 'done')
                if result["status"] == "SUCCESS":
                    report["status"] = "success"
                else:
                    code = 2
                    report["status"] = "incomplete"

    except Exception as exc:
        progress.stop(str(exc))
        report["error"] = f"{type(exc).__name__}: {exc}"
        if report["status"] == "partial":
            report["status"] = "error"
        code = 2

    report["report_path"] = str((out / "report.json").resolve())
    report["account_assessment"] = {
        "status": "unverified", "scope": "submitted_page_or_image",
        "note": "Content risk is not proof of account ownership or account-wide fraud."
    }
    from report_explanation import annotate, markdown
    annotate(report)
    (out / "report.md").write_text(markdown(report), encoding="utf-8")
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    (out / "report.json").write_text(payload, encoding="utf-8")
    try:
        from review_workflow import enqueue
        enqueue(out / "report.json", Path(__file__).parent / "artifacts" / "review_queue")
    except (OSError, ValueError) as exc:
        print(f"Review queue unavailable: {exc}", file=sys.stderr)
    print(payload)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
