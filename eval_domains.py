"""Offline evaluation of the URL/domain module on the 165 fraud-website list vs well-known legitimate domains.

Reports how many 165 domains the lookalike *rules* catch without the blocklist, and a temporal
check of the blocklist: domains first listed in the newest month are looked up in a list built
only from earlier months. No page is visited.
"""
import argparse
import json
from pathlib import Path
import random
from unittest.mock import patch

import domain_check

ROOT = Path(__file__).resolve().parent


LOOKUP = domain_check.listed_165


def flagged(host, use_blocklist, before=None):
    lookup = (lambda h: LOOKUP(h, before)) if use_blocklist else (lambda h: None)
    with patch('domain_check.require_public_url', lambda value: value), patch('domain_check.listed_165', lookup):
        try:
            result = domain_check.analyze_url('https://' + host + '/')
        except ValueError:
            return None  # malformed entry in the open data
    return result['risk_score'] >= 60 or not result['capture_allowed']


def rate(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample', type=int, default=2000)
    args = parser.parse_args()
    rows = [line.split('\t') for line in domain_check.BLOCKLIST_PATH.read_text(encoding='utf-8').splitlines()
            if line and not line.startswith('#')]
    newest = max(month for _, month in rows)
    fraud = [d for d, _ in rows]
    random.Random(1).shuffle(fraud)
    fraud = fraud[:args.sample]
    fresh = [d for d, month in rows if month == newest][:args.sample]
    normal = [l.strip() for l in (ROOT / 'data' / 'normal_domains.txt').read_text(encoding='utf-8').splitlines()
              if l.strip() and not l.startswith('#')]
    tld = lambda d: d.rsplit('.', 1)[-1]
    from collections import Counter
    result = {
        'fraud_sample': len(fraud), 'normal_domains': len(normal), 'newest_month': newest, 'newest_month_domains': len(fresh),
        'rules_only_recall': rate([flagged(d, False) for d in fraud]),
        'rules_only_fpr': rate([flagged(d, False) for d in normal]),
        'blocklist_fpr': rate([flagged(d, True) for d in normal]),
        'temporal_blocklist_recall_newest_month': rate([flagged(d, True, newest) for d in fresh]),
        'top_tlds_165': Counter(tld(d) for d, _ in rows).most_common(10),
        'note': 'Rules target social-platform lookalikes; most 165 domains are generic scam shops/investment sites, '
                'so new domains must be judged by page content. Normal list is hand-curated (small).',
    }
    out = ROOT / 'artifacts' / 'experiments' / 'domain_eval.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
