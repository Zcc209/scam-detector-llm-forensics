const $ = id => document.getElementById(id);
let report = null, mode = 'url', target = '', jobId = '';
const el = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; };
const pct = p => !Number.isFinite(p) ? '—' : p < 0.01 ? '<1%' : p > 0.99 ? '>99%' : (p * 100).toFixed(1) + '%';

const SIGNALS = {off_platform_contact:'引導到站外聯絡', short_link:'使用短網址', guaranteed_return:'保證獲利／高報酬', investment_lure:'投資招攬用語',
  crypto_or_payment:'匯款或虛擬貨幣', urgency:'催促、限時用語', free_giveaway:'免費贈送／中獎', job_lure:'輕鬆高薪兼職', impersonation_claim:'提到官方機構或客服',
  verified_badge:'有平台驗證標章', throwaway_profile:'粉絲很少的新帳號', large_audience:'粉絲數很多', random_digit_handle:'帳號名稱是隨機數字', simplified_chinese:'大量簡體字'};
const ACTS = {solicitation:'招攬讀者採取行動', advertisement:'一般商品廣告', discussion:'討論／提問', news_or_info:'新聞／宣導', personal_share:'個人生活分享',
  ui_or_navigation:'網站介面文字', other:'其他'};
const TACTICS = {guaranteed_return:'保證獲利', off_platform_contact:'引導站外私聊', investment_group:'投資群組／老師帶單', impersonation:'冒充名人或機構',
  urgency:'製造急迫感', upfront_payment:'要求先付費', crypto_transfer:'匯款／虛擬貨幣指示', job_lure:'高薪兼職', romance_lure:'感情誘騙',
  prize_or_giveaway:'中獎／免費領取', account_phishing:'要求登入或驗證帳號', threat_or_extortion:'威脅勒索', charity_request:'可疑募款'};
const UNUSABLE = {unreachable:'網址無法連線：網域不存在、已下架，或已被停止解析（165 公告的涉詐網站常被停止解析）。', login_wall:'頁面需要登入才能查看（Instagram、Facebook 常見），請改用圖片模式上傳截圖。', load_error:'頁面無法載入或已被移除。',
  http_error:'網站回應錯誤，無法取得內容。', obstructing_overlay:'畫面被彈出視窗遮住，無法取得有效內容。', empty_page:'頁面沒有任何內容。', screenshot_missing:'沒有成功截圖。'};
const ERRORS = [[/whitespace|control characters|Invalid hostname|Invalid port|Only HTTP|Invalid URL/i, '網址格式不正確，請輸入完整網址，例如 https://www.instagram.com/帳號名稱。'],
  [/Non-public network|credentials/i, '這個網址指向內部網路或含有帳號密碼，基於安全考量不會開啟。'],
  [/timed? ?out|逾時/i, '分析時間過長，已停止。請稍後再試一次。'], [/Queue full/i, '目前分析的人數較多，請稍後再試。'],
  [/Upload limit|10 MB/i, '圖片不得超過 10 MB。'], [/cannot identify image|Invalid base64|Incorrect padding/i, '無法讀取這張圖片，請改用 PNG 或 JPG 檔。'],
  [/MacBERT model missing|Input image does not exist/i, '系統模型檔案不完整，請聯絡管理員。']];
// Messages the site itself wrote in Chinese pass through; English exceptions from the pipeline are translated.
const friendlyError = message => (ERRORS.find(([pattern]) => pattern.test(message || '')) || [null, /[一-鿿]/.test(message || '') ? message : '分析時發生錯誤，請稍後再試。'])[1];
const SEVERITY = {high: 0, medium: 1, low: 2};
const worstFlag = link => [...(link.flags || [])].sort((a, b) => SEVERITY[a.level] - SEVERITY[b.level])[0];
const RULES = ['known_scam_image', 'domain_rule', 'redirect_rule', 'screenshot_domain', 'brand_impersonation'];
const LABEL = {text_logit: ['文字內容像詐騙文案', '文字內容不像詐騙文案'], ocr_logit: ['圖片裡的文字像詐騙文案', '圖片裡的文字不像詐騙文案'],
  llm_solicitation: ['LLM 判斷在招攬讀者', 'LLM 判斷在招攬讀者'], llm_benign_act: ['LLM 判斷為討論或分享', 'LLM 判斷為討論或分享'],
  llm_tactic_count: ['LLM 找到詐騙手法', 'LLM 找到詐騙手法'], llm_risk: ['LLM 評估的證據風險', 'LLM 評估的證據風險'],
  llm_addresses_reader: ['LLM 判斷直接要求讀者行動', 'LLM 判斷直接要求讀者行動'],
  image_known_scam_match: ['與已確認的詐騙圖片相似', '與已確認的詐騙圖片相似'], image_brand_mismatch: ['品牌名稱與網址不符', '品牌名稱與網址不符']};

/* ---------- loading & progress ---------- */
function activity(state, title, message) {
  $('busy-box').className = 'busy-box ' + state;
  $('busy-title').textContent = title;
  $('status').textContent = message;
  $('activity-tag').textContent = ({idle: '準備就緒', running: '分析中', complete: '流程完成', warning: '需要確認'})[state];
}
function timeline(progress) {
  const labels = {waiting: '等待中', running: '處理中', done: '已完成', skipped: '不適用', warning: '需確認'};
  const steps = progress?.steps || Array.from({length: 5}, () => ({state: 'waiting'}));
  const applicable = steps.filter(step => step.state !== 'skipped');
  const percent = applicable.length ? Math.floor(100 * applicable.filter(step => step.state === 'done').length / applicable.length) : 0;
  $('progress-percent').textContent = percent + '%';
  $('progress-fill').style.width = percent + '%';
  $('progress-bar').setAttribute('aria-valuenow', String(percent));
  const experiment = progress?.experiment;
  $('experiment-progress').hidden = !experiment;
  if (experiment) $('experiment-progress').textContent = `熱點圖計算：${experiment.completed} / ${experiment.total}`;
  document.querySelectorAll('[data-step]').forEach((node, i) => {
    const state = steps[i]?.state || 'waiting';
    node.dataset.state = state;
    $('stage-' + i).textContent = labels[state] || labels.waiting;
  });
}

