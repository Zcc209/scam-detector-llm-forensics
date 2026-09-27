"""Learned evidence fusion (stacking) with conformal abstention.

Replaces "any DOM/OCR disagreement -> Unknown" by a logistic regression over reliability-aware
evidence: MacBERT logits of the account text and of image-only OCR text, account/tactic rules,
LLM-extracted tactics and image forensics. Weights are fitted on the train split of officially
adjudicated cases, conformal thresholds on the calibration split, and metrics on the untouched
test split (see run_experiments.py). Every decision lists per-feature contributions (w * x).
"""
import json
import math
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

from account_signals import FEATURES as ACCOUNT_FEATURES
import conformal

MODEL_PATH = Path(__file__).resolve().parent / 'models' / 'fusion_model.json'
SOCIAL_MODEL_PATH = MODEL_PATH.with_name('fusion_model_social.json')
LLM_FEATURES = ['llm_tactic_count', 'llm_solicitation', 'llm_addresses_reader', 'llm_risk', 'llm_benign_act']
IMAGE_FEATURES = ['image_known_scam_match', 'image_brand_mismatch', 'image_editor_tag']
GROUPS = {
    'text': ['text_logit', 'text_missing'],
    'ocr': ['ocr_logit', 'ocr_missing'],
    'account': ACCOUNT_FEATURES,
    'llm': LLM_FEATURES + ['llm_missing'],
    'image': IMAGE_FEATURES,
}
LABELS_ZH = {
    'text_logit': 'MacBERT（帳號文字）', 'ocr_logit': 'MacBERT（圖片內文字）', 'text_missing': '沒有可用帳號文字',
    'ocr_missing': '沒有圖片內文字', 'off_platform_contact': '引導站外聯絡', 'short_link': '短網址',
    'guaranteed_return': '保證獲利／高報酬', 'investment_lure': '投資招攬用語', 'crypto_or_payment': '匯款／虛擬貨幣',
    'urgency': '急迫用語', 'free_giveaway': '免費贈送／中獎', 'job_lure': '輕鬆高薪兼職', 'impersonation_claim': '提及官方或機構',
    'verified_badge': '已驗證標章', 'throwaway_profile': '低粉絲新帳號', 'large_audience': '大量粉絲',
    'random_digit_handle': '隨機數字帳號名', 'simplified_chinese': '簡體字比例高', 'llm_tactic_count': 'LLM 抽出的詐騙手法數',
    'llm_solicitation': 'LLM：招攬讀者', 'llm_addresses_reader': 'LLM：直接對讀者喊話', 'llm_risk': 'LLM 證據風險等級',
    'llm_benign_act': 'LLM：討論／新聞／分享', 'llm_missing': 'LLM 未執行', 'image_known_scam_match': '與已確認詐騙圖片相似',
    'image_brand_mismatch': '品牌名稱與網域不符', 'image_editor_tag': '圖片含編修軟體標記',
}


def _logit(result):
    if not result or result.get('status') != 'SUCCESS':
        return None
    value = (result.get('score_provenance') or {}).get('logit_difference')
    if value is None and isinstance(result.get('fraud_confidence'), (int, float)):
        p = min(max(result['fraud_confidence'], 1e-6), 1 - 1e-6)
        value = math.log(p / (1 - p))
    return value


def vectorize(evidence):
    """evidence: {'text_model', 'ocr_model', 'account', 'llm', 'image'} -> {feature: value}."""
    x = {}
    for key, name in (('text_model', 'text'), ('ocr_model', 'ocr')):
        value = _logit(evidence.get(key))
        x[f'{name}_logit'] = max(-8.0, min(8.0, value)) / 4 if value is not None else 0.0
        x[f'{name}_missing'] = float(value is None)
    account = (evidence.get('account') or {}).get('features') or {}
    x.update({name: float(account.get(name, 0)) for name in ACCOUNT_FEATURES})
    llm = evidence.get('llm') or {}
    features = llm.get('features') if llm.get('status') == 'SUCCESS' else None
    for name in LLM_FEATURES:
        value = float((features or {}).get(name, 0))
        x[name] = min(value, 4) / 2 if name in ('llm_tactic_count', 'llm_risk') else value
    x['llm_missing'] = float(features is None)
    image = (evidence.get('image') or {}).get('features') or {}
    x.update({name: float(image.get(name, 0)) for name in IMAGE_FEATURES})
    return x


