"""Explainable URL checks inspired by URL_Analyzer.ipynb (not a fraud oracle)."""
import ipaddress
import re
import socket
from difflib import SequenceMatcher
from urllib.parse import urlsplit, urlunsplit

SOCIAL_DOMAINS = {
    "Facebook": ["facebook.com", "fb.com", "fb.me", "messenger.com"],
    "Instagram": ["instagram.com"],
    "Threads": ["threads.net", "threads.com"],
    "X": ["x.com", "twitter.com", "t.co"],
    "YouTube": ["youtube.com", "youtu.be"],
    "TikTok": ["tiktok.com"], "LINE": ["line.me"],
    "WhatsApp": ["whatsapp.com", "wa.me"],
    "LinkedIn": ["linkedin.com", "lnkd.in"],
    "Discord": ["discord.com", "discord.gg"],
    "Telegram": ["telegram.org", "t.me"],
    "Reddit": ["reddit.com", "redd.it"],
    "Snapchat": ["snapchat.com"], "Pinterest": ["pinterest.com", "pin.it"],
    "Weibo": ["weibo.com"], "Dcard": ["dcard.tw"],
    "Plurk": ["plurk.com"], "Bluesky": ["bsky.app"],
}


def normalize_url(value):
    value = value.strip()
    if not value or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        raise ValueError("URL is empty or contains whitespace/control characters/backslashes")
    if "://" not in value:
        value = "https://" + value
    p = urlsplit(value)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("Only HTTP(S) URLs are supported")
    host = p.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if len(host) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", s) for s in host.split(".")):
            raise ValueError("Invalid hostname")
    port = p.port
    if port == 0:
        raise ValueError("Invalid port")
    authority = f"[{host}]" if ":" in host else host
    if port is not None:
        authority += f":{port}"
    return urlunsplit((p.scheme, authority, p.path or "/", p.query, p.fragment)), p, host


def require_public_url(value):
    """防止 SSRF 攻擊：阻擋 localhost、127.0.0.1 及 192.168.x.x 等內網位址"""
    normalized, parsed, host = normalize_url(value)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs containing credentials are blocked")
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                host,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        }
        if not addresses or any(not ipaddress.ip_address(addr).is_global for addr in addresses):
            raise ValueError("Non-public network destinations are blocked")
    except socket.gaierror:
        # DNS 無法解析時，交由後續 Playwright 或網路層處理，不在此處阻擋離線測試
        pass
    return normalized


def analyze_url(value):
    # 1. 啟用 SSRF 防護檢查
    require_public_url(value)

    normalized, parsed, host = normalize_url(value)
    evidence, score, platform, target = [], 0, None, None

    # 比對官方社群網域
    for name, domains in SOCIAL_DOMAINS.items():
        for domain in domains:
            if host == domain or host.endswith("." + domain):
                platform, target = name, domain

    similarity = 0.0
    impersonated = None

    if not platform:
        compact = lambda s: s.translate(str.maketrans("013457", "oieast"))
        for name, domains in SOCIAL_DOMAINS.items():
            for domain in domains:
                brand = domain.split(".")[0]
                if len(brand) < 4:
                    continue
                # 比對網域標籤與官方品牌名的相似度
                ratio = max(SequenceMatcher(None, compact(label), brand).ratio() for label in host.split("."))
                if brand in compact(host) and brand != compact(host):
                    ratio = max(ratio, 0.82)
                if ratio > similarity:
                    similarity, impersonated = ratio, name

        # 2. 提高門檻至 0.80，防止一般單字（如 instantly）被誤判為仿冒
        if similarity >= 0.80:
            score += 65
            evidence.append(f"Hostname resembles '{impersonated}' brand; possible lookalike domain")
        else:
            impersonated = None
            evidence.append("Not a major social platform domain; generic web verification applied")
    else:
        evidence.append("Official-domain match does not authenticate an individual account")

    if parsed.scheme == "http":
        score += 15
        evidence.append("HTTP transport is unencrypted")

    if "xn--" in host:
        score += 15
        evidence.append("Internationalized hostname (Punycode); inspect for homograph attacks")

    credentials = parsed.username is not None or parsed.password is not None
    if credentials:
        score += 35
        evidence.append("URL authority contains user credentials; capture is blocked")

    if parsed.port not in (None, 80, 443):
        score += 10
        evidence.append("Nonstandard port")

    score = min(score, 100)

    # 3. 放寬 capture_allowed：允許一般合法網站進行 OCR/AI 分析
    # 只要「不是疑似仿冒釣魚網站」、「未攜帶帳密憑證」且「風險分未達 60」即放行截圖
    is_capture_allowed = (not impersonated) and (not credentials) and (score < 60)

    return dict(
        normalized_url=normalized,
        hostname=host,
        matched_platform=platform,
        matched_official_domain=target,
        possible_impersonated_platform=impersonated,
        lookalike_similarity=round(similarity, 3),
        risk_score=score,
        risk_level="High" if score >= 60 else "Medium" if score >= 25 else "Low",
        domain_status="official" if platform else "lookalike" if impersonated else "unverified",
        capture_allowed=is_capture_allowed,
        block_reason=(
            "URL contains credentials" if credentials else
            f"Suspected {impersonated} impersonation" if impersonated else
            "High-risk URL" if score >= 60 else None
        ),
        evidence=evidence,
        scoring_note="Heuristic score, not a calibrated fraud probability; low does not mean verified",
    )