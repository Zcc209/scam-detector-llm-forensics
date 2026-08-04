# **防詐分析系統** 

**2026-06-15** 

01 

**EXECUTIVE SUMMARY** 

### **目前進度** 

###### **已完成** 

- **已完成 進行中 待決策** • 圖片輸入測試流程 • URL 風險權重校準 • 風險等級門檻 • YoloV8 物件偵測 • 輸出格式整理 / 美化 • 網域特徵分析如何進行 • PaddleOCR 文字辨識 • Demo 前端製作 • SerpApi 分析 

- • LLM Api 產出 json 報告 

02 

**PROJECT OBJECTIVE** 

### **專題目標** 

###### **使用者痛點** 

- 收到可疑網址或截圖時不知道是否 安全 

- 看不出假登入頁、假投資頁或假客 服頁 

- 圖片中的文字與畫面線索難以快速 判斷 

###### **系統任務** 

- 將網址轉成網頁畫面 

- • 辨識畫面物件與文字 

- 分析 URL 本身的可疑特徵 

- • 整合多來源證據並給出風險等級 

###### **報告輸出** 

- 風險等級 

- • 可疑證據 

- • 風險原因 

- • 建議處置 

- • 文字報告 

03 













<!-- Start of picture text -->
.<br>—<br>Pier HS<br><!-- End of picture text -->



<!-- Start of picture text -->
€)<br>YOLOv8<br>a -B|<br><!-- End of picture text -->



<!-- Start of picture text -->
Y<br>tae aw aw<br>REE<br><!-- End of picture text -->



<!-- Start of picture text -->
€) .<br>ha a YOLOv8 Y —<br>a -B|<br>BRLS RISK tae aw aw Pier HS<br>e > REE<br>OCR<br>LE al<br>— PaddleOCR<br>we ion MFP aa<br>aywrig a<br>ca QQ () mase<br>‘iss PoE fi<br>rts SerpApi GF:<br>ai : Qu wees<br>LLM API<br>Ye Y BEER OQ mem<br>it URL<br>BS<br>Hr<br><!-- End of picture text -->



<!-- Start of picture text -->
ha<br>BRLS<br><!-- End of picture text -->



<!-- Start of picture text -->
‘iss<br>rts<br><!-- End of picture text -->



<!-- Start of picture text -->
() mase<br><!-- End of picture text -->



<!-- Start of picture text -->
Qu wees<br><!-- End of picture text -->



<!-- Start of picture text -->
Y<br>URL<br>BS<br>Hr<br><!-- End of picture text -->



**DELIVERABLES** 

### **目前已完成** 

###### **資料與輸入** 

圖片上傳測試、 URL 輸入設計、 Playwright 截圖流程 

###### **決策與報告** 

特徵融合、風險評估、 LLM 分析報告 

###### **模型分析** 

YoloV8 推論、 PaddleOCR 辨識、 SerpApi 分析 **Demo 準備** 測試圖片準備、高風險案例測試 

**目前狀態：可執行骨架完成，資料集與測試案例決定準確度** 

05 

**INPUT LAYER** 

### **輸入與資料取得層** 

###### **圖片上傳** 

- LINE 訊息截圖 

- 假投資廣告 

###### **統一圖片證據** 

- 假客服頁面 

- 假登入頁截圖 

- URL 先截圖 

- 保留原始 URI 

- 截圖與上傳圖片共用分析流程 

- 後續交給 YOLOv8 與 OCR 

###### **URL 輸入** 

- 可疑連結 

- 假購物網站 

- 釣魚登入頁 

- 高報酬投資網站 

06 

**IMPLEMENTED PIPELINE** 

## **程式流程與輸出格式** 



<!-- Start of picture text -->
01 讀取輸入 02 影像取得 03 多模組分析 04 LLM 評估 05 結果輸出<br>接收圖片或URL， URL 由Playwright  YOLOv8、 把結構化證據交給 輸出risk_level、<br>建立本次分析任務。 轉成網頁截圖；圖片 PaddleOCR、URL /  LLM 生成風險結論 reason、<br>。<br>直接使用原始檔。 SerpApi 產生證據。 reported_text、<br>status。<br><!-- End of picture text -->

07 

###### **IMAGE ANALYSIS** 

## **圖片分析模組：YOLOv8 與PaddleOCR** 

###### **YOLOv8 物件偵測** 

###### **PaddleOCR 文字辨識** 

目前測試結果：偵測到person。 未來可訓練/標註的類別： 

- QR Code 

- 登入框login_box 

- 付款按鈕payment_button 

- 品牌Logo brand_logo 

- 假客服customer_service 

目前測試結果：擷取94 字。 可疑文字類型： 

- 查看連結、取得內容 

- 促銷中、優惠、限時 

- 陌生短網址 

- 誘導加入或點擊 

- 可能與詐騙話術相關的描述 

08 

###### **URL FEATURE ANALYSIS** 

### **URL 分析：不能只看截圖，網址本身也要評分** 

**字串特徵** URL 長度、特殊符號、 query 長度 **網域特徵** 子網域數量、 IP 型網址、可疑 TLD **可疑字詞** login 、 verify 、 secure 、 gift 、 bonus 、 wallet **協定檢查** 是否 HTTPS ，是否格式正確 **輸出： URL 分數、觸發規則、可疑原因清單** 

09 

**DECISION LAYER** 

#### **決策層** 



<!-- Start of picture text -->
1 影像證據 2 網址證據 3 特徵整合 4 LLM 報告<br>YOLOv8：物件、人物、 原始URI 整理成結構化欄位 產生risk_level<br>登入框、付款元素 URL 特徵規則 計算風險分數 生成原因說明<br>PaddleOCR：可疑文字 SerpApi 外部搜尋線索 保留觸發原因 輸出建議處置<br>與短網址<br><!-- End of picture text -->

**預設風險等級門檻 低風險< 40 中風險40–69 高風險** ≥ **70** 

目前純粹設置任意值先測試，後續這裡會用dataset找出比較好的值 

10 

**REPORT OUTPUT** 

### **輸出層** 

##### **報告格式** 

- 整體風險等級 

##### **輸出範例** 

   - 風險等級：高 

- 風險分數 ( 可選 ) 

- 可疑證據 

- 系統分析結果 

   - 原因：誘導點擊連結、短網址 rebrand.ly/yjchen 、促銷 / 內容引導語句 

- 建議處置 

- 建議：不要輸入帳號密碼，請改由官方 管道確認。 

11 

**RISKS & MITIGATION** 

#### **主要風險與解法** 



<!-- Start of picture text -->
! 資料不足 ✓ 對應解法<br>YOLO/測試案例少，容易造成結果不穩<br>。 先建立展示用案例庫，類別不求多，但要穩定。<br>! OCR 誤判 ✓ 對應解法<br>截圖解析度、特殊字體會影響辨識。 加入前處理與文字清理，保留原始文字供檢查。<br>! API 不穩 ✓ 對應解法<br>LLM 或SerpApi 可能遇到額度、網路或<br>金鑰問題。 保留規則式fallback，Demo 前先快取測試結果。<br>! 評分門檻 ✓ 對應解法<br>高/中/低風險如果不直覺，User會覺得可<br>信度不足。 用10–20 個案例回推權重，並附上證據說明。<br><!-- End of picture text -->