/* ---------- result card ---------- */
function reasonLabel(item) { return LABEL[item.feature]?.[item.contribution > 0 ? 0 : 1] || SIGNALS[item.feature] || item.label; }
function contributions(data, skipImage) {
  return (data.content_analysis?.contributions || []).filter(x => !x.feature.endsWith('_missing') && !(skipImage && x.feature === 'image_known_scam_match'));
}
function verdictOf(data) {
  const a = data.assessment || {}, c = data.content_analysis || {}, d = data.domain_analysis || {};
  const domainReason = d.listed_165 ? `網址列在 165 涉詐網站公告中（民國 ${d.listed_165} 起），系統不會開啟這個網址。`
    : d.possible_impersonated_platform ? `網址疑似仿冒 ${d.possible_impersonated_platform}，系統不會開啟這個網址。` : '網址觸發高風險規則。';
  if (data.status === 'error') return {cls: 'unknown', title: '無法完成分析', reason: friendlyError(data.error)};
  if (data.status === 'blocked') return {cls: 'high', title: '高風險網址，已攔截', reason: domainReason};
  if (data.status === 'unusable') return {cls: 'unknown', title: '無法取得頁面內容', reason: UNUSABLE[data.browser_capture?.unusable_reason] || '頁面內容無法使用。'};
  const brand = (data.image_forensics?.brand_domain_mismatch || [])[0];
  if (a.risk_level === 'High') return {cls: 'high', title: '高度疑似詐騙',
    reason: a.basis === 'known_scam_image' ? '畫面中的圖片與主管機關已確認的詐騙素材幾乎相同。'
      : a.basis === 'redirect_rule' ? `追蹤連結時發現：${worstFlag((data.link_trace?.links || []).find(l => l.risk === 'high') || {})?.evidence || '網址是 165 涉詐網站、仿冒網址或冒用品牌的網域'}。`
      : a.basis === 'screenshot_domain' ? '截圖中的網址列於 165 涉詐網站公告，或疑似仿冒知名平台。'
      : a.basis === 'brand_impersonation' && brand ? `畫面出現「${brand.brand}」，但網址 ${brand.page_domain} 不是官方網域，疑似冒名的釣魚網站。` : domainReason};
  if (a.basis === 'too_little_text') return {cls: 'unknown', title: '需要人工查證',
    reason: `截圖中只有 ${a.text_characters ?? '很少'} 個字，也沒有網址、詐騙手法或帳號特徵等具體證據。詐騙誘餌貼文和一般聊天在這麼短的文字上看起來一樣，系統不下結論（文字模型分數 ${pct(c.fraud_score)} 僅供參考）。`};
  if (a.basis === 'redirect_heuristic') return {cls: 'medium', title: '疑似詐騙', reason: '連結點開後出現可疑的跳轉或落地頁，詳見「連結跳轉追蹤」。建議不要點擊或匯款。'};
  const items = contributions(data);
  const ups = items.filter(x => x.contribution > 0).slice(0, 2).map(reasonLabel);
  const downs = items.filter(x => x.contribution < 0).slice(0, 2).map(reasonLabel);
  if (a.risk_level === 'Medium') return {cls: 'medium', title: '疑似詐騙', reason: (ups.length ? `主要原因：${ups.join('、')}。` : '') + '建議不要私訊或匯款，並向 165 查證。'};
  if (a.risk_level === 'Low') return {cls: 'low', title: '未發現明顯詐騙跡象', reason: downs.length ? `主要原因：${downs.join('、')}。` : '各項證據都偏向正常內容。'};
  const ocrLines = (data.evidence?.screenshot_ocr?.texts || []).length;
  if (!data.browser_capture && !ocrLines) return {cls: 'unknown', title: '需要人工查證',
    reason: '截圖中沒有辨識到任何文字。請確認上傳的是清楚、未過度壓縮的貼文或聊天截圖。'};
  return {cls: 'unknown', title: '需要人工查證',
    reason: (c.prediction_set || []).length === 2 ? '證據同時符合詐騙與正常的特徵。這是安全設計：不確定時不硬判，交給人工查證，避免冤枉正常帳號或放過詐騙。' : '可用的證據不足，系統不硬判，請人工查證。'};
}
const LEVEL_ZH = {High: '高度疑似詐騙', Medium: '疑似詐騙', Low: '未發現明顯詐騙跡象', Unknown: '需要人工查證'};
const BASIS_ZH = {redirect_rule: '連結追蹤命中高風險規則', redirect_heuristic: '連結跳轉出現多項可疑特徵', screenshot_domain: '截圖中的網址是已知涉詐或仿冒網域',
  brand_impersonation: '畫面品牌與網址不符', known_scam_image: '圖片與主管機關確認的詐騙素材相同', too_little_text: '截圖文字太短，又沒有任何具體證據'};
