"""A 同學影像分析模組的可重用命令列版本。

API 金鑰只從環境變數讀取，不應寫入原始碼。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    import cv2
except ImportError:
    cv2 = None

try:
    import requests
except ImportError:
    requests = None

try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None

try:
    from paddleocr import PaddleOCR
except ImportError:
    PaddleOCR = None

try:
    from google import genai
except ImportError:
    genai = None

try:
    from serpapi import GoogleSearch
except ImportError:
    GoogleSearch = None


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


class AntiScamImageAnalyzer:
    def __init__(self, yolo_model_path: str = "yolov8n.pt") -> None:
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.serpapi_key = os.getenv("SERPAPI_KEY")
        self.imgbb_key = os.getenv("IMGBB_API_KEY")

        self.yolo_engine = None
        if YOLO is not None:
            try:
                self.yolo_engine = YOLO(yolo_model_path)
                log("YOLOv8 載入成功")
            except Exception as exc:
                log(f"YOLOv8 載入失敗，將略過：{exc}")

        self.ocr_engine = self._create_ocr_engine()
        self.gemini_client = None
        if genai is not None and self.gemini_key:
            self.gemini_client = genai.Client(api_key=self.gemini_key)

    def _create_ocr_engine(self):
        if PaddleOCR is None:
            return None

        constructors = [
            {"lang": "ch", "device": "cpu", "enable_mkldnn": False, "use_angle_cls": False},
            {"lang": "ch", "use_angle_cls": False},
            {"lang": "ch"},
        ]
        for options in constructors:
            try:
                engine = PaddleOCR(**options)
                log("PaddleOCR 載入成功")
                return engine
            except Exception:
                continue
        log("PaddleOCR 初始化失敗，將略過 OCR")
        return None

    def analyze(self, image_path: Path) -> dict[str, Any]:
        missing = self._missing_dependencies()
        if missing:
            return {
                "status": "error",
                "message": f"缺少必要套件：{', '.join(missing)}",
                "risk_level": "Unknown",
                "reason": "影像分析模組尚未完成安裝，無法取得 YOLO/OCR 證據。",
                "repaired_text": [],
            }

        if not image_path.exists():
            return {
                "status": "error",
                "message": "圖片檔案不存在",
                "risk_level": "Unknown",
                "reason": "找不到待分析圖片。",
                "repaired_text": [],
            }

        image = cv2.imread(str(image_path))
        if image is None:
            return {
                "status": "error",
                "message": "圖片無法讀取",
                "risk_level": "Unknown",
                "reason": "圖片格式損壞或 OpenCV 無法解碼。",
                "repaired_text": [],
            }

        yolo_objects = self._run_yolo(image)
        ocr_details = self._run_ocr(image)
        ocr_texts = [item["text"] for item in ocr_details]
        reverse_results = self._run_reverse_image_search(image_path)
        image_features = {
            "yolo_objects": yolo_objects,
            "ocr_texts": ocr_texts,
            "ocr_details": ocr_details,
            "reverse_image_search": reverse_results,
        }

        if self.gemini_client is None:
            return {
                "status": "partial",
                "risk_level": "Unknown",
                "reason": "YOLO/OCR 證據已取得，但未設定 Gemini API，尚未進行 LLM 綜合判斷。",
                "repaired_text": ocr_texts,
                "image_features": image_features,
            }

        decision = self._call_gemini(image_features)
        decision["image_features"] = image_features
        return decision

    @staticmethod
    def _missing_dependencies() -> list[str]:
        missing = []
        if cv2 is None:
            missing.append("opencv-python")
        return missing

    def _run_yolo(self, image) -> list[str]:
        if self.yolo_engine is None:
            return []
        labels: list[str] = []
        try:
            for result in self.yolo_engine(image, verbose=False):
                for box in result.boxes:
                    if float(box.conf[0]) <= 0.35:
                        continue
                    name = self.yolo_engine.names[int(box.cls[0])]
                    if name not in labels:
                        labels.append(name)
        except Exception as exc:
            log(f"YOLO 推論失敗：{exc}")
        return labels

    @staticmethod
    def _is_useful_ocr_text(text: str) -> bool:
        compact = "".join(text.split())
        if len(compact) < 2:
            return False
        meaningful = sum(
            char.isalnum() or "\u4e00" <= char <= "\u9fff" for char in compact
        )
        return meaningful >= 2

    def _run_ocr(self, image) -> list[dict[str, Any]]:
        if self.ocr_engine is None:
            return []

        if image.shape[1] > 1600:
            ratio = 1600 / float(image.shape[1])
            image = cv2.resize(
                image,
                (1600, int(image.shape[0] * ratio)),
                interpolation=cv2.INTER_AREA,
            )

        details: list[dict[str, Any]] = []
        seen: set[str] = set()
        tile_height = 1800
        overlap = 120
        step = tile_height - overlap

        for top in range(0, image.shape[0], step):
            tile = image[top : min(top + tile_height, image.shape[0])]
            if tile.size == 0:
                continue
            try:
                results = self.ocr_engine.predict(tile)
            except Exception as exc:
                log(f"OCR 分段推論失敗：{exc}")
                continue

            for result in list(results or []):
                if hasattr(result, "get") and result.get("rec_texts"):
                    texts = list(result.get("rec_texts", []))
                    scores = list(result.get("rec_scores", []))
                    for index, raw_text in enumerate(texts):
                        text = str(raw_text).strip()
                        score = float(scores[index]) if index < len(scores) else 1.0
                        if score < 0.68 or not self._is_useful_ocr_text(text):
                            continue
                        if text not in seen:
                            details.append({"text": text, "confidence": round(score, 4)})
                            seen.add(text)
                elif isinstance(result, list):
                    for line in result:
                        if not isinstance(line, list) or len(line) != 2:
                            continue
                        recognition = line[1]
                        if not isinstance(recognition, (tuple, list)) or not recognition:
                            continue
                        text = str(recognition[0]).strip()
                        score = float(recognition[1]) if len(recognition) > 1 else 1.0
                        if score < 0.68 or not self._is_useful_ocr_text(text):
                            continue
                        if text not in seen:
                            details.append({"text": text, "confidence": round(score, 4)})
                            seen.add(text)

        return details

    def _upload_to_imgbb(self, image_path: Path) -> str | None:
        if not self.imgbb_key or requests is None:
            return None
        try:
            encoded_image = base64.b64encode(image_path.read_bytes()).decode("ascii")
            response = requests.post(
                "https://api.imgbb.com/1/upload",
                params={"key": self.imgbb_key},
                data={"image": encoded_image, "name": image_path.stem},
                timeout=30,
            )
            if not response.ok:
                response_excerpt = response.text[:500].replace("\n", " ")
                log(
                    f"ImgBB 上傳失敗：HTTP {response.status_code}; "
                    f"回應：{response_excerpt}"
                )
                return None
            return response.json()["data"]["url"]
        except Exception as exc:
            log(f"ImgBB 上傳失敗：{exc}")
            return None

    def _run_reverse_image_search(self, image_path: Path) -> list[str]:
        if not self.serpapi_key or not self.imgbb_key or GoogleSearch is None:
            return ["未完整配置 SerpApi/ImgBB，略過反向圖片搜尋。"]

        image_url = self._upload_to_imgbb(image_path)
        if not image_url:
            return ["圖片上傳失敗，略過反向圖片搜尋。"]

        try:
            search = GoogleSearch(
                {
                    "engine": "google_lens",
                    "url": image_url,
                    "api_key": self.serpapi_key,
                    "hl": "zh-TW",
                }
            )
            results = search.get_dict()
            evidence: list[str] = []
            graph = results.get("knowledge_graph", {})
            if graph.get("title"):
                evidence.append(
                    f"公眾實體: {graph.get('title')} ({graph.get('subtitle', '')})"
                )
            for match in results.get("visual_matches", [])[:3]:
                if match.get("title"):
                    evidence.append(f"視覺匹配: {match['title']}")
            return evidence or ["未比對到明確公眾身分。"]
        except Exception as exc:
            log(f"SerpApi 查詢失敗：{exc}")
            return ["反向圖片搜尋暫時失敗。"]

    def _call_gemini(self, features: dict[str, Any]) -> dict[str, Any]:
        prompt = f"""
