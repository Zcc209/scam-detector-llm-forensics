import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import cv2
import time
import easyocr
import matplotlib.pyplot as plt
import matplotlib
import statistics
from inference import FraudDetector

# 設定 matplotlib 支援中文
matplotlib.rcParams['font.sans-serif'] = ['Microsoft JhengHei', 'SimHei', 'Arial', 'sans-serif']
matplotlib.rcParams['axes.unicode_minus'] = False


class ScamDetectionPipeline:
    def __init__(self, model_path: str = "./anti_fraud_E3_macbert"):
        print("=== 初始化系統組件 (EasyOCR + MacBERT) ===")
        self.reader = easyocr.Reader(['ch_tra', 'en'], gpu=False)
        self.detector = FraudDetector(model_path)
        print("=== 系統初始化完成 ===")

    @staticmethod
    def _rule_signals(text: str) -> list[str]:
        """Explainable hints only; never overwrite the model's probabilities."""
        signals = []
        if any(term in text for term in ("保證獲利", "穩賺不賠", "零風險", "保證回本")):
            signals.append("guaranteed_return_claim")
        if any(term in text for term in ("USDT", "泰達幣", "匯款", "入金")) and any(
            term in text for term in ("私訊", "加LINE", "加賴", "老師", "助理")
        ):
            signals.append("payment_and_off_platform_contact")
        return signals

    def process_image(self, image_path: str) -> dict:
        """單張圖片的推論流程"""
        print(f"\n[1/2] 正在對圖片進行 OCR 文字擷取: {image_path}")

        img = cv2.imread(image_path)
        if img is None:
            return {"status": "ERROR", "prediction": "Unknown", "message": "Image not found or unreadable"}

        # 影像前處理：深色模式色彩反轉
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if gray.mean() < 127:
            img = cv2.bitwise_not(img)
            print("  🎨 [影像前處理] 偵測到深色背景 (Dark Mode)，已自動進行色彩反轉 (Inversion) 以提升 OCR 辨識率")

        extracted_texts = []
        try:
            results = self.reader.readtext(img, mag_ratio=2.5, contrast_ths=0.1, adjust_contrast=0.5)
            if results:
                probs = [prob for bbox, text, prob in results]

                if len(probs) > 2:
                    mean_prob = statistics.mean(probs)
                    stdev_prob = statistics.stdev(probs)
                    dynamic_threshold = max(0.1, mean_prob - stdev_prob)
                    print(f"  📊 [自適應統計] 樣本數: {len(probs)}, 平均值: {mean_prob:.2f}, 標準差: {stdev_prob:.2f}")
                    print(f"  ⚙️ [動態門檻] 設定為: {dynamic_threshold:.2f} (低於此分數且未觸發保護的雜訊將被剔除)")
                else:
                    dynamic_threshold = 0.0
                    print("  ⚠️ [自適應統計] 文字區塊過少，不啟動統計剔除機制。")

                scam_keywords = [
                    "翻紅", "飆股", "翻倍", "翻倉", "穩賺不賠", "高收益", "零風險", "內部消息", "內幕", "獨家專利", "暴利", "財富自由", "被套",
                    "緊急通知", "刪掉", "限時", "即刻", "馬上", "錯過不再", "最後機會", "私訊", "卡位",
                    "新台幣", "台幣", "台帶", "元", "目標", "現價", "預計", "本金", "入金", "出金", "USDT", "泰達幣", "匯款",
                    "加LINE", "加賴", "老師", "助理", "群組", "客服"
                ]

                for bbox, text, prob in results:
                    has_digits = any(char.isdigit() for char in text)
                    has_symbol = any(sym in text for sym in ["$", "＄", "¥", "%", "％"])
                    has_scam_keyword = any(keyword in text for keyword in scam_keywords)

                    if prob >= dynamic_threshold or has_digits or has_symbol or has_scam_keyword:
                        extracted_texts.append(text)
                        print(f"  ✅ [保留] '{text}' (信心度: {prob:.2f} | 觸發保護: 數字={has_digits}, 符號={has_symbol}, 關鍵字={has_scam_keyword})")
                    else:
                        print(f"  🗑️ [剔除] '{text}' (信心度: {prob:.2f} < 門檻 {dynamic_threshold:.2f})")

        except Exception as e:
            return {"status": "OCR_ERROR", "prediction": "Unknown", "message": str(e)}

        combined_ocr_text = "\n".join(extracted_texts)
        if not combined_ocr_text.strip():
            return {"status": "SKIPPED_OCR_EMPTY", "prediction": "Unknown", "ocr_texts": []}

        print(f"-> 最終合併辨識文字: '{combined_ocr_text}'")
        print("[2/2] 正在將文字送入 MacBERT 模型進行防詐推論...")

        start_time = time.time()
        result = self.detector.predict(combined_ocr_text)
        latency_ms = (time.time() - start_time) * 1000

        result['rule_signals'] = self._rule_signals(combined_ocr_text)

        result['latency_ms'] = latency_ms
        result['ocr_texts'] = extracted_texts
        result.setdefault('status', 'SUCCESS')

        return result

    def process_text(self, text: str) -> dict:
        """從網頁直接傳入純文字進行推論 (跳過 OCR)"""
        print(f"\n[光速分析] 成功攔截網頁純文字 (長度: {len(text)} 字)，直接送入 MacBERT 模型...")

        start_time = time.time()
        result = self.detector.predict(text)
        latency_ms = (time.time() - start_time) * 1000

        result['rule_signals'] = self._rule_signals(text)

        result['latency_ms'] = latency_ms
        result['ocr_texts'] = ["(系統優化：直接從網頁擷取純文字，已跳過 OCR)"]
        result.setdefault('status', 'SUCCESS')

        return result

    def plot_inference_result(self, result: dict, save_path="inference_result_chart.png"):
        """繪製單張圖片的預測結果、延遲與信心度圖表"""
        plt.rcParams['font.sans-serif'] = ['Microsoft JhengHei', 'SimHei', 'sans-serif']
        plt.rcParams['axes.unicode_minus'] = False

        prediction = result.get('prediction', 'Unknown')
        latency = result.get('latency_ms', 0.0)

        norm_conf = result.get('normal_confidence', 0.0)
        fraud_conf = result.get('fraud_confidence', 0.0)

        if norm_conf <= 1.0 and fraud_conf <= 1.0:
            norm_conf *= 100
            fraud_conf *= 100

        if norm_conf == 0.0 and fraud_conf == 0.0:
            conf = float(result.get('confidence', 0.85))
            conf = conf * 100 if conf <= 1.0 else conf
            if 'fraud' in str(prediction).lower() or '詐騙' in str(prediction):
                fraud_conf, norm_conf = conf, max(0.0, 100.0 - conf)
            else:
                norm_conf, fraud_conf = conf, max(0.0, 100.0 - conf)

        scores = {
            'Normal (正常)': norm_conf,
            'Fraud (詐騙)': fraud_conf
        }

        sorted_scores = sorted(scores.items(), key=lambda x: x[1])
        labels = [item[0] for item in sorted_scores]
        values = [item[1] for item in sorted_scores]

        colors = ['#27ae60' if '正常' in l else '#e74c3c' for l in labels]

        fig = plt.figure(figsize=(7, 4.5))
        fig.patch.set_facecolor('#f8f9fb')

        # 上半部：文字摘要
        plt.subplot(2, 1, 1)
        plt.axis('off')
        plt.text(0.0, 0.8, "即時推論結果 (Real-time Inference Result)", fontsize=14, fontweight='bold', color='#2c3e50')
        is_fraud = 'fraud' in str(prediction).lower() or '詐騙' in str(prediction)
        pred_color = '#e74c3c' if is_fraud else '#27ae60'
        plt.text(0.05, 0.4, f"• 最終判定 (Prediction): {prediction}", fontsize=12, fontweight='bold', color=pred_color)
        plt.text(0.05, 0.0, f"• 推論延遲 (Latency): {latency:.1f} ms", fontsize=12)

        # 下半部：信心度長條圖
        ax = plt.subplot(2, 1, 2)
        bars = ax.barh(labels, values, color=colors, height=0.5)

        ax.set_xlim(0, 100)
        ax.set_xlabel('信心度 Confidence (%)', fontsize=10)
        ax.set_title('各類別信心度分佈', fontsize=11, fontweight='bold')
        ax.grid(axis='x', linestyle='--', alpha=0.6)
        ax.set_axisbelow(True)

        for bar in bars:
            width = bar.get_width()
            ax.text(width + 2, bar.get_y() + bar.get_height() / 2, f'{width:.1f}%',
                    ha='left', va='center', fontsize=10, fontweight='bold')

        plt.tight_layout()
        plt.savefig(save_path, dpi=200, bbox_inches='tight', facecolor=fig.get_facecolor())
        plt.close()


if __name__ == "__main__":
    pipeline = ScamDetectionPipeline()
    target_image = "scam_screenshot.png"
    if os.path.exists(target_image):
        final_result = pipeline.process_image(target_image)
        if final_result.get("status") == "SUCCESS":
            pipeline.plot_inference_result(final_result, save_path="inference_result_chart.png")
        print("\n最終輸出結果 JSON:")
        print(final_result)
