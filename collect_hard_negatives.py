"""Collect hard-negative texts: ordinary PTT posts that use scam-adjacent vocabulary.

Boards are chosen for topics the classifier over-reacts to (salary, stocks, crypto, exams,
relationships, deals). Labels are *presumed normal* (ordinary users' discussion posts, not an
official adjudication), so these cases are used to measure/penalize false positives and are
reported separately from the officially labeled benchmark. Whole boards are held out for testing.
"""
import argparse
import hashlib
import html
import json
import re
import time
from pathlib import Path

import requests

BOARDS = ['Stock', 'Salary', 'Tech_Job', 'DigiCurrency', 'Lifeismoney', 'SENIORHIGH', 'Boy-Girl',
          'Bank_Service', 'creditcard', 'e-shopping', 'Finance', 'Insurance', 'WomenTalk', 'Foreign_Inv']
BASE = 'https://www.ptt.cc'
HEADERS = {'User-Agent': 'Mozilla/5.0 (academic anti-fraud project; low-rate crawler)'}


def clean(page):
    body = re.search(r'<div id="main-content"[^>]*>(.*?)<span class="f2">※ 發信站', page, re.S)
    if not body:
        return None, None
    title = re.search(r'<span class="article-meta-tag">標題</span><span class="article-meta-value">(.*?)</span>', page)
    author = re.search(r'<span class="article-meta-tag">作者</span><span class="article-meta-value">(\S+)', page)
    text = re.sub(r'<div class="article-metaline.*?</div>', '', body.group(1), flags=re.S)
    text = html.unescape(re.sub(r'<[^>]+>', '', text))
    text = '\n'.join(line for line in text.splitlines() if line.strip() and not line.startswith(('※', '--', ': ')))
    return (html.unescape(title.group(1)) if title else '') + '\n' + text.strip()[:800], author.group(1) if author else ''


def collect_pushes(session, articles, output, delay, per_sample=3):
    """Short casual lines (PTT comments), a few per sample, like a chat screenshot. Official ads never cover this style."""
    rows = []
    for article in articles:
        time.sleep(delay)
        page = session.get(article['source_url'], headers=HEADERS, timeout=20).text
        lines = [html.unescape(re.sub(r'<[^>]+>', '', text)).lstrip(': ').strip()
                 for text in re.findall(r'<span class="f3 push-content">(.*?)</span>', page)]
        lines = [line for line in lines if len(line) >= 4 and not re.search(r'https?://', line)]
        for start in range(0, min(len(lines), 12) - per_sample + 1, per_sample):
            text = '\n'.join(lines[start:start + per_sample])
            rows.append({'case_id': f"{article['case_id']}-push{start}", 'text': text, 'label': 'Normal',
                         'label_kind': 'presumed_normal_hard_negative_chat', 'board': article['board'], 'group_id': article['group_id'],
                         'label_source': f"PTT {article['board']} 板推文（推定正常，非官方判定）", 'source_url': article['source_url']})
    with Path(output).open('w', encoding='utf-8') as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False) + '\n')
    print(f'{len(rows)} chat-style samples -> {output}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('data/hard_negatives/ptt.jsonl'))
    parser.add_argument('--per-board', type=int, default=25)
    parser.add_argument('--delay', type=float, default=0.4)
    parser.add_argument('--pushes', type=Path, help='instead: collect comment lines from already collected articles into this file')
    args = parser.parse_args()
    session = requests.Session()
    session.cookies.set('over18', '1', domain='www.ptt.cc')
    if args.pushes:
        articles = [json.loads(line) for line in args.output.read_text(encoding='utf-8').splitlines()]
        collect_pushes(session, articles, args.pushes, args.delay)
        return
    seen = set()
    if args.output.exists():
        seen = {json.loads(line)['source_url'] for line in args.output.read_text(encoding='utf-8').splitlines()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('a', encoding='utf-8') as out:
        for board in BOARDS:
            url, links = f'{BASE}/bbs/{board}/index.html', []
            while len(links) < args.per_board * 2 and url:
                time.sleep(args.delay)
                page = session.get(url, headers=HEADERS, timeout=20).text
                links += [BASE + href for href in re.findall(r'<a href="(/bbs/[^/]+/M\.[^"]+\.html)">', page)
                          if 'Re:' not in href]
                previous = re.search(r'<a class="btn wide" href="([^"]+)">&lsaquo; 上頁</a>', page)
                url = BASE + previous.group(1) if previous else None
            kept = 0
            for link in reversed(links):
                if kept >= args.per_board or link in seen:
                    continue
                time.sleep(args.delay)
                text, author = clean(session.get(link, headers=HEADERS, timeout=20).text)
                if not text or len(text) < 40 or text.split('\n', 1)[0].startswith(('[公告]', '[協尋]', '[閒聊] 盤中', '[情報]')):
                    continue
                row = {'case_id': 'ptt-' + hashlib.sha1(link.encode()).hexdigest()[:12], 'text': text, 'label': 'Normal',
                       'label_kind': 'presumed_normal_hard_negative', 'board': board, 'group_id': f'ptt:{board}',
                       'author_hash': hashlib.sha1(author.encode()).hexdigest()[:10],
                       'label_source': f'PTT {board} 板一般使用者文章（推定正常，非官方判定）', 'source_url': link}
                out.write(json.dumps(row, ensure_ascii=False) + '\n')
                seen.add(link)
                kept += 1
            print(board, kept, flush=True)


if __name__ == '__main__':
    main()
