# 本週可操作流程與交付界線

## 現在能用的部分

網址：網域守門 → Playwright 可用性檢查 → 同視窗截圖與可見 DOM → OCR → 兩路 MacBERT → 保留來源的風險提示。
圖片：OCR → MacBERT → 單一路內容證據；不虛構 DOM 或帳號身分驗證。
所有有內容的結果可進本機待複核佇列。登入牆、遮擋、錯誤頁不能用於內容訓練。
沒有品牌白名單、不用 Normal 強制覆蓋衝突、不逐筆自動更新模型。

DOM 範圍改為截圖視窗內，排除隱藏／畫面外／被遮住的文字；保留文字框、viewport、捲動位置及 DPR。
截圖前後 DOM 文字或 viewport 改變，或抽取超過上限時，停用這次 DOM 分數。
完整 body 仍保存在 full_page_text 作除錯，不送入分類器。
這不是逐像素同步保證：動態圖片、canvas、iframe、CSS 產生文字等可能只有 OCR 看得到。
字元重疊率只是範圍診斷，不用來修改風險分數。
OCR 信心度篩選對所有文字採同一政策，不再特別保留低信心詐騙關鍵字；這項修改需在測試集比較漏字／漏報，不能假定必然改善。

## 1. 啟動與蒐集

在專案根目錄、已安裝依賴的 Python 環境執行：

```powershell
python web_server.py
```

開啟 http://127.0.0.1:8765，網址或圖片分析後，點「案例複核」。
程式碼所在路徑：`C:\Users\linzi\Downloads\url\Antifraud-refresh`。
此機可用的 Python：`C:\Users\linzi\Downloads\antifraud\.venv312\Scripts\python.exe`。
其他電腦請改用自己的虛擬環境，不需 Gemini、ImgBB 或 SerpAPI key。

待複核資料存 `artifacts/review_queue`，不會送到外部服務。它可能含私人文字，請勿上傳 GitHub。
舊報告可匯入（但舊 DOM 不是同視窗內容，建議重跑網址後再建立新版資料集）：

```powershell
python review_workflow.py collect artifacts/web_temp_output/report.json
```

## 2. 真實成對案例與人工複核

建議先找：正常品牌活動 vs 冒用品牌釣魚、正常交友閒聊 vs 誘導匯款、一般促銷 vs 假交易付款。
每一組必須是兩個獨立查證的真實案例，不可把同一正常文手改幾字後當真實詐騙。
不要求都來自 Dcard，應涵蓋 IG、Facebook、Threads、其他公開網站及圖片；無法擷取的社群可用合法取得的原始截圖。
一般使用者也不需先登入才可上傳图片，但截圖本身不能證明帳號由誰控制。

參考來源：https://fraudbuster.digiat.org.tw/accessibility/index
該站有「疑似詐騙」「高風險」「詐騙訊息，已通知移除」「經數發部確認，非詐騙訊息」等不同狀態。
前兩者保留 Unresolved，不直接標 Fraud。確認類也需核對案件詳情、日期、原始內容與使用依據。
本次可以讀到首頁清單，但詳情頁的工具讀取失敗，沒有據此自動建立確定標籤。
不要把案件查詢頁中的「詐騙」「非詐騙」結論當模型輸入，否則是標籤洩漏；要用原始貼文／圖片。
查無通報不等於正常；品牌名稱／粉絲數／藍勾勾不等於安全。

複核頁填寫：內容標籤、來源、判定依據、授權／使用依據、複核者、group_id、split，成對案例另填 pair_id。
group_id 用帳號識別，例如 instagram:example；同帳號跨網址與截圖也必须同一 ID。
同帳號或同 pair_id 不可分散至 train / validation / calibration / test。
相同文字跨 split、矛盾標籤、未複核、合成資料及缺欄位會被拒絕。
近似文案／跨帳號同一詐騙活動仍需人工用同 group_id 管理，程式沒有保證偵測所有近重複。
只標內容，不要因一篇正常貼文就把整個帳號標成永久正常。
一人複核可執行，但報告要承認沒有雙人標註一致性驗證。

資料切分先定好再看結果；test 不用來選模型、調參數或選 Platt/Isotonic。
微調最低守門：train、validation 各類至少 2 個獨立 group；校準預設 calibration、test 各類至少 10 個。
這些是程式守門，不是足夠準確率的保證。只收集 Unknown 會偏樣，應另外抽樣正常及高信心結果。
若本週蒐集不足，保留原模型、展示流程與案例分析，不能宣稱已完成正式泛化驗證。

匯出不可覆寫的資料快照：

```powershell
python review_workflow.py build --output artifacts/dataset-v1.jsonl
```

## 3. 批次微調（不取代原模型）

