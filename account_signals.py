"""Account-level and tactic signals that a keyword-sensitive text classifier does not capture.

Each signal keeps the exact matched text so a reviewer can verify it. The hand-set weights only
order the evidence for display; fusion_model.py learns the real weights from labeled cases.
"""
import re

from opencc import OpenCC

_S2T = OpenCC('s2t')
NUMBER = r'(\d[\d,.]*)\s*([kKmM萬千]?)'

PATTERNS = {
    'off_platform_contact': (r'(line\s*(?:id|🆔)?\s*[:：]?\s*@?[a-z0-9_.\-]{4,}|🆔\s*[:：]?\s*@?[a-z0-9_.\-]{4,}|加\s*(?:賴|line|LINE)|'
                             r'line\.me/\S+|lin\.ee/\S+|t\.me/\S+|telegram|wa\.me/\S+|whatsapp|微信|vx\s*[:：]|wechat)', 2.0),
    'short_link': (r'(tinyurl\.com|bit\.ly|reurl\.cc|pse\.is|lihi\d?\.\w+|0rz\.tw|is\.gd|cutt\.ly|shorturl\.at|t\.cn|goo\.su|ppt\.cc)', 1.5),
    'guaranteed_return': (r'(保證(?:獲利|賺|回本|收益)|穩賺|零風險|保本|包賺|月入\s*\d|日賺|日入|翻倍|報酬率\s*\d+\s*%|賺到\s*\d+\s*倍)', 2.0),
    'investment_lure': (r'(飆股|老師帶|帶單|報牌|選股|投顧|熱搜名單|股票群|投資群|內線|主力|抄底|漲停)', 1.0),
    'crypto_or_payment': (r'(usdt|泰達幣|虛擬貨幣|加密貨幣|幣安|入金|出金|匯款|轉帳|儲值|點數卡)', 1.0),
    'urgency': (r'(限時|名額有限|最後\s*\d+\s*(?:名|位|天)|倒數|立即|馬上|錯過|今天就)', 0.5),
    'free_giveaway': (r'(免費(?:索取|領取|送|加入)|限量贈送|抽獎|中獎|領取獎|0\s*元)', 1.0),
    'job_lure': (r'(在家(?:工作|兼職)|輕鬆賺|日領|打字員|按讚賺|代購兼職|高薪輕鬆|無經驗可|時薪\s*\d{4})', 1.5),
    'impersonation_claim': (r'(官方客服|金管會|證交所|中華郵政|165|警察局|檢察官|法院|台積電|國稅局|銀行客服|蝦皮客服)', 1.0),
    'verified_badge': (r'(已驗證|verified)', -1.0),
}


def _count(value, unit):
    number = float(value.replace(',', ''))
    return number * {'k': 1e3, 'K': 1e3, 'm': 1e6, 'M': 1e6, '萬': 1e4, '千': 1e3}.get(unit, 1)


def profile_counts(text):
    found = {}
    for key, words in (('followers', r'(?:followers|粉絲|位粉絲|追蹤者)'), ('following', r'(?:following|追蹤中)'),
                       ('posts', r'(?:posts|threads|篇貼文|則貼文|貼文)')):
        match = re.search(NUMBER + r'\s*' + words, text, re.I) or re.search(words + r'\s*[:：]?\s*' + NUMBER, text, re.I)
        if match:
            found[key] = _count(*match.groups()[:2])
    return found


def extract(text, handle=None):
    text = text or ''
    signals = []
    for name, (pattern, weight) in PATTERNS.items():
        match = re.search(pattern, text, re.I)
        if match:
            signals.append({'signal': name, 'weight': weight, 'evidence': match.group(0).strip()[:80]})
    counts = profile_counts(text)
    if 'followers' in counts and counts['followers'] <= 20 and counts.get('posts', 0) <= 10:
        signals.append({'signal': 'throwaway_profile', 'weight': 1.5,
                        'evidence': f"followers={counts['followers']:.0f}, posts={counts.get('posts', 0):.0f}"})
    if counts.get('followers', 0) >= 100_000:
        signals.append({'signal': 'large_audience', 'weight': -1.0, 'evidence': f"followers={counts['followers']:.0f}"})
    handles = [handle] if handle else re.findall(r'@([a-z0-9_.]{3,30})', text, re.I)
    random_handle = next((h for h in handles if re.search(r'\d{5,}', h)), None)
    if random_handle:
        signals.append({'signal': 'random_digit_handle', 'weight': 1.0, 'evidence': '@' + random_handle})
    han = re.findall(r'[一-鿿]', text)
    if len(han) >= 20:
        changed = sum(a != b for a, b in zip(''.join(han), _S2T.convert(''.join(han))))
        if changed / len(han) >= 0.08:
            signals.append({'signal': 'simplified_chinese', 'weight': 0.5, 'evidence': f'{changed}/{len(han)} characters'})
    score = sum(item['weight'] for item in signals)
    return {'signals': signals, 'profile_counts': counts, 'rule_score': score,
            'features': {name: int(any(s['signal'] == name for s in signals)) for name in FEATURES},
            'note': 'Rule evidence for review; weights are display order, not probabilities.'}


FEATURES = [*PATTERNS, 'throwaway_profile', 'large_audience', 'random_digit_handle', 'simplified_chinese']