你是資深防詐與資安鑑識專家。請根據下列結構化證據判斷圖片風險。

判斷原則：
1. 若 OCR 主要是登入、註冊、忘記密碼等社群登入牆文字，且真正內容被遮擋，risk_level 必須是 Unknown。
2. 檢查人物身分與反向圖片搜尋結果是否和 OCR 宣稱一致。
3. 尋找保證獲利、投資、加 LINE、限時、贈品、付款或要求輸入帳密等組合訊號。
4. OCR 可能誤讀。不得使用孤立、語意不通或只有 2-3 字的片段推論詐騙，例如把錯字自行解釋成投資術語。
5. Medium/High 至少需要兩項語意清楚且互相支持的可疑證據；沒有明確詐騙訊號時應判為 Low，證據無法閱讀時才判 Unknown。
6. 若畫面是可辨識的官方品牌或一般社群帳號，且沒有要求付款、帳密、私下聯絡或保證獲利，應判為 Low。
7. 僅依證據判斷，不可捏造未出現的內容。理由使用繁體中文且不超過 150 字。

YOLO 物件：{features['yolo_objects']}
OCR 文字：{features['ocr_texts']}
OCR 文字與信心分數：{features['ocr_details']}
反向圖片搜尋：{features['reverse_image_search']}

只輸出 JSON：
{{
  "risk_level": "High / Medium / Low / Unknown",
  "reason": "繁體中文分析理由",
  "repaired_text": ["用於判斷的 OCR 關鍵文字"]
}}
"""

        for attempt in range(3):
            try:
                response = self.gemini_client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                )
                clean = response.text.replace("```json", "").replace("```", "").strip()
                output = json.loads(clean)
                output = self._validate_llm_output(output, features)
                output["status"] = "success"
                return output
            except Exception as exc:
                if attempt < 2 and ("503" in str(exc) or "UNAVAILABLE" in str(exc)):
                    time.sleep(2**attempt)
                    continue
                return {
                    "status": "error",
                    "risk_level": "Unknown",
                    "reason": "LLM 服務未能完成判斷，請稍後重試。",
                    "repaired_text": features["ocr_texts"],
                    "message": str(exc),
                }

    @staticmethod
    def _validate_llm_output(
        output: dict[str, Any], features: dict[str, Any]
    ) -> dict[str, Any]:
        valid_levels = {"High", "Medium", "Low", "Unknown"}
        if output.get("risk_level") not in valid_levels:
            output["risk_level"] = "Unknown"

        source_texts = [str(item) for item in features.get("ocr_texts", [])]
        selected = [
            str(item)
            for item in output.get("repaired_text", [])
            if str(item) in source_texts
        ]
        output["repaired_text"] = selected

        suspicious_terms = [
            "投資",
            "獲利",
            "获利",
            "保證",
            "保证",
            "匯款",
            "汇款",
            "轉帳",
            "转账",
            "信用卡",
            "驗證碼",
            "验证码",
            "密碼",
            "密码",
            "付款",
            "中獎",
            "中奖",
            "限時",
            "限时",
            "加line",
            "私訊",
            "私信",
            "點擊",
            "点击",
            "連結",
            "链接",
            "短網址",
            "wallet",
            "crypto",
            "profit",
            "investment",
            "bonus",
            "otp",
        ]
        joined = " ".join(source_texts).lower()
        has_explicit_signal = any(term in joined for term in suspicious_terms)
        has_coherent_selected_text = any(
            len("".join(item.split())) >= 4 for item in selected
        )
        external_results = features.get("reverse_image_search", [])
        has_external_signal = any(
            "公眾實體:" in str(item) or "視覺匹配:" in str(item)
            for item in external_results
        )

        if (
            output.get("risk_level") in {"High", "Medium"}
            and not has_explicit_signal
            and not has_external_signal
            and not has_coherent_selected_text
        ):
            output["risk_level"] = "Low"
            output["reason"] = (
                "OCR 僅出現零碎或疑似誤讀文字，未找到付款、帳密、投資、"
                "私下聯絡或誘導點擊等明確詐騙訊號，因此不採用原本的中高風險推論。"
            )
            output["repaired_text"] = []

        return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="執行 YOLO/OCR/SerpApi/Gemini 圖片分析")
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--yolo-model", default="yolov8n.pt")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = AntiScamImageAnalyzer(args.yolo_model).analyze(args.image.resolve())
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized)
    return 0 if result.get("status") in {"success", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
