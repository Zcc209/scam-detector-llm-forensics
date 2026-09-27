"""Source-specific calibration candidates with model identity and held-out evaluation."""
import argparse
from bisect import bisect_right
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

from attribution import fingerprint
from evaluate import load_cases


def sigmoid(value):
    return 1 / (1 + math.exp(-value)) if value >= 0 else math.exp(value) / (1 + math.exp(value))


def fit_mapping(samples, method):
    # Samples are (logit difference, binary label, independent-group weight).
    if {y for _, y, _ in samples} != {0, 1}:
        raise ValueError('Both labels are required')
    if method == 'platt':
        import numpy as np
        from scipy.optimize import minimize
        positive = sum(w for _, y, w in samples if y)
        negative = sum(w for _, y, w in samples if not y)
        x = np.array([x for x, _, _ in samples])
        y = np.array([(positive + 1)/(positive + 2) if label else 1/(negative + 2) for _, label, _ in samples])
        w = np.array([w for _, _, w in samples])

        def objective(params):
            a, b = params
            z = a * x + b
            p = np.array([sigmoid(value) for value in z])
            loss = float(np.sum(w * (np.logaddexp(0, z) - y * z)))
            gradient = np.array([np.sum(w * (p-y) * x), np.sum(w * (p-y))])
            return loss, gradient

        result = minimize(objective, [1., 0.], jac=True, method='L-BFGS-B', bounds=[(0, None), (None, None)])
        if not result.success:
            raise ValueError('Platt fit did not converge')
        return {'method': 'platt', 'slope': float(result.x[0]), 'intercept': float(result.x[1]),
                'fit_note': 'Platt smoothed targets; nonnegative slope preserves Fraud score order'}
    if method != 'isotonic':
        raise ValueError('Unsupported calibration method')
    points = {}
    for x, y, weight in samples:
        positive, total = points.get(x, (0., 0.))
        points[x] = positive + y * weight, total + weight
    blocks = []
    for x, (positive, weight) in sorted(points.items()):
        blocks.append({'xs': [x], 'positive': positive, 'weight': weight})
        while len(blocks) > 1 and blocks[-2]['positive']/blocks[-2]['weight'] > blocks[-1]['positive']/blocks[-1]['weight']:
            right = blocks.pop()
            left = blocks.pop()
            blocks.append({'xs': left['xs'] + right['xs'], 'positive': left['positive'] + right['positive'],
                           'weight': left['weight'] + right['weight']})
    knots = [(x, block['positive']/block['weight']) for block in blocks for x in block['xs']]
    return {'method': 'isotonic', 'x': [x for x, _ in knots], 'y': [y for _, y in knots],
            'fit_note': 'Weighted pool-adjacent-violators; linear interpolation; clipped endpoints'}


def mapped_score(mapping, logit):
    if not math.isfinite(logit):
        raise ValueError('Invalid logit')
    if mapping['method'] == 'platt':
        if not all(math.isfinite(mapping[key]) for key in ('slope', 'intercept')) or mapping['slope'] < 0:
            raise ValueError('Invalid Platt parameters')
        return sigmoid(mapping['slope'] * logit + mapping['intercept'])
    if mapping['method'] != 'isotonic':
        raise ValueError('Unsupported mapping')
    xs, ys = mapping['x'], mapping['y']
    if not xs or len(xs) != len(ys) or not all(math.isfinite(x) for x in xs) or not all(math.isfinite(y) and 0 <= y <= 1 for y in ys):
        raise ValueError('Invalid isotonic knots')
    if any(a >= b for a, b in zip(xs, xs[1:])) or any(a > b for a, b in zip(ys, ys[1:])):
        raise ValueError('Isotonic knots must be increasing and scores monotonic')
    index = bisect_right(xs, logit)
    if index == 0:
        return ys[0]
    if index == len(xs):
        return ys[-1]
    fraction = (logit-xs[index-1])/(xs[index]-xs[index-1])
    return ys[index-1] + fraction * (ys[index]-ys[index-1])


