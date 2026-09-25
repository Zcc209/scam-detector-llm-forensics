# 社群網址與圖片防詐分析

本專案先以網域規則檢查網址，再用 Playwright 擷取頁面，最後將可用的網頁文字或上傳圖片的 OCR 文字送入 MacBERT。`risk_level` 是風險提示，不是詐騙事實認定；模型 softmax 信心度也不是經校準的詐騙機率。

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

報告與截圖預設存於 `artifacts/`，命令列可用 `--output-dir` 指定輸出。`report.json` 會保留原始網域檢查、轉址檢查、擷取狀態、模型結果與最後風險依據。`page.png` 可能是錯誤頁或登入牆；**檔案存在不代表擷取成功**。此時報告為 `unusable`、風險為 `Unknown`，不會送入模型。網址疑似仿冒時為 `blocked`，不會前往該網址。只做 `--domain-only` 或 `--capture-only` 時，沒有內容模型判斷的風險可能為 `Unknown`。

預設使用未登入瀏覽器。不會自動載入本機社群登入狀態，也不會自動打開有頭瀏覽器；如確實需要，可明確指定 `--headed` 和 `--storage-state "C:\private\state.json"`。登入狀態與截圖可能含個資，請勿提交至 Git。

## 測試與限制

```powershell
python -m unittest discover -s tests -v
```

`tests/fixtures/regression_cases.json` 是**人工編寫的離線回歸案例**，涵蓋錯誤頁、登入牆、HTTP 錯誤、仿冒網址與風險合併；它只驗證程式行為，不能用來宣稱模型準確率。仍需蒐集有來源、合法使用且獨立標註的真實案例，才能計算 Precision、Recall、F1。部分社群平台對未登入瀏覽有限制，遇到登入牆應回報無法判斷，而非繞過限制或判成低風險。
