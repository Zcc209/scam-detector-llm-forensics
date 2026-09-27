"""Image forensics signals: known-scam image matching, error level analysis, metadata, brand/domain mismatch.

* Perceptual hashing (pHash + dHash) compares the image, and each <img> region of a screenshot,
  against images of cases that the MODA fraud-report site confirmed as fraud. Scam ads reuse
  the same creatives across throwaway accounts, so a near-duplicate is strong, checkable evidence.
* Error Level Analysis re-saves a JPEG and maps where the compression error differs. Locally
  higher error can indicate pasted or edited regions (fake transfer receipts, doctored chats).
  It is an indicator for review, not proof: resizing, screenshots and text edges also light up.
* EXIF editing-software tags are reported as metadata facts only.
* Brand/domain mismatch: an official brand named in the page image while the page is not on that
  brand's domain (pages on social platforms are skipped because posts mention brands legitimately).
"""
import argparse
import io
import json
from pathlib import Path

import numpy as np
from PIL import ExifTags, Image, ImageChops
from scipy.fft import dct

INDEX = Path(__file__).resolve().parent / 'data' / 'fraudbuster' / 'image_index.json'
EDITORS = ('photoshop', 'gimp', 'snapseed', 'picsart', 'meitu', '美圖', 'canva', 'lightroom', 'pixlr', 'faceapp')
BRANDS = {
    '中華郵政': ('post.gov.tw',), '郵局': ('post.gov.tw',), '國泰世華': ('cathaybk.com.tw',), '中國信託': ('ctbcbank.com',),
    '玉山銀行': ('esunbank.com', 'esunbank.com.tw'), '台新銀行': ('taishinbank.com.tw',), '富邦': ('fubon.com', 'taipeifubon.com.tw'),
    '蝦皮': ('shopee.tw',), '賣貨便': ('7-11.com.tw',), 'myship': ('7-11.com.tw',), '7-eleven': ('7-11.com.tw',), '全家': ('family.com.tw',),
    'momo': ('momoshop.com.tw',), 'pchome': ('pchome.com.tw',), '台積電': ('tsmc.com',), '金管會': ('fsc.gov.tw',),
    '國稅局': ('ntbt.gov.tw', 'mof.gov.tw'), '監理': ('mvdis.gov.tw', 'thb.gov.tw'), '中華電信': ('cht.com.tw', 'hinet.net'),
    '台灣大哥大': ('taiwanmobile.com',), '遠傳': ('fetnet.net',), '台電': ('taipower.com.tw',), '健保': ('nhi.gov.tw',),
    'netflix': ('netflix.com',), 'apple': ('apple.com',), '黑貓宅急便': ('t-cat.com.tw',), 'etag': ('fetc.net.tw',),
}
SOCIAL = ('facebook.com', 'instagram.com', 'threads.net', 'threads.com', 'x.com', 'twitter.com', 'youtube.com',
          'tiktok.com', 'dcard.tw', 'ptt.cc', 'line.me', 'reddit.com')


def _gray(image, size):
    return np.asarray(image.convert('L').resize(size, Image.LANCZOS), dtype=np.float64)


def phash(image):
    pixels = _gray(image, (32, 32))
    low = dct(dct(pixels, axis=0, norm='ortho'), axis=1, norm='ortho')[:8, :8]
    bits = (low > np.median(low.ravel()[1:])).ravel()  # DC term excluded from the median
    return ''.join('1' if b else '0' for b in bits)


def dhash(image):
    pixels = _gray(image, (9, 8))
    return ''.join('1' if b else '0' for b in (pixels[:, 1:] > pixels[:, :-1]).ravel())


def hamming(a, b):
    return sum(x != y for x, y in zip(a, b))


def hashes(image):
    return {'phash': phash(image), 'dhash': dhash(image)}


def load_index(path=INDEX):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []


def match_known(image, index, threshold=10):
    """Best match in the known-scam index; both hashes must agree to limit chance collisions."""
    if not index:
        return None
    probe = hashes(image)
    best = min(index, key=lambda row: hamming(probe['phash'], row['phash']) + hamming(probe['dhash'], row['dhash']))
    distance = {'phash': hamming(probe['phash'], best['phash']), 'dhash': hamming(probe['dhash'], best['dhash'])}
    if distance['phash'] <= threshold and distance['dhash'] <= threshold + 4:
        return {'case_id': best['case_id'], 'source_url': best.get('source_url'), 'distance': distance, 'threshold': threshold}
    return None


