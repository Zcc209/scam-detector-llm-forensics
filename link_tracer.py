"""Dynamic behaviour: follow the links a page (or a screenshot) points to and judge where they really land.

Scams often look harmless until the click: an Instagram bio link that bounces through a shortener
to a LINE "stock tips" group, or to a one-page shop. For up to N candidate links (page anchors,
link-in-bio, and URLs read from the screenshot by OCR) this reuses web_capture.capture, which
checks every document hop against the 165 list / lookalike rules *before* loading it, blocks
downloads and pop-ups, and records the redirect chain, final URL, landing text and screenshot.
The heuristics below are hand-written rules; no labeled redirect dataset exists to validate them.
"""
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from domain_check import SOCIAL_DOMAINS, analyze_url

WRAPPERS = {'l.instagram.com': 'u', 'l.facebook.com': 'u', 'lm.facebook.com': 'u', 'l.threads.net': 'u',
            'l.threads.com': 'u', 'www.youtube.com': 'q', 'out.reddit.com': 'url'}
SHORTENERS = ('bit.ly', 'tinyurl.com', 'reurl.cc', 'pse.is', 'lihi.cc', 'lihi1.com', 'lihi2.com', 'lihi3.com', '0rz.tw',
              'is.gd', 'cutt.ly', 'shorturl.at', 't.cn', 'goo.su', 'ppt.cc', 'rb.gy', 'tiny.cc', 'linktr.ee', 'lin.ee', 'bio.link')
MESSAGING = {'line.me': 'LINE', 'lin.ee': 'LINE', 't.me': 'Telegram', 'telegram.me': 'Telegram', 'wa.me': 'WhatsApp',
             'api.whatsapp.com': 'WhatsApp', 'chat.whatsapp.com': 'WhatsApp', 'u.wechat.com': 'WeChat'}
# Most common TLDs in the 165 fraud-website open data (see eval_domains.py).
RISKY_TLDS = ('shop', 'top', 'cc', 'site', 'xyz', 'asia', 'store', 'online', 'icu', 'vip', 'buzz', 'cyou', 'sbs')
PLATFORM_INTERNAL = ('instagram.com', 'facebook.com', 'fb.com', 'meta.com', 'threads.net', 'threads.com', 'messenger.com',
                     'fbcdn.net', 'cdninstagram.com', 'whatsapp.com', 'oculus.com', 'x.com', 'twitter.com', 'dcard.tw',
                     'youtube.com', 'google.com', 'apple.com', 'tiktok.com', 'ptt.cc')
URL_TEXT = re.compile(r'(?<![@\w.])((?:https?://)?(?:[a-z0-9-]+\.)+(?:com|net|org|tw|cc|top|shop|xyz|site|vip|me|ee|ly|io|co|gd|at|'
                      r'info|online|store|asia|icu|app|link|cn|hk|buzz|cyou|sbs)(?:/[^\s，。、）)」」"\'<>]*)?)', re.I)
SHOP_WORDS = ('貨到付款', '限時', '倒數', '特價', '原價', '折扣', '下單', '立即購買', '搶購', '僅剩', '庫存', '免運', '七天鑑賞', '買一送一')
COMPANY_WORDS = ('統一編號', '統編', '公司地址', '營業登記', '客服電話', '退貨政策', '隱私權政策')
# Brand words that scammers put next to (or inside) a fake URL, with the brand's official domains.
BRAND_TOKENS = {
    'myship': ('7-11賣貨便', ('7-11.com.tw',)), '賣貨便': ('7-11賣貨便', ('7-11.com.tw',)), '7-11': ('7-ELEVEN', ('7-11.com.tw', 'openpoint.com.tw')),
    'shopee': ('蝦皮', ('shopee.tw', 'shopee.com')), '蝦皮': ('蝦皮', ('shopee.tw', 'shopee.com')), '全家': ('全家', ('family.com.tw',)),
    '好賣+': ('全家好賣+', ('family.com.tw',)), 'momoshop': ('momo', ('momoshop.com.tw',)), 'pchome': ('PChome', ('pchome.com.tw',)),
    't-cat': ('黑貓宅急便', ('t-cat.com.tw',)), '黑貓': ('黑貓宅急便', ('t-cat.com.tw',)), '郵局': ('中華郵政', ('post.gov.tw',)),
    '中華郵政': ('中華郵政', ('post.gov.tw',)), 'ctbc': ('中國信託', ('ctbcbank.com',)), 'cathaybk': ('國泰世華', ('cathaybk.com.tw',)),
    '國泰世華': ('國泰世華', ('cathaybk.com.tw',)), 'esunbank': ('玉山銀行', ('esunbank.com', 'esunbank.com.tw')), 'etag': ('遠通電收', ('fetc.net.tw',)),
    'mvdis': ('監理服務', ('mvdis.gov.tw',)), '監理站': ('監理服務', ('mvdis.gov.tw', 'thb.gov.tw')),
}
INVEST_WORDS = ('飆股', '老師', '帶單', '選股', '加入群組', '股票群', '投資群', '領取名單', '免費領取', '穩賺', '保證獲利', '內線')


