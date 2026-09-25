import os
import sys
import json
import tempfile
import subprocess
from pathlib import Path
from PIL import Image
import streamlit as st

# ==========================================
# 1. 網頁基本設定
# ==========================================
st.set_page_config(page_title="AI 圖文防詐檢測系統", page_icon="🛡️", layout="wide")
st.title("🛡️ AI 圖文防詐檢測系統")
st.markdown("貼上網址或上傳截圖，讓 EasyOCR 與 MacBERT 為您快速辨識潛在詐騙風險。")

# ==========================================
# 2. 建立側邊欄 (設定區)
# ==========================================
output_dir = Path("artifacts/web_temp_output")
report_path = output_dir / "report.json"
chart_path = output_dir / "inference_result_chart.png"
screenshot_path = output_dir / "page.png"

with st.sidebar:
    st.header("⚙️ 系統設定")
    selected_mode = st.radio("請選擇分析模式：", ["網址全自動分析 (URL)", "單張圖片分析 (Image)"])

    # 檢查是否切換了模式，若是則徹底清除舊檔案並重整
    if "last_mode" not in st.session_state:
        st.session_state.last_mode = selected_mode

    if st.session_state.last_mode != selected_mode:
        st.session_state.last_mode = selected_mode
        for p in [report_path, chart_path, screenshot_path]:
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass
        st.rerun()

# ==========================================
# 3. 主畫面邏輯
# ==========================================
run_env = os.environ.copy()
run_env["PYTHONIOENCODING"] = "utf-8"


def show_run_status(process, stderr_str):
    if report_path.is_file():
        result = json.loads(report_path.read_text(encoding="utf-8"))
        status = result.get("status")
        if status == "success" and process.returncode == 0:
            st.success("分析完成。")
        elif status == "blocked":
            st.error("網址遭攔截，未進行頁面分析。")
        elif status == "unusable":
            st.warning("頁面無法取得有效內容，風險為 Unknown；截圖僅供排查。")
        else:
            st.error("分析未完成，請查看報告中的錯誤原因。")
    else:
        st.error("未產生報告。")
    if stderr_str.strip():
        with st.expander("系統日誌"):
            st.text(stderr_str)

if selected_mode == "網址全自動分析 (URL)":
    url_input = st.text_input("🌐 請輸入要檢測的網址 (例如: https://www.instagram.com/...):")

    # 如果網址輸入框被清空，自動清除下方的舊報告
    if not url_input and report_path.exists():
        for p in [report_path, chart_path, screenshot_path]:
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass
        st.rerun()

    if st.button("🚀 開始網址分析", type="primary"):
        if not url_input:
            st.warning("請先輸入網址！")
        else:
            for p in [report_path, chart_path, screenshot_path]:
                if p.exists():
                    try:
                        p.unlink()
                    except Exception:
                        pass

            with st.spinner("系統正在執行網域檢查、網頁截圖與 AI 分析，請稍候..."):
                cmd = [
                    sys.executable, "run_pipeline.py",
                    "--url", url_input,
                    "--output-dir", str(output_dir),
                ]
                process = subprocess.run(cmd, capture_output=True, env=run_env)
                stderr_str = process.stderr.decode("utf-8", errors="replace") if process.stderr else ""

            show_run_status(process, stderr_str)

elif selected_mode == "單張圖片分析 (Image)":
    uploaded_file = st.file_uploader("📷 請上傳要檢測的截圖 (PNG/JPG)", type=["png", "jpg", "jpeg"])

    if uploaded_file is None:
        if report_path.exists() or screenshot_path.exists():
            for p in [report_path, chart_path, screenshot_path]:
                if p.exists():
                    try:
                        p.unlink()
                    except Exception:
                        pass
            st.rerun()

    if st.button("🚀 開始圖片分析", type="primary"):
        if uploaded_file is None:
            st.warning("請先上傳圖片！")
        else:
            for p in [report_path, chart_path]:
                if p.exists():
                    try:
                        p.unlink()
                    except Exception:
                        pass

            with st.spinner("AI 正在進行 OCR 擷取與 MacBERT 推論..."):
                output_dir.mkdir(parents=True, exist_ok=True)

                # 將上傳的圖片儲存覆寫為 page.png
                with open(screenshot_path, "wb") as f:
                    f.write(uploaded_file.getvalue())

                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp_file:
                    tmp_file.write(uploaded_file.getvalue())
                    tmp_image_path = tmp_file.name

                try:
                    cmd = [
                        sys.executable, "run_pipeline.py",
                        "--image", tmp_image_path,
                        "--output-dir", str(output_dir),
                    ]
                    process = subprocess.run(cmd, capture_output=True, env=run_env)
                    stderr_str = process.stderr.decode("utf-8", errors="replace") if process.stderr else ""
                finally:
                    # 清理臨時圖片檔案
                    if os.path.exists(tmp_image_path):
                        try:
                            os.remove(tmp_image_path)
                        except Exception:
                            pass

            show_run_status(process, stderr_str)

# ==========================================
# 4. 顯示分析結果報告
# ==========================================
if report_path.exists():
    st.markdown("---")
    st.subheader("📊 分析結果報告")
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            report_data = json.load(f)

        status = report_data.get("status", "unknown")
        if status == "success":
            st.info("🟢 系統狀態：分析成功")
        elif status == "blocked":
            st.error("🔴 系統狀態：高風險，已攔截 (Blocked)")
        elif status == "unusable":
            st.warning("頁面不可用；截圖不是有效分析證據。")
        else:
            st.warning(f"分析狀態：{status}")

        assessment = report_data.get("assessment") or {}
        st.write(f"風險等級：{assessment.get('risk_level', 'Unknown')}；依據：{assessment.get('basis', 'insufficient_evidence')}")

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("#### 📷 檢測目標畫面")
            # 優先讀取真實存在的截圖檔案
            target_img_path = screenshot_path if screenshot_path.exists() else None
            if not target_img_path and report_data.get("browser_capture"):
                cand = report_data["browser_capture"].get("screenshot_path")
                if cand and Path(cand).exists():
                    target_img_path = Path(cand)

            if target_img_path and target_img_path.exists():
                st.image(Image.open(target_img_path), caption="檢測目標畫面", use_container_width=True)
            else:
                st.write("無圖片資料。")

        with col2:
            st.markdown("#### 📈 AI 推論效能圖表")
            # 優先採用 report.json 中記錄的 chart_path，若無則回退至預設 chart_path
            target_chart_path = chart_path
            if report_data.get("chart_path") and Path(report_data["chart_path"]).exists():
                target_chart_path = Path(report_data["chart_path"])

            if target_chart_path.exists():
                st.image(Image.open(target_chart_path), caption="信心度與延遲分析", use_container_width=True)
            else:
                st.warning("⚠️ 未產生圖表。")

        with st.expander("📑 查看完整 JSON 報告細節"):
            st.json(report_data)

    except Exception as e:
        st.error(f"讀取報告時發生錯誤：{e}")
