"""Explain saved evidence without relabeling it or altering model scores."""
import argparse
from copy import deepcopy
import json
import math
from pathlib import Path


def spatial_coverage(report):
    browser = report.get('browser_capture') or {}
    capture = browser.get('viewport_evidence') or {}
    viewport = capture.get('viewport') or {}
    dpr = viewport.get('dpr')
    boxes = [box for item in capture.get('items', []) for box in item.get('boxes', [])]
    items = ((report.get('evidence') or {}).get('screenshot_ocr') or {}).get('items') or []
    result = {'method': 'ocr_center_in_dom_character_box_css_pixels', 'tolerance_css_px': 2,
              'matched': [], 'unmatched': [], 'unlocated': [], 'available': False}
    if not isinstance(dpr, (int, float)) or not math.isfinite(dpr) or dpr <= 0 or not boxes or not items:
        return result
    result['available'] = True
    for index, item in enumerate(items):
        if not item.get('accepted'):
            continue
        entry = {'ocr_item_index': index, 'text': item.get('text', '')}
        points = item.get('bbox') or []
        if len(points) < 4:
            result['unlocated'].append(entry)
            continue
        x = sum(p[0] for p in points) / len(points) / dpr
        y = sum(p[1] for p in points) / len(points) / dpr
        matched = any(left-2 <= x <= right+2 and top-2 <= y <= bottom+2 for left, top, right, bottom in boxes)
        result['matched' if matched else 'unmatched'].append(entry)
    return result


def heatmap_regions(report):
    """Place each deletion-experiment segment on the screenshot (percent coordinates) with its Δ."""
    attribution = report.get('attribution') or {}
    sources = attribution.get('sources') or {}
    browser = report.get('browser_capture') or {}
    capture = browser.get('viewport_evidence') or {}
    viewport = capture.get('viewport') or {}
    ocr = (report.get('evidence') or {}).get('screenshot_ocr') or {}
    path = ocr.get('screenshot_path')
    try:
        from PIL import Image
        with Image.open(path) as image:
            width, height = image.size
    except Exception:
        return []
    regions = []

    def add(box, row, source):
        left, top, right, bottom = box
        if right > left and bottom > top:
            regions.append({'source': source, 'text': row['text'][:120], 'delta': row['delta_fraud_score'],
                            'left': 100 * left, 'top': 100 * top, 'width': 100 * (right - left), 'height': 100 * (bottom - top)})
    dom = sources.get('dom') or {}
    vw, vh = viewport.get('width'), viewport.get('height')
    for row in dom.get('segments') or []:
        if row.get('delta_fraud_score') is None or not vw or not vh:
            continue
        for item in capture.get('items') or []:
            if row['text'] in item.get('text', '').splitlines() and item.get('boxes'):
                xs = [b[0] for b in item['boxes']] + [b[2] for b in item['boxes']]
                ys = [b[1] for b in item['boxes']] + [b[3] for b in item['boxes']]
                add((min(xs) / vw, min(ys) / vh, max(xs) / vw, max(ys) / vh), row, 'dom')
    for row in (sources.get('screenshot_ocr') or {}).get('segments') or []:
        if row.get('delta_fraud_score') is None:
            continue
        lines = set(row['text'].splitlines())
        for item in ocr.get('items') or []:
            points = item.get('bbox') or []
            if item.get('accepted') and item.get('text') in lines and len(points) >= 3:
                xs, ys = [p[0] for p in points], [p[1] for p in points]
                add((min(xs) / width, min(ys) / height, max(xs) / width, max(ys) / height), row, 'screenshot_ocr')
    return regions


def explain(report):
    evidence = report.get('evidence') or {}
    dom = evidence.get('dom') or {}
    ocr = evidence.get('screenshot_ocr') or {}
    assessment = report.get('assessment') or {}
    spatial = spatial_coverage(report)
    scores = []
    for source, label, item in [('dom', '網頁可見文字', dom), ('screenshot_ocr', '整張截圖文字', ocr)]:
        model = item.get('model') or {}
        if model.get('status') == 'SUCCESS':
            scores.append({'source': source, 'label': label, 'fraud_score': model.get('fraud_confidence'),
                           'score_type': 'raw_softmax', 'prediction': model.get('prediction')})
    conflict = (report.get('content_analysis') or {}).get('basis') == 'modality_conflict'
    warnings = []
    if dom and ocr:
        warnings.append('同一視窗不等於同一份文字。DOM 不含圖片內文字；OCR 可能跨多張貼文、漏字或誤辨。')
        if spatial['available']:
            warnings.append(f"OCR 有 {len(spatial['matched'])} 段對應 DOM 文字位置，{len(spatial['unmatched'])} 段未對應；位置對應不是語意一致的證明。")
    if conflict:
        warnings.append('兩個分數來自不同輸入，不能直接解讀成模型對同一內容自相矛盾，也不能平均成帳號詐騙機率。')
    warnings.append('原始 softmax 是文字分類分數，不是帳號為詐騙的機率。')
    if (report.get('ood') or {}).get('out_of_distribution'):
        warnings.insert(0, '這段文字與訓練資料差異很大（分布外），MacBERT 分數參考價值較低，請以 LLM 引用、帳號訊號與原始畫面人工判斷。')
    level = assessment.get('risk_level', 'Unknown')
    stopped = report.get('status') in ('error', 'blocked', 'unusable')
    title = {'High':'網址觸發高風險規則', 'Medium':'文字模型出現風險訊號，尚待查證',
             'Low':'目前取得的文字未呈現一致的詐騙訊號', 'Unknown':'分析完成，帳號風險仍待查證'}.get(level, '仍待查證')
    if stopped:
        title = '未取得有效分析結果' if report.get('status') != 'blocked' else '網址已攔截'
    if conflict:
        reason = 'DOM 與整張截圖 OCR 的分類方向不同；目前沒有足夠的同內容對照或獨立身分證據判定此帳號。'
    else:
        reason = '結論僅限這次擷取的頁面／圖片，不是帳號身分認證。'
    content = report.get('content_analysis') or {}
    if str(content.get('score_type', '')).startswith('stacked') and not stopped:
        title = {'Fraud': '多項證據指向詐騙風險', 'Normal': '整體證據偏向正常內容'}.get(
            content.get('prediction'), '證據不足以在設定的誤差範圍內下結論')
        top = [f"{item['label']}（{'+' if item['contribution'] > 0 else '−'}）" for item in content.get('contributions', [])[:3]]
        reason = ('主要依據：' + '、'.join(top) + '。' if top else '') + reason
        warnings.insert(0, f"融合分數 {content['fraud_score'] * 100:.1f}% 是以官方判定案例訓練的堆疊模型輸出；"
                           f"Unknown 代表 conformal 預測集合（α={content.get('alpha')}）不是單一類別。")
    if assessment.get('basis') == 'known_scam_image':
        title = '圖片與主管機關確認的詐騙素材幾乎相同'
        reason = '感知雜湊比對命中數發部通報網已判定為詐騙的案例圖片（' + '、'.join(assessment.get('evidence_refs', [])[:2]) + '）。' + reason
    return {'title': title, 'reason': reason, 'execution': 'stopped' if stopped else 'completed',
            'decision': 'needs_review' if level == 'Unknown' else 'risk_signal_only',
            'account_identity': 'not_verified', 'scores': scores, 'warnings': warnings,
            'content_equivalence': 'not_established' if dom and ocr else 'single_source',
            'spatial_coverage': spatial,
            'next_action': '核對未對應 DOM 的 OCR 文字及原始貼文；未核實前不要把此帳號標成正常或詐騙。'}