function basisNote(data) {
  const a = data.assessment || {};
  if (data.status === 'blocked') return '這個網址命中已知涉詐網域（165）或品牌仿冒網域，系統在第一層就停止，不開啟網頁，也不執行內容模型。';
  if (!a.model_risk_level || a.model_risk_level === a.risk_level || !BASIS_ZH[a.basis]) return '';
  if (a.basis === 'too_little_text') return `模型的基礎結論是「${LEVEL_ZH[a.model_risk_level]}」；但${BASIS_ZH[a.basis]}，為避免硬判而誤判，改為「需要人工查證」。`;
  return `模型（融合＋拒答）的基礎結論是「${LEVEL_ZH[a.model_risk_level]}」；因為${BASIS_ZH[a.basis]}，依安全優先原則提高為「${LEVEL_ZH[a.risk_level]}」。硬證據只會提高風險，不會把結論改得更安全。`;
}
function renderResult(data) {
  const v = verdictOf(data), c = data.content_analysis || {}, a = data.assessment || {};
  $('result-card').className = 'result ' + v.cls;
  $('risk').textContent = v.title;
  $('reason').textContent = v.reason;
  $('target').textContent = target;
  const note = basisNote(data);
  $('basis-note').hidden = !note; $('basis-note').textContent = note;
  const score = c.fraud_score;
  const ruleHit = data.status === 'blocked' || (a.risk_level === 'High' && RULES.includes(a.basis));
  $('scale').hidden = $('scale-labels').hidden = ruleHit || !Number.isFinite(score);
  $('score-label').textContent = ruleHit ? '判定方式' : '詐騙可能性';
  if (ruleHit) {
    $('score-value').textContent = '規則命中';
    $('certainty').textContent = a.basis === 'known_scam_image'
      ? `與官方判定的詐騙案例圖片相同，直接列為高風險（僅依文字的融合分數為 ${pct(score)}，不作為依據）。`
      : a.basis === 'redirect_rule' ? `連結追蹤命中高風險規則（165 清單、仿冒網址或冒用品牌），直接列為高風險（內容本身的融合分數為 ${pct(score)}）。`
      : a.basis === 'brand_impersonation' ? `品牌名稱與網址不符屬於冒名釣魚的典型特徵，直接列為高風險（內容本身的融合分數為 ${pct(score)}）。`
      : '命中 165 涉詐清單或仿冒網址規則，直接列為高風險。';
    $('how').hidden = true;
    return;
  }
  if (a.basis === 'too_little_text') { $('scale').hidden = $('scale-labels').hidden = true; $('score-label').textContent = '判定方式'; $('score-value').textContent = '證據不足';
    $('certainty').textContent = '文字太短且沒有具體證據，系統不下結論。'; $('how').hidden = true; return; }
  $('score-value').textContent = Number.isFinite(score) ? pct(score) : '—';
  $('score-marker').style.left = (Number.isFinite(score) ? score * 100 : 0) + '%';
  const alpha = c.alpha ? Math.round((1 - c.alpha) * 100) : 90, set = c.prediction_set;
  $('certainty').textContent = !Number.isFinite(score) ? '本次沒有足夠內容計算分數。'
    : set?.length === 1 ? `結論明確：在 ${alpha}% 信心水準下只剩「${set[0] === 'Fraud' ? '詐騙' : '正常'}」一種可能。`
    : `在 ${alpha}% 信心水準下，詐騙與正常都無法排除，建議人工查證。`;
  const q = c.conformal || {};
  $('how').hidden = !(Number.isFinite(score) && Number.isFinite(q.q_normal) && Number.isFinite(q.q_fraud));
  if (!$('how').hidden) {
    const excludeNormal = q.q_normal * 100, excludeFraud = (1 - q.q_fraud) * 100, p = score * 100;
    $('how-text').replaceChildren(
      el('p', `① 門檻不是人訂的：系統用 ${q.n_fraud + q.n_normal} 筆沒有參與訓練的官方判定案例（${q.n_fraud} 筆詐騙、${q.n_normal} 筆非詐騙）計算出兩條線：`),
      el('p', `　・融合分數高於 ${excludeNormal.toFixed(1)}% → 排除「正常」　・融合分數低於 ${excludeFraud.toFixed(1)}% → 排除「詐騙」`),
      el('p', `② 這次分數是 ${p.toFixed(1)}%，` + (p > excludeNormal && p >= excludeFraud ? '高於排除「正常」的線，所以只剩「詐騙」。'
        : p < excludeFraud && p <= excludeNormal ? '低於排除「詐騙」的線，所以只剩「正常」。' : '落在兩條線之間，兩種結論都無法排除，所以交給人工查證。')),
      el('p', `③ ${alpha}% 信心水準的意思：照這個規則，真正的詐騙帳號約每 10 個最多 1 個會被誤排除「詐騙」，真正的正常帳號也一樣。這是方法（conformal prediction）在統計上保證的，前提是新案例和校準資料性質相近。`),
      el('p', '④「需要人工查證」是刻意的安全設計，不是系統故障：證據不足或互相矛盾時不硬判，避免把正常帳號冤枉成詐騙，也避免把詐騙誤放為正常。'));
  }
}

/* ---------- reasons ---------- */
function reasonDetail(item, data) {
  const e = data.evidence || {}, llm = data.llm_evidence || {};
  const textModel = e.dom?.model || e.screenshot_ocr?.model || data.image_analysis;  // no DOM text -> the OCR text is the main text
  const signal = (data.account_signals?.signals || []).find(s => s.signal === item.feature);
  if (item.feature === 'text_logit') return `文字像詐騙文案的程度 ${pct(textModel?.fraud_confidence)}`;
  if (item.feature === 'ocr_logit') return `圖片文字像詐騙文案的程度 ${pct((e.screenshot_ocr?.model || data.image_analysis)?.fraud_confidence)}`;
  if (signal) return `原文：「${signal.evidence}」`;
  if (item.feature.startsWith('llm_')) return `LLM 判斷為「${ACTS[llm.speech_act] || '—'}」` + (llm.tactics?.length ? `，找到 ${llm.tactics.length} 個詐騙手法` : '');
  return '';
}
function reasonRow(dir, label, detail, level, tip) {
  const li = el('li', null, dir); li.title = tip || '';
  const text = el('div'); text.append(el('strong', label)); if (detail) text.append(el('small', detail));
  const bars = el('span', null, 'strength');
  for (let i = 0; i < 3; i++) bars.append(el('i', null, i < level ? 'on' : ''));
  bars.append(el('span', ['', '弱', '中', '強'][level]));
  li.append(el('span', dir === 'up' ? '▲' : '▼', 'arrow'), text, bars);
  return li;
}
function renderReasons(data) {
  const list = $('reasons'); list.replaceChildren();
  const a = data.assessment || {}, d = data.domain_analysis || {};
  const ruleImage = a.basis === 'known_scam_image';
  if (ruleImage) list.append(reasonRow('up', '與已確認的詐騙圖片相同', '圖片比對命中主管機關判定案例', 3));
  if (a.basis === 'domain_rule' || data.status === 'blocked') list.append(reasonRow('up', d.listed_165 ? '列於 165 涉詐網站公告' : '疑似仿冒網址', d.hostname || '', 3));
  const brand = (data.image_forensics?.brand_domain_mismatch || [])[0];
  if (a.basis === 'brand_impersonation' && brand) list.append(reasonRow('up', `冒用「${brand.brand}」名義`, `畫面出現${brand.brand}，網址卻是 ${brand.page_domain}（官方：${brand.official_domains.join('、')}）`, 3));
  for (const dm of (data.image_domains || []).filter(x => x.capture_allowed === false))
    list.append(reasonRow('up', dm.listed_165 ? '截圖中的網址列於 165 涉詐公告' : '截圖中的網址疑似仿冒', dm.hostname || dm.normalized_url, 3));
  for (const link of (data.link_trace?.links || []).filter(l => l.risk === 'high' || l.risk === 'medium'))
    list.append(reasonRow('up', link.risk === 'high' ? '連結是高風險網址' : '連結跳轉可疑', worstFlag(link)?.evidence || link.hosts.join(' → '), link.risk === 'high' ? 3 : 2));
  const shown = contributions(data, ruleImage).slice(0, 6);
  for (const item of shown) {
    const size = Math.abs(item.contribution);
    list.append(reasonRow(item.contribution > 0 ? 'up' : 'down', reasonLabel(item), reasonDetail(item, data), size >= 1.5 ? 3 : size >= 0.5 ? 2 : 1,
      `影響力 ${item.contribution.toFixed(2)}`));
  }
  // A model score the viewer can see in the charts but that barely moved the fused result still deserves a line.
  const ocr = data.evidence?.screenshot_ocr?.model;
  if (data.evidence?.dom?.model && Number.isFinite(data.content_analysis?.fraud_score) && ocr?.status === 'SUCCESS' && !shown.some(x => x.feature === 'ocr_logit')) {
    const fraudish = ocr.fraud_confidence > .5;
    list.append(reasonRow(fraudish ? 'up' : 'down', fraudish ? '圖片裡的文字有些像詐騙文案' : '圖片裡的文字不像詐騙文案',
      `像詐騙文案的程度 ${pct(ocr.fraud_confidence)}；截圖文字辨識誤差較大，融合模型給它的比重很低`, 1));
  }
  const llm = data.llm_evidence || {};
  if (llm.status === 'SUCCESS' && Number.isFinite(data.content_analysis?.fraud_score) && !shown.some(x => x.feature.startsWith('llm_'))) {
    const risky = llm.risk !== 'low' || llm.tactics?.length;
    list.append(reasonRow(risky ? 'up' : 'down', llm.tactics?.length ? `LLM 找到 ${llm.tactics.length} 個詐騙手法` : `LLM 判斷為「${ACTS[llm.speech_act] || '一般內容'}」`,
      'LLM 的判斷在融合模型中比重很低，原因見「LLM 證據抽取」', 1));
  }
  if (!list.children.length) list.append(el('li', '本次沒有明顯偏向任一方的證據。', 'empty'));
}

