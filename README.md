# 防詐分析整合系統

目前系統依照 `arch.jpg` 與 `report.md` 保留兩條輸入流程：

- URL：Playwright 開啟網頁、處理常見彈窗並截圖，再交給圖片分析。
- 圖片：直接交給 YOLOv8、PaddleOCR、SerpApi 與 Gemini 分析。

URL 特徵分析是 C 同學的部分，目前不執行也不會出現在輸出 JSON。兩條流程最後都會輸出影像風險等級、分數、可疑證據、原因與建議處置。

## 安裝 Python 影像分析套件

```powershell
python -m pip install -r requirements.txt
```

如果本機的 `python` 沒有 pip，請使用有安裝 Python 3.10/3.11 的環境，或在 Google Colab 安裝 `requirements.txt` 內的套件。

## 設定 API key

不要把 key 寫進程式。請在目前 PowerShell 視窗設定環境變數：

```powershell
$env:GEMINI_API_KEY="你的新 Gemini key"
$env:SERPAPI_KEY="你的新 SerpApi key"
$env:IMGBB_API_KEY="你的新 ImgBB key"
```

因為舊 key 已經貼到對話中，建議先到三個服務後台撤銷並重新產生，再設定新 key。

## 完整分析 URL

```powershell
.\run_analysis.ps1 -Url "https://example.com" -AnalyzeImage
```

流程會將網頁截圖存到 `artifacts/screenshots/page.png`，接著讓 A 同學的影像模組分析截圖。

### Instagram 顯示無法載入時

Instagram 對未登入或自動化瀏覽器常會回傳錯誤頁。先建立一次登入 session：

```powershell
.\setup_instagram_session.ps1
```

Chrome 開啟後，請在瀏覽器內手動完成登入，再回到 PowerShell 按 Enter。登入狀態會存到 `artifacts/auth/instagram-storage-state.json`，不會保存帳號密碼；之後執行 Instagram URL 時會自動套用：

```powershell
.\run_analysis.ps1 -Url "https://www.instagram.com/juksy_mag/" -AnalyzeImage
```

也可明確指定 session 檔：

```powershell
.\run_analysis.ps1 -Url "https://www.instagram.com/juksy_mag/" -AnalyzeImage -StorageState ".\artifacts\auth\instagram-storage-state.json"
```

如果已登入仍顯示錯誤，代表 Instagram 暫時限制該 IP/session 或頁面本身不可用，請改成手動截圖後使用 `-Image` 分析。系統會將這類錯誤標示為 `Unknown`，不會把錯誤頁當成正常貼文。

## 完整分析本機圖片

```powershell
.\run_analysis.ps1 -Image ".\test.jpg" -AnalyzeImage
```

## 尚未安裝影像套件時測試整合

可先用範例上游 JSON 取代真正的 YOLO/OCR 執行：

```powershell
.\run_analysis.ps1 -Url "https://example.com" -Upstream ".\sample_upstream.json"
```

也可以單獨測圖片整合：

```powershell
.\run_analysis.ps1 -Image ".\image_process.jpg" -Upstream ".\sample_upstream.json"
```

## 輸出檔案

- `artifacts/final_report.json`：前後端或 LLM 可讀的結構化結果。
- `artifacts/analysis_report.md`：依照 `report.md` 欄位整理的人類可讀報告。
- `artifacts/image_analysis.json`：YOLO/OCR/SerpApi/Gemini 的原始影像分析結果。
- `artifacts/screenshots/page.png`：URL 輸入時的 Playwright 截圖。

風險門檻依 `report.md`：Low 0-39、Medium 40-69、High 70-100。現階段分數只來自影像分析；若社群登入牆遮擋主要內容或影像模組無法判斷，則標示 Unknown。
