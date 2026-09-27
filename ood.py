"""Out-of-distribution check: is this text unlike anything the classifier was fitted on?

Mahalanobis distance of the MacBERT [CLS] embedding from the training-split posts, in a 64-dim
PCA space (Lee et al., 2018, simplified). The threshold is the 99th percentile of calibration-split
distances. A flagged input gets a warning that the text score is unreliable; it does not change
the decision by itself.
"""
import json
from pathlib import Path

import numpy as np

PATH = Path(__file__).resolve().parent / 'models' / 'ood_reference.json'


def embed(detector, texts, batch=16):
    import torch
    vectors = []
    for start in range(0, len(texts), batch):
        chunk = [detector.clean_text(t) or '空' for t in texts[start:start + batch]]
        encoded = detector.tokenizer(chunk, truncation=True, max_length=256, padding=True, return_tensors='pt').to(detector.device)
        with torch.no_grad():
            hidden = detector.model.base_model(**encoded).last_hidden_state[:, 0]
        vectors.append(hidden.float().cpu().numpy())
    return np.concatenate(vectors) if vectors else np.zeros((0, 768))


def fit(train_vectors, calibration_vectors, dims=64, quantile=0.99):
    mean = train_vectors.mean(axis=0)
    centered = train_vectors - mean
    _, singular, components = np.linalg.svd(centered, full_matrices=False)
    variance = singular[:dims] ** 2 / (len(train_vectors) - 1) + 1e-6
    reference = {'mean': mean.tolist(), 'components': components[:dims].tolist(), 'variance': variance.tolist()}
    distances = distance(reference, calibration_vectors)
    reference['threshold'] = float(np.quantile(distances, quantile))
    reference['quantile'] = quantile
    return reference


def distance(reference, vectors):
    projected = (np.asarray(vectors) - np.asarray(reference['mean'])) @ np.asarray(reference['components']).T
    return np.sqrt((projected ** 2 / np.asarray(reference['variance'])).sum(axis=1))


def load(path=PATH):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def check(detector, text, reference=None):
    reference = reference or load()
    if not reference or not (text or '').strip():
        return {'status': 'unavailable'}
    value = float(distance(reference, embed(detector, [text]))[0])
    return {'status': 'SUCCESS', 'distance': value, 'threshold': reference['threshold'],
            'out_of_distribution': value > reference['threshold'],
            'note': 'Distance from training posts in MacBERT embedding space; flagged text scores are less reliable.'}
