# 校準與敏感度實驗

## 校準不是把分數乘上一個比例

本專案新增 calibration.py。Platt 使用 sigmoid(a * d + b)，d 為平均 logits 的 Fraud − Normal 差值；以 calibration split 的真值擬合，使用 Platt 平滑目標與非負斜率。Isotonic 使用加權 pool-adjacent-violators，得到單調映射，內插且對範圍外輸入夾到端點。Isotonic 更彈性，資料少時更容易過擬合，不能假設一定比 Platt 好。

DOM 與 OCR 各自訓練映射，不混成一個來源。每帳號／網站 group 總權重為一，避免同帳號多張截圖主導擬合。所有 calibration／test 報告需有當前模型 SHA-256 和 logit 差值，舊報告需重新分析。原始模型訓練資料重疊、標籤來源與資料授權仍需人工稽核。

先選方法，再用獨立 test 比較原始／校準後的 Brier、Log loss、ECE 和可靠度分箱。不能反覆根據 test 挑方法，否則需要另留最終測試集。Brier 同時受分類能力與校準影響，不能單看下降就宣布機率可靠。

```powershell
python calibration.py --manifest data/my_labeled_cases.csv --method platt --output artifacts/platt-candidate.json
python calibration.py --manifest data/my_labeled_cases.csv --method isotonic --output artifacts/isotonic-candidate.json
```

沒有足夠標註獨立 group 時不建立映射。預設每個 split／每類至少 10 group 是防止過少資料的工程檢查，不是可靠性保證。正式所需樣本數由部署分布、每類比例與統計不確定性決定。使用平衡的正常／涉詐案例集，也不保證符合真實上線的詐騙盛行率。

輸出是候選 JSON，包含模型雜湊、來源、參數、獨立測試統計與限制，不會自動部署。檢視並審核後可明確啟用：

```powershell
python web_server.py --calibration-json artifacts/platt-candidate.json
python run_pipeline.py --image C:/path/sample.png --calibration-json artifacts/platt-candidate.json
```

保留原始 softmax 及原始分類，另存 calibrated_fraud_score 與 calibrated_normal_score。來源有候選映射時，融合使用該來源校準分數做 argmax；未校準來源維持原始分數。介面分別顯示兩組分數，不叫它「綜合帳號詐騙機率」。候選套用不代表已驗證部署；仍需確認標籤範圍、preprocessing 與來源分布相符。

**目前沒有真實標註集，所以本次沒有產生正式校準參數或校準成效。單元測試的人工數字只驗證演算法，不是實際模型準確率。**

## Leave-One-Segment-Out 原理

1. 固定模型權重、eval 模式，對原始文字計算 f(x)。
2. DOM 依非空行、OCR 依被採納文字段落切分。
3. 每次只移除第 i 段，其他文字順序不變，重新跑文字清理、tokenizer、各 chunk 與 softmax，取得 f(x_without_i)。
4. 記錄 Δ_i = f(x) − f(x_without_i)。正值表示移除後 Fraud 分數降低；負值則相反。

改變的是「實驗副本的輸入」，不是模型權重或原始報告分數。可以查找模型對哪些句子、平台導覽文字、OCR 誤辨敏感，定位誤判與設計後續對照實驗；不是詐騙事實、因果證明，也不是解釋模型全部內部推理。

## 非線性與成本

新增雙段比較：Δ_AB − Δ_A − Δ_B。非零表示這兩段在此刪除設定下的效果不加成。預設最多 6 組，優先選單段絕對效果大的組合；仍可能漏掉單段看不出、組合才有影響的情形，不能當成完整 interaction attribution。

全測 n 段至少 n+1 次 document 推論，每份長文件可能還有多個 chunks，因此不是 n+1 次底層模型呼叫。介面預設單段 budget 64，不是逾時後中斷，而是事先設定預算；現在依文件順序均勻選段，不只測前 64 段。紀錄 tested_indices、coverage_complete、推論比較次數與未測限制。

```powershell
python attribution.py --report artifacts/web_temp_output/report.json --output artifacts/research/attribution.json --limit 128 --pair-budget 6
```

重要限制：刪除可能改變句法與 token/chunk 邊界、輸入變成訓練分布外、段落太粗、漏掉未測段落、不同片段效果不可加總。新介面顯示當前來源實驗的完成次數；五階段百分比是適用步驟完成率，非剩餘秒數。圖片模式略過網域及擷取步驟。

## 為何沒有直接換成 SHAP

SHAP 的 coalition／permutation 框架可在不同上下文下分配片段貢獻，但近似方法仍需很多遮罩推論，200 段未必比單段刪除快；其分配也不是全部交互作用矩陣或因果證明。還要定義文字 masker、基線、分段與推論預算。可以作後續對照，不應只因理論完整就宣稱更快。

Integrated Gradients 也是可比較的方向：以 embedding 基線與積分步數估算 token 歸因，但依赖基線、梯度與聚合方式，不是現成的低成本正確答案。本次保留可重現刪除法、預算化組合與明確限制，沒有假稱已執行 SHAP。

參考：[scikit-learn 校準文件](https://scikit-learn.org/stable/modules/calibration.html)、[SHAP PermutationExplainer](https://shap.readthedocs.io/en/latest/generated/shap.PermutationExplainer.html)。
