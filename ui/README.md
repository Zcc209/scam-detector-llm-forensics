# 網頁介面

先啟用虛擬環境，再在專案根目錄執行 `python web_server.py`，開啟 http://127.0.0.1:8765（或 http://localhost:8765）。

- 模型放在 `models/macbert_social/`（下載方式見專案 README）。用其他模型時加 `--model-path`。
- 啟動時會檢查必要套件；用錯 Python（沒有啟用虛擬環境）會直接顯示缺少哪些套件並停止。
- 可用 `--port 8766` 更換連接埠。只接受本機連線，不適合直接部署到公開網路。
- 內部的案例複核頁（`/review`）預設關閉，需要時加 `--review`。
- 每次分析的報告、截圖、執行紀錄存在 `artifacts/web/<id>`（不會上傳 GitHub）。網站不提供歷史紀錄列表，可用「下載 JSON 報告」保存結果。
- 分析逾時為 10 分鐘；同時只執行一份分析，最多排隊 3 份。圖片上限 10 MB。
- 「系統實測成效」的數字讀自 `models/fusion_model_social.json`，重新訓練後自動更新。
- 各區塊代表的意思見 `docs/system_walkthrough.md`。
