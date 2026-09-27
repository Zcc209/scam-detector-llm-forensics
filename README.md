# 詐騙帳號偵測系統－整合影像鑑識與 LLM 技術

輸入**社群帳號／網頁網址**，或上傳**截圖**，系統會實際開啟頁面與連結，結合文字模型、本機 LLM 與影像鑑識，給出四種結論之一，並附上判斷依據：

**高度疑似詐騙**｜**疑似詐騙**｜**未發現明顯詐騙跡象**｜**需要人工查證**

![系統流程](docs/pipeline_flow.png)

| 模組 | 技術 | 做什麼 |
|---|---|---|
| 網域檢查 | 165 涉詐網域清單（7.5 萬筆）、仿冒平台規則 | 命中就不開啟網頁，直接判定高風險 |
| 網頁擷取 | Playwright Chromium | 截圖與同一畫面的網頁文字；每一步跳轉前都先檢查網域 |
| 文字整理 | 介面文字過濾、EasyOCR、OCR↔網頁文字對齊 | 只把「圖片裡才有的字」送去判斷，OCR 讀錯的字用網頁文字更正 |
| 文字分類 | MacBERT（社群貼文微調） | 網頁文字、圖片內文字各自評分 |
| LLM 證據抽取 | 本機 Ollama + Qwen2.5-7B | 判斷「招攬讀者」還是「討論／分享」，列出詐騙手法並附逐字引用 |
| 影像鑑識 | pHash／dHash、ELA、EXIF | 比對 948 張官方確認的詐騙圖片，檢查品牌與網址是否相符 |
| 連結跳轉追蹤 | 受保護的瀏覽器實際點開 | 追到最終落地頁，檢查短網址、165 清單、冒用品牌、LINE 群組、一頁式購物 |
| 證據融合與拒答 | 有符號限制的 logistic 堆疊、Mondrian conformal | 合成融合分數；證據不足以分辨時回答「需要人工查證」 |

**實測成效**（數位發展部「網路詐騙通報查詢網」主管機關判定的測試案例 178 筆，與訓練資料依帳號分組、不重疊）：Precision 81.5%、Recall 76.8%、F1 79.1%、FPR 11.0%、AUC 0.92。加上拒答機制後，22% 的案例交給人工，其餘準確率 89%。原始 MacBERT 在同一份測試集上 Recall 只有 1.4%、AUC 0.47。完整數字見 [實驗結果](docs/experiment_results.md)。

更多說明：[系統運作說明與版本比較](docs/system_walkthrough.md)（輸入一個網址後的每一步、網頁每個區塊的意思）。

---

## 在電腦安裝與執行

以下以 **Windows 10／11 + PowerShell** 為例。macOS／Linux 的差異寫在每一步的備註。

### 需要準備

| 項目 | 說明 |
|---|---|
| Python 3.12 | https://www.python.org/downloads/ （安裝時勾選「Add python.exe to PATH」） |
| Git | https://git-scm.com/downloads |
| 硬碟空間 | 約 6 GB（Python 套件 3 GB、模型 0.4 GB、LLM 4.7 GB） |
| 顯示卡（選用） | NVIDIA 顯示卡可加速；**沒有顯示卡也能執行**，只是比較慢 |
| 網路 | 第一次執行會下載 OCR 模型；分析網址時需要連上該網站 |

### 1. 下載程式

```powershell
git clone https://github.com/Zcc209/Antifraud.git
cd Antifraud
```

### 2. 建立虛擬環境並安裝套件

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

如果出現「因為這個系統上已停用指令碼執行」，先執行 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`，再重新啟用虛擬環境。macOS／Linux 改用 `python3.12 -m venv .venv` 與 `source .venv/bin/activate`。

接著安裝 PyTorch，**二選一**：

```powershell
# 有 NVIDIA 顯示卡
python -m pip install torch==2.3.1 torchvision==0.18.1 --index-url https://download.pytorch.org/whl/cu121
# 沒有顯示卡（CPU 版）
python -m pip install torch==2.3.1 torchvision==0.18.1 --index-url https://download.pytorch.org/whl/cpu
```

再安裝其他套件和瀏覽器：

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
```

### 3. 放入 MacBERT 模型

