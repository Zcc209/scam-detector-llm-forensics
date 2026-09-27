"""User-facing explanations for abstentions, distinct from execution failures."""


def incomplete_message(report):
    content = report.get("content_analysis") or {}
    image = report.get("image_analysis") or {}
    if content.get("basis") == "modality_conflict":
        evidence = report.get("evidence") or {}
        dom = ((evidence.get("dom") or {}).get("model") or {}).get("prediction", "Unknown")
        ocr = ((evidence.get("screenshot_ocr") or {}).get("model") or {}).get("prediction", "Unknown")
        return f"分析已執行，但結果衝突：DOM 文字為 {dom}，截圖 OCR 為 {ocr}。風險維持 Unknown，請人工核對。"
    if image.get("status") == "SKIPPED_OCR_LOW_QUALITY":
        return "截圖 OCR 辨識品質不足，未進行內容判斷，風險維持 Unknown。請提供更清晰的圖片。"
    if image.get("status") == "SKIPPED_OCR_EMPTY":
        return "截圖未取得足夠可辨識文字，風險維持 Unknown。"
    if image.get("status") == "OCR_ERROR":
        return "OCR 執行失敗，風險維持 Unknown；請查看報告中的 image_analysis.message。"
    return "目前沒有足夠有效證據形成結論，風險維持 Unknown。"
