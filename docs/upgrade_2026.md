# 2026 競賽版升級說明

這份文件說明競賽前新增的模組、為什麼這樣設計，以及怎麼重現實驗。數字請看自動產生的 [experiment_results.md](experiment_results.md)。

## 1. 整體架構

```
網址 ─► 網域規則 + 165 涉詐網域清單 ─► Playwright 同視窗截圖 + DOM（含 HTML 地標、<img> 位置）
                                         │
         ┌──────────────────────────────┼──────────────────────────────┐
         ▼                              ▼                              ▼
  介面文字過濾（content_filter）   OCR ↔ DOM 區域對齊（alignment）   影像鑑識（image_forensics）
  導覽列／選單／登入按鈕移除        重複／誤辨／圖片內文字／無法定位     已知詐騙圖比對、ELA、EXIF、品牌/網域不符
         │                              │                              │
         ▼                              ▼                              │
   MacBERT（帳號文字）             MacBERT（只看圖片內文字）               │
         │                              │                              │
         ├──────────── 帳號／手法訊號（account_signals） ───────────────┤
         ├──────────── 本機 LLM 證據抽取（llm_evidence, Qwen2.5-7B） ───┤
         ▼                                                             ▼
                 學習式證據融合（fusion_model：logistic stacking）
                                 │
                 Mondrian conformal prediction（conformal）─► Fraud / Normal / Unknown（有覆蓋率保證）
                                 │
                 報告：各證據貢獻、LLM 引用原文、熱點圖（逐段刪除 + Integrated Gradients）
```

## 2. 各項改進對應的問題

| 問題 | 做法 | 程式 |
|---|---|---|
| 題目寫「影像鑑識」但只有 OCR | 以數發部已確認詐騙貼文圖片建立 pHash/dHash 圖庫，比對整張圖與截圖中每個 `<img>` 區塊；ELA 誤差熱度圖；EXIF 編修軟體；畫面品牌名稱 vs 網域 | `image_forensics.py` |
| 題目寫「LLM」但沒有 LLM | 本機 Ollama + Qwen2.5-7B 當「證據抽取器」：判斷言語行為（招攬 vs 討論/新聞）、列出 13 類手法且必須逐字引用原文；原文找不到的引用自動剔除（防幻覺） | `llm_evidence.py` |
| OCR 與 DOM 衝突 → Unknown | OCR 框與 DOM 字元框逐一對齊：DOM 已有的文字不再重複評分；OCR 誤辨（Instagum）記錄更正；只把圖片內文字依所屬圖片分段送模型 | `alignment.py`、`run_pipeline.py` |
| 熱門標題、登入按鈕造成誤判 | 依 HTML 地標（nav/header/aside/menu/button）與介面詞彙過濾頁面框架文字；含聯絡方式、連結、金錢字眼的行永遠保留 | `content_filter.py` |
| 關鍵字敏感（年薪、學測…） | ① LLM 區分「討論」與「招攬」② 帳號層級特徵（粉絲數、隨機數字帳號、站外聯絡、短網址）③ PTT 困難負樣本 ④ MacBERT 社群領域微調 ⑤ CheckList 行為測試量化 | `account_signals.py`、`collect_hard_negatives.py`、`finetune_macbert.py`、`data/behavioral_tests.json` |
| Softmax 不是機率 | 以官方判定案例訓練堆疊模型並檢查 ECE/Brier；Mondrian conformal 讓 Unknown 有「每類覆蓋率 ≥ 1−α」的統計保證（若單一集合與分數方向相反則擴為 Unknown，只會讓集合變大、不破壞保證）；`adjust_prior` 換算真實盛行率 | `fusion_model.py`、`conformal.py` |
| 逐段刪除表格難讀 | 截圖上疊加熱點框、文字熱點；新增 Integrated Gradients 逐字歸因並顯示完整性誤差；表格收進「數值表格」 | `attribution.py`、`report_explanation.py`、`ui/app.js` |
| 沒有準確率數字 | 自動蒐集數發部「網路詐騙通報查詢網」中主管機關已判定的案例（詐騙 / 非詐騙），以 LINE ID、帳號、連結、文字分組切分 train/calibration/test | `collect_fraudbuster.py`、`build_dataset.py`、`score_dataset.py`、`run_experiments.py` |
| 已知詐騙素材 | 與官方已判定詐騙圖片幾乎相同（pHash、dHash 距離 ≤ 4）視同清單命中，直接列為 High 並附案例連結 | `run_pipeline.py` |
| 分布外內容 | MacBERT [CLS] 向量與訓練貼文的 Mahalanobis 距離（PCA 64 維），超過校準集 99 百分位就警告分數不可靠 | `ood.py` |
| 只看靜態內容，看不到點擊後的跳轉 | 從網頁連結（含 IG 自介連結，自動解開 l.instagram.com 轉址）與截圖 OCR 中的網址挑出最多 4 個最可疑的連結，用同一套受保護的 Playwright 實際點開，記錄每一步跳轉與最終網址；每一步先查 165 清單／仿冒規則，命中就不開啟。規則：短網址、跨 3 個以上網域、導向 LINE／Telegram 群組、165 常見頂級域名、一頁式購物（限時、貨到付款等且無公司資訊）、投資招攬落地頁，並以 MacBERT 評分落地頁文字。跳轉終點命中 165／仿冒 → 高風險；兩項中度訊號 → 疑似詐騙。**這些規則沒有標註資料可驗證，答辯時請說明是啟發式規則** | `link_tracer.py` |
| 165 涉詐網域未利用 | 7.5 萬個 165 公告網域做成本機清單，命中即攔截不造訪；修正短品牌名誤判（airlines ≠ LINE） | `domain_check.py`、`data/165_domains.tsv`、`eval_domains.py` |

