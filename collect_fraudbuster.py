"""Collect officially adjudicated cases from the MODA fraud-report site (fraudbuster.digiat.org.tw).

Labels come only from the case timeline written by the reviewing agency:
  "...確認，這是詐騙訊息"  -> Fraud
  "...非詐騙..."          -> Normal
Anything still pending or merely "高風險" stays unlabeled and is not used for evaluation.
The original post URL is masked by the site, so each case is text + posted image, not a live page.
"""
import argparse
import hashlib
import html
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

BASE = "https://fraudbuster.digiat.org.tw/accessibility"
IMAGE = "https://digi-runner.digiat.org.tw/api/fbr/v3/m/getIssueImage/{}"
HEADERS = {"User-Agent": "Mozilla/5.0 (academic anti-fraud project; low-rate crawler)"}
FRAUD_TYPES = ("PAS", "IMP", "INV", "ROM", "UM", "JAE", "TAE", "MM", "CHA", "OTH")


def strip(value):
    return html.unescape(re.sub(r"<[^>]+>", " ", value or "")).replace("\xa0", " ").strip()


def label_from_timeline(entries):
    text = " ".join(entries)
    if "非詐騙" in text or re.search(r"(並?不是|並非)詐騙", text):
        return "Normal"
    if re.search(r"確認，?這是詐騙|確認為詐騙|已確認.{0,6}詐騙", text):
        return "Fraud"
    return None


def parse_detail(page, case_id):
    summary = re.search(r'class="summary-content">\s*<p>(.*?)</p>', page, re.S)
    platforms = re.findall(r'<ul class="socialIcons"[^>]*>(.*?)</ul>', page, re.S)
    timeline = [strip(p) for p in re.findall(r'<ol class="timeline".*?<p>(.*?)</p>', page, re.S)]
    times = re.findall(r'<time datetime="([^"]+)"', page)
    status = re.search(r'<h3>案件狀態</h3>.*?<span>([^<]+)</span>\s*</span>', page, re.S)
    fraud_type = re.search(r'id="imgPic" alt="([^"]*)"', page)
    return {
        "case_id": case_id,
        "text": strip(summary.group(1)) if summary else "",
        "platforms": re.findall(r'alt="([^"]+)"', platforms[0]) if platforms else [],
        "status": strip(status.group(1)) if status else "",
        "category": fraud_type.group(1) if fraud_type else "",
        "timeline": timeline,
        "reported_at": times[0] if times else "",
        "label": label_from_timeline(timeline),
        "verdict": ("high_risk" if any("高風險" in t for t in timeline) else
                    "pending" if not label_from_timeline(timeline) else "final"),
        "label_source": "fraudbuster timeline: " + (timeline[-1] if timeline else ""),
        "source_url": f"{BASE}/detail?listType=N&id={case_id}",
    }