CONFUSABLE = str.maketrans({'t': '7', 'l': '1', 'i': '1', 'o': '0', 's': '5', 'z': '2', 'b': '8', 'g': '9'})


def ocr_near_official(host, domains):
    """OCR often turns 7-11 into T-ll or drops a dot; a near-exact official domain read from a picture is probably a misread."""
    from difflib import SequenceMatcher
    squash = lambda h: re.sub(r'[^a-z0-9]', '', h.lower()).translate(CONFUSABLE)
    for domain in domains:
        for candidate in (domain, 'myship.' + domain, 'www.' + domain):
            if squash(host) == squash(candidate) or SequenceMatcher(None, squash(host), squash(candidate)).ratio() >= 0.9:
                return candidate
    return None


def host_of(url):
    return (urlsplit(url).hostname or '').lower().removeprefix('www.')


def matches(host, domains):
    return next((d for d in domains if host == d or host.endswith('.' + d)), None)


def unwrap(url):
    """Platform click-tracking wrappers (l.instagram.com/?u=...) hide the real destination."""
    parts = urlsplit(url)
    key = WRAPPERS.get(parts.hostname or '')
    if key and (parts.hostname != 'www.youtube.com' or parts.path == '/redirect'):
        target = parse_qs(parts.query).get(key, [None])[0]
        if target and target.startswith('http'):
            return target
    return url


def candidates(page_url=None, anchors=(), texts=(), limit=4):
    """Outbound links worth following, most suspicious first."""
    page_host = host_of(page_url or '')
    found = {}
    for anchor in anchors or []:
        if anchor.get('visible') is False:
            continue
        found.setdefault(unwrap(anchor['href']), 'page_link')
    context = {}
    for source, text in texts or []:  # (source, text) pairs, e.g. ('screenshot_text', OCR lines)
        for line in (text or '').splitlines():
            for match in URL_TEXT.finditer(line):
                if re.search(r'@\s*$', line[:match.start()]):
                    continue  # e-mail domain, often split by OCR as "business@ dcard.cc"
                raw = match.group(1)
                url = (raw if raw.lower().startswith('http') else 'https://' + raw).rstrip('.,')
                found.setdefault(url, source)
                context.setdefault(url, line.strip()[:200])  # the line the URL appeared on (brand words, OCR splits)
    ranked = []
    for url, source in found.items():
        host = host_of(url)
        if not host or host == page_host or (matches(host, PLATFORM_INTERNAL) and not matches(host, MESSAGING)):
            continue
        priority = 0 if matches(host, SHORTENERS) else 1 if matches(host, MESSAGING) else 2 if host.rsplit('.', 1)[-1] in RISKY_TLDS else 3
        ranked.append((priority, url, source))
    seen, picked = set(), []
    for _, url, source in sorted(ranked):
        if host_of(url) + urlsplit(url).path not in seen:
            seen.add(host_of(url) + urlsplit(url).path)
            picked.append({'url': url, 'source': source, 'context': context.get(url, '')})
    return picked[:limit]