def regions(image, boxes=(), dpr=1.0):
    """Whole image, a center crop, and every DOM <img> box (CSS px -> screenshot px)."""
    width, height = image.size
    crops = [('whole', image), ('center', image.crop((width // 10, height // 10, width * 9 // 10, height * 9 // 10)))]
    for index, box in enumerate(boxes):
        left, top, right, bottom = [max(0, round(v * dpr)) for v in box]
        right, bottom = min(width, right), min(height, bottom)
        if right - left >= 64 and bottom - top >= 64:
            crops.append((f'img:{index}', image.crop((left, top, right, bottom))))
    return crops


def ela(image, quality=90, output=None):
    rgb = image.convert('RGB')
    buffer = io.BytesIO()
    rgb.save(buffer, 'JPEG', quality=quality)
    buffer.seek(0)
    diff = np.asarray(ImageChops.difference(rgb, Image.open(buffer).convert('RGB')), dtype=np.float64).max(axis=2)
    h, w = diff.shape
    size = 32
    blocks = [diff[y:y + size, x:x + size].mean() for y in range(0, h - size + 1, size) for x in range(0, w - size + 1, size)]
    median = float(np.median(blocks)) if blocks else 0.0
    peak = float(np.max(blocks)) if blocks else 0.0
    if output:
        scale = 255.0 / max(1.0, float(np.percentile(diff, 99.5)))
        Image.fromarray(np.clip(diff * scale, 0, 255).astype(np.uint8)).save(output)
    return {'quality': quality, 'mean_error': float(diff.mean()), 'p99_error': float(np.percentile(diff, 99)),
            'block_peak_to_median': peak / median if median > 0 else None,
            'heatmap_path': str(output) if output else None,
            'note': 'Localized high error is a cue for manual review; screenshots, resizing and sharp text also raise it.'}


def metadata(image):
    exif = {}
    try:
        exif = {ExifTags.TAGS.get(k, str(k)): str(v)[:120] for k, v in (image.getexif() or {}).items()}
    except Exception:
        pass
    software = exif.get('Software', '')
    return {'format': image.format, 'exif_present': bool(exif), 'software': software or None,
            'editor_tag': next((e for e in EDITORS if e in software.lower()), None),
            'camera': ' '.join(filter(None, (exif.get('Make'), exif.get('Model')))) or None}


def brand_mismatch(texts, domain):
    """`domain` is the page domain (URL mode) or the list of domains printed in an uploaded screenshot."""
    from link_tracer import SHORTENERS, ocr_near_official
    domains = [d.lower() for d in ([domain] if isinstance(domain, str) else domain or []) if d]
    domains = [d for d in domains if not any(d == s or d.endswith('.' + s) for s in SOCIAL + SHORTENERS)]
    if not domains:
        return []
    joined = '\n'.join(texts or []).lower()
    found = []
    for brand, owners in BRANDS.items():
        if brand.lower() in joined and not any(d == o or d.endswith('.' + o) for d in domains for o in owners)                 and not any(ocr_near_official(d, owners) for d in domains):  # an OCR-misread official URL is not impersonation
            found.append({'brand': brand, 'official_domains': list(owners), 'page_domain': domains[0]})
    return found


def analyze(path, *, boxes=(), dpr=1.0, texts=(), domain=None, output_dir=None, index=None):
    image = Image.open(path)
    image.load()
    index = load_index() if index is None else index
    matches = []
    for name, crop in regions(image, boxes, dpr):
        match = match_known(crop, index)
        if match:
            matches.append({'region': name, **match})
    heatmap = Path(output_dir) / 'ela.png' if output_dir else None
    mismatch, meta = brand_mismatch(texts, domain), metadata(image)
    return {'status': 'SUCCESS', 'known_scam_matches': matches, 'ela': ela(image, output=heatmap),
            'metadata': meta, 'brand_domain_mismatch': mismatch, 'index_size': len(index),
            'features': {'image_known_scam_match': int(bool(matches)), 'image_brand_mismatch': int(bool(mismatch)),
                         'image_editor_tag': int(bool(meta['editor_tag']))}}


def build_index(cases_path, output=INDEX, min_side=200):
    """Index confirmed-fraud images, excluding small icons and anything that also appears in non-fraud cases
    (default avatars and platform placeholders would otherwise match innocent pages)."""
    fraud, normal = [], []
    for line in Path(cases_path).read_text(encoding='utf-8').splitlines():
        case = json.loads(line)
        if case.get('label') not in ('Fraud', 'Normal') or not case.get('image_path') or not Path(case['image_path']).is_file():
            continue
        try:
            with Image.open(case['image_path']) as image:
                if min(image.size) < min_side:
                    continue
                row = {'case_id': case['case_id'], 'source_url': case['source_url'], **hashes(image)}
        except OSError:
            continue
        (fraud if case['label'] == 'Fraud' else normal).append(row)
    rows = []
    for row in fraud:
        shared = any(hamming(row['phash'], n['phash']) <= 12 for n in normal)
        duplicate = any(hamming(row['phash'], r['phash']) <= 4 and hamming(row['dhash'], r['dhash']) <= 4 for r in rows)
        if not shared and not duplicate:
            rows.append(row)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(rows), encoding='utf-8')
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    build = sub.add_parser('build-index', help='hash images of confirmed-fraud cases')
    build.add_argument('--cases', type=Path, default=Path('data/fraudbuster/cases.jsonl'))
    build.add_argument('--output', type=Path, default=INDEX)
    check = sub.add_parser('check', help='analyze one image')
    check.add_argument('image', type=Path)
    args = parser.parse_args()
    if args.command == 'build-index':
        print(f'{len(build_index(args.cases, args.output))} fraud images indexed -> {args.output}')
    else:
        print(json.dumps(analyze(args.image), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
