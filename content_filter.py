"""Separate page chrome (navigation, login buttons, trending sidebars) from account-authored content.

The MacBERT model was trained on scam conversations, so platform UI text such as 登入／註冊 or a
forum's trending-title sidebar is out of distribution and was measured to move the Fraud score
(see docs/model_scores.md). The filter is structural first (HTML landmarks recorded by
alignment.VIEWPORT_SCRIPT), lexical second, and never drops a line that carries a contact,
link or money signal, so scam text placed inside a button or header is still scored.
"""
from difflib import SequenceMatcher
import re
import unicodedata

LANDMARKS = {'nav', 'header', 'footer', 'aside', 'navigation', 'banner', 'contentinfo', 'complementary',
             'menu', 'menubar', 'tablist', 'dialog', 'button'}
UI_LINES = {
    '登入', '登錄', '註冊', '立即註冊', '建立新帳號', '忘記密碼', '忘記密碼?', '登出', '首頁', '搜尋', '探索', '通知', '訊息',
    '更多', '查看更多', '顯示更多', '展開', '收合', '分享', '追蹤', '追蹤中', '讚', '留言', '回覆', '收藏', '檢舉', '設定',
    '隱私權政策', '隱私政策', '使用條款', '服務條款', 'cookie政策', '關於', '說明', '幫助中心', '語言', '中文台灣',
    '下載app', '開啟app', '在app中開啟', '使用app開啟', '熱門', '最新', '推薦', '看板', '全部', '貼文', '連續短片', '標註',
    'log in', 'login', 'sign up', 'signup', 'log out', 'home', 'search', 'explore', 'more', 'see more', 'see translation',
    'share', 'follow', 'following', 'like', 'reply', 'comment', 'privacy', 'terms', 'cookies', 'about', 'help',
    'open app', 'get the app', 'meta', 'api', 'jobs', 'blog', 'locations', 'threads', 'reels', 'posts', 'tagged',
}
RISK = re.compile(r'(line\s*id|line\.me|lin\.ee|t\.me|wa\.me|telegram|whatsapp|https?://|www\.|@[a-z0-9_.]{3,}|'
                  r'09\d{2}[-\s]?\d{3}[-\s]?\d{3}|匯款|轉帳|入金|出金|usdt|泰達幣|保證|獲利|穩賺|投資|老師|助理|私訊|加賴|加line|'
                  r'領取|中獎|免費|限時|點擊|連結|驗證|帳戶|解凍|退款)', re.I)


def _key(line):
    return re.sub(r'[\s·•|:：!！?？.。,，、\-_/]+', '', unicodedata.normalize('NFKC', line).lower())


PLATFORM_LOGOS = ('instagram', 'facebook', 'threads', 'dcard', 'youtube', 'tiktok', 'line', 'twitter')


def is_ui_line(line):
    key = _key(line)
    if not key:
        return 'empty'
    if key in {_key(item) for item in UI_LINES}:
        return 'ui_lexicon'
    if any(SequenceMatcher(None, key, logo).ratio() >= 0.8 for logo in PLATFORM_LOGOS):
        return 'platform_logo'
    if re.fullmatch(r'[\d,.]+[kmw萬千]?', key):
        return 'bare_number'
    cjk = len(re.findall(r'[㐀-鿿]', key))
    if cjk <= 1 and len(key) <= 3:
        return 'too_short'
    return None


def filter_lines(lines):
    kept, removed = [], []
    for line in (str(line).strip() for line in lines):
        if not line:
            continue
        reason = None if RISK.search(line) else is_ui_line(line)
        if reason:
            removed.append({'text': line, 'reason': reason})
        else:
            kept.append(line)
    return kept, removed


def filter_viewport(capture):
    """Filter DOM viewport items using their landmark region, then line-level rules."""
    kept, removed = [], []
    for item in (capture or {}).get('items') or []:
        text = item.get('text', '').strip()
        region = item.get('region')
        if region in LANDMARKS and not RISK.search(text):
            removed.append({'text': text, 'reason': f'landmark:{region}'})
            continue
        lines, dropped = filter_lines(text.splitlines())
        kept.extend(lines)
        removed.extend(dropped)
    return summarize(kept, removed, 'viewport_landmarks_and_lexicon')


def filter_text(text):
    kept, removed = filter_lines((text or '').splitlines())
    return summarize(kept, removed, 'line_lexicon_only')


def summarize(kept, removed, method):
    total = len(kept) + len(removed)
    return {'text': '\n'.join(kept), 'kept_lines': len(kept), 'removed': removed, 'method': method,
            'boilerplate_ratio': len(removed) / total if total else 0.0,
            'note': 'Removed lines stay in the report; lines with contact, link or money signals are never removed.'}
