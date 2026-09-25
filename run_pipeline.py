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


def domain_assessment(initial, browser=None):
    return assess(initial, browser)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url")
    source.add_argument("--image", type=Path)
    parser.add_argument("--domain-only", action="store_true")
    parser.add_argument("--capture-only", action="store_true")
    parser.add_argument("--model-path", type=Path, default=Path(__file__).parent / "anti_fraud_E3_macbert")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--headed", action="store_true")
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

    try:
        image = args.image
        if args.url:
            domain = report["domain_analysis"] = analyze_url(args.url)
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
            if not image or not image.is_file():
                raise ValueError("Input image does not exist")
            if not (args.model_path / "config.json").is_file():
                raise ValueError("MacBERT model missing: extract the project's model download and pass --model-path to the folder containing config.json")

            # Existing OCR progress belongs on stderr; stdout remains machine-readable JSON.
            with redirect_stdout(sys.stderr):
                from integrated_app import ScamDetectionPipeline
                pipeline = ScamDetectionPipeline(str(args.model_path.resolve()))

                browser_data = report.get("browser_capture")
                page_text = browser_data.get("page_text") if browser_data else None
                dom_result = pipeline.process_text(page_text) if page_text else None
                ocr_result = pipeline.process_image(str(image.resolve()))
                report["image_analysis"] = ocr_result
                report["evidence"]["dom"] = ({"id": "content:dom", "text": page_text, "model": dom_result}
                                             if page_text else None)
                report["evidence"]["screenshot_ocr"] = {
                    "id": "content:screenshot_ocr",
                    "screenshot_path": str(image.resolve()),
                    "texts": ocr_result.get("ocr_texts", []),
                    "items": ocr_result.get("ocr_items", []),
                    "model": ocr_result,
                }
                result = fuse(dom_result, ocr_result)
                report["content_analysis"] = result
                report["assessment"] = assess(report["domain_analysis"], report["browser_capture"], result)

                if result["prediction"] in ("Fraud", "Normal"):
                    report["status"] = "success"
                else:
                    code = 2
                    report["status"] = "incomplete"

    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        if report["status"] == "partial":
            report["status"] = "error"
        code = 2

    report["report_path"] = str((out / "report.json").resolve())
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    (out / "report.json").write_text(payload, encoding="utf-8")
    print(payload)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