def site_of(host):
    """Who owns a hop: one platform's official domains (lin.ee, line.me, page.line.me) count as one site,
    otherwise the registrable domain (shop.example.com.tw -> example.com.tw)."""
    platform = next((name for name, domains in SOCIAL_DOMAINS.items() if matches(host, domains)), None)
    if platform:
        return platform
    labels = host.split('.')
    size = 3 if len(labels) >= 3 and labels[-2] in ('com', 'net', 'org', 'gov', 'edu', 'co', 'idv') else 2
    return '.'.join(labels[-size:])


def official(host, domains):
    return any(host == d or host.endswith('.' + d) for d in domains)


def judge(start, capture, context='', source=''):
    """Heuristic flags for one followed link. `context` is the text line the URL was found on."""
    checks = capture.get('navigation_checks') or []
    chain = []
    for item in checks:
        url = item.get('normalized_url')
        if url and (not chain or chain[-1] != url):
            chain.append(url)
    final = capture.get('final_url') or (chain[-1] if chain else start)
    if final and (not chain or chain[-1] != final):
        chain.append(final)
    hosts = list(dict.fromkeys(host_of(u) for u in chain if host_of(u)))
    final_host = host_of(final)
    text = (capture.get('full_page_text') or '')[:6000]
    flags = []

    def flag(name, level, evidence):
        flags.append({'signal': name, 'level': level, 'evidence': evidence})
    listed = next((c for c in checks if c.get('listed_165')), None)
    if listed:
        flag('listed_165', 'high', f"{listed['hostname']} 列於 165 涉詐網站公告（民國 {listed['listed_165']} 起），已停止前往")
    lookalike = next((c for c in checks if c.get('possible_impersonated_platform')), None)
    if lookalike:
        flag('lookalike', 'high', f"{lookalike['hostname']} 疑似仿冒 {lookalike['possible_impersonated_platform']}")
    if capture.get('status') == 'blocked' and not (listed or lookalike):
        flag('blocked_hop', 'high', capture.get('error') or '跳轉途中觸發高風險規則')
    short = [h for h in hosts if matches(h, SHORTENERS)]
    if short:
        flag('shortener', 'low', '經過短網址：' + '、'.join(short))
    sites = list(dict.fromkeys(site_of(h) for h in hosts))
    if len(sites) >= 3:
        flag('multi_hop', 'medium', f'跨 {len(sites)} 個不同網站跳轉：' + ' → '.join(hosts))
    messaging = matches(final_host, MESSAGING) or next((matches(h, MESSAGING) for h in hosts if matches(h, MESSAGING)), None)
    if messaging:
        path = urlsplit(final).path.lower()
        group = any(k in path for k in ('/ti/g', '/r/ti/g', 'joinchat', '/+')) or final_host == 'chat.whatsapp.com'
        flag('private_chat', 'medium' if group else 'low', f"導向 {MESSAGING[messaging]} {'群組' if group else '帳號加好友頁'}")
    start_host = host_of(start)
    risky_tld = next((h for h in [final_host, start_host] if h and h.rsplit('.', 1)[-1] in RISKY_TLDS), None)
    if risky_tld:
        flag('risky_tld', 'low', f'網域使用 .{risky_tld.rsplit(".", 1)[-1]}（165 涉詐網站最常見的頂級域名之一）')
    labels = [label for h in {start_host, final_host} if h for label in h.split('.')[:-1]]
    random_label = next((label for label in labels if re.fullmatch(r'(?=[a-z0-9]*\d)[a-z0-9]{1,5}', label)), None)
    if random_label:
        flag('random_host', 'medium' if risky_tld else 'low', f'網址含隨機字串「{random_label}」，常見於大量申請的拋棄式網域')
    for token, (brand, domains) in BRAND_TOKENS.items():
        in_host = any(token in h for h in hosts or [start_host])
        in_context = token in (context or '').lower()
        if (in_host or in_context) and not any(official(h, domains) for h in hosts or [start_host]):
            misread = ocr_near_official(start_host, domains) if source == 'screenshot_text' else None
            if misread:
                flag('ocr_misread', 'low', f'網址 {start_host} 和{brand}官方網址 {misread} 幾乎一樣，可能是 OCR 讀錯，請對照截圖人工確認')
                break
            # Brand inside a foreign hostname is impersonation; a brand word merely next to the link is only
            # treated as impersonation when the domain also looks disposable (risky TLD or random label).
            level = 'high' if in_host or risky_tld or random_label else 'medium'
            where = '網址中' if in_host else '網址旁'
            flag('brand_impersonation', level, f'{where}出現「{brand}」字樣，但網域 {start_host} 不是{brand}官方網域（{"、".join(domains)}）')
            break
    if capture.get('status') == 'error' or (capture.get('status') == 'unusable' and capture.get('unusable_reason') in ('http_error', 'load_error', 'unreachable')):
        flag('unreachable', 'low', '網址目前無法連線（可能已被停止解析、下架，或只在特定裝置開啟）')
    shop = [w for w in SHOP_WORDS if w in text]
    company = [w for w in COMPANY_WORDS if w in text]
    if len(shop) >= 3 and not company:
        flag('one_page_shop', 'medium', f"疑似一頁式購物：出現「{'、'.join(shop[:5])}」，但沒有公司資訊或退貨政策")
    invest = [w for w in INVEST_WORDS if w in text]
    if len(invest) >= 2:
        flag('investment_landing', 'medium', f"落地頁出現投資招攬用語：「{'、'.join(invest[:5])}」")
    levels = [f['level'] for f in flags]
    medium = levels.count('medium')
    risk = 'high' if 'high' in levels else 'medium' if medium >= 2 or (medium and 'low' in levels) else 'low' if levels else 'none'
    return {'url': start, 'chain': chain, 'hosts': hosts, 'final_url': final, 'final_host': final_host,
            'final_title': capture.get('page_title'), 'status': capture.get('status'), 'unusable_reason': capture.get('unusable_reason'),
            'screenshot_path': capture.get('screenshot_path') if capture.get('status') != 'blocked' else None,
            'flags': flags, 'risk': risk, 'landing_text': text[:1500]}


