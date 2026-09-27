"""Domain-adapt MacBERT to social-media posts using only the *train* split (official verdicts + hard negatives).

The released MacBERT was trained on scam conversations; social posts and forum text are out of
distribution (see docs/model_scores.md). This fine-tunes a separate candidate in
models/macbert_social/ with class- and group-balanced weights and early stopping on the
calibration split. The original model is never modified; run_experiments.py reports both.
"""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parent


def examples(manifest, split):
    rows = [json.loads(l) for l in Path(manifest).read_text(encoding='utf-8').splitlines()]
    rows = [r for r in rows if r['split'] == split and (r.get('text') or '').strip()]
    # Weight by independent source (PTT: the article, not the whole board, which is only the split unit).
    key = lambda r: r.get('weight_group') or r['group_id']
    size = Counter(key(r) for r in rows)
    weight = [1 / size[key(r)] for r in rows]
    per_class = Counter()
    for r, w in zip(rows, weight):
        per_class[r['label']] += w
    weight = [w * sum(per_class.values()) / (2 * per_class[r['label']]) for r, w in zip(rows, weight)]
    return [(r['text'], int(r['label'] == 'Fraud'), w) for r, w in zip(rows, weight)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'data' / 'dataset' / 'manifest.jsonl')
    parser.add_argument('--base', type=Path, default=ROOT / 'anti_fraud_E3_macbert')
    parser.add_argument('--output', type=Path, default=ROOT / 'models' / 'macbert_social')
    parser.add_argument('--epochs', type=int, default=4)
    parser.add_argument('--lr', type=float, default=2e-5)
    parser.add_argument('--batch', type=int, default=16)
    parser.add_argument('--max-length', type=int, default=256)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--max-gpu-temp', type=int, default=80, help='pause training while the GPU is at or above this temperature')
    args = parser.parse_args()
    import torch
    from inference import FraudDetector
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    detector = FraudDetector(args.base)
    model, tokenizer, device = detector.model, detector.tokenizer, detector.device
    train, calib = examples(args.manifest, 'train'), examples(args.manifest, 'calibration')
    print(f'train {len(train)} calibration {len(calib)} device {device}', flush=True)

    def batches(rows, shuffle):
        order = list(range(len(rows)))
        if shuffle:
            random.shuffle(order)
        for start in range(0, len(order), args.batch):
            chunk = [rows[i] for i in order[start:start + args.batch]]
            encoded = tokenizer([detector.clean_text(t) for t, _, _ in chunk], truncation=True,
                                max_length=args.max_length, padding=True, return_tensors='pt').to(device)
            yield encoded, torch.tensor([y for _, y, _ in chunk], device=device), torch.tensor([w for _, _, w in chunk], device=device)

    def loss_on(rows):
        model.eval()
        total, weight = 0.0, 0.0
        with torch.no_grad():
            for encoded, y, w in batches(rows, False):
                losses = torch.nn.functional.cross_entropy(model(**encoded).logits, y, reduction='none')
                total += float((losses * w).sum())
                weight += float(w.sum())
        return total / weight

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    steps = args.epochs * math.ceil(len(train) / args.batch)
    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lambda s: min(1.0, (s + 1) / (0.1 * steps)) * max(0.0, (steps - s) / steps))
    def cool_down(limit=args.max_gpu_temp):
        """Laptops can hard-power-off under sustained full load; pause whenever the GPU runs hot."""
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
            print(f'GPU {temp}°C, pausing to cool down', flush=True)
            time.sleep(15)

    best, history = loss_on(calib), []
    print(f'epoch 0 calibration loss {best:.4f}', flush=True)
    args.output.mkdir(parents=True, exist_ok=True)
    for epoch in range(1, args.epochs + 1):
        model.train()
        for step, (encoded, y, w) in enumerate(batches(train, True)):
            if device.type == 'cuda' and step % 10 == 0:
                cool_down()
            losses = torch.nn.functional.cross_entropy(model(**encoded).logits, y, reduction='none')
            loss = (losses * w).sum() / w.sum()
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
        current = loss_on(calib)
        history.append({'epoch': epoch, 'calibration_loss': current})
        print(f'epoch {epoch} calibration loss {current:.4f}', flush=True)
        if current < best:
            best = current
            model.save_pretrained(args.output)
            tokenizer.save_pretrained(args.output)
    (args.output / 'training_info.json').write_text(json.dumps({
        'base_model': str(args.base), 'base_sha256': detector.model_identity, 'manifest': str(args.manifest),
        'train_examples': len(train), 'calibration_examples': len(calib), 'history': history,
        'best_calibration_loss': best, 'settings': vars(args) | {'manifest': str(args.manifest), 'base': str(args.base),
                                                               'output': str(args.output)},
        'note': 'Trained on train split only; test split untouched.'}, ensure_ascii=False, indent=2), encoding='utf-8')
    if not (args.output / 'config.json').exists():
        print('No epoch improved calibration loss; candidate not saved.')


if __name__ == '__main__':
    main()
