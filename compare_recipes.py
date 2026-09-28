"""Controlled comparison of the dataset recipe on the *same* test split.

The previous recipe kept the newest 900 Fraud cases overall, which were ~95% Threads. This script trains a
second MacBERT on the current manifest with those same training cases only (train-split Fraud outside the
old set is set aside; calibration and test are untouched), then scores every case with it. run_experiments.py
picks the scores up from artifacts/dataset/base_ft_old_recipe.jsonl and reports both recipes side by side.

    python compare_recipes.py        # after build_dataset.py and score_dataset.py base
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from build_dataset import old_recipe_fraud_ids

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'data' / 'dataset' / 'manifest.jsonl')
    parser.add_argument('--fraudbuster', type=Path, default=ROOT / 'data' / 'fraudbuster' / 'cases.jsonl')
    parser.add_argument('--scores', type=Path, default=ROOT / 'artifacts' / 'dataset')
    parser.add_argument('--work', type=Path, default=ROOT / 'artifacts' / 'recipe_compare')
    args = parser.parse_args()
    kept = old_recipe_fraud_ids(args.fraudbuster)
    args.work.mkdir(parents=True, exist_ok=True)
    manifest = args.work / 'manifest_old_recipe.jsonl'
    removed = 0
    with manifest.open('w', encoding='utf-8') as out:
        for line in args.manifest.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            if row['split'] == 'train' and row.get('source') == 'fraudbuster' and row['label'] == 'Fraud' and row['case_id'] not in kept:
                row['split'], removed = 'unused', removed + 1
            out.write(json.dumps(row, ensure_ascii=False) + '\n')
    print(f'old recipe: {removed} train Fraud cases set aside', flush=True)
    model = args.work / 'macbert_old_recipe'
    subprocess.run([sys.executable, str(ROOT / 'finetune_macbert.py'), '--manifest', str(manifest), '--output', str(model),
                    '--batch', '16', '--max-length', '256'], check=True)
    from score_dataset import load_jsonl, rescore_stage
    cases = [json.loads(l) for l in args.manifest.read_text(encoding='utf-8').splitlines()]
    rescore_stage(cases, args.scores / 'base.jsonl', args.scores / 'base_ft_old_recipe.jsonl', model.resolve())
    print(f"scored {len(load_jsonl(args.scores / 'base_ft_old_recipe.jsonl'))} cases -> {args.scores / 'base_ft_old_recipe.jsonl'}")


if __name__ == '__main__':
    main()