## 3. 資料集

- **官方判定集**：`collect_fraudbuster.py` 讀取數發部網路詐騙通報查詢網的案件時間軸。只有寫明「內政部／數發部已經確認，這是詐騙訊息」或「經數發部確認，這並不是詐騙訊息」的案件才有標籤；「確認中」「高風險，請謹慎評估」不列入。原網址被網站遮蔽，因此每筆是「貼文文字 + 貼文圖片」。
- **困難負樣本**：`collect_hard_negatives.py` 從 PTT 的 Stock、Salary、DigiCurrency、SENIORHIGH、Boy-Girl 等板收集一般文章（推定正常，非官方判定）。測試看板（Salary、SENIORHIGH、DigiCurrency、Boy-Girl、e-shopping）訓練時完全沒見過，只用來量測誤報率，不混入官方 F1。
- **短句負樣本**：PTT 文章標題（例如「年薪100萬該買車嗎」）另存為短句負樣本，與原文章同組同 split。官方資料的兩類幾乎都是廣告／招攬文，沒有這類日常短句，微調模型會對「銀行、年薪、中獎」過度敏感。
- **行為測試**：`data/behavioral_tests.json` 是人工撰寫的合成句，只測穩健性（關鍵字陷阱、防詐宣導、介面文字干擾不變性）。
- **分組**：共用 LINE ID、@帳號、連結或前 60 字相同的案例視為同一組，組不跨 split；每組總權重為 1，再做類別平衡。

## 4. 重現步驟

```powershell
# 1) 蒐集資料（已附在 data/，重跑才需要）
python collect_fraudbuster.py --pages 110 --images            # 詐騙判定案例 + 圖片
python collect_fraudbuster.py --scan-normals 400 --images     # 掃描「非詐騙」判定案例
python collect_hard_negatives.py --per-board 25
python build_dataset.py

# 2) 抽取證據（GPU 約 30–60 分鐘；LLM 階段需要 ollama serve）
python score_dataset.py base
python score_dataset.py llm

# 3) 社群領域微調（只用 train split），再用微調模型重新評分文字
python finetune_macbert.py
python score_dataset.py rescore --model-path models/macbert_social

# 4) 訓練融合模型、conformal、全部消融實驗
python run_experiments.py              # 產生 models/fusion_model*.json、docs/experiment_results.md
python eval_domains.py                 # 網域模組離線評估
```

## 5. 組員的電腦需要安裝什麼？

| 項目 | 必要？ | 沒有的話 |
|---|---|---|
| 原本的 requirements + MacBERT 權重 | 必要 | — |
| CUDA 版 PyTorch | 選用 | 用 CPU，OCR 較慢（每張截圖約 20–60 秒），結果相同 |
| Ollama + `ollama pull qwen2.5:7b`（約 4.7 GB） | 選用 | 報告顯示「LLM 未啟動」，融合模型以「LLM 缺席」權重計算（訓練時做過 modality dropout） |
| `models/macbert_social/`（微調權重，約 400 MB，跟原權重一樣用雲端分享） | 選用 | 自動改用原 MacBERT + `models/fusion_model.json` |
| `models/fusion_model*.json` | 已在 repo | — |