def feature_names(groups):
    return [name for group in groups for name in GROUPS[group]]


# Domain-knowledge sign constraints: risk evidence may only raise the Fraud score, benign evidence only lower it.
# Without them the official data teaches confounds (e.g. Threads engagement-bait scams read as "personal sharing",
# so "benign-looking" would get a positive weight) that do not transfer to ordinary posts.
NEGATIVE = {'verified_badge', 'large_audience', 'llm_benign_act'}
FREE = {'text_missing', 'ocr_missing', 'llm_missing'}


def bounds_for(names):
    return [(None, None) if n in FREE else (None, 0.0) if n in NEGATIVE else (0.0, None) for n in names]


def fit(rows, labels, weights, names, l2=1.0, signed=True):
    X = np.array([[row[name] for name in names] for row in rows])
    y, w = np.array(labels, dtype=float), np.array(weights, dtype=float)
    w = w / w.sum() * len(w)

    def objective(params):
        z = X @ params[:-1] + params[-1]
        p = 1 / (1 + np.exp(-z))
        loss = np.sum(w * (np.logaddexp(0, z) - y * z)) + l2 * np.sum(params[:-1] ** 2) / 2
        grad = np.append(X.T @ (w * (p - y)) + l2 * params[:-1], np.sum(w * (p - y)))
        return loss, grad

    bounds = bounds_for(names) + [(None, None)] if signed else None
    result = minimize(objective, np.zeros(len(names) + 1), jac=True, method='L-BFGS-B', bounds=bounds)
    return {'feature_names': names, 'weights': result.x[:-1].tolist(), 'bias': float(result.x[-1]), 'l2': l2,
            'sign_constrained': signed, 'converged': bool(result.success)}


def score(model, x):
    z = model['bias'] + sum(w * x[name] for name, w in zip(model['feature_names'], model['weights']))
    return 1 / (1 + math.exp(-z))


def contributions(model, x):
    items = [{'feature': name, 'label': LABELS_ZH.get(name, name), 'value': x[name], 'contribution': w * x[name]}
             for name, w in zip(model['feature_names'], model['weights']) if abs(w * x[name]) >= 0.05]
    return sorted(items, key=lambda item: -abs(item['contribution']))


def select(requested, force_original=False):
    """Use the domain-adapted MacBERT and its fusion weights only when both exist (weights are model-specific)."""
    root = Path(__file__).resolve().parent
    social = root / 'models' / 'macbert_social'
    if (not force_original and Path(requested).resolve() == (root / 'anti_fraud_E3_macbert').resolve()
            and (social / 'config.json').is_file() and SOCIAL_MODEL_PATH.is_file()):
        return social, SOCIAL_MODEL_PATH
    return Path(requested), MODEL_PATH


def load(path=MODEL_PATH):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def decide(evidence, model=None):
    model = model or load()
    if not model:
        return {'status': 'unavailable', 'reason': 'models/fusion_model.json not found; run run_experiments.py'}
    x = vectorize(evidence)
    p = score(model, x)
    result = conformal.predict(p, model['conformal']) if model.get('conformal') else {
        'set': [], 'prediction': 'Fraud' if p > 0.5 else 'Normal', 'basis': 'argmax_no_conformal'}
    return {'status': 'SUCCESS', 'fraud_score': p, 'score_type': 'stacked_logistic_fitted_on_labeled_cases',
            'prediction': result['prediction'], 'prediction_set': result['set'], 'basis': result['basis'],
            'alpha': (model.get('conformal') or {}).get('alpha'), 'features': x,
            'conformal': {k: (model.get('conformal') or {}).get(k) for k in ('q_fraud', 'q_normal', 'n_fraud', 'n_normal')},
            'disagreement': model.get('disagreement'),
            'contributions': contributions(model, x), 'model_version': model.get('version'),
            'trained_on': model.get('trained_on'),
            'note': ('Score fitted on officially adjudicated social-media cases (balanced sample); '
                     'Unknown = conformal prediction set is not a single class.')}