```powershell
python train_reviewed.py --corpus artifacts/dataset-v1.jsonl --output artifacts/models/candidate-v1
```

預設只解凍最後兩層 encoder、pooler 與分類頭；每個 epoch 依帳號權重累積文件梯度後更新一次。
沿用 inference 的清理、512-token / stride 64、文件 chunk logits 平均策略，不靜默截掉長文件。
預設 2 epochs、2e-5 learning rate 是實驗起點，不是已驗證最佳參數；可在 validation 上選擇。
`--layers 0` 只調 pooler／分類頭，CPU 可先驗證流程；`--epochs`、`--learning-rate` 可調。
原模型不動，新資料建議加入可合法使用的舊領域複核樣本，並觀察是否遺忘原本詐騙類型。
選 validation loss 最低的候選權重，訓練紀錄保存來源模型 SHA、資料 SHA、參數和 loss。
不同訓練版本必須用不同 output 資料夾；完成訓練不等於可以發布。

## 4. 固定模型後的校準與測試

先產生原模型與候選模型在同一批內容上的新報告，不重新爬取已變動的網站：

```powershell
python review_workflow.py score --corpus artifacts/dataset-v1.jsonl --output artifacts/baseline-v1
python review_workflow.py score --corpus artifacts/dataset-v1.jsonl --model-path artifacts/models/candidate-v1 --output artifacts/candidate-scores-v1
python calibration.py --manifest artifacts/candidate-scores-v1/manifest.csv --model-path artifacts/models/candidate-v1 --method platt --output artifacts/calibration-v1.json
python review_workflow.py score --corpus artifacts/dataset-v1.jsonl --model-path artifacts/models/candidate-v1 --calibration-json artifacts/calibration-v1.json --output artifacts/calibrated-scores-v1
python evaluate.py --manifest artifacts/baseline-v1/manifest.csv --output artifacts/baseline-evaluation.json
python evaluate.py --manifest artifacts/candidate-scores-v1/manifest.csv --output artifacts/candidate-evaluation.json
python evaluate.py --manifest artifacts/calibrated-scores-v1/manifest.csv --output artifacts/calibrated-evaluation.json
```

Platt 只 fit calibration split，test 只量測；不夠資料不會生成可用映射。
校準依 DOM／OCR 分開，綁定模型指紋，不能拿舊模型的映射套新模型。
指標包含 Precision、Recall、F1、誤報率、Unknown 比例、混淆矩陣與誤判案例；校準檔另有 Brier、log loss、ECE。
evaluate 的 macbert_only 刻意保留原始分類作消融；fusion 才反映已套用校準的兩路決策。
不可因 test 看起來不好再改參數並把同一批 test 當未見資料；重新選模後要另留測試資料。
測試驗證內容風險，不是帳號身分辨識準確率。圖片只有一路證據，Unknown 是允許的結果。

確認候選沒有增加真實詐騙漏報、且正常行銷誤報有所改善後，再明確指定使用：

```powershell
python web_server.py --model-path artifacts/models/candidate-v1 --calibration-json artifacts/calibration-v1.json
```

## 5. 書面／影片／海報可使用的描述

系統定位：以社群帳號頁面與使用者截圖為輸入的詐騙風險輔助偵測，不是司法認定或帳號所有權驗證。
架構圖：輸入 → 網域／畫面守門 → 同視窗 DOM + OCR → MacBERT → 來源別校準候選 → 證據融合／Unknown → 人工複核。
離線回饋支線：複核資料 → 帳號／成對案例隔離 → 批次微調 → validation 選模 → calibration 校準 → test 評估 → 人工決定部署。
影片展示：正常公開頁面、仿冒網域阻擋、圖片輸入、來源分歧、複核頁面，並說明原始分數與校準分數的差異。
未完成真實資料驗證時，只寫「已實作評估與校準流程」，不要寫「已提高準確率」或填入合成案例指標。

方法參考：
- https://huggingface.co/docs/transformers/training
- https://scikit-learn.org/stable/modules/calibration.html

## 本次工程驗證紀錄（2026-09-26）

- 執行 65 個測試：64 通過，1 個舊 Streamlit 選用測試略過。
- Chromium 本機頁面驗證：隱藏、畫面外與遮擋文字不進 viewport DOM。
- 公開 example.com 實際擷取成功，text_scope=viewport、viewport_stable=true。
- 小型隨機 BERT 驗證訓練、另存、重載、來源別校準及評估指令，測試資料只存在暫存目錄；不是詐騙準確率實驗。
- 現有 Dcard 截圖重跑成功，原始 Fraud 分數約 52.34%，仍偏 Fraud。沒有強制改為 Normal。
- 尚未完成：有代表性的真實成對案例集、真實資料微調後改善驗證、正式獨立測試及帳號層級真實標籤驗證。
