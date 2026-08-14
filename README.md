# Antifraud 防詐分析系統

本專案接受「圖片」或「網址」作為輸入，整合 Playwright、YOLOv8、PaddleOCR、SerpApi、ImgBB 與 Gemini，產生結構化防詐分析報告。

目前 URL 特徵評分屬於其他模組，暫未納入此版本。網址輸入的流程是：

```text
網址 -> Playwright 開啟頁面與截圖 -> YOLO/OCR/反向圖片搜尋 -> Gemini 判斷 -> JSON/Markdown 報告
```

圖片輸入則直接從影像分析開始：

```text
圖片 -> YOLO/OCR/反向圖片搜尋 -> Gemini 判斷 -> JSON/Markdown 報告
```

## 支援環境

目前建議使用：

- Windows 10/11
- PowerShell 5.1 或更新版本
- Python 3.10 或 3.11
- Node.js 18 或更新版本
- Google Chrome 或 Microsoft Edge（安裝於預設路徑）

第一次執行 PaddleOCR/YOLO 時會下載模型，可能需要數分鐘。

## 專案結構

```text
Antifraud/
├─ main.js                       # 整合流程入口
├─ image_analyzer.py             # YOLO、OCR、SerpApi、Gemini
├─ run_analysis.ps1              # 原開發機 PowerShell 快捷入口
├─ setup_instagram_session.js    # 建立 Instagram 登入狀態
├─ setup_instagram_session.ps1   # 原開發機 Instagram 快捷入口
├─ requirements.txt              # Python 套件
├─ sample_upstream.json          # 不呼叫模型時的測試資料
└─ src/
   ├─ browserCapture.js          # Playwright 開頁與截圖
   ├─ mergeReport.js             # 整合風險結果
   └─ reportWriter.js            # Markdown 報告
```

## 1. 下載專案

```powershell
git clone https://github.com/Zcc209/Antifraud.git
cd .\Antifraud
```

## 2. 安裝 Python 環境

建議使用虛擬環境，避免影響電腦上的其他 Python 專案：

```powershell
py -3.11 -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

如果電腦沒有 `py` 指令，可改用：

```powershell
python -m venv .venv
```

確認 Python：

```powershell
python --version
python -m pip --version
```

## 3. 安裝 Node.js 與 Playwright

先確認 Node.js：

```powershell
node --version
npm --version
```

在專案資料夾安裝 Playwright：

```powershell
npm install --no-save --package-lock=false playwright
```

程式會優先使用電腦預設路徑中的 Chrome 或 Edge。

## 4. 設定 API Key

請勿把 API key 寫進程式碼、README、commit 或公開聊天內容。建議使用重新產生的 key，並在「同一個 PowerShell 視窗」設定：

```powershell
$env:GEMINI_API_KEY="你的 Gemini API key"
$env:SERPAPI_KEY="你的 SerpApi key"
$env:IMGBB_API_KEY="你的 ImgBB API key"
```

安全確認是否已設定（只顯示 True/False，不顯示內容）：

```powershell
[bool]$env:GEMINI_API_KEY
[bool]$env:SERPAPI_KEY
[bool]$env:IMGBB_API_KEY
```

API 用途：

- `GEMINI_API_KEY`：產生最終風險判斷。完整分析需要設定。
- `SERPAPI_KEY`：呼叫 Google Lens 反向圖片搜尋。
- `IMGBB_API_KEY`：把待查圖片暫時上傳，提供 SerpApi 查詢。

未設定 SerpApi 或 ImgBB 時，系統仍可執行 YOLO、OCR、Gemini，但會略過反向圖片搜尋。

## 5. 圖片輸入

先啟用虛擬環境：

```powershell
.\.venv\Scripts\Activate.ps1
```

完整分析圖片：

```powershell
node .\main.js `
  --image "C:\path\to\test.jpg" `
  --analyze-image `
  --python ".\.venv\Scripts\python.exe"
```

支援常見的 JPG、JPEG、PNG 圖片，實際可讀格式由 OpenCV 決定。

## 6. 一般網址輸入

Playwright 會開啟網址、處理常見彈窗、產生截圖，再將截圖送進影像分析：

```powershell
node .\main.js `
  --url "https://example.com" `
  --analyze-image `
  --python ".\.venv\Scripts\python.exe"