/* ---------- model score charts (DOM / OCR) ---------- */
function decision(model) {
  if (model?.status === 'SKIPPED_NO_IMAGE_ONLY_TEXT') return '截圖文字都已包含在網頁文字中，不重複計分';
  if (model?.status !== 'SUCCESS' || !Number.isFinite(model.fraud_confidence)) return '未取得有效分數';
  return model.fraud_confidence > .5 ? '偏向詐騙' : '偏向正常';
}
function chart(title, model) {
  const box = el('div', null, 'chart');
  box.append(el('h4', title), el('p', decision(model), 'badge'));
  if (model?.status !== 'SUCCESS' || !Number.isFinite(model.fraud_confidence)) return box;
  for (const [name, value] of [['正常', model.normal_confidence ?? 1 - model.fraud_confidence], ['詐騙', model.fraud_confidence]]) {
    const row = el('div', null, 'bar'), track = el('div', null, 'track'), fill = el('div', null, 'fill' + (name === '正常' ? ' normal' : ''));
    fill.style.width = Math.max(0, Math.min(1, value)) * 100 + '%';
    track.append(fill); row.append(el('span', name), track, el('strong', (value * 100).toFixed(1) + '%')); box.append(row);
  }
  return box;
}
function renderCharts(data) {
  const e = data.evidence || {}, ocr = e.screenshot_ocr?.model || data.image_analysis;
  $('charts').replaceChildren(...(data.browser_capture && e.dom?.model
    ? [chart('網頁文字', e.dom.model), chart('圖片裡的文字（OCR 辨識）', ocr)]
    : [chart(data.browser_capture ? '截圖文字（網頁內容持續變動，改用整張截圖辨識）' : '截圖文字（OCR 辨識）', ocr)]));
  const path = data.models?.macbert || '';
  $('text-model').textContent = path.includes('macbert_social') ? '模型：社群貼文微調版 MacBERT。' : '模型：MacBERT。';
}