def annotate(report):
    report['report_summary'] = explain(report)
    if (report.get('attribution') or {}).get('sources'):
        report['attribution']['heatmap_regions'] = heatmap_regions(report)
    report['analysis_status'] = report['report_summary']['execution']
    report['decision_status'] = report['report_summary']['decision']
    # Successful execution and an abstained decision are independent states.
    if report.get('status') == 'incomplete' and (report.get('content_analysis') or {}).get('status') == 'SUCCESS' and not report.get('error'):
        report['status'] = 'success'
    return report


def markdown(report):
    s = report.get('report_summary') or explain(report)
    def safe(value):
        return str(value).replace('<', '&lt;').replace('>', '&gt;').replace('\n', ' ')
    lines = ['# 防詐分析報告', '', '## '+s['title'], '', s['reason'], '',
             '- 流程狀態：'+s['execution'], '- 判斷狀態：'+s['decision'], '- 帳號身分：未驗證', '', '## 分數來源', '']
    for item in s['scores']:
        score = item.get('fraud_score')
        if isinstance(score, (int, float)) and math.isfinite(score):
            lines.append(f"- {item['label']}：Fraud 原始分數 {score*100:.2f}%，不是帳號詐騙機率。")
    lines += ['', '## 限制與原因', ''] + ['- '+text for text in s['warnings']]
    experiment = report.get('region_experiment') or {}
    if experiment.get('status') == 'completed':
        lines += ['', '## 分區敏感度實驗（不取代原始分數）', '']
        for key, label in [('matched','對應 DOM 位置的 OCR'), ('unmatched','未對應 DOM 位置的 OCR')]:
            model = experiment['regions'][key]['model']
            p = model.get('fraud_confidence')
            if model.get('status') == 'SUCCESS' and isinstance(p,(int,float)):
                lines.append(f'- {label}：Fraud 原始分數 {p*100:.2f}%。')
        lines.append('分區是幾何位置診斷，不保證語意對齊；分類變動反映輸入組合敏感性，不證明帳號安全。')
    extra = s['spatial_coverage']['unmatched']
    if extra:
        lines += ['', '## OCR 未對應 DOM 位置的文字（不是詐騙證據清單）', '']
        lines += ['- '+safe(item['text']) for item in extra]
    lines += ['', '## 建議', '', s['next_action'], '']
    return '\n'.join(lines)


def compare_regions(report, detector):
    """Sensitivity experiment only: never feeds back into the original decision."""
    coverage = spatial_coverage(report)
    if not coverage['available']:
        return {'status': 'unavailable'}
    original = (((report.get('evidence') or {}).get('screenshot_ocr') or {}).get('model') or {})
    identity = (original.get('score_provenance') or {}).get('model_files_sha256')
    if identity != detector.model_identity:
        raise ValueError('Use the original model for a controlled comparison')
    results = {}
    for key in ('matched', 'unmatched'):
        text = '\n'.join(item['text'] for item in coverage[key])
        results[key] = {'text': text, 'model': detector.predict(text)}
    return {'status':'completed', 'regions':results,
            'note':'Geometry-based subsets, not verified semantic alignment or fraud truth; original scores unchanged.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model-path', type=Path, help='Optional controlled inference on matched/unmatched OCR regions')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new output directory; original reports are preserved')
    report = annotate(deepcopy(json.loads(args.report.read_text(encoding='utf-8'))))
    report['source_report_path'] = str(args.report.resolve())
    if args.model_path:
        from inference import FraudDetector
        report['region_experiment'] = compare_regions(report, FraudDetector(args.model_path))
    args.output.mkdir(parents=True)
    (args.output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (args.output/'report.md').write_text(markdown(report), encoding='utf-8')
    print(args.output/'report.md')


if __name__ == '__main__':
    main()
