"""Build the group-separated benchmark manifest from collected cases.

Official benchmark: MODA fraud-report cases whose timeline states the reviewing agency's verdict
(Fraud = "確認，這是詐騙訊息", Normal = "確認，這並不是詐騙訊息"). Cases sharing a contact ID,
account handle, link or near-identical text are one group, and a group never spans splits.
Hard negatives: presumed-normal PTT posts, split by *board* so test boards are unseen topics.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random
import re
import unicodedata

TEST_BOARDS = {'Salary', 'SENIORHIGH', 'DigiCurrency', 'Boy-Girl', 'e-shopping'}
CALIBRATION_BOARDS = {'Insurance', 'Foreign_Inv'}
PLACEHOLDER = re.compile(r'^(疑似)?詐騙訊息[，,]?(已通知\S*移除)?$|^高風險訊息')


def norm(text):
    return re.sub(r'\W+', '', unicodedata.normalize('NFKC', text or '').lower())


def keys(case):
    text = case.get('text') or ''
    found = {'text:' + norm(text)[:60]} if len(norm(text)) >= 12 else set()
    found |= {'contact:' + m.lower() for m in re.findall(r'(?:line\s*(?:id|🆔)?\s*[:：]?\s*@?|🆔\s*[:：]?\s*@?)([a-z0-9_.\-]{4,})', text, re.I)}
    found |= {'contact:' + m.lower() for m in re.findall(r'(?:line\.me/ti/p/|lin\.ee/|t\.me/|wa\.me/)([\w\-~%]+)', text, re.I)}
    found |= {'handle:' + m.lower() for m in re.findall(r'@([a-z0-9_.]{4,30})', text, re.I)}
    found |= {'link:' + m.lower() for m in re.findall(r'https?://([^\s/]+/[^\s]{3,})', text)}
    return found or {'case:' + case['case_id']}


def groups(cases):
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for case in cases:
        first, *rest = sorted(keys(case))
        for other in rest:
            parent[find(other)] = find(first)
        case['_root'] = find(first)
    # Near-duplicate reposts (same ad with small edits): character 4-gram Jaccard >= 0.5.
    shingles = [(case, {norm(case['text'])[i:i + 4] for i in range(max(0, len(norm(case['text'])) - 3))})
                for case in cases if len(norm(case.get('text'))) >= 20]
    for index, (a, sa) in enumerate(shingles):
        for b, sb in shingles[index + 1:]:
            if 0.5 <= len(sa) / max(1, len(sb)) <= 2 and len(sa & sb) >= 0.5 * len(sa | sb):
                parent[find(b['_root'])] = find(a['_root'])
    for case in cases:
        case['group_id'] = 'fb:' + hashlib.sha1(find(case['_root']).encode('utf-8')).hexdigest()[:12]
        del case['_root']


def platform(case):
    return (case.get('platforms') or ['Unknown'])[0]


def split_groups(cases, seed, fractions=(0.6, 0.2, 0.2)):
    # Stratify by (label, platform) so small platforms (LINE, TikTok, web) also reach the test split.
    by_label = defaultdict(set)
    for case in cases:
        by_label[(case['label'], platform(case))].add(case['group_id'])
    assignment = {}
    rng = random.Random(seed)
    for label, members in sorted(by_label.items()):
        members = sorted(members)
        rng.shuffle(members)
        cut1, cut2 = round(len(members) * fractions[0]), round(len(members) * (fractions[0] + fractions[1]))
        for index, group in enumerate(members):
            assignment.setdefault(group, 'train' if index < cut1 else 'calibration' if index < cut2 else 'test')
    for case in cases:
        case['split'] = assignment[case['group_id']]


def load_official(path):
    """Adjudicated cases, newest first, without placeholders or exact reposts."""
    unique = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        try:
            case = json.loads(line)
        except ValueError:
            continue
        if case.get('label') in ('Fraud', 'Normal'):
            unique[case['case_id']] = case
    cases, seen_text = [], set()
    for case in sorted(unique.values(), key=lambda c: c.get('reported_at', ''), reverse=True):
        text = (case.get('text') or '').strip()
        if PLACEHOLDER.match(text):
            text = ''
        if len(norm(text)) < 8 and not case.get('image_path'):
            continue
        key = norm(text)[:120]
        if key and key in seen_text:  # exact reposts add no information
            continue
        seen_text.add(key)
        cases.append({'case_id': 'fb-' + case['case_id'], 'source': 'fraudbuster', 'text': text,
                      'image_path': case.get('image_path'), 'label': case['label'], 'label_kind': 'official_verdict',
                      'label_source': case['label_source'], 'source_url': case['source_url'],
                      'category': case.get('category'), 'platforms': case.get('platforms'),
                      'reported_at': case.get('reported_at')})
    return cases


def old_recipe_fraud_ids(path, max_fraud=900):
    """Fraud cases the previous recipe kept: the newest `max_fraud` overall, which were ~95% Threads."""
    fraud = [c for c in load_official(path) if c['label'] == 'Fraud']
    return {c['case_id'] for c in fraud[:max_fraud]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fraudbuster', type=Path, default=Path('data/fraudbuster/cases.jsonl'))
    parser.add_argument('--hard-negatives', type=Path, default=Path('data/hard_negatives/ptt.jsonl'))
    parser.add_argument('--output', type=Path, default=Path('data/dataset/manifest.jsonl'))
    parser.add_argument('--max-fraud', type=int, default=900,
                        help='cap Fraud cases per platform (newest first); Threads dominates recent reports')
    parser.add_argument('--max-per-group', type=int, default=10)
    parser.add_argument('--seed', type=int, default=20261115)
    args = parser.parse_args()
    cases = load_official(args.fraudbuster)
    # A global newest-first cap kept ~95% Threads and dropped most Facebook / web / LINE fraud; cap each platform instead.
    per_platform, fraud = Counter(), []
    for case in (c for c in cases if c['label'] == 'Fraud'):
        per_platform[platform(case)] += 1
        if per_platform[platform(case)] <= args.max_fraud:
            fraud.append(case)
    cases = fraud + [c for c in cases if c['label'] == 'Normal']
    groups(cases)
    # One campaign (e.g. hundreds of template bot profiles) must not dominate the benchmark.
    rng, members = random.Random(args.seed), defaultdict(list)
    for case in cases:
        members[case['group_id']].append(case)
    capped = [case for group in members.values() for case in (rng.sample(group, args.max_per_group)
                                                              if len(group) > args.max_per_group else group)]
    print('groups capped:', {g: len(v) for g, v in members.items() if len(v) > args.max_per_group})
    cases = capped
    split_groups(cases, args.seed)
    hard = []
    if args.hard_negatives.exists():
        for line in args.hard_negatives.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            row.update(source='ptt', image_path=None, weight_group=row['source_url'],
                       split='test' if row['board'] in TEST_BOARDS else 'calibration' if row['board'] in CALIBRATION_BOARDS else 'train')
            hard.append(row)
            # Titles are short, question-like texts ("[問題] 年薪100萬該買車嗎") that official ads never cover.
            title = re.sub(r'^\s*(Re:\s*)?\[[^\]]{1,6}\]\s*', '', row['text'].split('\n', 1)[0]).strip()
            if len(norm(title)) >= 6:
                hard.append({**row, 'case_id': row['case_id'] + '-title', 'text': title, 'label_kind': 'presumed_normal_hard_negative_title'})
    pushes = args.hard_negatives.with_name('ptt_pushes.jsonl')
    if pushes.exists():
        # Short casual chat lines; capped per board so they do not swamp the benchmark.
        by_board = defaultdict(list)
        for line in pushes.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            by_board[row['board']].append(row)
        rng = random.Random(args.seed)
        for board, rows in sorted(by_board.items()):
            for row in rng.sample(rows, min(len(rows), 80)):
                row.update(source='ptt', image_path=None, weight_group=row['source_url'],
                           split='test' if board in TEST_BOARDS else 'calibration' if board in CALIBRATION_BOARDS else 'train')
                hard.append(row)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('w', encoding='utf-8') as out:
        for case in cases + hard:
            out.write(json.dumps(case, ensure_ascii=False) + '\n')
    table = Counter((c['source'], c['split'], c['label']) for c in cases + hard)
    for key in sorted(table):
        print(*key, table[key])
    for key, count in sorted(Counter((platform(c), c['label'], c['split']) for c in cases).items()):
        print('platform', *key, count)
    print('official groups:', len({c['group_id'] for c in cases}), 'cases:', len(cases), '-> ', args.output)


if __name__ == '__main__':
    main()
