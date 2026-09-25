"""
Input:
    OCR 擷取後、依閱讀順序合併的完整文字
Output:
    {
        "prediction": "Fraud" | "Normal" | "UNKNOWN",
        "confidence": float,
        "fraud_confidence": float,
        "normal_confidence": float,
        "cleaned_text": str,
        "num_chunks": int,
        "status": str
    }

- 長文字使用 512-token sliding window，stride=64。
- 同一 document 的 chunk logits 取平均後再做 softmax。
- confidence 是 softmax confidence，不代表已校準的真實機率。
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Union

import torch
from opencc import OpenCC
from transformers import AutoModelForSequenceClassification, AutoTokenizer

# 正規表達式定義
ROLE_PATTERN = re.compile(
    r"(?im)^\s*(?:"
    r"骗子|詐騙者|"
    r"欺诈者|欺詐者|"
    r"骗子|騙人|"
    r"发言人|發言人|"
    r"受害者|被害者|"
    r"用户|用戶|"
    r"客户|客戶|"
    r"客服|"
    r"对方|對方的|"
    r"陌生人|朋友|商家|"
    r"买家|買家|"
    r"老师|老師|"
    r"导师|導師|"
    r"工作人员|工作人員|"
    r"员工|員工|旁白"
    r")\s*\d*\s*[:：]\s*"
)

PUNCT_PATTERN = re.compile(
    r"""[
    ，。！？；：、，。！？；：
    “”‘’「」『』
    ()（）\[\] {}
    《》〈〉
    --- \-+ = ~ @#$%^&*
    \\/<>
    ]\n\r\t
    """,
    flags=re.VERBOSE,
)


class FraudDetector:
    """Fine-tuned MacBERT fraud detector."""

    def __init__(
        self,
        model_path: Union[str, Path],
        device: str | None = None,
        max_length: int = 512,
        stride: int = 64,
    ) -> None:
        self.model_path = str(model_path)

        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"

        self.device = torch.device(device)
        self.max_length = max_length
        self.stride = stride

        if self.stride >= self.max_length:
            raise ValueError("stride 必須小於 max_length。")

        self.cc = OpenCC("s2t")

        self.tokenizer = AutoTokenizer.from_pretrained(
            self.model_path,
            use_fast=True,
        )

        self.model = AutoModelForSequenceClassification.from_pretrained(
            self.model_path,
        ).to(self.device)

        self.model.eval()

    def clean_text(self, text: Any) -> str:
        """套用與訓練階段一致的 preprocessing"""
        text = "" if text is None else str(text)

        text = unicodedata.normalize("NFKC", text)
        text = ROLE_PATTERN.sub("", text)
        text = self.cc.convert(text)
        text = PUNCT_PATTERN.sub(" ", text)
        text = re.sub(r"\s+", " ", text)

        return text.strip()

    def _encode_chunks(self, cleaned_text: str):
        return self.tokenizer(
            cleaned_text,
            max_length=self.max_length,
            truncation=True,
            stride=self.stride,
            return_overflowing_tokens=True,
            return_tensors=None,
        )

    def _predict_logits(self, cleaned_text: str) -> tuple[torch.Tensor, int]:
        encoded = self._encode_chunks(cleaned_text)

        chunk_logits = []

        with torch.inference_mode():
            for input_ids, attention_mask in zip(
                encoded["input_ids"],
                encoded["attention_mask"],
            ):
                inputs = {
                    "input_ids": torch.tensor(
                        [input_ids],
                        dtype=torch.long,
                        device=self.device,
                    ),
                    "attention_mask": torch.tensor(
                        [attention_mask],
                        dtype=torch.long,
                        device=self.device,
                    ),
                }

                outputs = self.model(**inputs)
                chunk_logits.append(outputs.logits[0])

        if not chunk_logits:
            raise RuntimeError("Tokenizer 未產生任何可用 chunk。")

        document_logits = torch.stack(
            chunk_logits,
            dim=0,
        ).mean(dim=0)

        return document_logits, len(chunk_logits)

    def predict(self, text: str) -> Dict[str, Any]:
        """
        輸入 OCR 文字，回傳 Fraud / Normal 與 confidence。
        加入防呆機制：若清理後為空值，直接回傳 UNKNOWN 狀態，不引發例外。
        """
        cleaned_text = self.clean_text(text)

        # 守門員防呆：若文字為空，平滑跳過模型推論
        if not cleaned_text:
            return {
                "prediction": "UNKNOWN",
                "confidence": 0.0,
                "fraud_confidence": 0.0,
                "normal_confidence": 0.0,
                "cleaned_text": "",
                "num_chunks": 0,
                "status": "SKIPPED_OCR_EMPTY",
            }

        document_logits, num_chunks = self._predict_logits(cleaned_text)

        probabilities = torch.softmax(
            document_logits,
            dim=-1,
        )

        prediction_id = int(
            torch.argmax(probabilities).item()
        )

        pred_label = "Fraud" if prediction_id == 1 else "Normal"
        fraud_conf = float(probabilities[1].item())
        normal_conf = float(probabilities[0].item())

        return {
            "prediction": pred_label,
            "confidence": fraud_conf if prediction_id == 1 else normal_conf,
            "fraud_confidence": fraud_conf,
            "normal_confidence": normal_conf,
            "cleaned_text": cleaned_text,
            "num_chunks": num_chunks,
            "status": "SUCCESS",
        }


if __name__ == "__main__":
    # 測試主流程
    detector = FraudDetector("./anti_fraud_E3_macbert")

    test_inputs = [
        "老師帶你投資，保證獲利，現在加入群組並依照指示轉帳。",
        "",  # 測試空值防呆
        "今天天氣很好，我們去爬山吧。",
    ]

    for t in test_inputs:
        print("-" * 50)
        print(f"原始輸入: '{t}'")
        result = detector.predict(t)
        print(f"預測結果 JSON: {result}")