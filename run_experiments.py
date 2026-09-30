"""Benchmark: baselines vs learned evidence fusion, with conformal abstention and robustness tests.

- official benchmark : MODA-adjudicated Fraud vs Normal cases, group-separated train/calibration/test.
- hard negatives     : presumed-normal PTT posts from boards never seen in training -> false-positive rate.
- behavioral tests   : CheckList-style synthetic probes (data/behavioral_tests.json) -> pass rate.
Fits models/fusion_model*.json (the configuration used by the live pipeline) and writes every published number
from this one run: docs/experiment_results.json (the single source), docs/experiment_results.md and the
results block of README.md. tests/test_results_consistency.py fails if any of them is edited by hand.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random

import numpy as np

import calibration
import conformal
import fusion_model
from attribution import fingerprint
from fusion import fuse
from image_forensics import hamming

ROOT = Path(__file__).resolve().parent
ABLATIONS = [
    ('lr_text', ['text']),
    ('lr_text_ocr', ['text', 'ocr']),
    ('lr_+account', ['text', 'ocr', 'account']),
    ('lr_+llm', ['text', 'ocr', 'account', 'llm']),
    ('lr_full', ['text', 'ocr', 'account', 'llm', 'image']),
    ('lr_no_macbert', ['account', 'llm', 'image']),
]
NAMES_ZH = {'macbert_argmax': 'MacBERT 原始 argmax（原系統單路）', 'legacy_agreement': '原系統 DOM/OCR 一致性規則',
            'lr_text': 'MacBERT（重新校準）', 'lr_text_ocr': '+ 圖片內文字', 'lr_+account': '+ 帳號/手法規則',
            'lr_+llm': '+ LLM 證據抽取', 'lr_full': '+ 影像鑑識（完整）', 'lr_no_macbert': '不含 MacBERT（規則+LLM+影像）'}


def load(path, key='case_id'):
    return {json.loads(l)[key]: json.loads(l) for l in Path(path).read_text(encoding='utf-8').splitlines()} if Path(path).exists() else {}


def known_scam_features(cases, base):
    """Hash match only against *training* Fraud images from other groups (no test leakage)."""
    from PIL import Image

    def usable(case):
        try:
            with Image.open(case['image_path']) as image:
                return min(image.size) >= 200
        except (OSError, TypeError, AttributeError):
            return False
    train = [c for c in cases if c['split'] == 'train' and base.get(c['case_id'], {}).get('hashes') and usable(c)]
    normal = [base[c['case_id']]['hashes'] for c in train if c['label'] == 'Normal']
    # Same exclusions as image_forensics.build_index: no icons, nothing that also appears in non-fraud cases.
    index = [(c['group_id'], base[c['case_id']]['hashes']) for c in train if c['label'] == 'Fraud'
             and not any(hamming(base[c['case_id']]['hashes']['phash'], n['phash']) <= 12 for n in normal)]
    for case in cases:
        probe = base.get(case['case_id'], {}).get('hashes')
        match = probe and any(g != case['group_id'] and hamming(probe['phash'], h['phash']) <= 10
                              and hamming(probe['dhash'], h['dhash']) <= 14 for g, h in index)
        case['evidence']['image'] = {'features': {'image_known_scam_match': int(bool(match))}}


def assemble(manifest, base, llm):
    cases = []
    for case in manifest.values():
        row = base.get(case['case_id'])
        if not row:
            continue
        case = dict(case)
        text_model, ocr_model = row.get('text_model'), row.get('ocr_model')
        if text_model is None and ocr_model is not None:
            text_model, ocr_model = ocr_model, None  # image-only case: OCR text is the account text
        case['evidence'] = {'text_model': text_model, 'ocr_model': ocr_model, 'account': row['account'],
                            'llm': (llm.get(case['case_id']) or {}).get('llm') or {'status': 'missing'}}
        if text_model is None:
            continue
        cases.append(case)
    return cases


def weights(cases):
    """Each group totals weight 1, then classes are balanced (the sample prior is not deployment prior)."""
    key = lambda c: c.get('weight_group') or c['group_id']  # PTT boards are split units, articles are the weighting unit
    size = Counter(key(c) for c in cases)
    raw = [1 / size[key(c)] for c in cases]
    per_class = defaultdict(float)
    for c, w in zip(cases, raw):
        per_class[c['label']] += w
    return [w * sum(per_class.values()) / (2 * per_class[c['label']]) for c, w in zip(cases, raw)]


first_platform = lambda c: (c.get('platforms') or ['Unknown'])[0]
PLATFORM_GROUPS = (('Threads', lambda c: first_platform(c) == 'Threads'),
                   ('Facebook', lambda c: first_platform(c) == 'FB'),
                   ('一般網頁', lambda c: first_platform(c) == 'Web'),
                   ('LINE／TikTok／IG／YT', lambda c: first_platform(c) in ('LINE', 'Tiktok', 'IG', 'YT')),
                   ('other', lambda c: first_platform(c) != 'Threads'))


def binary_metrics(labels, predictions):
    tp = sum(y == 1 and p == 'Fraud' for y, p in zip(labels, predictions))
    fp = sum(y == 0 and p == 'Fraud' for y, p in zip(labels, predictions))
    tn = sum(y == 0 and p == 'Normal' for y, p in zip(labels, predictions))
    fn = sum(y == 1 and p != 'Fraud' for y, p in zip(labels, predictions))
    unknown = sum(p == 'Unknown' for p in predictions)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {'n': len(labels), 'tp': tp, 'fp': fp, 'tn': tn, 'fn': fn, 'unknown': unknown,
            'precision': precision, 'recall': recall, 'f1': 2 * tp / (2 * tp + fp + fn) if tp else 0.0,
            'fpr': fp / sum(y == 0 for y in labels) if any(y == 0 for y in labels) else None,
            'accuracy': (tp + tn) / len(labels), 'unknown_rate': unknown / len(labels)}


def auc(labels, scores):
    pos = [s for s, y in zip(scores, labels) if y == 1]
    neg = [s for s, y in zip(scores, labels) if y == 0]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def bootstrap(cases, predictions, reps=1000, seed=7):
    by_group = defaultdict(list)
    for case, prediction in zip(cases, predictions):
        by_group[case['group_id']].append((int(case['label'] == 'Fraud'), prediction))
    groups = list(by_group)
    rng = random.Random(seed)
    f1s, fprs = [], []
    for _ in range(reps):
        sample = [row for g in (rng.choice(groups) for _ in groups) for row in by_group[g]]
        m = binary_metrics([y for y, _ in sample], [p for _, p in sample])
        f1s.append(m['f1'])
        if m['fpr'] is not None:
            fprs.append(m['fpr'])
    pick = lambda values, q: sorted(values)[int(q * (len(values) - 1))] if values else None
    return {'f1_95ci': [pick(f1s, .025), pick(f1s, .975)], 'fpr_95ci': [pick(fprs, .025), pick(fprs, .975)]}


def macbert_argmax(case):
    result = case['evidence']['text_model']
    return result.get('prediction') if result and result.get('prediction') in ('Fraud', 'Normal') else 'Unknown'


def legacy(case):
    ev = case['evidence']
    return fuse(ev['text_model'], ev['ocr_model'])['prediction']


def evaluate_method(name, test, hard_test, predict, score=None):
    labels = [int(c['label'] == 'Fraud') for c in test]
    predictions = [predict(c) for c in test]
    result = {'name': name, 'label_zh': NAMES_ZH.get(name, name), 'official_test': binary_metrics(labels, predictions)}
    result['official_test'].update(bootstrap(test, predictions))
    # Fraud reports are mostly Threads; show whether performance holds on each other platform.
    result['by_platform'] = {}
    for name_, keep in PLATFORM_GROUPS:
        subset = [(int(c['label'] == 'Fraud'), p) for c, p in zip(test, predictions) if keep(c)]
        if subset:
            result['by_platform'][name_] = binary_metrics([y for y, _ in subset], [p for _, p in subset])
    if score:
        scores = [score(c) for c in test]
        result['auc'] = auc(labels, scores)
        result['calibration'] = {k: v for k, v in calibration.statistics([(s, y, 1.0) for s, y in zip(scores, labels)]).items()
                                 if k != 'reliability_bins'}
        result['reliability_bins'] = calibration.statistics([(s, y, 1.0) for s, y in zip(scores, labels)])['reliability_bins']
    hard = [predict(c) for c in hard_test]
    result['hard_negative_fpr'] = sum(p == 'Fraud' for p in hard) / len(hard) if hard else None
    result['hard_negative_unknown'] = sum(p == 'Unknown' for p in hard) / len(hard) if hard else None
    return result


def without_llm(evidence):
    return {**evidence, 'llm': {'status': 'unavailable'}}


def disagreement(cases):
    """When MacBERT and the LLM disagree on held-out cases, how often was each one right?"""
    def llm_benign(llm):
        return llm.get('status') == 'SUCCESS' and llm.get('risk') == 'low' and not llm.get('tactics')

    def llm_risky(llm):
        return llm.get('status') == 'SUCCESS' and (llm.get('risk') in ('medium', 'high') or llm.get('tactics'))
    out = {}
    for key, keep in (('macbert_fraud_llm_low', lambda c: c['evidence']['text_model']['fraud_confidence'] > .5 and llm_benign(c['evidence']['llm'])),
                      ('macbert_normal_llm_risky', lambda c: c['evidence']['text_model']['fraud_confidence'] <= .5 and llm_risky(c['evidence']['llm']))):
        subset = [c for c in cases if keep(c)]
        out[key] = {'n': len(subset), 'fraud_rate': sum(c['label'] == 'Fraud' for c in subset) / len(subset) if subset else None}
    out['split'] = 'calibration (official verdicts, not used to fit weights)'
    return out


def fit_variant(groups, train, calib, alpha, signed=True):
    names = fusion_model.feature_names(groups)
    rows = [fusion_model.vectorize(c['evidence']) for c in train]
    labels, w = [int(c['label'] == 'Fraud') for c in train], weights(train)
    if 'llm' in groups:
        # Modality dropout: teammates without Ollama still get a model that has seen "LLM missing".
        rows += [fusion_model.vectorize(without_llm(c['evidence'])) for c in train]
        labels, w = labels * 2, w + [x * 0.5 for x in w]
    model = fusion_model.fit(rows, labels, w, names, signed=signed)
    official_calib = [c for c in calib if c['label_kind'] == 'official_verdict']
    probabilities = [fusion_model.score(model, fusion_model.vectorize(c['evidence'])) for c in official_calib]
    model['conformal'] = conformal.fit(probabilities, [int(c['label'] == 'Fraud') for c in official_calib], alpha)
    return model


def behavioral(detector, model, use_llm, system_detector=None):
    system_detector = system_detector or detector
    from account_signals import extract
    from content_filter import filter_text
    import llm_evidence
    suite = json.loads((ROOT / 'data' / 'behavioral_tests.json').read_text(encoding='utf-8'))
    rows = []
    for test in suite['tests']:
        for text in test['texts']:
            variants = [text]
            if test.get('padding'):
                pad = test['padding']
                variants = ['\n'.join(pad[:4] + [text] + pad[4:])]
            for sample in variants:
                raw = detector.predict(sample)
                filtered = filter_text(sample)['text']
                model_input = system_detector.predict(filtered)
                llm = llm_evidence.analyze(filtered) if use_llm else {'status': 'disabled'}
                decision = fusion_model.decide({'text_model': model_input, 'ocr_model': None, 'account': extract(filtered),
                                                'llm': llm, 'image': None}, model)
                rows.append({'type': test['type'], 'expect': test['expect'], 'text': sample,
                             'macbert_raw': raw['prediction'], 'macbert_raw_score': raw['fraud_confidence'],
                             'system': decision['prediction'], 'system_score': decision['fraud_score']})
    summary = {}
    for kind in dict.fromkeys(r['type'] + ':' + r['expect'] for r in rows):
        subset = [r for r in rows if r['type'] + ':' + r['expect'] == kind]
        summary[kind] = {'n': len(subset),
                         'macbert_raw_pass': sum(r['macbert_raw'] == r['expect'] for r in subset) / len(subset),
                         'system_pass': sum(r['system'] == r['expect'] for r in subset) / len(subset),
                         'system_wrong': sum(r['system'] not in (r['expect'], 'Unknown') for r in subset) / len(subset)}
    return {'summary': summary, 'rows': rows, 'synthetic': True}


def pct(value):
    return '—' if value is None else f'{value * 100:.1f}%'


PREVIOUS_SPLIT_NOTE = ('舊資料切分（只取最新 900 筆詐騙，測試集 178 筆：69 詐騙／109 非詐騙，幾乎全為 Threads）曾量到完整系統 '
                       'F1 79.1%、AUC 0.92。該切分高估了跨平台表現，已停用；其餘數字全部來自目前的切分。')
FEATURE_ZH = {'text_logit': 'MacBERT 文字分數', 'text_missing': '沒有文字', 'ocr_logit': 'MacBERT 圖片內文字分數', 'ocr_missing': '沒有圖片內文字',
              'off_platform_contact': '引導到站外聯絡', 'short_link': '短網址', 'guaranteed_return': '保證獲利', 'investment_lure': '投資招攬用語',
              'crypto_or_payment': '匯款／虛擬貨幣', 'urgency': '催促、限時', 'free_giveaway': '免費贈送／中獎', 'job_lure': '輕鬆高薪兼職',
              'impersonation_claim': '提到官方機構或客服', 'verified_badge': '平台驗證標章（只能推向正常）', 'throwaway_profile': '粉絲很少的新帳號',
              'large_audience': '大量粉絲（只能推向正常）', 'random_digit_handle': '隨機數字帳號', 'simplified_chinese': '大量簡體字',
              'llm_tactic_count': 'LLM 找到的手法數', 'llm_solicitation': 'LLM：招攬讀者', 'llm_addresses_reader': 'LLM：直接要求讀者行動',
              'llm_risk': 'LLM 風險等級', 'llm_benign_act': 'LLM：討論／新聞／分享（只能推向正常）', 'llm_missing': '沒有 LLM 結果',
              'image_known_scam_match': '與已知詐騙圖片相符', 'image_brand_mismatch': '品牌與網址不符', 'image_editor_tag': '圖片編修紀錄'}
BEHAVIOR_ZH = {'keyword_trap:Normal': '關鍵字陷阱句（含詐騙常見字眼的正常句子，應判正常）',
               'anti_fraud_awareness:Normal': '反詐騙宣導文（應判正常）', 'scam_positive:Fraud': '典型詐騙句（應判詐騙）',
               'invariance_ui_padding:Fraud': '詐騙句加上介面文字（結果應不變）',
               'invariance_ui_padding:Normal': '正常句加上介面文字（結果應不變）'}
README_START = '<!-- results:start（由 run_experiments.py 自動產生，請勿手動修改） -->'
README_END = '<!-- results:end -->'


def readme_section(results):
    """The README's results block, generated from the same results.json as docs/experiment_results.md."""
    by = {m['name']: m for m in results['methods']}
    final, base = by['lr_full_ft'], by['macbert_argmax']
    t, c = final['official_test'], final['conformal_test']
    ci = t.get('f1_95ci') or [None, None]
    n_fraud, n_normal = t['tp'] + t['fn'], t['tn'] + t['fp']
    lines = ['## 實測成效', '',
             f"測試資料：數位發展部「網路詐騙通報查詢網」中主管機關已判定的案例，**{t['n']} 筆（{n_fraud} 詐騙、{n_normal} 非詐騙）**，"
             '和訓練資料依帳號、聯絡方式、相似文字分組，完全不重疊。以下所有數字都由 `run_experiments.py` 從同一份 '
             '[`docs/experiment_results.json`](docs/experiment_results.json) 產生，與 [實驗結果](docs/experiment_results.md) 和網站上的「系統實測成效」一致。', '',
             '**① 全體分類效能**（每一筆都強制判為詐騙或正常）', '',
             '| Precision | Recall | F1（95% 信賴區間） | FPR | AUC |', '|---:|---:|---:|---:|---:|',
             f"| {pct(t['precision'])} | {pct(t['recall'])} | {pct(t['f1'])}（{pct(ci[0])}～{pct(ci[1])}） | {pct(t['fpr'])} | {final['auc']:.2f} |", '',
             '**② 加上「需要人工查證」之後**（和①分母不同，不能直接比較）', '',
             '| 交給人工的比例 | 已判斷樣本的準確率 | 詐騙覆蓋率 | 正常覆蓋率 |', '|---:|---:|---:|---:|',
             f"| {pct(c['unknown_rate'])} | {pct(c['accuracy_when_decided'])} | {pct(c['coverage_fraud'])} | {pct(c['coverage_normal'])} |", '',
             '**③ 各平台**（全體分類，不拒答）', '', '| 平台 | 詐騙／非詐騙 | F1 | FPR |', '|---|---:|---:|---:|']
    for name, b in (final.get('by_platform') or {}).items():
        if name != 'other':
            lines.append(f"| {name} | {b['tp'] + b['fn']}／{b['tn'] + b['fp']} | {pct(b['f1']) if b['tp'] + b['fn'] else '—'} | {pct(b['fpr'])} |")
    if 'macbert_ft_argmax' in by:
        lines += ['', '**和原始 MacBERT 比較**（同一份測試集，每一筆都強制判定）：原始模型 `anti_fraud_E3_macbert` 是用詐騙對話訓練的，'
                  '直接拿來判斷社群貼文幾乎抓不到詐騙；`macbert_social` 是在它的基礎上，用官方判定的社群貼文與 PTT 一般文章再微調'
                  '（做法見[系統運作說明](docs/system_walkthrough.md#macbert-的三個版本)）；完整系統再加上帳號特徵、LLM、影像鑑識一起融合。', '',
                  '| 模型 | Precision | Recall | F1 | FPR | AUC |', '|---|---:|---:|---:|---:|---:|']
        for key, label in (('macbert_argmax', '原始 MacBERT（anti_fraud_E3_macbert）'), ('macbert_ft_argmax', '社群微調 MacBERT（macbert_social）'),
                           ('lr_full_ft', '完整系統（macbert_social＋證據融合）')):
            m, o = by[key], by[key]['official_test']
            lines.append(f"| {label} | {pct(o['precision'])} | {pct(o['recall'])} | {pct(o['f1'])} | {pct(o['fpr'])} | {m['auc']:.2f} |")
    if 'lr_+llm' in by and 'lr_full_llm_off' in by:
        gain = by['lr_+llm']['official_test']['f1'] - by['lr_+account']['official_test']['f1']
        off = by['lr_full']['official_test']['f1'] - by['lr_full_llm_off']['official_test']['f1']
        lines += ['', f"**LLM 的貢獻**：消融實驗中，加入 LLM 讓 F1 變化 {gain * 100:+.1f} 個百分點；完整模型關掉 LLM，F1 變化 {-off * 100:+.1f} 個百分點。"
                  'LLM 不是準確率的主要來源，定位是證據抽取與可解釋性。']
    lines += ['', f"其他：訓練時沒看過的 PTT 看板一般文章，誤判為詐騙的比例 {pct(final['hard_negative_fpr'])}。"
              '官方資料中 LINE、TikTok、IG 的詐騙案例內容多已被移除（只剩預設圖示），無法用於評估。'
              '融合模型的權重與門檻見[實驗結果](docs/experiment_results.md#融合模型權重與門檻)，代表性案例見 [demo 案例](docs/demo_cases.md)。']
    return '\n'.join(lines)


