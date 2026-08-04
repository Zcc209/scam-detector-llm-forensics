function escapeMarkdown(value) {
  return String(value ?? "").replace(/\|/g, "\\|").replace(/\r?\n/g, " ");
}

function bulletLines(items, fallback) {
  if (!items || items.length === 0) return `- ${fallback}`;
  return items.map((item) => `- ${escapeMarkdown(item)}`).join("\n");
}

function createMarkdownReport(report) {
  const assessment = report.combined_assessment;
  const browser = report.browser_capture;
  const imageAnalysis = report.image_analysis;

  return `# 防詐分析報告

- 產生時間：${escapeMarkdown(report.generated_at)}
- 輸入類型：${escapeMarkdown(report.input.type)}
- 分析目標：${escapeMarkdown(report.input.url || report.input.image_path)}
- 整體風險等級：${escapeMarkdown(assessment.risk_level)}
- 風險分數：${escapeMarkdown(assessment.risk_score)} / 100

## 系統分析結果

${bulletLines(assessment.reasons, "目前沒有可用的分析理由。")}

## 可疑證據

${bulletLines(assessment.suspicious_evidence, "未發現明顯可疑證據。")}

## 建議處置

${bulletLines(assessment.recommendations, "請持續保持基本查證。")}

## Playwright 瀏覽器證據

| 欄位 | 結果 |
|---|---|
| 原始 URL | ${escapeMarkdown(report.input.url || "不適用")} |
| 最終 URL | ${escapeMarkdown(browser?.final_url || "不適用")} |
| HTTP 狀態 | ${escapeMarkdown(browser?.http_status ?? "不適用")} |
| 頁面標題 | ${escapeMarkdown(browser?.page_title || "不適用")} |
| 登入牆 | ${browser?.login_wall_detected ? "是" : "否"} |
| 載入失敗 | ${browser?.load_error_detected ? "是" : "否"} |
| 使用登入 session | ${browser?.storage_state_used ? "是" : "否"} |

## 影像分析證據

| 欄位 | 結果 |
|---|---|
| YOLO 物件 | ${escapeMarkdown(imageAnalysis?.image_features?.yolo_objects?.join(", ") || "未取得")} |
| OCR 文字數 | ${escapeMarkdown(imageAnalysis?.image_features?.ocr_texts?.length ?? "未取得")} |
| 影像風險等級 | ${escapeMarkdown(imageAnalysis?.risk_level || "未執行")} |
| 影像分析狀態 | ${escapeMarkdown(imageAnalysis?.status || "未執行")} |

## 評分方式

目前 URL 特徵分析屬於 C 同學模組，暫不納入。低風險 0-39、中風險 40-69、高風險 70-100；登入牆遮擋或影像無法判斷時，結果標示為 Unknown。
`;
}

module.exports = {
  createMarkdownReport,
};
