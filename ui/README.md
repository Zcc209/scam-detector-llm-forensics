# 網頁介面

在專案根目錄執行 `python web_server.py`，開啟 http://127.0.0.1:8765（或 http://localhost:8765）。

- 模型放在 `anti_fraud_E3_macbert`；有 `models/macbert_social` 時會自動改用社群微調版。其他位置可加 `--model-path`。
- 可用 `--port 8766` 更換連接埠。只接受本機連線，不適合直接部署到公開網路。
- 內部的案例複核頁（`/review`）預設關閉，需要時加 `--review`。
- 每次分析保存在 `artifacts/web/<id>`，包括報告、截圖、執行日誌；「最近分析」列出這個資料夾中的結果，同一個目標只顯示最新一筆。
- 分析逾時為 10 分鐘；同時只執行一份分析，最多排隊 3 份。圖片上限 10 MB。
- 各區塊代表的意思見 `docs/system_walkthrough.md`。
