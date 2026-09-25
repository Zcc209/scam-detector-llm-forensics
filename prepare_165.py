"""Extract candidate positive domains from Taiwan's 165 open-data CSV.

This does not create normal examples, social-account labels, or benchmark scores.
"""

import argparse
import csv
from pathlib import Path

SOURCE = "https://data.gov.tw/dataset/176455"


def read_rows(path):
    for encoding in ("utf-8-sig", "cp950"):
        try:
            with Path(path).open(encoding=encoding, newline="") as stream:
                rows = list(csv.DictReader(stream))
            if rows and "網域" in rows[0]:
                return rows
        except UnicodeDecodeError:
            pass
    raise ValueError("CSV must contain a 網域 column and use UTF-8 or CP950 encoding")


def extract_candidates(path, limit=100):
    seen, candidates = set(), []
    for row in read_rows(path):
        domain = (row.get("網域") or "").strip().lower().rstrip(".")
        if not domain or domain in seen:
            continue
        seen.add(domain)
        candidates.append({"domain": domain, "year_month": (row.get("民國年月") or "").strip(),
                           "site_type": (row.get("網站性質") or "").strip(),
                           "label_scope": "listed_domain_only", "source_dataset": SOURCE})
        if len(candidates) >= limit:
            break
    return candidates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Downloaded 165 CSV")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    rows = extract_candidates(args.input, args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("domain", "year_month", "site_type", "label_scope", "source_dataset"))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} domain-only candidates to {args.output}")


if __name__ == "__main__":
    main()