class Crawler:
    def __init__(self, cache, delay):
        self.cache, self.delay, self.local = Path(cache), delay, threading.local()
        self.cache.mkdir(parents=True, exist_ok=True)

    def get(self, url, binary=False):
        key = self.cache / (hashlib.sha1(url.encode()).hexdigest() + (".bin" if binary else ".html"))
        if key.exists():
            return key.read_bytes() if binary else key.read_text(encoding="utf-8")
        time.sleep(self.delay)
        if not hasattr(self.local, 'session'):
            self.local.session = requests.Session()
        response = self.local.session.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        if binary:
            key.write_bytes(response.content)
            return response.content
        key.write_text(response.text, encoding="utf-8")
        return response.text

    def scan_normal_ids(self, list_type, start, size, fraud_rate=0):
        """List cards carry the reviewing agency's status; keep ids whose card says 非詐騙
        (plus a deterministic 1/fraud_rate sample of confirmed-fraud cards for diversity)."""
        page = self.get(f"{BASE}/index?listType={list_type}&startSerialNo={start}&pageSize={size}")
        chunks = re.split(r'(?=<a[^>]+detail\?listType=[A-Z]+&amp;id=)', page)
        found = []
        for chunk in chunks:
            match = re.search(r"detail\?listType=[A-Z]+&amp;id=([0-9a-f]{24})", chunk)
            if match and re.search(r'class="green"[^>]*>[^<]*非詐騙', chunk):
                found.append(match.group(1))
            elif (match and fraud_rate and re.search(r'>詐騙訊息，已通知', chunk)
                  and int(hashlib.sha1(match.group(1).encode()).hexdigest(), 16) % fraud_rate == 0):
                found.append(match.group(1))
        return found, page.count("detail?listType")

    def list_ids(self, list_type, start, size, fraud_type=""):
        url = f"{BASE}/index?listType={list_type}&startSerialNo={start}&pageSize={size}"
        if fraud_type:
            url = f"{BASE}/search?keyword=&listType={list_type}&startSerialNo={start}&pageSize={size}&fraudType={fraud_type}"
        return list(dict.fromkeys(re.findall(r"detail\?listType=[A-Z]+&amp;id=([0-9a-f]{24})", self.get(url))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/fraudbuster/cases.jsonl"))
    parser.add_argument("--image-dir", type=Path, default=Path("artifacts/fraudbuster/images"))
    parser.add_argument("--cache", type=Path, default=Path("artifacts/fraudbuster/cache"))
    parser.add_argument("--pages", type=int, default=10, help="list pages per list type / category")
    parser.add_argument("--page-size", type=int, default=80)
    parser.add_argument("--delay", type=float, default=0.8, help="seconds between uncached requests")
    parser.add_argument("--by-category", action="store_true", help="also walk each fraud category")
    parser.add_argument("--images", action="store_true", help="save the case image for OCR experiments")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--fraud-rate", type=int, default=0, help="with --scan-normals, also keep 1/N fraud verdict cards")
    parser.add_argument("--scan-normals", type=int, default=0, metavar="PAGES",
                        help="scan this many 500-card pages of the newest list for 非詐騙 verdicts only")
    args = parser.parse_args()
    crawler = Crawler(args.cache, args.delay)
    existing = {}
    if args.output.exists():
        for line in args.output.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            existing[row["case_id"]] = row
    queries = [("H", ""), ("N", "")] + ([("N", t) for t in FRAUD_TYPES] if args.by_category else [])
    ids = []
    if args.scan_normals:
        # Non-fraud verdicts are ~0.5% of reports, so scan 500-card list pages and fetch only those details.
        queries = []
        for list_type, pages in (("H", 18), ("N", args.scan_normals)):
            for page in range(pages):
                try:
                    found, cards = crawler.scan_normal_ids(list_type, 1 + page * 500, 500, args.fraud_rate)
                except requests.RequestException as error:
                    print(f"scan {list_type} page {page}: {error}", flush=True)
                    continue
                ids.extend(found)
                if not cards:
                    break
                if page % 10 == 0:
                    print(f"scan {list_type} page {page}: {len(ids)} normal ids", flush=True)
    for list_type, fraud_type in queries:
        for page in range(args.pages):
            try:
                found = crawler.list_ids(list_type, 1 + page * args.page_size, args.page_size, fraud_type)
            except requests.RequestException as error:
                print(f"list {list_type}/{fraud_type} page {page}: {error}")
                break
            if not found:
                break
            ids.extend(found)
    ids = list(dict.fromkeys(ids))
    print(f"{len(ids)} case ids; {len(existing)} already parsed")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    counts = {"Fraud": 0, "Normal": 0, None: 0}
    lock = threading.Lock()

    def work(case_id):
        try:
            row = parse_detail(crawler.get(f"{BASE}/detail?listType=N&id={case_id}"), case_id)
            if args.images and row["label"]:
                data = crawler.get(IMAGE.format(case_id), binary=True)
                if len(data) > 2000:
                    args.image_dir.mkdir(parents=True, exist_ok=True)
                    path = args.image_dir / f"{case_id}.jpg"
                    path.write_bytes(data)
                    row["image_path"] = str(path)
        except requests.RequestException as error:
            print(f"{case_id}: {error}", flush=True)
            return
        with lock:
            counts[row["label"]] += 1
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            if sum(counts.values()) % 100 == 0:
                print(sum(counts.values()), counts, flush=True)

    with args.output.open("a", encoding="utf-8") as out, ThreadPoolExecutor(args.workers) as pool:
        for case_id in ids:
            if case_id in existing:
                counts[existing[case_id]["label"]] += 1
        list(pool.map(work, [case_id for case_id in ids if case_id not in existing]))
    print("labels:", counts)


if __name__ == "__main__":
    main()
