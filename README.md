# 社群網址與圖片防詐分析

本專案先以網域規則檢查網址，再用 Playwright 擷取頁面，將 DOM 文字及截圖 OCR **分別**送入 MacBERT，最後保守融合。`risk_level` 是風險提示，不是詐騙事實認定；模型 softmax 信心度也不是經校準的詐騙機率。

## 安裝（Windows PowerShell）

需要 Python 3.11/3.12、Git，以及網路連線。取得本倉庫後，在專案根目錄執行：

```powershell
git clone https://github.com/Zcc209/Antifraud.git
cd Antifraud
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install torch==2.3.1 torchvision==0.18.1 --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements.txt
python -m playwright install chromium
```

若 `python` 指向錯誤版本，先執行 `python --version`，或以已安裝的 Python 3.12 完整路徑建立虛擬環境。模型權重不在 GitHub 倉庫內。請將已取得的 MacBERT 模型放在 `anti_fraud_E3_macbert/`；該資料夾至少需含 `config.json`、`model.safetensors` 或 `pytorch_model.bin`，以及 tokenizer 所需檔案。模型在其他位置時，於分析指令加入 `--model-path "D:\models\anti_fraud_E3_macbert"`。

## 執行

只檢查網址，不需要模型或瀏覽器：

```powershell
python run_pipeline.py --url "https://www.instagram.com/juksy_mag/" --domain-only
```

檢查網址並截圖，不需要模型：

```powershell
python run_pipeline.py --url "https://www.instagram.com/juksy_mag/" --capture-only
```

完整網址分析，或分析本機圖片：

```powershell
python run_pipeline.py --url "https://www.instagram.com/juksy_mag/"
python run_pipeline.py --image "C:\path\to\sample.png"
```

啟動網頁介面：

```powershell
python -m streamlit run web_ui.py
```

報告與截圖預設存於 `artifacts/`，命令列可用 `--output-dir` 指定輸出。`report.json` 的 `evidence` 記錄原始網域、轉址檢查、DOM 文字與截圖 OCR，各有可引用的 ID；`content_analysis` 記錄融合結論，`assessment.evidence_refs` 指向依據。兩路模型結論衝突時輸出 `Unknown`。單一路來源判為正常時也保留 `Unknown`；單一路詐騙訊號僅作風險提示，不當作已證實詐騙。

`page.png` 可能是錯誤頁或登入牆；**檔案存在不代表擷取成功**。此時報告為 `unusable`、風險為 `Unknown`，不會送入模型。網址疑似仿冒時為 `blocked`，不會前往該網址。只做 `--domain-only` 或 `--capture-only` 時，沒有內容模型判斷的風險可能為 `Unknown`。

截圖 OCR 會記錄每段原始辨識文字與 OCR 信心度；低於品質門檻或有效文字太少時不送入 MacBERT。品質門檻是保守的工程規則，尚未用正式標註集最佳化，可能漏掉只有短句的真實詐騙內容。

預設使用未登入瀏覽器。不會自動載入本機社群登入狀態，也不會自動打開有頭瀏覽器；如確實需要，可明確指定 `--headed` 和 `--storage-state "C:\private\state.json"`。登入狀態與截圖可能含個資，請勿提交至 Git。

## 測試與限制

```powershell
python -m unittest discover -s tests -v
```

`tests/fixtures/regression_cases.json` 是**人工編寫的離線回歸案例**，涵蓋錯誤頁、登入牆、HTTP 錯誤、仿冒網址與風險合併；它只驗證程式行為，不能用來宣稱模型準確率。仍需蒐集有來源、合法使用且獨立標註的真實案例，才能計算 Precision、Recall、F1。部分社群平台對未登入瀏覽有限制，遇到登入牆應回報無法判斷，而非繞過限制或判成低風險。

## 建立正式評估集

`data/annotation_template.csv` 是空白標註表。每列代表一個實際分析的帳號／網頁；`group_id` 必須是帳號或網站的穩定識別碼，**同一組所有案例只能屬於同一個 split**。`label` 只能填 `Fraud` 或 `Normal`，`split` 可填 `train`、`calibration`、`test`。`label_source` 請記錄原始公告／查證依據，`reviewer` 記錄人工複核者，`report_path` 指向 `run_pipeline.py` 產出的 `report.json`（相對路徑以標註表所在資料夾為基準）。同一帳號的多張截圖不得跨組；已用於 MacBERT 訓練的案例也不可再放入校準或測試集。

```powershell
python evaluate.py --manifest data\my_labeled_cases.csv --output artifacts\evaluation.json
```

評估輸出比較 `domain_only`、`macbert_only`（舊版優先用 DOM，無 DOM 才用 OCR）和 `fusion` 的 Precision、Recall、F1、混淆矩陣、`Unknown` 覆蓋率與錯誤案例。`Unknown` 在 Fraud 召回率中算漏判。少於每類 30 筆測試案例會標記樣本不足；即使達標，仍需人工稽核標籤、來源、平台分布、相同模型權重與原模型訓練資料的重疊，不能自動宣稱結果可公開。只有校準與獨立測試 split **各自每類至少 10 筆**有效模型分數時，才比較原始與候選溫度校準的 Brier score、ECE。**候選校準不會自動用於正式推論**；目前報告明示 `not_calibrated`，不把 softmax 百分比稱作詐騙機率。

[165 涉詐網站開放資料](https://data.gov.tw/dataset/176455)可作已知涉詐**網域候選**，不是正常帳號對照組，也不是社群截圖標籤。下載 CSV 後可抽出待審核清單：

```powershell
python prepare_165.py --input "C:\path\to\NPA_WEBURL.csv" --output artifacts\165_candidates.csv --limit 100
```