def trace(page_url, anchors, texts, output_dir, limit=4, capture=None, detector=None, budget=150):
    """Follow each candidate link in a fresh guarded browser session."""
    if capture is None:
        from web_capture import capture
    import time
    picked = candidates(page_url, anchors, texts, limit)
    results, started = [], time.monotonic()
    for index, item in enumerate(picked):
        if time.monotonic() - started > budget:
            results.append({**judge(item['url'], {'status': 'skipped', 'navigation_checks': []}, item.get('context', ''), item['source']),
                            'source': item['source'], 'index': index, 'context': item.get('context', ''), 'status': 'skipped'})
            continue
        target = Path(output_dir) / 'links' / str(index)
        target.mkdir(parents=True, exist_ok=True)
        try:
            first = analyze_url(item['url'])
            data = capture(item['url'], target) if first['capture_allowed'] else {
                'status': 'blocked', 'error': first['block_reason'], 'navigation_checks': [first]}
        except Exception as exc:
            data = {'status': 'error', 'error': f'{type(exc).__name__}: {exc}', 'navigation_checks': []}
        judged = judge(item['url'], data, item.get('context', ''), item['source'])
        judged.update(source=item['source'], index=index, context=item.get('context', ''))
        if detector and judged['landing_text'].strip() and data.get('status') == 'success':
            result = detector.predict(judged['landing_text'])
            judged['landing_model'] = {k: result.get(k) for k in ('status', 'prediction', 'fraud_confidence')}
        results.append(judged)
    order = {'high': 3, 'medium': 2, 'low': 1, 'none': 0}
    worst = max((r['risk'] for r in results), key=order.get, default='none')
    return {'status': 'SUCCESS', 'followed': len(results), 'candidates_found': len(picked), 'links': results, 'worst_risk': worst,
            'note': 'Redirect heuristics are hand-written rules, not validated on labeled data; 165-listed hops are never loaded.'}