def statistics(scores):
    # Each account/site has total weight one, including reliability bins.
    total = sum(w for _, _, w in scores)
    bins = []
    for index in range(10):
        rows = [(p, y, w) for p, y, w in scores if min(int(p*10), 9) == index]
        weight = sum(w for _, _, w in rows)
        bins.append({'bin': index, 'count': len(rows), 'weight': weight,
                     'mean_score': sum(p*w for p, _, w in rows)/weight if weight else None,
                     'observed_fraud_rate': sum(y*w for _, y, w in rows)/weight if weight else None})
    return {'brier': sum(w*(p-y)**2 for p, y, w in scores)/total,
            'log_loss': -sum(w*(y*math.log(max(p, 1e-12)) + (1-y)*math.log(max(1-p, 1e-12))) for p, y, w in scores)/total,
            'ece': sum(row['weight']/total * abs(row['mean_score']-row['observed_fraud_rate']) for row in bins if row['weight']),
            'reliability_bins': bins}


def fit_candidates(cases, identity, method, minimum=10):
    if minimum < 2:
        raise ValueError('Minimum must be at least two independent groups per class')
    output = {'version': 1, 'status': 'candidate_not_validated_for_deployment', 'model_files_sha256': identity,
              'input_kind': 'document_logit_difference', 'method': method, 'sources': {}, 'skipped_sources': {},
              'limitations': ['Only calibration split is fitted; test is held out.',
                             'Minimum group counts are a safeguard, not proof of reliable calibration.',
                             'Labels and original training-set separation require manual audit.',
                             'Case-control sampling class balance may differ from real deployment prevalence.']}
    for source in ('dom', 'screenshot_ocr'):
        rows = {split: [] for split in ('calibration', 'test')}
        for case in cases:
            if case['split'] not in rows:
                continue
            browser = case['report'].get('browser_capture')
            if browser and browser.get('status') != 'success':
                continue
            result = ((case['report'].get('evidence') or {}).get(source) or {}).get('model') or {}
            if result.get('status') != 'SUCCESS':
                continue
            provenance = result.get('score_provenance') or {}
            if provenance.get('model_files_sha256') != identity:
                raise ValueError('Model identity missing/mismatched; regenerate reports with this model')
            logit = provenance.get('logit_difference')
            if not isinstance(logit, (int, float)) or not math.isfinite(logit):
                raise ValueError('Report has no finite logit difference')
            rows[case['split']].append((logit, int(case['label'] == 'Fraud'), case['group_id']))
        counts = {split: {label: len({group for _, y, group in values if y == label}) for label in (0, 1)}
                  for split, values in rows.items()}
        if not all(counts[split][label] >= minimum for split in rows for label in (0, 1)):
            output['skipped_sources'][source] = {'reason': 'insufficient_independent_labeled_groups', 'counts': counts}
            continue
        weighted = {}
        for split, values in rows.items():
            groups = Counter(group for _, _, group in values)
            weighted[split] = [(x, y, 1/groups[group]) for x, y, group in values]
        mapping = fit_mapping(weighted['calibration'], method)
        test = weighted['test']
        raw = statistics([(sigmoid(x), y, w) for x, y, w in test])
        calibrated = statistics([(mapped_score(mapping, x), y, w) for x, y, w in test])
        output['sources'][source] = {'mapping': mapping, 'group_counts': counts, 'test_raw': raw,
                                     'test_calibrated': calibrated, 'automatic_deployment': False}
    return output


def apply_candidate(result, source, artifact, identity):
    if result is None or result.get('status') != 'SUCCESS':
        return result
    if artifact.get('version') != 1 or artifact.get('input_kind') != 'document_logit_difference':
        raise ValueError('Unsupported calibration artifact')
    if artifact.get('model_files_sha256') != identity:
        raise ValueError('Calibration model fingerprint mismatch')
    entry = artifact.get('sources', {}).get(source)
    if not entry:
        return {**result, 'calibration': {'status': 'unavailable_for_source'}}
    score = mapped_score(entry['mapping'], result['score_provenance']['logit_difference'])
    return {**result, 'calibrated_fraud_score': score, 'calibrated_normal_score': 1-score,
            'calibration': {'status': 'candidate_applied', 'method': entry['mapping']['method'],
                            'scope': source, 'deployment_validated': False}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--model-path', type=Path, default=Path(__file__).parent / 'anti_fraud_E3_macbert')
    parser.add_argument('--method', choices=['platt', 'isotonic'], default='platt')
    parser.add_argument('--minimum-groups-per-class', type=int, default=10)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        cases = load_cases(args.manifest)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    result = fit_candidates(cases, fingerprint(args.model_path), args.method, args.minimum_groups_per_class)
    result['manifest_sha256'] = hashlib.sha256(args.manifest.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Saved {args.output}; sources fitted: {len(result["sources"])}; not automatically deployed')
    return 0 if result['sources'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