```

只測試 Playwright 截圖，不執行影像模型：

```powershell
node .\main.js --url "https://example.com"
```

## 7. Instagram 網址

Instagram 對未登入的自動化瀏覽器可能顯示登入牆或「無法載入頁面」。第一次使用前，請建立自己的登入 session：

```powershell
node .\setup_instagram_session.js
```

執行後：

1. Chrome 會自動開啟 Instagram 登入頁。
2. 請在 Chrome 內手動登入，帳密不要輸入 PowerShell。
3. 確認登入成功後，回到 PowerShell 按 Enter。
4. 登入狀態會儲存在 `artifacts/auth/instagram-storage-state.json`。

接著執行：

```powershell
node .\main.js `
  --url "https://www.instagram.com/juksy_mag/" `
  --analyze-image `
  --python ".\.venv\Scripts\python.exe" `
  --storage-state ".\artifacts\auth\instagram-storage-state.json"
```

登入狀態檔包含 session cookie，請勿分享。`artifacts/` 已被 `.gitignore` 排除。

如果 session 過期，重新執行 `node .\setup_instagram_session.js` 即可。

## 8. 不呼叫模型的整合測試

尚未安裝 AI 模型或尚未申請 API key 時，可使用範例 JSON 驗證整合與報告輸出：

```powershell
node .\main.js `
  --url "https://example.com" `
  --upstream ".\sample_upstream.json"
```

圖片流程也可測試：

```powershell
node .\main.js `
  --image "C:\path\to\test.jpg" `
  --upstream ".\sample_upstream.json"
```

## 9. 原開發機快捷指令

`run_analysis.ps1` 與 `setup_instagram_session.ps1` 目前含原開發機的 Codex Node runtime 路徑。只有該路徑存在時才可直接使用：

```powershell
.\run_analysis.ps1 -Image "C:\path\to\test.jpg" -AnalyzeImage

.\run_analysis.ps1 -Url "https://example.com" -AnalyzeImage

.\setup_instagram_session.ps1
```

其他電腦請優先使用前面文件中的 `node .\main.js` 與 `node .\setup_instagram_session.js`，不需要修改原始碼中的使用者名稱。

## 輸出檔案

執行後會建立 `artifacts/`：

```text
artifacts/
├─ final_report.json                 # 完整結構化報告
├─ analysis_report.md                # 人類可讀報告
├─ image_analysis.json               # YOLO/OCR/SerpApi/Gemini 原始結果
├─ screenshots/page.png              # 網址輸入的 Playwright 截圖
└─ auth/instagram-storage-state.json # Instagram session（建立後才有）
```

重要欄位：

- `browser_capture`：頁面標題、HTTP 狀態、最終網址、登入牆與載入錯誤。
- `image_analysis`：YOLO 物件、OCR 文字、反向圖片搜尋與 Gemini 判斷。
- `combined_assessment`：風險等級、分數、證據、理由與建議。

風險等級為 `Low`、`Medium`、`High` 或 `Unknown`。頁面載入失敗、登入牆遮擋或證據不足時應回傳 `Unknown`，不應把錯誤頁誤判為正常內容。

## 常見問題

### `Cannot find module 'playwright'`

請確認目前位於專案資料夾，然後執行：

```powershell
npm install --no-save --package-lock=false playwright
```

### `No supported browser executable was found`

請安裝 Google Chrome 或 Microsoft Edge，並使用預設安裝位置。

### `No module named pip`

目前使用的 Python 不含 pip。請重新安裝官方 Python 3.10/3.11，安裝時勾選 `Add Python to PATH`，再重新建立 `.venv`。

### `GEMINI_API_KEY is not set`

API key 必須設定在執行程式的同一個 PowerShell 視窗：

```powershell
$env:GEMINI_API_KEY="你的新 key"
```

### `ImgBB 上傳失敗：HTTP 400`

通常是 ImgBB key 無效、過期或 API 回應格式錯誤。請重新產生 key，並查看終端顯示的 ImgBB 回應內容。ImgBB 失敗不會中止主要分析，只會略過反向圖片搜尋。

### Instagram 顯示「無法載入頁面」

重新建立登入狀態：

```powershell
node .\setup_instagram_session.js
```

若登入後仍失敗，可能是 Instagram 暫時限制該 IP/session。可改為手動截圖後使用圖片輸入模式。

### OCR 出現大量錯字

系統已使用分段 OCR、信心門檻與 LLM 防誤判規則，但解析度、字體和壓縮仍會影響結果。重要案例應人工檢查 `image_analysis.image_features.ocr_details` 與原始截圖。

## 安全注意事項

- 不要提交 `.env`、API key、Instagram storage state 或真實帳號密碼。
- 不要把可疑網站中的帳密、信用卡、驗證碼填入頁面。
- 對未知網址使用自動化瀏覽器仍有風險，建議在測試帳號、隔離環境或沙箱中執行。
- 此系統只能提供輔助判斷，不能取代人工查證或官方通報流程。
