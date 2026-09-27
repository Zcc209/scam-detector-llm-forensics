"""Run every manifest case through the same evidence extractors as the live pipeline.

Stage `base`: MacBERT on the case text, EasyOCR + MacBERT on the case image (image-only lines,
i.e. OCR lines not already in the text, mirroring the DOM/OCR alignment), account signals and
perceptual hashes. Stage `llm`: the local Ollama evidence extractor. Results are cached per case.
"""
import argparse
from contextlib import redirect_stdout
from difflib import SequenceMatcher
import io
import json
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parent


def norm(text):
    return re.sub(r'\W+', '', unicodedata.normalize('NFKC', text or '').lower())


def image_only_lines(ocr_lines, text):
    """OCR lines that the case text does not already contain (fuzzy, OCR errors tolerated)."""
    base = norm(text)
    kept = []
    for line in ocr_lines:
        key = norm(line)
        if not key:
            continue
        if key in base:
            continue
        if base and len(key) >= 4:
            # Anchor on the longest common run, then compare the aligned window (tolerates OCR typos).
            match = SequenceMatcher(None, key, base, autojunk=False).find_longest_match(0, len(key), 0, len(base))
            start = max(0, match.b - match.a)
            window = base[start:start + len(key)]
            if match.size and SequenceMatcher(None, key, window, autojunk=False).ratio() >= 0.7:
                continue
        kept.append(line)
    return kept


def compact(result):
    if not result:
        return None
    keep = ('status', 'prediction', 'fraud_confidence', 'num_chunks')
    out = {k: result.get(k) for k in keep}
    provenance = result.get('score_provenance') or {}
    out['score_provenance'] = {'logit_difference': provenance.get('logit_difference')}
    return out


def load_jsonl(path):
    rows = {}
    if Path(path).exists():
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            rows[row['case_id']] = row
    return rows


def base_stage(cases, output, model_path):
    from account_signals import extract
    from image_forensics import hashes
    from PIL import Image
    with redirect_stdout(sys.stderr):
        from integrated_app import ScamDetectionPipeline
        pipeline = ScamDetectionPipeline(str(model_path))
    done = load_jsonl(output)
    texts = {case['case_id']: case.get('text') or '' for case in cases}
    changed = 0
    for row in done.values():  # refresh image-only text from cached OCR lines (no re-OCR) if the rule changed
        only = '\n'.join(image_only_lines(row.get('ocr_texts') or [], texts.get(row['case_id'], '')))
        if only != row.get('image_only_text'):
            row['image_only_text'] = only
            with redirect_stdout(io.StringIO()):
                row['ocr_model'] = compact(pipeline.detector.predict(only)) if sum(c.isalnum() for c in only) >= 20 else None
            changed += 1
    if changed:
        Path(output).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in done.values()), encoding='utf-8')
        print(f'refreshed image-only text for {changed} cached rows', flush=True)
    with open(output, 'a', encoding='utf-8') as out:
        for index, case in enumerate(cases):
            if case['case_id'] in done:
                continue
            text = case.get('text') or ''
            row = {'case_id': case['case_id'], 'model_sha256': pipeline.detector.model_identity.get('model.safetensors')}
            with redirect_stdout(io.StringIO()):
                row['text_model'] = compact(pipeline.detector.predict(text)) if norm(text) else None
                ocr_lines, only = [], []
                if case.get('image_path') and Path(case['image_path']).is_file():
                    ocr = pipeline.process_image(case['image_path'])
                    ocr_lines = ocr.get('ocr_texts') or []
                    only = image_only_lines(ocr_lines, text)
                    row['ocr_status'] = ocr.get('status')
                    with Image.open(case['image_path']) as image:
                        row['hashes'] = hashes(image)
                image_text = '\n'.join(only)
                row['ocr_texts'], row['image_only_text'] = ocr_lines, image_text
                row['ocr_model'] = (compact(pipeline.detector.predict(image_text))
                                    if sum(c.isalnum() for c in image_text) >= 20 else None)
            row['account'] = extract('\n'.join([text, *ocr_lines]))
            out.write(json.dumps(row, ensure_ascii=False) + '\n')
            out.flush()
            if index % 50 == 0:
                print(f'base {index}/{len(cases)}', flush=True)