## 6. 實驗中發現、並據此修正的問題

1. **原 MacBERT 不適用社群貼文**：在官方判定測試集 AUC 0.47（接近亂猜），因此新增社群領域微調（只用 train split）。
2. **一個 Threads 機器人帳號群佔 570 筆詐騙**：`@動物.數字` 模板帳號是同一活動，視為同一組並每組最多 10 筆，避免基準被單一簡單樣式灌水。
3. **官方詐騙貼文 56% 看起來像「個人分享」**（養號、交友誘餌，詐騙發生在私訊）。LLM 只在 14% 找到明確手法。未加限制的融合模型因此學到「看似無害 → 詐騙」，在 PTT 誤報大增。第一輪實驗後改為**符號限制的 logistic regression**（風險證據只能推向詐騙、無害證據只能推向正常），兩種版本都列在結果表中；這個設計是在看過第一輪結果後才加入，答辯時應說明。
4. **微調模型對日常短句過度敏感**：官方兩類都是廣告／招攬文，因此加入 PTT 標題作短句負樣本；行為測試關鍵字陷阱誤判仍有 25%，列為待改進。

## 7. 答辯時要誠實說明的限制

- 官方資料是「民眾通報後經主管機關判定」，不是隨機抽樣；非詐騙樣本多是被懷疑的廣告，比一般貼文更難分。
- 訓練與測試採類別平衡；實際詐騙盛行率低很多，Precision 會下降，可用 `conformal.adjust_prior` 換算。
- 通報網站遮蔽原始網址，所以官方基準評估的是貼文文字 + 圖片，不是完整的網址擷取流程；165 網域多數已停止解析，無法重播頁面。
- PTT 困難負樣本是推定正常；行為測試是合成句，只測穩健性。
- ELA、EXIF 只是人工複核線索；已知詐騙圖比對只對「重複使用素材」有效。
- 原 MacBERT 訓練資料未知，無法排除與通報資料重疊。

## 8. Demo 前的檢查與已知狀況

- **圖片中的網址**：上傳截圖時，OCR 讀到的網址一樣會做 165 清單／仿冒檢查與連結追蹤（流程中的「網域檢查」「頁面擷取」會亮起）。畫面出現品牌（例如 myship／賣貨便）但網址不是該品牌官方網域 → 直接列為高風險（冒名釣魚）。
- **OCR 讀錯官方網址**：例如把 `myship.7-11.com.tw` 讀成 `myship.T-llcom.tw`；若與官方網域只差易混淆字元（T/7、l/1、O/0），只標示「可能是 OCR 讀錯，請人工核對」，不判高風險。
- **LLM 與 MacBERT 意見不同**：融合模型依驗證資料決定比重。校準集中「MacBERT 說像詐騙、LLM 說低風險」的案例有約六成實際是詐騙（許多詐騙貼文表面像個人分享），因此系統以 MacBERT 為主，畫面上會顯示這個實測數字。LLM 提示詞已加入台灣常見話術（假賣貨便、假客服、假投資、假交友）。
- **「結論明確」的由來**：conformal prediction 用校準集算出兩條門檻（排除正常、排除詐騙），結果卡下方「這句話怎麼來的？」會顯示本次分數與門檻。
- **執行時間上限**：單頁擷取最多 90 秒、連結追蹤總共最多 150 秒，避免網站卡住整個分析。
- **筆電過熱**：長時間滿載（微調 MacBERT、批次跑 LLM）曾讓電腦兩次直接斷電（Windows 事件 Kernel-Power 41、BugcheckCode 0）。`finetune_macbert.py` 與 `score_dataset.py llm` 已加入溫度保護：GPU ≥ 80°C 會暫停降溫。一般 demo 的單次分析是短時間負載，不受影響；批次訓練請使用原廠充電器、放在硬桌面或散熱墊上。
- **demo 前自我檢查**：`python scenario_check.py` 會跑 10 個固定情境（正常 IG、165 網域、仿冒網址、政府網站、不存在的網址、假賣貨便截圖、真賣貨便截圖、含 165 網址的截圖、一般聊天、空白圖），列出每個結果與耗時。
