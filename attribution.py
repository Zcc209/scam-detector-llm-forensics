"""Reproducible leave-one-segment-out sensitivity; not causal attribution."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from itertools import combinations
from pathlib import Path


def fingerprint(folder):
    result = {}
    for path in sorted(Path(folder).iterdir()):
        if path.is_file() and (path.suffix in ('.json', '.safetensors', '.bin', '.txt')):
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            result[path.name] = digest.hexdigest()
    return result


def explain_segments(detector, segments, limit=64, pair_budget=0, callback=None):
    if limit < 1 or pair_budget < 0:
        raise ValueError('Invalid experiment budget')
    segments = [str(s).strip() for s in segments if str(s).strip()]
    text = '\n'.join(segments)
    baseline = detector.predict(text)
    if baseline.get('status') != 'SUCCESS':
        return {'status': 'unavailable', 'baseline': baseline}
    rows = []
    count = min(limit, len(segments))
    indices = [round(i * (len(segments) - 1) / (count - 1)) for i in range(count)] if count > 1 else [0]
    total = 1 + len(indices) + min(pair_budget, len(indices) * (len(indices) - 1) // 2)
    if callback:
        callback(1, total)
    for index in indices:
        segment = segments[index]
        perturbed = detector.predict('\n'.join(segments[:index] + segments[index + 1:]))
        usable = perturbed.get('status') == 'SUCCESS'
        score = perturbed.get('fraud_confidence') if usable else None
        rows.append({'segment_index': index, 'text': segment, 'removed_prediction': perturbed.get('prediction'),
                     'removed_fraud_score': score,
                     'delta_fraud_score': baseline['fraud_confidence'] - score if usable else None})
        if callback:
            callback(1 + len(rows), total)
    ordered = sorted(rows, key=lambda row: abs(row['delta_fraud_score'] or 0), reverse=True)
    pairs = []
    for a, b in list(combinations(ordered, 2))[:pair_budget]:
        removed = {a['segment_index'], b['segment_index']}
        result = detector.predict('\n'.join(s for i, s in enumerate(segments) if i not in removed))
        score = result.get('fraud_confidence') if result.get('status') == 'SUCCESS' else None
        valid = all(value is not None for value in (score, a['delta_fraud_score'], b['delta_fraud_score']))
        delta = baseline['fraud_confidence'] - score if score is not None else None
        pairs.append({'segment_indices': sorted(removed), 'removed_fraud_score': score,
                      'joint_delta': delta,
                      'nonadditivity': delta - a['delta_fraud_score'] - b['delta_fraud_score'] if valid else None})
        if callback:
            callback(1 + len(rows) + len(pairs), total)
    return {'status': 'completed', 'method': 'leave_one_segment_out', 'baseline': baseline,
            'input_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'total_segments': len(segments), 'tested_segments': len(rows),
            'coverage_complete': len(rows) == len(segments), 'segments': rows,
            'selection': 'uniform_document_order', 'tested_indices': indices,
            'pairs': pairs, 'pair_selection': 'largest_absolute_single_effects_first',
            'inference_calls': 1 + len(rows) + len(pairs),
            'limitations': ['Deletion changes context and may change token chunks; effects are not additive.',
                            'Positive delta means removal reduced the Fraud score, not proof of scam intent.',
                            'This is local model sensitivity, not calibration, causal explanation or accuracy validation.']}


def integrated_gradients(detector, text, steps=32, batch=8):
    """Token attributions for z_Fraud - z_Normal on the first 512-token window (Sundararajan et al., 2017).

    Baseline keeps [CLS]/[SEP] and replaces every other token embedding with [PAD]. The sum of
    attributions approximates f(x) - f(baseline); the gap is reported as a numerical check.
    """
    import torch
    cleaned = detector.clean_text(text)
    if not cleaned:
        return {'status': 'unavailable'}
    tokenizer, model = detector.tokenizer, detector.model
    encoded = tokenizer(cleaned, truncation=True, max_length=detector.max_length, return_offsets_mapping=True,
                        return_tensors='pt')
    ids = encoded['input_ids'].to(detector.device)
    mask = encoded['attention_mask'].to(detector.device)
    embed = model.get_input_embeddings()
    with torch.no_grad():
        actual = embed(ids)
        special = torch.tensor([t in tokenizer.all_special_ids for t in ids[0].tolist()], device=detector.device)
        baseline = torch.where(special[None, :, None], actual, embed(torch.full_like(ids, tokenizer.pad_token_id)))

    def margin(inputs):
        logits = model(inputs_embeds=inputs, attention_mask=mask.expand(inputs.shape[0], -1)).logits
        return logits[:, 1] - logits[:, 0]

    total = torch.zeros_like(actual)
    alphas = (torch.arange(steps, device=detector.device, dtype=actual.dtype) + 0.5) / steps  # midpoint rule
    for start in range(0, steps, batch):
        a = alphas[start:start + batch][:, None, None]
        inputs = (baseline + a * (actual - baseline)).detach().requires_grad_(True)
        grads, = torch.autograd.grad(margin(inputs).sum(), inputs)
        total += grads.sum(dim=0, keepdim=True)
    scores = ((actual - baseline) * total / steps).sum(-1)[0].detach().cpu().tolist()
    with torch.no_grad():
        gap = float(margin(actual) - margin(baseline))
    tokens = []
    for token_id, (start, end), value in zip(ids[0].tolist(), encoded['offset_mapping'][0].tolist(), scores):
        if token_id in tokenizer.all_special_ids or end <= start:
            continue
        tokens.append({'text': cleaned[start:end], 'start': start, 'end': end, 'attribution': value})
    return {'status': 'completed', 'method': 'integrated_gradients', 'target': 'logit_Fraud_minus_logit_Normal',
            'steps': steps, 'baseline': 'pad_embeddings_keep_special_tokens', 'text': cleaned, 'tokens': tokens,
            'truncated': len(detector._encode_chunks(cleaned)['input_ids']) > 1,
            'completeness_gap': gap - sum(scores), 'target_difference': gap,
            'limitations': ['Only the first 512-token window is attributed.',
                            'Gradients describe this model locally; they are not proof of scam intent.']}


def explain_report(detector, report, limit=64, pair_budget=6, callback=None):
    evidence = report.get('evidence') or {}
    sources = {}
    dom = evidence.get('dom') or {}
    ocr = evidence.get('screenshot_ocr') or {}
    gradients = {}
    dom_text = dom.get('model_text') or dom.get('text') or ''
    ocr_segments = ocr.get('model_segments') or ocr.get('texts') or (report.get('image_analysis') or {}).get('ocr_texts') or []
    for name, segments in [('dom', dom_text.splitlines()), ('screenshot_ocr', ocr_segments)]:
        if segments:
            sources[name] = explain_segments(detector, segments, limit, pair_budget, callback)
            try:
                gradients[name] = integrated_gradients(detector, '\n'.join(segments))
            except Exception as exc:
                gradients[name] = {'status': 'error', 'error': f'{type(exc).__name__}: {exc}'}
    return {'generated_at': datetime.now(timezone.utc).isoformat(), 'sources': sources, 'gradients': gradients,
            'model_files_sha256': fingerprint(detector.model_path),
            'validation_status': 'not_a_labeled_benchmark'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--model-path', type=Path, default=Path(__file__).parent / 'anti_fraud_E3_macbert')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=64)
    parser.add_argument('--pair-budget', type=int, default=6)
    args = parser.parse_args()
    if args.limit < 1 or args.pair_budget < 0:
        parser.error('--limit must be positive and --pair-budget must be nonnegative')
    from inference import FraudDetector
    result = explain_report(FraudDetector(args.model_path), json.loads(args.report.read_text(encoding='utf-8')), args.limit, args.pair_budget)
    result['source_report'] = str(args.report.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Saved: {args.output}')


if __name__ == '__main__':
    main()
