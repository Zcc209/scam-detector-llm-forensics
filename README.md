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