def rescore_stage(cases, base_path, output, model_path):
    """Re-run only MacBERT (e.g. the fine-tuned candidate) on the cached text and image-only OCR text."""
    from inference import FraudDetector
    detector = FraudDetector(model_path)
    base = load_jsonl(base_path)
    with open(output, 'w', encoding='utf-8') as out:
        for case in cases:
            row = base.get(case['case_id'])
            if not row:
                continue
            text, image_text = case.get('text') or '', row.get('image_only_text') or ''
            out.write(json.dumps({'case_id': case['case_id'],
                                  'text_model': compact(detector.predict(text)) if norm(text) else None,
                                  'ocr_model': compact(detector.predict(image_text)) if sum(c.isalnum() for c in image_text) >= 20 else None,
                                  'model_path': str(model_path)}, ensure_ascii=False) + '\n')


def cool_down(limit=80):
    """Sustained GPU load made this laptop hard-power-off twice; pause while the GPU is hot."""
    import subprocess
    import time
    while True:
        try:
            temp = int(subprocess.run(['nvidia-smi', '--query-gpu=temperature.gpu', '--format=csv,noheader'],
                                      capture_output=True, text=True, timeout=10).stdout.split()[0])
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            return
        if temp < limit:
            return
        time.sleep(15)


def llm_stage(cases, base_path, output, workers=1):
    import llm_evidence
    if not llm_evidence.available():
        raise SystemExit(f'Ollama model {llm_evidence.MODEL} is not available at {llm_evidence.HOST}')
    import threading
    from concurrent.futures import ThreadPoolExecutor
    base = load_jsonl(base_path)
    done = load_jsonl(output)
    todo = [case for case in cases if case['case_id'] not in done and case['case_id'] in base]
    lock = threading.Lock()

    def work(case):
        cool_down()
        text = '\n'.join(filter(None, [case.get('text'), base[case['case_id']].get('image_only_text')]))
        if not norm(text):
            text = '\n'.join(base[case['case_id']].get('ocr_texts') or [])
        result = llm_evidence.analyze(text)
        if result.get('status') == 'unavailable':
            print(case['case_id'], result.get('error'), flush=True)
            return
        with lock:
            out.write(json.dumps({'case_id': case['case_id'], 'llm': result}, ensure_ascii=False) + '\n')
            out.flush()
            work.count += 1
            if work.count % 25 == 0:
                print(f'llm {work.count}/{len(todo)}', flush=True)
    work.count = 0
    with open(output, 'a', encoding='utf-8') as out, ThreadPoolExecutor(workers) as pool:
        list(pool.map(work, todo))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['base', 'llm', 'rescore'])
    parser.add_argument('--manifest', type=Path, default=ROOT / 'data' / 'dataset' / 'manifest.jsonl')
    parser.add_argument('--out-dir', type=Path, default=ROOT / 'artifacts' / 'dataset')
    parser.add_argument('--model-path', type=Path, default=ROOT / 'anti_fraud_E3_macbert')
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding='utf-8')
    cases = [json.loads(line) for line in args.manifest.read_text(encoding='utf-8').splitlines()]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    if args.stage == 'base':
        base_stage(cases, args.out_dir / 'base.jsonl', args.model_path.resolve())
    elif args.stage == 'rescore':
        rescore_stage(cases, args.out_dir / 'base.jsonl', args.out_dir / 'base_ft.jsonl', args.model_path.resolve())
    else:
        llm_stage(cases, args.out_dir / 'base.jsonl', args.out_dir / 'llm.jsonl')


if __name__ == '__main__':
    main()
