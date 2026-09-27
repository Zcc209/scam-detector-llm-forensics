const $ = id => document.getElementById(id);
const fields = ['label','group_id','pair_id','split','reviewer','label_source','permission','notes'];
let selected;
async function refresh() {
  try {
    const response = await fetch('/api/reviews');
    if (!response.ok) throw Error('無法載入案例');
    const rows = await response.json();
    $('cases').replaceChildren();
    for (const row of rows) {
      const button = document.createElement('button'); button.className = 'secondary';
      button.textContent = `${row.status} · ${row.source_url || row.case_id}`;
      button.onclick = () => {
        selected = row.queue_id;
        $('texts').textContent = `原始報告：${row.report_path}\n\nDOM\n${row.texts.dom || '無'}\n\nOCR\n${row.texts.screenshot_ocr || '無'}`;
        for (const name of fields) $(name).value = row[name] || (name === 'label' ? 'Unresolved' : name === 'split' ? 'train' : '');
        $('review-form').hidden = false;
        $('status').textContent = '請核對原始畫面及外部證據；不確定就保留 Unresolved。';
      };
      $('cases').append(button);
    }
    if (!rows.length) $('status').textContent = '目前沒有案例。請先分析網址或圖片。';
  } catch (error) { $('status').textContent = error.message; }
}
$('refresh').onclick = refresh;
$('review-form').onsubmit = async event => {
  event.preventDefault();
  const button = event.submitter; button.disabled = true;
  try {
    const payload = Object.fromEntries(fields.map(name=>[name,$(name).value]));
    const response = await fetch(`/api/reviews/${selected}`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const result = await response.json();
    if (!response.ok) throw Error(result.error);
    $('status').textContent = '複核已儲存，模型未變動。完成一批案例後再執行批次微調。';
    await refresh();
  } catch (error) { $('status').textContent = error.message; }
  finally { button.disabled = false; }
};
refresh();