def update_readme(results, path=ROOT / 'README.md'):
    text = path.read_text(encoding='utf-8')
    if README_START not in text or README_END not in text:
        print('README markers missing; results section not updated', flush=True)
        return
    head, rest = text.split(README_START, 1)
    tail = rest.split(README_END, 1)[1]
    path.write_text(head + README_START + '\n' + readme_section(results) + '\n' + README_END + tail, encoding='utf-8')


def report_markdown(results):
    data = results['data']
    lines = ['# 實驗結果（自動產生）', '', f"產生時間：{results['generated_at']}　模型：MacBERT `{results['macbert_sha256'][:12]}`",
             '', '## 資料', '',
             f"- 官方判定資料（數發部網路詐騙通報查詢網，時間軸載明主管機關判定）：{data['official']}",
             f"- 困難負樣本（PTT 一般看板文章，推定正常，測試看板訓練時未見過）：{data['hard_negative']}",
             f"- 分組：同一 LINE ID／帳號／連結／相同文字 = 同一組，組不跨 split（共 {data['official_groups']} 組）。",
             '', '## 官方判定測試集（每一列都只在 test split 評估）', '',
             '|方法|Precision|Recall|F1 (95% CI)|FPR|Unknown|AUC|ECE|困難負樣本誤報率|',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for m in results['methods']:
        t = m['official_test']
        ci = t.get('f1_95ci') or [None, None]
        area = f"{m['auc']:.3f}" if m.get('auc') is not None else '—'
        lines.append(f"|{m['label_zh']}|{pct(t['precision'])}|{pct(t['recall'])}|{pct(t['f1'])} ({pct(ci[0])}–{pct(ci[1])})|"
                     f"{pct(t['fpr'])}|{pct(t['unknown_rate'])}|{area}|"
                     f"{pct((m.get('calibration') or {}).get('ece'))}|{pct(m['hard_negative_fpr'])}|")
    lines += ['', 'LR 列為「不拒答」的 argmax 結果；下表為加上 conformal 拒答後的結果。Unknown 在 Recall 中算漏判。', '',
              '## 拒答（Mondrian conformal, α = %.2f）' % results['alpha'], '',
              '|方法|詐騙覆蓋率|正常覆蓋率|Unknown 比例|有判定時準確率|F1（Unknown 算錯）|',
              '|---|---:|---:|---:|---:|---:|']
    for m in results['methods']:
        c = m.get('conformal_test')
        if c:
            lines.append(f"|{m['label_zh']}|{pct(c['coverage_fraud'])}|{pct(c['coverage_normal'])}|{pct(c['unknown_rate'])}|"
                         f"{pct(c['accuracy_when_decided'])}|{pct(c['f1_with_unknown'])}|")
    lines += ['', '## 平台分組（詐騙通報以 Threads 為主，檢查是否只學到平台差異）', '',
              '官方判定測試集依原貼文平台拆開計算（不拒答的 argmax 結果）。樣本少的平台信賴區間很寬，只能當參考。', '',
              '|方法|平台|詐騙／非詐騙筆數|Precision|Recall|F1|FPR|', '|---|---|---:|---:|---:|---:|---:|']
    for m in results['methods']:
        if m['name'] not in ('macbert_argmax', 'macbert_ft_argmax', 'lr_full_ft'):
            continue
        for platform_name, _ in PLATFORM_GROUPS:
            b = (m.get('by_platform') or {}).get(platform_name)
            if b:
                lines.append(f"|{m['label_zh']}|{'其他平台合計' if platform_name == 'other' else platform_name}|{b['tp'] + b['fn']}／{b['tn'] + b['fp']}|"
                             f"{pct(b['precision'])}|{pct(b['recall'])}|{pct(b['f1']) if b['tp'] + b['fn'] else '—'}|{pct(b['fpr'])}|")
    by = {m['name']: m for m in results['methods']}
    if 'macbert_ft_old_recipe_argmax' in by:
        lines += ['', '## 資料取樣方式的對照實驗（同一份測試集，python compare_recipes.py）', '',
                  '|MacBERT 微調資料|F1|AUC|Threads F1|Facebook F1|一般網頁 F1|', '|---|---:|---:|---:|---:|---:|']
        for key in ('macbert_ft_old_recipe_argmax', 'macbert_ft_argmax'):
            m, b = by[key], by[key].get('by_platform') or {}
            lines.append(f"|{m['label_zh']}|{pct(m['official_test']['f1'])}|{m['auc']:.3f}|{pct((b.get('Threads') or {}).get('f1'))}|"
                         f"{pct((b.get('Facebook') or {}).get('f1'))}|{pct((b.get('一般網頁') or {}).get('f1'))}|")
    lines += ['', '## 舊資料切分（已停用，不能與上表比較）', '', PREVIOUS_SPLIT_NOTE]
    final = by.get('lr_full_ft') or {}
    if final.get('weights'):
        q = final['conformal_model']
        lines += ['', '## 融合模型權重與門檻', '',
                  '網站實際使用的融合模型（`models/fusion_model_social.json`）。融合分數 = 1 ÷ (1 + e^(−z))，'
                  f"z = 截距 {final['bias']:+.3f} ＋ Σ 權重 × 特徵值。權重為 0 代表在訓練資料中沒有幫助（被符號限制或 L2 壓到 0）。", '',
                  '|特徵|說明|權重|', '|---|---|---:|']
        for name, weight in sorted(final['weights'].items(), key=lambda item: -abs(item[1])):
            lines.append(f"|{name}|{FEATURE_ZH.get(name, '')}|{weight:+.3f}|")
        lines += ['', f"Conformal 門檻（α = {q['alpha']}，校準資料 {q['n_fraud']} 筆詐騙、{q['n_normal']} 筆非詐騙）：", '',
                  f"- 融合分數 ≥ {1 - q['q_fraud']:.1%}：保留「詐騙」",
                  f"- 融合分數 ≤ {q['q_normal']:.1%}：保留「正常」",
                  f"- 只剩「詐騙」→ 疑似詐騙；只剩「正常」→ 未發現明顯詐騙跡象；兩者都保留 → 需要人工查證"]
    lines += ['', '## 行為測試（人工合成句，只測穩健性，不是準確率）', '',
              '|測試類型|句數|MacBERT 原始通過率|本系統通過率|本系統判錯（非 Unknown）|', '|---|---:|---:|---:|---:|']
    for kind, s in (results.get('behavioral') or {}).get('summary', {}).items():
        lines.append(f"|{BEHAVIOR_ZH.get(kind, kind)}|{s['n']}|{pct(s['macbert_raw_pass'])}|{pct(s['system_pass'])}|{pct(s['system_wrong'])}|")
    d = results.get('domain_eval')
    if d:
        lines += ['', '## 網域模組（離線，python eval_domains.py）', '',
                  f"- 仿冒規則（不含清單）在 165 涉詐網域抽樣 {d['fraud_sample']} 筆的偵測率：{pct(d['rules_only_recall'])}；"
                  f"{d['normal_domains']} 個知名正常網域誤報率：{pct(d['rules_only_fpr'])}（含 165 清單：{pct(d['blocklist_fpr'])}）。",
                  f"- 時間切分：只用 {d['newest_month']} 之前的清單，查最新一個月新增網域，命中率 {pct(d['temporal_blocklist_recall_newest_month'])}。"
                  '新網域幾乎都查不到，必須靠頁面內容判斷；165 清單的價值在於直接攔截已知網域。',
                  '- 165 網域常見頂級域名：' + '、'.join(f'.{t}（{n}）' for t, n in d['top_tlds_165'][:6])]
    lines += ['', '## 分布外（OOD）偵測', '', '|MacBERT|測試集|OOD 比例|OOD 案例錯誤率|非 OOD 錯誤率|', '|---|---|---:|---:|---:|']
    for model, sets in (results.get('ood') or {}).items():
        for name, r in sets.items():
            lines.append(f"|{model}|{name}|{pct(r['flagged_rate'])}|{pct(r['error_rate_flagged'])}|{pct(r['error_rate_unflagged'])}|")
    lines += ['', '## 限制', ''] + ['- ' + text for text in results['limitations']]
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'data' / 'dataset' / 'manifest.jsonl')
    parser.add_argument('--scores', type=Path, default=ROOT / 'artifacts' / 'dataset')
    parser.add_argument('--alpha', type=float, default=0.1)
    parser.add_argument('--no-behavioral', action='store_true')
    parser.add_argument('--model-path', type=Path, default=ROOT / 'anti_fraud_E3_macbert')
    args = parser.parse_args()
    manifest = load(args.manifest)
    base, llm = load(args.scores / 'base.jsonl'), load(args.scores / 'llm.jsonl')
    cases = assemble(manifest, base, llm)
    known_scam_features(cases, base)
    official = [c for c in cases if c['label_kind'] == 'official_verdict']
    hard = [c for c in cases if c['label_kind'] != 'official_verdict']
    train = [c for c in cases if c['split'] == 'train']
    train_official_only = [c for c in train if c['label_kind'] == 'official_verdict']
    calib = [c for c in cases if c['split'] == 'calibration']
    test = [c for c in official if c['split'] == 'test']
    hard_test = [c for c in hard if c['split'] == 'test']
    methods = [evaluate_method('macbert_argmax', test, hard_test, macbert_argmax,
                               lambda c: c['evidence']['text_model']['fraud_confidence']),
               evaluate_method('legacy_agreement', test, hard_test, legacy)]
    models = {}
    for name, groups in ABLATIONS:
        model = fit_variant(groups, train, calib, args.alpha)
        models[name] = model
        prob = lambda c, m=model: fusion_model.score(m, fusion_model.vectorize(c['evidence']))
        result = evaluate_method(name, test, hard_test, lambda c, p=prob: 'Fraud' if p(c) > 0.5 else 'Normal', prob)
        labels = [int(c['label'] == 'Fraud') for c in test]
        probabilities = [prob(c) for c in test]
        result['conformal_test'] = conformal.evaluate(probabilities, labels, model['conformal'])
        conformal_predictions = [conformal.predict(p, model['conformal'])['prediction'] for p in probabilities]
        result['conformal_test']['f1_with_unknown'] = binary_metrics(labels, conformal_predictions)['f1']
        hard_conf = [conformal.predict(prob(c), model['conformal'])['prediction'] for c in hard_test]
        result['conformal_test']['hard_negative_fpr'] = sum(p == 'Fraud' for p in hard_conf) / len(hard_conf) if hard_conf else None
        result['weights'] = dict(zip(model['feature_names'], model['weights']))
        methods.append(result)
    # Hard-negative augmentation ablation: same full model trained without PTT posts.
    no_hard = fit_variant(ABLATIONS[4][1], train_official_only, calib, args.alpha)
    prob = lambda c: fusion_model.score(no_hard, fusion_model.vectorize(c['evidence']))
    extra = evaluate_method('lr_full_without_hard_negatives', test, hard_test, lambda c: 'Fraud' if prob(c) > 0.5 else 'Normal', prob)
    extra['label_zh'] = '完整（訓練不含困難負樣本）'
    methods.append(extra)

    # Full model evaluated with the LLM switched off at test time (teammates without Ollama).
    full = models['lr_full']
    prob = lambda c: fusion_model.score(full, fusion_model.vectorize(without_llm(c['evidence'])))
    extra = evaluate_method('lr_full_llm_off', test, hard_test, lambda c: 'Fraud' if prob(c) > 0.5 else 'Normal', prob)
    extra['label_zh'] = '完整（推論時未啟用 LLM）'
    methods.append(extra)

    # Domain-adapted MacBERT candidate (finetune_macbert.py), if its scores exist.
    ft_scores = load(args.scores / 'base_ft.jsonl')
    ft_model, ft_cases = None, []
    if ft_scores:
        ft_cases = []
        for case in cases:
            row = ft_scores.get(case['case_id'])
            if not row:
                continue
            text_model, ocr_model = row['text_model'], row['ocr_model']
            if text_model is None and ocr_model is not None:
                text_model, ocr_model = ocr_model, None
            if text_model is None:
                continue
            ft_cases.append({**case, 'evidence': {**case['evidence'], 'text_model': text_model, 'ocr_model': ocr_model}})
        ft_train = [c for c in ft_cases if c['split'] == 'train']
        ft_calib = [c for c in ft_cases if c['split'] == 'calibration']
        ft_test = [c for c in ft_cases if c['split'] == 'test' and c['label_kind'] == 'official_verdict']
        ft_hard = [c for c in ft_cases if c['split'] == 'test' and c['label_kind'] != 'official_verdict']
        extra = evaluate_method('macbert_ft_argmax', ft_test, ft_hard, macbert_argmax,
                                lambda c: c['evidence']['text_model']['fraud_confidence'])
        extra['label_zh'] = 'MacBERT 社群領域微調（argmax）'
        methods.append(extra)
        # Controlled recipe comparison (compare_recipes.py): same test cases, MacBERT trained on the old Threads-heavy set.
        old_scores = load(args.scores / 'base_ft_old_recipe.jsonl')
        if old_scores:
            old_test = []
            for case in ft_test:
                row = old_scores.get(case['case_id']) or {}
                model_ = row.get('text_model') or row.get('ocr_model')
                if model_:
                    old_test.append({**case, 'evidence': {**case['evidence'], 'text_model': model_}})
            if len(old_test) == len(ft_test):
                extra = evaluate_method('macbert_ft_old_recipe_argmax', old_test, [], macbert_argmax,
                                        lambda c: c['evidence']['text_model']['fraud_confidence'])
                extra['label_zh'] = 'MacBERT 社群微調・舊作法（只取最新 900 筆詐騙，Threads 為主）'
                methods.append(extra)
        ft_model = fit_variant(ABLATIONS[4][1], ft_train, ft_calib, args.alpha)
        prob = lambda c: fusion_model.score(ft_model, fusion_model.vectorize(c['evidence']))
        extra = evaluate_method('lr_full_ft', ft_test, ft_hard, lambda c: 'Fraud' if prob(c) > 0.5 else 'Normal', prob)
        extra['label_zh'] = '完整 + 微調 MacBERT'
        extra['weights'] = dict(zip(ft_model['feature_names'], ft_model['weights']))
        extra['bias'] = ft_model['bias']
        extra['conformal_model'] = ft_model['conformal']
        labels = [int(c['label'] == 'Fraud') for c in ft_test]
        probabilities = [prob(c) for c in ft_test]
        extra['conformal_test'] = conformal.evaluate(probabilities, labels, ft_model['conformal'])
        extra['conformal_test']['f1_with_unknown'] = binary_metrics(
            labels, [conformal.predict(p, ft_model['conformal'])['prediction'] for p in probabilities])['f1']
        methods.append(extra)
        loose = fit_variant(ABLATIONS[4][1], ft_train, ft_calib, args.alpha, signed=False)
        prob = lambda c: fusion_model.score(loose, fusion_model.vectorize(c['evidence']))
        extra = evaluate_method('lr_full_ft_unsigned', ft_test, ft_hard, lambda c: 'Fraud' if prob(c) > 0.5 else 'Normal', prob)
        extra['label_zh'] = '完整 + 微調（無符號限制）'
        extra['weights'] = dict(zip(loose['feature_names'], loose['weights']))
        methods.append(extra)

    loose = fit_variant(ABLATIONS[4][1], train, calib, args.alpha, signed=False)
    prob = lambda c: fusion_model.score(loose, fusion_model.vectorize(c['evidence']))
    extra = evaluate_method('lr_full_unsigned', test, hard_test, lambda c: 'Fraud' if prob(c) > 0.5 else 'Normal', prob)
    extra['label_zh'] = '完整（無符號限制）'
    methods.append(extra)

    final = models['lr_full']
    # Held-out numbers stored with the model so the website's "系統實測成效" always matches the deployed weights.
    headline = lambda m: {'test_metrics': m['official_test'], 'auc': m.get('auc'), 'conformal_test': m.get('conformal_test'),
                          'hard_negative_fpr': m.get('hard_negative_fpr'), 'by_platform': m.get('by_platform')}
    final['disagreement'] = disagreement([c for c in calib if c['label_kind'] == 'official_verdict'])
    if ft_model:
        ft_model['disagreement'] = disagreement([c for c in ft_cases if c['split'] == 'calibration' and c['label_kind'] == 'official_verdict'])
    sha = next(iter(base.values()))['model_sha256']
    final.update(version='fusion-v1', trained_on={
        'dataset': 'MODA fraudbuster official verdicts + PTT hard negatives', 'train_cases': len(train),
        'calibration_cases': len([c for c in calib if c['label_kind'] == 'official_verdict']),
        **headline(next(m for m in methods if m['name'] == 'lr_full')), 'macbert_sha256': sha,
        'generated_at': datetime.now(timezone.utc).isoformat()})
    fusion_model.MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    fusion_model.MODEL_PATH.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding='utf-8')
    if ft_model:
        ft_model.update(version='fusion-v1-social', text_model_path='models/macbert_social', trained_on={
            **final['trained_on'], **headline(next(m for m in methods if m['name'] == 'lr_full_ft')),
            'macbert': 'models/macbert_social (fine-tuned on train split)',
            # run_pipeline warns when the downloaded MacBERT is not the one these weights were fitted with
            'text_model_sha256': fingerprint(ROOT / 'models' / 'macbert_social').get('model.safetensors')})
        fusion_model.SOCIAL_MODEL_PATH.write_text(json.dumps(ft_model, ensure_ascii=False, indent=2), encoding='utf-8')

    results = {'generated_at': datetime.now(timezone.utc).isoformat(), 'alpha': args.alpha, 'macbert_sha256': sha,
               'data': {'official': dict(Counter(f"{c['split']}/{c['label']}" for c in official)),
                        'hard_negative': dict(Counter(c['split'] for c in hard)),
                        'official_groups': len({c['group_id'] for c in official})},
               'methods': methods,
               'limitations': [
                   '官方資料是民眾通報後經主管機關判定的案例，不是隨機抽樣；正常樣本多為「被懷疑但判定非詐騙」的廣告，比一般貼文更難。',
                   '訓練與測試採類別平衡權重；實際上線的詐騙盛行率遠低於此，可用 conformal.adjust_prior 換算。',
                   '通報網站遮蔽了原始網址，因此這份基準評估的是貼文文字＋圖片，不是完整的網址擷取流程。',
                   'PTT 困難負樣本是「推定正常」，未經官方判定，只用來量測誤報，不混入官方 F1。',
                   '原 MacBERT 的訓練資料未知，無法排除與通報資料的重疊。']}
    from inference import FraudDetector
    import ood
    social = ROOT / 'models' / 'macbert_social'
    for model_dir, cases_, path, model in [(args.model_path, cases, ood.PATH, final)] + (
            [(social, ft_cases, ood.PATH.with_name('ood_reference_social.json'), ft_model)] if ft_model else []):
        detector = FraudDetector(model_dir)
        text = lambda c: c.get('text') or '\n'.join(base[c['case_id']].get('ocr_texts') or [])
        train_v = ood.embed(detector, [text(c) for c in cases_ if c['split'] == 'train'])
        calib_v = ood.embed(detector, [text(c) for c in cases_ if c['split'] == 'calibration'])
        reference = ood.fit(train_v, calib_v)
        path.write_text(json.dumps(reference), encoding='utf-8')
        report = {}
        for name, subset in (('official_test', [c for c in cases_ if c['split'] == 'test' and c['label_kind'] == 'official_verdict']),
                             ('hard_negative_test', [c for c in cases_ if c['split'] == 'test' and c['label_kind'] != 'official_verdict'])):
            distances = ood.distance(reference, ood.embed(detector, [text(c) for c in subset]))
            flagged = distances > reference['threshold']
            wrong = [('Fraud' if fusion_model.score(model, fusion_model.vectorize(c['evidence'])) > .5 else 'Normal') != c['label']
                     for c in subset]
            report[name] = {'n': len(subset), 'flagged_rate': float(flagged.mean()) if len(subset) else None,
                            'error_rate_flagged': float(np.mean([w for w, f in zip(wrong, flagged) if f])) if flagged.any() else None,
                            'error_rate_unflagged': float(np.mean([w for w, f in zip(wrong, flagged) if not f])) if (~flagged).any() else None}
        results.setdefault('ood', {})[str(model_dir.name)] = report
    if not args.no_behavioral:
        import llm_evidence
        # Evaluate the configuration the live pipeline deploys (fine-tuned MacBERT + its fusion weights when present).
        results['behavioral'] = behavioral(FraudDetector(args.model_path), ft_model or final, llm_evidence.available(),
                                           FraudDetector(social) if ft_model else None)
        results['behavioral']['system'] = 'macbert_social + fusion_model_social' if ft_model else 'original + fusion_model'
    out = ROOT / 'artifacts' / 'experiments'
    out.mkdir(parents=True, exist_ok=True)
    if (out / 'domain_eval.json').exists():  # written by eval_domains.py; kept in results so the report needs no local file
        results['domain_eval'] = json.loads((out / 'domain_eval.json').read_text(encoding='utf-8'))
    (out / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    # Tracked copy: the single source of truth for README, docs/experiment_results.md and the website metrics.
    (ROOT / 'docs' / 'experiment_results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    (ROOT / 'docs' / 'experiment_results.md').write_text(report_markdown(results), encoding='utf-8')
    update_readme(results)
    print(report_markdown(results))


if __name__ == '__main__':
    main()