模型檔太大（約 380 MB），不放在 GitHub。請從雲端下載 [macbert_social.zip](https://drive.google.com/file/d/1NgAbpjChd6UDuxgK23RqRxmHYsOynJqP/view?usp=drive_link)，解壓縮到專案的 `models/` 資料夾，會得到 `models/macbert_social/`。

解壓縮後的資料夾結構：

```text
Antifraud/
  models/
    macbert_social/
      config.json
      model.safetensors
      tokenizer.json、tokenizer_config.json、vocab.txt、special_tokens_map.json
    fusion_model_social.json      ← 已在 GitHub 上，不用另外下載
    ood_reference_social.json     ← 已在 GitHub 上
  web_server.py
```

### 4. 安裝本機 LLM（選用，但建議）

1. 到 https://ollama.com/download 下載安裝 Ollama。
2. 下載模型（約 4.7 GB）：

   ```powershell
   ollama pull qwen2.5:7b
   ```

3. Ollama 安裝後會在背景執行（網址 `http://127.0.0.1:11434`）。

**沒有裝 Ollama 也能用**：網頁會顯示「本次沒有取得 LLM 分析」，其他證據照常判斷；融合模型訓練時已模擬過 LLM 缺席的情況。

### 5. 啟動網站

```powershell
python web_server.py
```

用瀏覽器開啟 **http://127.0.0.1:8765**，輸入網址或上傳截圖即可。

- 第一次分析會下載 EasyOCR 的文字辨識模型（約 100 MB），需要多等一兩分鐘。
- 每次分析大約 10 秒～3 分鐘，視網頁與連結數量而定。
- 換連接埠：`python web_server.py --port 8766`。
- 網站只接受本機連線（127.0.0.1／localhost），不適合直接開放到公開網路。

### 6. demo 前的自我檢查

```powershell
python -m unittest discover -s tests
python scenario_check.py
```

`scenario_check.py` 會實際跑 10 個情境：5 個網址（官方帳號、165 涉詐網站、仿冒網址、政府網站、不存在的網域），以及 5 張 `data/demo/` 裡的示範截圖。最後一行應為 `failures: 0`。沒裝 Ollama 時加 `--no-llm`。

---

## 常見問題

| 狀況 | 解法 |
|---|---|
| `MacBERT model missing` | 沒有放模型，或資料夾放錯層。確認 `models/macbert_social/config.json` 存在 |
| 網頁顯示「頁面需要登入才能查看」 | Instagram／Facebook 對未登入的瀏覽有限制。請改用截圖模式上傳 |
| 「本次沒有取得 LLM 分析」 | Ollama 沒有啟動，或沒有執行 `ollama pull qwen2.5:7b` |
| 顯示卡記憶體不足 | EasyOCR 在顯示卡剩餘記憶體少於 1.5 GB 時會自動改用 CPU，不需處理 |
| `playwright` 找不到瀏覽器 | 重新執行 `python -m playwright install chromium` |
| 開啟網址出現 403 | 請用 `http://127.0.0.1:8765` 或 `http://localhost:8765` 開啟，不要用電腦的區域網路 IP |

---

## 命令列用法

```powershell
python run_pipeline.py --url "https://www.instagram.com/帳號/"      # 完整分析網址
python run_pipeline.py --image "C:\path\to\screenshot.png"          # 分析截圖
python run_pipeline.py --url "https://example.com" --domain-only    # 只檢查網域
python run_pipeline.py --url "https://example.com" --explain        # 另外產生熱點圖
```

其他選項：`--no-llm`（不用 LLM）、`--no-follow`（不追蹤連結）、`--output-dir`（指定輸出資料夾）。結果存在 `artifacts/`，包括 `report.json`、截圖與執行紀錄。

## 重新訓練（選用，需要 NVIDIA 顯示卡與 Ollama）

資料集已在 `data/`（官方判定案例、PTT 困難負樣本、圖片雜湊庫、165 清單）。微調以原始版 MacBERT 權重為起點，需放在 `anti_fraud_E3_macbert/`：

```powershell
python build_dataset.py
python score_dataset.py base
python finetune_macbert.py --batch 16 --max-length 256
python score_dataset.py rescore --model-path models/macbert_social
python score_dataset.py llm
python run_experiments.py
```

最後一步會更新 `models/fusion_model*.json`、`models/ood_reference*.json` 和 `docs/experiment_results.md`，網站上的「系統實測成效」也會跟著更新。

## 專案結構

| 路徑 | 內容 |
|---|---|
| `web_server.py`、`ui/` | 網站後端與前端（HTML／CSS／JavaScript） |
| `run_pipeline.py` | 主流程 |
| `domain_check.py`、`web_capture.py` | 網域檢查、網頁擷取 |
| `content_filter.py`、`alignment.py`、`integrated_app.py`、`inference.py` | 文字整理、OCR、MacBERT |
| `account_signals.py`、`llm_evidence.py`、`image_forensics.py`、`link_tracer.py`、`ood.py` | 各項證據 |
| `fusion_model.py`、`conformal.py`、`risk_assessment.py` | 融合、拒答、結論 |
| `attribution.py`、`report_explanation.py` | 熱點圖與報告說明 |
| `collect_*.py`、`build_dataset.py`、`score_dataset.py`、`finetune_macbert.py`、`run_experiments.py` | 資料蒐集、訓練與評估 |
| `data/` | 165 清單、資料集、詐騙圖片雜湊庫、示範截圖 |
| `models/` | 融合模型與分布外偵測參考值（MacBERT 權重另外下載） |
| `docs/` | 說明文件、實驗結果、流程圖 |
| `tests/`、`scenario_check.py` | 單元測試與端對端自我檢查 |

## 使用限制

- 結論只針對這次看到的頁面或截圖，不代表已證實帳號身分。
- 測試資料以 Threads 貼文為主；其他平台的案例較少，成效可能不同。
- 預設使用未登入的瀏覽器，不會繞過平台的登入限制。
- 分析結果僅供參考，不能取代官方查證。遇到可疑情況請撥打 **165 反詐騙諮詢專線**。
