"""Batch fine-tuning of reviewed real cases; never replaces the deployed model."""
import argparse
from collections import Counter
import hashlib
import math
from pathlib import Path
import random

from review_workflow import ROOT, load_corpus, write_json


def training_rows(rows):
    splits = {name: [row for row in rows if row['split'] == name] for name in ('train', 'validation')}
    for name, cases in splits.items():
        for label in ('Normal', 'Fraud'):
            if len({r['group_id'] for r in cases if r['label'] == label}) < 2:
                raise ValueError(f'{name} needs at least 2 independent groups per class (engineering guard, not statistical validity)')
    return splits


def train(corpus, model_path, output, epochs=2, learning_rate=2e-5, seed=42, layers=2):
    import torch
    from inference import FraudDetector
    rows = load_corpus(corpus)
    splits = training_rows(rows)
    output = Path(output).resolve()
    if output.exists() or Path(model_path).resolve().is_relative_to(output):
        raise ValueError('Output must be a new, separate candidate directory')
    if epochs < 1 or not math.isfinite(learning_rate) or learning_rate <= 0 or layers < 0:
        raise ValueError('Invalid training settings')
    random.seed(seed)
    torch.manual_seed(seed)
    torch.set_num_threads(min(torch.get_num_threads(), 4))
    detector = FraudDetector(model_path)
    model = detector.model
    base = model.base_model
    if not hasattr(base, 'encoder') or layers > len(base.encoder.layer):
        raise ValueError('This trainer requires a BERT encoder and a valid --layers count')
    for param in base.parameters():
        param.requires_grad = False
    if layers:
        for layer in base.encoder.layer[-layers:]:
            for param in layer.parameters():
                param.requires_grad = True
    if getattr(base, 'pooler', None):
        for param in base.pooler.parameters():
            param.requires_grad = True
    optimizer = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad), lr=learning_rate)
    prepared = {}
    for split, cases in splits.items():
        entries = []
        for row in cases:
            for source, text in row['texts'].items():
                cleaned = detector.clean_text(text)
                if not cleaned:
                    raise ValueError('Empty text after preprocessing')
                tokens = detector._encode_chunks(cleaned)
                if len(tokens['input_ids']) > 16:
                    raise ValueError('Document exceeds 16 chunks; review capture scope rather than silently truncate')
                entries.append((row, source, tokens))
        prepared[split] = entries
    counts = {split: Counter(row['group_id'] for row, _, _ in entries) for split, entries in prepared.items()}

    def logits(tokens):
        # Match inference: mean all chunk logits, then document-level loss.
        outputs = []
        for ids, mask in zip(tokens['input_ids'], tokens['attention_mask']):
            outputs.append(model(input_ids=torch.tensor([ids], device=detector.device),
                                 attention_mask=torch.tensor([mask], device=detector.device)).logits[0])
        return torch.stack(outputs).mean(0)

    history, best = [], float('inf')
    output.mkdir(parents=True)
    for epoch in range(epochs):
        model.train()
        entries = list(prepared['train'])
        random.shuffle(entries)
        # One document at a time; account-normalized gradient accumulation.
        optimizer.zero_grad()
        loss_sum = 0.
        total_groups = len(counts['train'])
        for row, _, tokens in entries:
            target = torch.tensor([int(row['label'] == 'Fraud')], device=detector.device)
            loss = torch.nn.functional.cross_entropy(logits(tokens).unsqueeze(0), target)
            weight = 1 / counts['train'][row['group_id']] / total_groups
            (loss * weight).backward()
            loss_sum += loss.item() * weight
        torch.nn.utils.clip_grad_norm_((p for p in model.parameters() if p.requires_grad), 1.)
        optimizer.step()
        model.eval()
        validation_loss = 0.
        with torch.no_grad():
            for row, _, tokens in prepared['validation']:
                target = torch.tensor([int(row['label'] == 'Fraud')], device=detector.device)
                loss = torch.nn.functional.cross_entropy(logits(tokens).unsqueeze(0), target)
                validation_loss += loss.item() / counts['validation'][row['group_id']] / len(counts['validation'])
        if not math.isfinite(validation_loss) or not math.isfinite(loss_sum):
            raise ValueError('Non-finite loss; candidate must not be deployed')
        history.append({'epoch': epoch+1, 'train_loss': loss_sum, 'validation_loss': validation_loss})
        print(history[-1], flush=True)
        if validation_loss < best:
            best = validation_loss
            model.save_pretrained(output)
            detector.tokenizer.save_pretrained(output)
    write_json(output / 'training_record.json', {
        'status': 'candidate_not_deployed', 'parent_model_sha256': detector.model_identity,
        'corpus_sha256': hashlib.sha256(Path(corpus).read_bytes()).hexdigest(),
        'epochs': epochs, 'seed': seed, 'learning_rate': learning_rate, 'unfrozen_encoder_layers': layers,
        'objective': 'account-balanced document cross entropy on mean chunk logits',
        'optimizer_steps': epochs, 'selection': 'minimum validation loss; calibration/test never used',
        'history': history, 'group_counts': {k: len(v) for k,v in counts.items()},
        'limitations': ['Small-data adaptation is an experiment, not a validated improvement.',
                        'Audit overlap with the original training corpus; include historical reviewed examples to monitor forgetting.']})
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--model-path', type=Path, default=ROOT/'anti_fraud_E3_macbert')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--epochs', type=int, default=2)
    parser.add_argument('--learning-rate', type=float, default=2e-5)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--layers', type=int, default=2, help='Unfreeze last N encoder layers; 0 trains pooler/classifier only')
    args = parser.parse_args()
    try:
        print(train(args.corpus, args.model_path, args.output, args.epochs, args.learning_rate, args.seed, args.layers))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
