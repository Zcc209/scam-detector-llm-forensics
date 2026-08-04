function levelToBaseScore(level) {
  switch (String(level || "").toLowerCase()) {
    case "high":
      return 70;
    case "medium":
      return 45;
    case "low":
      return 10;
    default:
      return 0;
  }
}

function scoreToLevel(score) {
  if (score >= 70) return "High";
  if (score >= 40) return "Medium";
  return "Low";
}

function mergeAssessment(imageAnalysis, browserCapture) {
  const imageScore = levelToBaseScore(imageAnalysis?.risk_level);
  const loginWall = Boolean(browserCapture?.login_wall_detected);
  const loadError = Boolean(browserCapture?.load_error_detected);
  const imageUnknown = String(imageAnalysis?.risk_level || "").toLowerCase() === "unknown";
  const geminiMissing =
    imageAnalysis?.status === "partial" &&
    String(imageAnalysis?.reason || "").includes("Gemini API");

  let riskLevel = scoreToLevel(imageScore);
  if (!imageAnalysis || imageUnknown || loadError || (loginWall && imageUnknown)) {
    riskLevel = "Unknown";
  }

  const reasons = [];
  const suspiciousEvidence = [];
  const recommendations = new Set();

  if (imageAnalysis?.reason) reasons.push(`Image/OCR: ${imageAnalysis.reason}`);

  for (const repairedText of imageAnalysis?.repaired_text || []) {
    suspiciousEvidence.push(`OCR 關鍵文字: ${repairedText}`);
  }

  for (const item of imageAnalysis?.image_features?.reverse_image_search || []) {
    if (!String(item).includes("未配置") && !String(item).includes("略過")) {
      suspiciousEvidence.push(`外部圖片線索: ${item}`);
    }
  }

  if (loginWall) {
    reasons.push("Browser: 未登入狀態下偵測到平台登入牆或內容遮罩。");
    suspiciousEvidence.push("頁面顯示登入/註冊要求，截圖內容可能被平台限制。");
    recommendations.add("社群平台若限制未登入瀏覽，請改用手動截圖上傳分析。");
  }

  if (loadError) {
    reasons.push("Browser: 目標網頁載入失敗，截圖不是原始貼文內容。");
    suspiciousEvidence.push("Playwright 取得的是平台錯誤頁，不能用於內容風險判斷。");
    recommendations.add("請先建立已登入的瀏覽器 session 後重試，或改用手動截圖上傳。");
  }

  if (riskLevel === "High") {
    recommendations.add("不要輸入帳號、密碼、信用卡或驗證碼，並改由官方管道確認。");
  } else if (riskLevel === "Medium") {
    recommendations.add("先暫停互動，核對官方來源後再繼續。");
  } else if (riskLevel === "Unknown") {
    if (geminiMissing) {
      recommendations.add("請設定 GEMINI_API_KEY 後重新執行，以完成 LLM 綜合判斷。");
    } else {
      recommendations.add("目前證據不足，請提供未被遮擋的原始截圖重新分析。");
    }
  } else {
    recommendations.add("目前未發現明顯高風險訊號，但仍應保持基本查證。");
  }

  return {
    risk_level: riskLevel,
    risk_score: riskLevel === "Unknown" ? null : imageScore,
    reasons,
    suspicious_evidence: suspiciousEvidence,
    recommendations: [...recommendations],
    scoring: {
      image_score: imageScore,
      thresholds: {
        low: "0-39",
        medium: "40-69",
        high: "70-100",
      },
      note: "URL feature analysis is temporarily excluded and will be integrated by module C later.",
    },
  };
}

module.exports = {
  mergeAssessment,
};