/* ---------- LLM, forensics, domain ---------- */
function renderLLM(data) {
  const box = $('llm'); box.replaceChildren(); const llm = data.llm_evidence || {};
  if (llm.status !== 'SUCCESS') {
    box.append(el('p', llm.status === 'unavailable' ? '本次沒有取得 LLM 分析（LLM 服務未啟動），其他證據照常判斷。' : llm.status === 'SKIPPED_TEXT_EMPTY' ? '沒有可分析的文字。' : '本次沒有執行 LLM。', 'empty'));
    return;
  }
  box.append(el('span', ACTS[llm.speech_act] || llm.speech_act, 'speech' + (llm.speech_act === 'solicitation' ? ' risk' : '')));
  box.append(el('p', `直接要求讀者行動：${llm.addresses_reader ? '是' : '否'}　·　找到的詐騙手法：${llm.tactics.length} 個`, 'muted'));
  for (const t of llm.tactics) { const q = el('div', null, 'quote'); q.append(el('b', (TACTICS[t.tactic] || t.tactic) + '　'), document.createTextNode(`「${t.quote}」`)); box.append(q); }
  if (llm.rationale) box.append(el('p', llm.rationale));
  const e = data.evidence || {}, text = e.dom?.model || e.screenshot_ocr?.model || data.image_analysis;
  const stats = data.content_analysis?.disagreement || {};
  const llmLow = llm.risk === 'low' && !llm.tactics?.length, llmRisky = !llmLow;
  if (text?.status === 'SUCCESS' && ((text.fraud_confidence > .5 && llmLow) || (text.fraud_confidence <= .5 && llmRisky))) {
    const s = text.fraud_confidence > .5 ? stats.macbert_fraud_llm_low : stats.macbert_normal_llm_risky;
    const note = el('div', null, 'callout');
    note.append(el('strong', `LLM 與 MacBERT 看法不同（MacBERT：${pct(text.fraud_confidence)} 像詐騙）`));
    note.append(el('p', s?.n ? `系統以 MacBERT 為主，理由是實測：在沒有參與訓練的驗證資料中，遇到同樣「${text.fraud_confidence > .5 ? 'MacBERT 說像詐騙、LLM 說低風險' : 'MacBERT 說不像、LLM 說有風險'}」的 ${s.n} 筆案例，實際是詐騙的比例為 ${pct(s.fraud_rate)}。`
      : '融合模型依驗證資料決定兩者的比重；在這批資料中，許多詐騙貼文（例如先聊天養帳號）表面上看不出手法，LLM 常判為低風險。'));
    box.append(note);
  }
  if (llm.rejected_quotes?.length) box.append(el('p', `已剔除 ${llm.rejected_quotes.length} 筆原文中找不到的引用。`, 'muted'));
}
function check(list, kind, text, small, link) {
  const li = el('li', null, kind), body = el('div', text);
  if (small) body.append(el('small', small));
  if (link) { const a = el('a', link.text); a.href = link.href; a.target = '_blank'; a.rel = 'noopener noreferrer'; body.append(document.createTextNode(' '), a); }
  li.append(body); list.append(li);
}
function renderForensics(data) {
  const list = $('forensics'); list.replaceChildren(); const f = data.image_forensics || {};
  if (f.status !== 'SUCCESS') { check(list, 'info', '沒有可分析的圖片。'); return; }
  if (f.known_scam_matches?.length) for (const m of f.known_scam_matches) check(list, 'bad', '與已確認的詐騙圖片相符', m.region === 'whole' ? '整張圖片相符' : '畫面中的部分圖片相符', {text: '查看官方案例 ↗', href: m.source_url});
  else check(list, '', `已比對 ${f.index_size} 張官方確認的詐騙圖片，沒有相符`);
  if (f.brand_domain_mismatch?.length) for (const b of f.brand_domain_mismatch) check(list, 'bad', `畫面出現「${b.brand}」，但網址不是官方網域`, `官方：${b.official_domains.join('、')}；實際：${b.page_domain}`);
  else check(list, '', '品牌名稱與網址沒有矛盾');
  if (f.metadata?.editor_tag) check(list, 'bad', '圖片含有編修軟體紀錄', f.metadata.software);
  else check(list, '', '沒有圖片編修軟體紀錄');
  if (f.ela?.heatmap_path) check(list, 'info', 'ELA 誤差分析', '亮區代表壓縮誤差不一致，可能經過後製；文字邊緣也會偏亮，需人工判讀。', {text: '開啟 ↗', href: `/api/jobs/${jobId}/ela`});
}
function renderAccount(data) {
  const list = $('domain-checks'); list.replaceChildren(); const d = data.domain_analysis;
  const shots = data.image_domains || [];
  if (!d && shots.length) for (const x of shots) {
    if (x.error) check(list, 'info', `截圖中的網址 ${x.normalized_url}`, '網址格式不完整，無法檢查');
    else if (x.listed_165) check(list, 'bad', `截圖中的網址 ${x.hostname} 列於 165 涉詐網站公告（民國 ${x.listed_165} 起）`, x.context ? `原文：${x.context}` : '');
    else if (x.possible_impersonated_platform) check(list, 'bad', `截圖中的網址 ${x.hostname} 疑似仿冒 ${x.possible_impersonated_platform}`, x.context ? `原文：${x.context}` : '');
    else check(list, 'info', `截圖中的網址 ${x.hostname}：未列於 165 清單`, (x.context ? `原文：${x.context}；` : '') + '點開後的結果見「連結跳轉追蹤」');
  } else if (!d) check(list, 'info', '截圖中沒有網址，不需檢查網域');
  else if (d.listed_165) check(list, 'bad', `列於 165 涉詐網站公告（民國 ${d.listed_165} 起）`, d.hostname);
  else if (d.domain_status === 'lookalike') check(list, 'bad', `疑似仿冒 ${d.possible_impersonated_platform}`, d.hostname);
  else if (d.domain_status === 'official') check(list, '', `${d.matched_platform} 官方網域`, '網域正確不代表帳號本身可信。');
  else check(list, 'info', `一般網站：${d.hostname}`, '不在已知涉詐網域清單，也不像品牌仿冒網域。網域檢查只能抓這兩類，新出現的詐騙網站要靠內容與連結分析判斷。');
  const o = data.ood || {};
  if (o.status === 'SUCCESS') check(list, o.out_of_distribution ? 'bad' : '', o.out_of_distribution ? '內容和訓練資料差異大，文字分數參考價值較低' : '內容與訓練資料相近，模型分數可參考', '分布外偵測只提醒可信度，不會改變結論。');
  const chips = $('signals'); chips.replaceChildren();
  for (const s of data.account_signals?.signals || []) chips.append(el('span', `${SIGNALS[s.signal] || s.signal}：${s.evidence}`, 'chip ' + (s.weight > 0 ? 'risk' : 'safe')));
  if (!chips.children.length) chips.append(el('span', '沒有偵測到帳號層級的可疑特徵', 'chip'));
}
function renderAlignment(data) {
  const list = $('ocr-lines'), extra = $('align-extra'); list.replaceChildren(); extra.replaceChildren();
  const r = data.region_alignment || {}, items = data.evidence?.screenshot_ocr?.items || [];
  const kind = {};
  if (r.available) {
    for (const x of r.dom_duplicate || []) kind[x.ocr_item_index] = ['dup', '網頁已有'];
    for (const x of r.ocr_variant || []) kind[x.ocr_item_index] = ['fix', '已更正', x.dom_text];
    for (const x of [...(r.image_text || []), ...(r.unlocated_text || [])]) kind[x.ocr_item_index] = ['img', '送去判斷'];
  }
  const urlLike = /(https?:|www\.|[a-z0-9-]+\.(com|net|tw|cc|top|shop|xyz|site|vip|me|ee|ly|sbs|icu|online|store)\b)/i;
  const rows = items.map((item, i) => {
    let [cls, tag, fix] = kind[i] || (item.accepted ? ['img', '送去判斷'] : ['skip', '略過']);
    if (!item.accepted) [cls, tag] = ['skip', '略過'];
    return {cls, tag, fix, text: item.text, url: urlLike.test(item.text), conf: item.confidence};
  });
  const count = c => rows.filter(x => x.cls === c).length;
  $('align-title').textContent = '截圖上讀到的文字';
  $('align-note').textContent = r.available
    ? `系統把截圖上的字一行一行讀出來（OCR），再和網頁原本的文字比對。共 ${rows.length} 行：${count('dup')} 行網頁本來就有（已用網頁文字判斷，不重複計算）、${count('fix')} 行是 OCR 認錯（改用網頁上的正確文字）、${count('img')} 行只出現在圖片裡，這些才送去文字模型判斷${count('skip') ? `；另有 ${count('skip')} 行太模糊而略過` : ''}。`
    : `系統把截圖上的字一行一行讀出來（OCR）。共 ${rows.length} 行：${count('img')} 行清楚可用，送去文字模型判斷${count('skip') ? `；${count('skip')} 行太模糊而略過，避免錯字影響判斷` : ''}。含網址的行會另外做網域檢查與連結追蹤。`;
  const ocrRow = row => {
    const li = el('li', null, 'ocr-row ' + row.cls);
    li.append(el('span', row.tag, 'ocr-tag'));
    const body = el('span', row.text, 'ocr-text');
    if (row.fix) body.append(el('small', `網頁上的正確文字：${row.fix}`));
    if (!row.fix && row.cls === 'skip' && Number.isFinite(row.conf)) body.append(el('small', `辨識信心 ${(row.conf * 100).toFixed(0)}%`));
    li.append(body);
    if (row.url) li.append(el('span', '網址', 'ocr-url'));
    return li;
  };
  for (const row of rows.filter(x => x.cls !== 'skip').slice(0, 60)) list.append(ocrRow(row));
  const skipped = rows.filter(x => x.cls === 'skip');
  if (skipped.length) {
    const more = el('details', null, 'ocr-skipped'), inner = el('ul', null, 'ocr-lines');
    more.append(el('summary', `顯示 ${skipped.length} 行太模糊而略過的文字`), inner);
    for (const row of skipped.slice(0, 60)) inner.append(ocrRow(row));
    extra.append(more);
  }
  if (!rows.length) list.append(el('li', '截圖中沒有辨識到文字。', 'empty'));
  $('source-button').disabled = !r.available;
  const filter = data.content_filter;
  if (filter?.removed?.length) {
    extra.append(el('p', `網頁文字中另有 ${filter.removed.length} 行是選單、按鈕或熱門標題（例如「${filter.removed.slice(0, 3).map(x => x.text).join('」「')}」），不列入判斷。`, 'muted'));
  }
}
function drawSources(data) {
  const box = $('source-boxes'); box.replaceChildren();
  const r = data.region_alignment || {}, items = data.evidence?.screenshot_ocr?.items || [], img = $('image');
  if (!r.available || !img.naturalWidth) return;
  for (const [cls, list] of [['dup', r.dom_duplicate], ['fix', r.ocr_variant], ['img', [...r.image_text, ...r.unlocated_text]]]) {
    for (const entry of list || []) {
      const points = items[entry.ocr_item_index]?.bbox || [];
      if (points.length < 3) continue;
      const xs = points.map(p => p[0]), ys = points.map(p => p[1]), d = el('div', null, cls);
      d.style.cssText = `left:${Math.min(...xs) / img.naturalWidth * 100}%;top:${Math.min(...ys) / img.naturalHeight * 100}%;` +
        `width:${(Math.max(...xs) - Math.min(...xs)) / img.naturalWidth * 100}%;height:${(Math.max(...ys) - Math.min(...ys)) / img.naturalHeight * 100}%`;
      d.title = entry.text; box.append(d);
    }
  }
}

