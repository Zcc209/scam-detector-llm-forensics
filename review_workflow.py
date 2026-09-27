"""Local, explicitly reviewed corpus. Nothing is labeled or trained automatically."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import unicodedata

ROOT = Path(__file__).resolve().parent
SPLITS = ('train', 'validation', 'calibration', 'test')


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def enqueue(report_path, queue):
    report_path = Path(report_path).resolve()
    report = json.loads(report_path.read_text(encoding='utf-8'))
    identifier = hashlib.sha256(report_path.read_bytes()).hexdigest()[:24]
    target = Path(queue) / (identifier + '.json')
    if target.exists():
        return target
    evidence = report.get('evidence') or {}
    texts = {}
    if (evidence.get('dom') or {}).get('text'):
        texts['dom'] = evidence['dom']['text']
    ocr = evidence.get('screenshot_ocr') or {}
    if ocr.get('texts'):
        texts['screenshot_ocr'] = '\n'.join(ocr['texts'])
    if not texts or (report.get('browser_capture') and report['browser_capture'].get('status') != 'success'):
        return None
    write_json(target, {'case_id': identifier, 'report_path': str(report_path),
                       'report_sha256': hashlib.sha256(report_path.read_bytes()).hexdigest(),
                       'status': 'pending', 'created_at': datetime.now(timezone.utc).isoformat(),
                       'source_url': (report.get('domain_analysis') or {}).get('normalized_url', ''),
                       'texts': texts, 'label': 'Unresolved', 'scope': 'content',
                       'group_id': '', 'pair_id': '', 'split': '', 'reviewer': '',
                       'label_source': '', 'permission': '', 'notes': '', 'synthetic': False,
                       'priority': (report.get('content_analysis') or {}).get('basis', report.get('status'))})
    return target


def validate_rows(rows):
    ids, groups, pairs, texts = set(), {}, {}, {}
    for row in rows:
        for key in ('case_id', 'group_id', 'split', 'label', 'label_source', 'reviewer', 'permission', 'notes'):
            if not str(row.get(key, '')).strip():
                raise ValueError(f'Missing {key}: {row.get("case_id")}')
        if row['case_id'] in ids:
            raise ValueError('Duplicate case_id')
        ids.add(row['case_id'])
        if row['label'] not in ('Fraud', 'Normal') or row['split'] not in SPLITS:
            raise ValueError('Invalid label/split')
        if row.get('status') != 'reviewed' or row.get('synthetic') is not False or row.get('scope') != 'content':
            raise ValueError('Only reviewed, real content labels may enter this experiment')
        for key, registry in [('group_id', groups), ('pair_id', pairs)]:
            value = row.get(key)
            if value and value in registry and registry[value] != row['split']:
                raise ValueError(f'{key} leakage: {value}')
            if value:
                registry[value] = row['split']
        if not row.get('texts') or any(k not in ('dom', 'screenshot_ocr') for k in row['texts']):
            raise ValueError('Missing/invalid evidence texts')
        for text in row['texts'].values():
            if not isinstance(text, str) or not text.strip():
                raise ValueError('Empty evidence text')
            digest = hashlib.sha256(''.join(unicodedata.normalize('NFKC', text).lower().split()).encode()).hexdigest()
            previous = texts.get(digest)
            if previous and previous != (row['split'], row['label']):
                raise ValueError('Duplicate text across splits or contradictory labels')
            texts[digest] = (row['split'], row['label'])
    return rows


def load_corpus(path):
    rows = [json.loads(line) for line in Path(path).read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    if not rows:
        raise ValueError('No reviewed cases. Review real evidence first; demo data is not a benchmark.')
    return validate_rows(rows)


def save_review(path, fields):
    path = Path(path)
    row = json.loads(path.read_text(encoding='utf-8'))
    allowed = ('label', 'group_id', 'split', 'reviewer', 'label_source', 'permission', 'notes', 'pair_id')
    for key in allowed:
        value = fields.get(key, '')
        if not isinstance(value, str) or len(value) > 10000:
            raise ValueError('Invalid review field')
        row[key] = value.strip()
    if row['label'] not in ('Normal', 'Fraud', 'Unresolved'):
        raise ValueError('Invalid label')
    row.update(status='reviewed', reviewed_at=datetime.now(timezone.utc).isoformat())
    if row['label'] != 'Unresolved':
        validate_rows([row])
    with path.with_suffix('.history.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(row, ensure_ascii=False)+'\n')
    write_json(path, row)
    return row


def build(queue, output):
    rows = []
    for path in sorted(Path(queue).glob('*.json')):
        row = json.loads(path.read_text(encoding='utf-8'))
        if row.get('status') != 'reviewed' or row.get('label') == 'Unresolved':
            continue
        report_path = Path(row['report_path'])
        if hashlib.sha256(report_path.read_bytes()).hexdigest() != row['report_sha256']:
            raise ValueError(f'Report changed after review: {path}')
        report = json.loads(report_path.read_text(encoding='utf-8'))
        if report.get('browser_capture') and report['browser_capture'].get('status') != 'success':
            raise ValueError(f'Unusable page cannot become training text: {path}')
        for source in row.get('texts', {}):
            model = ((report.get('evidence') or {}).get(source) or {}).get('model') or {}
            if model.get('status') != 'SUCCESS':
                raise ValueError(f'Recapture evidence rejected by content quality guards before training: {path} / {source}')
        rows.append(row)
    if not rows:
        raise ValueError('No reviewed binary labels; pending/Unresolved are intentionally excluded')
    validate_rows(rows)
    output = Path(output)
    if output.exists():
        raise ValueError('Corpus is immutable; choose a new output version')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in rows), encoding='utf-8')
    return len(rows)


def score_corpus(corpus, model_path, output, calibration=None):
    from inference import FraudDetector
    from integrated_app import ScamDetectionPipeline
    from fusion import fuse
    from risk_assessment import assess
    from calibration import apply_candidate
    rows = load_corpus(corpus)
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a new score output directory')
    detector = FraudDetector(model_path)
    artifact = json.loads(Path(calibration).read_text(encoding='utf-8')) if calibration else None
    output.mkdir(parents=True)
    manifest = []
    for row in rows:
        # No training or selection uses test labels here; only frozen-model inference.
        if row['split'] == 'validation':
            continue
        report = json.loads(Path(row['report_path']).read_text(encoding='utf-8'))
        evidence = {'dom': None, 'screenshot_ocr': None}
        models = {}
        for source, text in row['texts'].items():
            result = detector.predict(text)
            result['rule_signals'] = ScamDetectionPipeline._rule_signals(text)
            if artifact:
                result = apply_candidate(result, source, artifact, detector.model_identity)
            models[source] = result
            evidence[source] = {'text': text, 'texts': text.splitlines(), 'model': result}
        report['evidence'] = evidence
        report['content_analysis'] = fuse(models.get('dom'), models.get('screenshot_ocr'))
        report['assessment'] = assess(report.get('domain_analysis'), report.get('browser_capture'), report['content_analysis'])
        name = hashlib.sha256(row['case_id'].encode()).hexdigest()[:24] + '.json'
        write_json(output / name, report)
        manifest.append({key: row[key] for key in ('case_id', 'group_id', 'split', 'label', 'label_source', 'reviewer')})
        manifest[-1].update(source_url=row.get('source_url') or 'local:image', report_path=name)
    with (output / 'manifest.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['case_id','group_id','split','label','label_source','reviewer','source_url','report_path'])
        writer.writeheader()
        writer.writerows(manifest)
    write_json(output / 'provenance.json', {'corpus_sha256': hashlib.sha256(Path(corpus).read_bytes()).hexdigest(),
                                          'model_files_sha256': detector.model_identity})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    queue = ROOT / 'artifacts' / 'review_queue'
    p = sub.add_parser('collect'); p.add_argument('report', type=Path); p.add_argument('--queue', type=Path, default=queue)
    p = sub.add_parser('list'); p.add_argument('--queue', type=Path, default=queue)
    p = sub.add_parser('review'); p.add_argument('case', type=Path)
    p.add_argument('--label', choices=['Fraud','Normal','Unresolved'], required=True)
    p.add_argument('--group', required=True); p.add_argument('--split', choices=SPLITS, required=True)
    p.add_argument('--reviewer', required=True); p.add_argument('--source', required=True)
    p.add_argument('--permission', required=True); p.add_argument('--notes', required=True)
    p.add_argument('--pair', default='')
    p = sub.add_parser('build'); p.add_argument('--queue', type=Path, default=queue); p.add_argument('--output', type=Path, required=True)
    p = sub.add_parser('score'); p.add_argument('--corpus', type=Path, required=True)
    p.add_argument('--model-path', type=Path, default=ROOT/'anti_fraud_E3_macbert')
    p.add_argument('--output', type=Path, required=True); p.add_argument('--calibration-json', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'collect':
            print(enqueue(args.report, args.queue))
        elif args.command == 'list':
            for path in sorted(args.queue.glob('*.json')):
                row = json.loads(path.read_text(encoding='utf-8'))
                print(f'{path.name} | {row["status"]} | {row["label"]} | {row.get("priority")} | {row.get("source_url")}')
        elif args.command == 'review':
            save_review(args.case, dict(label=args.label, group_id=args.group, split=args.split,
                        reviewer=args.reviewer, label_source=args.source, permission=args.permission,
                        notes=args.notes, pair_id=args.pair))
            print('Review saved; model unchanged')
        elif args.command == 'build':
            print(f'Exported {build(args.queue, args.output)} reviewed cases')
        else:
            score_corpus(args.corpus, args.model_path, args.output, args.calibration_json)
    except (ValueError, OSError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