/* ---------- link tracing ---------- */
const LINK_RISK = {high: ['高風險', 'high', '命中高風險規則，這個連結本身就足以判定為高風險。'],
  medium: ['可疑', 'medium', '出現多項可疑特徵，會把整體結果提高為「疑似詐騙」。'],
  low: ['留意', 'low', '只有輕度特徵，單獨出現不會判定為詐騙，但值得注意。'], none: ['未發現異常', '', '']};
const LINK_SOURCE = {page_link: '網頁上的連結', screenshot_text: '截圖中出現的網址', page_text: '網頁文字中的網址'};
function renderLinks(data) {
  const t = data.link_trace || {}, box = $('links'); box.replaceChildren();
  $('links-panel').hidden = !t.status || t.status === 'disabled';
  if (t.status !== 'SUCCESS') { box.append(el('p', t.status === 'error' ? '連結追蹤未完成。' : '', 'empty')); $('links-tag').textContent = ''; return; }
  $('links-tag').textContent = t.followed ? `追蹤 ${t.followed} 個連結` : '沒有外部連結';
  if (!t.links.length) { box.append(el('p', '頁面與截圖中沒有找到需要追蹤的外部連結。', 'empty')); return; }
  for (const link of t.links) {
    const [label, cls] = LINK_RISK[link.risk] || LINK_RISK.none;
    const card = el('div', null, 'link-card risk-' + link.risk), main = el('div');
    const meaning = (LINK_RISK[link.risk] || LINK_RISK.none)[2];
    const head = el('div', null, 'link-head'); head.append(el('span', label, 'risk-badge ' + cls), el('span', '來源：' + (LINK_SOURCE[link.source] || link.source)));
    if (link.context && link.source !== 'page_link') head.append(el('span', `原文：${link.context}`));
    const chain = el('div', null, 'chain');
    const bad = new Set(link.flags.filter(f => ['listed_165', 'lookalike'].includes(f.signal)).map(f => f.evidence.split(' ')[0]));
    link.hosts.forEach((host, i) => {
      if (i) chain.append(el('i', '→'));
      chain.append(el('span', host, [...bad].some(b => b.includes(host)) ? 'bad' : i === link.hosts.length - 1 ? 'final' : ''));
    });
    if (link.status === 'blocked') chain.append(el('i', '→'), el('span', '已攔截，未開啟', 'bad'));
    main.append(head, chain);
    const checks = el('ul', null, 'checks'), has = name => link.flags.find(f => f.signal === name);
    // What was checked, in a fixed order, so a single weak flag is read in context.
    check(checks, has('listed_165') ? 'bad' : '', has('listed_165') ? has('listed_165').evidence : '165 涉詐網站清單：沒有列入');
    check(checks, has('lookalike') || has('brand_impersonation') ? 'bad' : '',
      (has('lookalike') || has('brand_impersonation'))?.evidence || '仿冒平台或冒用品牌：沒有發現');
    check(checks, has('multi_hop') ? 'bad' : '', link.hosts.length > 1 ? `跳轉路線：經過 ${link.hosts.length} 個網域` + (has('shortener') ? '（含短網址）' : '') : '跳轉路線：直接開啟，沒有轉址');
    check(checks, link.status === 'success' ? '' : 'info', link.status === 'success' ? `落地頁：已開啟${link.final_title ? '「' + link.final_title + '」' : ''}`
      : link.status === 'blocked' ? '落地頁：命中規則，系統沒有開啟' : has('unreachable') ? has('unreachable').evidence : '落地頁：無法取得內容');
    for (const f of link.flags.filter(f => !['listed_165', 'lookalike', 'brand_impersonation', 'multi_hop', 'shortener', 'unreachable'].includes(f.signal)))
      check(checks, f.level === 'low' ? 'info' : 'bad', f.evidence);
    if (link.landing_model?.status === 'SUCCESS') check(checks, link.landing_model.fraud_confidence > .5 ? 'bad' : '', `落地頁文字像詐騙文案的程度 ${pct(link.landing_model.fraud_confidence)}`);
    main.append(checks);
    if (meaning) main.append(el('p', meaning, 'muted'));
    card.append(main);
    const shot = el('div', null, 'link-shot');
    if (link.screenshot_path) { const img = el('img'); img.src = `/api/jobs/${jobId}/link/${link.index}`; img.alt = '落地頁畫面'; img.onclick = () => window.open(img.src, '_blank'); shot.append(img, el('small', link.final_url)); }
    else shot.append(el('small', link.status === 'blocked' ? '命中高風險規則，系統沒有開啟這個網址。' : '沒有取得落地頁畫面。'));
    card.append(shot); box.append(card);
  }
}

/* ---------- screenshot & heatmap ---------- */
function heat(value, max) {
  const a = Math.min(1, Math.abs(value) / (max || 1)) * .7;
  return value >= 0 ? `rgba(201,58,58,${a})` : `rgba(36,99,235,${a})`;
}
function renderScreen(data) {
  const e = data.evidence || {};
  const has = Boolean(data.browser_capture?.screenshot_path || e.screenshot_ocr?.screenshot_path);
  $('image').hidden = !has;
  if (has) { $('image').onload = () => drawSources(data); $('image').src = `/api/jobs/${jobId}/image`; $('image').onclick = () => window.open($('image').src, '_blank'); }
  const regions = data.attribution?.heatmap_regions || [], boxes = $('heat-boxes'); boxes.replaceChildren();
  const max = Math.max(.01, ...regions.map(r => Math.abs(r.delta)));
  for (const r of regions) {
    const d = el('div'); d.title = `${r.text}\n移除後詐騙分數${r.delta >= 0 ? '下降' : '上升'} ${(Math.abs(r.delta) * 100).toFixed(1)} 個百分點`;
    d.style.cssText = `left:${r.left}%;top:${r.top}%;width:${r.width}%;height:${r.height}%;background:${heat(r.delta, max)}`;
    boxes.append(d);
  }
  $('heat-button').disabled = !regions.length;
  $('heat-button').title = regions.length ? '' : '需在進階設定勾選「產生熱點圖」';
  setView('plain', has);
}
function setView(view, has = !$('image').hidden) {
  document.querySelectorAll('[data-view]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.view === view)));
  $('heat-boxes').hidden = view !== 'heat';
  $('source-boxes').hidden = view !== 'source';
  $('image-note').textContent = !has ? '未取得畫面；遭攔截的網址不會開啟。'
    : view === 'heat' ? '紅框：讓分數偏向詐騙的區域；藍框：偏向正常。顏色越深影響越大。'
    : view === 'source' ? '灰框：網頁本來就有的文字　橘框：OCR 認錯、已更正　藍框：只在圖片裡、交給文字模型的文字' : '點擊圖片可查看原尺寸。';
}

/* ---------- research section ---------- */
function renderResearch(data) {
  const box = $('heat-text'), a = data.attribution || {};
  if (a.sources) {
    box.replaceChildren();
    for (const [name, analysis] of Object.entries(a.sources)) {
      if (analysis.status !== 'completed') continue;
      const source = name === 'dom' ? '網頁文字' : '截圖文字';
      const g = a.gradients?.[name];
      const wrap = el('div', null, 'heat-source');
      if (g?.status === 'completed') {
        wrap.append(el('h4', `${source}（逐字）`));
        const gmax = Math.max(1e-6, ...g.tokens.map(t => Math.abs(t.attribution))), line = el('div', null, 'heat-line');
        for (const t of g.tokens) { const s = el('span', t.text); s.style.background = heat(t.attribution, gmax); line.append(s); }
        wrap.append(line);
      }
      wrap.append(el('h4', `${source}（逐段刪除）`));
      const max = Math.max(.01, ...analysis.segments.map(s => Math.abs(s.delta_fraud_score ?? 0))), line = el('div', null, 'heat-line');
      for (const s of [...analysis.segments].sort((x, y) => x.segment_index - y.segment_index)) {
        const span = el('span', s.text + ' ');
        if (s.delta_fraud_score != null) { span.style.background = heat(s.delta_fraud_score, max); span.title = `移除後分數變化 ${(-s.delta_fraud_score * 100).toFixed(1)} 個百分點`; }
        line.append(span);
      }
      wrap.append(line); box.append(wrap);
    }
  }
  const table = $('provenance'); table.replaceChildren();
  const head = el('tr'); for (const t of ['項目', '模型輸出（正常／詐騙）', '差值', '詐騙分數', '說明']) head.append(el('th', t)); table.append(head);
  const e = data.evidence || {};
  const rows = data.browser_capture ? [['網頁文字', e.dom?.model], ['圖片裡的文字', e.screenshot_ocr?.model]] : [['截圖文字', e.screenshot_ocr?.model || data.image_analysis]];
  for (const [name, model] of rows) {
    const p = model?.score_provenance, tr = el('tr');
    tr.append(el('td', name), el('td', p ? p.document_logits.map(v => v.toFixed(2)).join(' ／ ') : '—', 'num'), el('td', p ? p.logit_difference.toFixed(2) : '—', 'num'),
      el('td', pct(model?.fraud_confidence), 'num'), el('td', p ? '分數 = 1 ÷ (1 + e^(−差值))，差值越大越像詐騙' : decision(model)));
    table.append(tr);
  }
  const c = data.content_analysis || {};
  if (Number.isFinite(c.fraud_score)) {
    const tr = el('tr'), sum = (c.contributions || []).reduce((s, x) => s + x.contribution, 0);
    tr.append(el('td', '融合模型'), el('td', `${(c.contributions || []).length} 項證據`, 'num'), el('td', '—', 'num'), el('td', pct(c.fraud_score), 'num'),
      el('td', `各項證據影響力加權後換算成分數；預測集合 {${(c.prediction_set || []).map(x => x === 'Fraud' ? '詐騙' : '正常').join('、')}}，只有一個類別時才下結論。`));
    table.append(tr);
  }
}
function evidence(tab) {
  const e = report.evidence || {};
  const value = tab === 'dom' ? e.dom?.text : tab === 'ocr' ? (e.screenshot_ocr?.texts || report.image_analysis?.ocr_texts || []).join('\n')
    : JSON.stringify({initial: report.domain_analysis, redirects: report.browser_capture?.navigation_checks, final: report.browser_capture?.final_domain_analysis}, null, 2);
  $('evidence').textContent = value || '本次沒有取得此類證據。';
  document.querySelectorAll('[data-tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
}

function render(data, id, name) {
  report = data; jobId = id; if (name) target = name;
  $('results').hidden = false;
  renderResult(data); $('model-warning').hidden = data.model_check?.status !== 'mismatch'; renderReasons(data); renderLinks(data); renderCharts(data); renderLLM(data); renderForensics(data); renderAccount(data); renderAlignment(data);
  renderScreen(data); renderResearch(data);
  evidence(data.evidence?.dom ? 'dom' : 'ocr');
}

/* ---------- measured performance (read from the deployed fusion model, so it follows retraining) ---------- */
async function loadMetrics() {
  try {
    const m = await (await fetch('/api/model-info')).json(), t = m.test || {}, c = m.conformal || {};
    if (!Number.isFinite(t.f1)) return;
    const f = v => (v * 100).toFixed(1) + '%';
    const tile = (name, v, note, fmt = f) => { const box = el('div', null, 'metric'); box.append(el('span', name), el('strong', fmt(v)), el('small', note)); return box; };
    $('metric-source').textContent = `測試資料：${t.n} 筆數位發展部「網路詐騙通報查詢網」中主管機關已判定的案例（${t.tp + t.fn} 筆詐騙、${t.tn + t.fp} 筆非詐騙），和訓練資料依帳號分組、完全不重疊。`;
    // ① and ② use different denominators, so they are shown apart and must not be compared directly.
    $('metric-tiles').replaceChildren(...[['Precision', t.precision, '判為詐騙的案例中，真的是詐騙'], ['Recall', t.recall, '詐騙案例中，被抓出來的比例'],
      ['F1', t.f1, 'Precision 與 Recall 的調和平均'], ['FPR', t.fpr, '正常案例被誤判為詐騙'], ['AUC', t.auc, '分數排序能力，0.5 等於亂猜', v => v.toFixed(2)]]
      .filter(([, v]) => Number.isFinite(v)).map(x => tile(...x)));
    $('abstain-tiles').replaceChildren(...[['交給人工的比例', c.unknown_rate, '證據不足以區分時不硬判'], ['已判斷樣本的準確率', c.accuracy_when_decided, '只計算系統有下結論的案例'],
      ['詐騙覆蓋率', c.coverage_fraud, '真詐騙沒有被誤排除「詐騙」的比例'], ['正常覆蓋率', c.coverage_normal, '真正常沒有被誤排除「正常」的比例']]
      .filter(([, v]) => Number.isFinite(v)).map(x => tile(...x)));
    const table = $('platform-table'); table.replaceChildren();
    const head = el('tr'); for (const h of ['平台', '詐騙／非詐騙筆數', 'Precision', 'Recall', 'F1', 'FPR']) head.append(el('th', h)); table.append(head);
    for (const [name, b] of Object.entries(m.by_platform || {})) {
      if (name === 'other') continue;
      const tr = el('tr'), p = v => Number.isFinite(v) ? f(v) : '—';
      tr.append(el('td', name), el('td', `${b.tp + b.fn}／${b.tn + b.fp}`, 'num'), el('td', p(b.precision), 'num'), el('td', p(b.recall), 'num'), el('td', b.tp + b.fn ? p(b.f1) : '—', 'num'), el('td', p(b.fpr), 'num'));
      table.append(tr);
    }
    const lines = ['①是「每一筆都強制判定」時的整體效能；②是加上拒答後，只看系統有下結論的案例。兩者分母不同，不能直接比較。',
      '③樣本少的平台（例如 LINE、TikTok）數字波動很大，只能當參考。'];
    if (Number.isFinite(m.hard_negative_fpr)) lines.push(`訓練時沒看過的 PTT 看板一般文章，誤判為詐騙的比例 ${f(m.hard_negative_fpr)}。`);
    lines.push('以上是融合模型對「貼文文字與圖片」的成效；165 清單、仿冒網址、連結追蹤等規則另外判定，不含在內。');
    $('metric-notes').replaceChildren(...lines.map(x => el('li', x)));
    $('metrics-block').hidden = false;
  } catch { /* metrics are informational; the page works without them */ }
}

/* ---------- events ---------- */
document.querySelectorAll('[data-tab]').forEach(b => { b.onclick = () => evidence(b.dataset.tab); });
document.querySelectorAll('[data-view]').forEach(b => { b.onclick = () => setView(b.dataset.view); });
document.querySelectorAll('[data-mode]').forEach(b => { b.onclick = () => {
  mode = b.dataset.mode; $('url-input').hidden = mode !== 'url'; $('image-input').hidden = mode !== 'image';
  $('results').hidden = true; report = null;
  document.querySelectorAll('[data-mode]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
  timeline(); activity('idle', '準備開始分析', '選擇輸入方式，開始檢查。');
}; });
$('download').onclick = () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], {type: 'application/json'}));
  const a = document.createElement('a'); a.href = url; a.download = `詐騙風險分析報告-${new Date().toISOString().slice(0, 10)}.json`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
};
$('form').onsubmit = async event => {
  event.preventDefault(); $('run').disabled = true; $('results').hidden = true;
  document.querySelectorAll('[data-mode]').forEach(b => { b.disabled = true; });
  try {
    let payload;
    if (mode === 'image') {
      const file = $('file').files[0];
      if (!file) throw Error('請選擇截圖');
      if (file.size > 10 * 1024 * 1024) throw Error('圖片不得超過 10 MB');
      const data = await new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(r.result); r.onerror = reject; r.readAsDataURL(file); });
      payload = {image: data.split(',')[1], name: file.name}; target = file.name;
    } else {
      target = $('url').value.trim(); if (!target) throw Error('請輸入網址');
      if (/\s/.test(target) || !/[a-z0-9-]\.[a-z]{2,}/i.test(target)) throw Error('請輸入完整網址，例如 https://www.instagram.com/帳號名稱');
      payload = {url: target};
    }
    payload.explain = $('explain').checked;
    timeline(); activity('running', '正在建立分析', '首次載入模型需要一些時間。');
    const response = await fetch('/api/jobs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
    const job = await response.json(); if (!response.ok) throw Error(job.error);
    const titles = ['確認輸入', '檢查網域', '擷取網頁畫面', '分析文字內容', '追蹤連結並整合證據'];
    while (true) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      const res = await fetch(`/api/jobs/${job.id}`); if (!res.ok) throw Error('無法取得工作狀態');
      const state = await res.json(); timeline(state.progress);
      if (state.state === 'error') throw Error(state.error);
      if (state.state === 'queued') activity('running', '等待分析', '目前有其他工作執行中。');
      else if (state.state === 'running') {
        const current = (state.progress?.steps || []).findIndex(step => step.state === 'running');
        activity('running', current >= 0 ? titles[current] : '正在分析', payload.explain ? '已啟用熱點圖，需要較長時間，請稍候。' : '系統會實際開啟網頁與連結，通常需要一到數分鐘。');
      }
      if (state.state === 'done') {
        render(state.report, job.id, target);
        const stopped = ['blocked', 'unusable', 'error'].includes(state.report.status);
        activity(stopped ? 'warning' : 'complete', stopped ? '分析已停止' : '分析完成', stopped ? '請查看結果中的原因。' : '結果與證據已整理完成。');
        $('results').scrollIntoView({behavior: 'smooth'}); break;
      }
    }
  } catch (error) {
    document.querySelectorAll('[data-step][data-state=running]').forEach(node => { node.dataset.state = 'warning'; $('stage-' + node.dataset.step).textContent = '未完成'; });
    activity('warning', '分析未完成', friendlyError(error.message));
  } finally { $('run').disabled = false; document.querySelectorAll('[data-mode]').forEach(b => { b.disabled = false; }); }
};
loadMetrics();
