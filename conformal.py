"""Class-conditional (Mondrian) split conformal prediction: a principled "Unknown".

Fitted on a held-out calibration split. For a new case with Fraud score p the prediction set is
    {Fraud}         if 1 - p <= q_Fraud and p > q_Normal
    {Normal}        if p <= q_Normal and 1 - p > q_Fraud
    {Fraud, Normal} or {} otherwise  -> Unknown (abstain)
Under exchangeability each true class is covered with probability >= 1 - alpha, whatever the
classifier. Unknown therefore means "at this error budget the evidence does not single out a
class", instead of a hand-set score band. The guarantee is only as good as the calibration data's
resemblance to deployment (source platforms, time period, prevalence).
"""
import math


def quantile(scores, alpha):
    """Finite-sample conformal quantile: the ceil((n+1)(1-alpha))-th smallest score."""
    n = len(scores)
    if n == 0:
        raise ValueError('Empty calibration class')
    rank = math.ceil((n + 1) * (1 - alpha))
    return 1.0 if rank > n else sorted(scores)[rank - 1]


def fit(probabilities, labels, alpha=0.1):
    fraud = [1 - p for p, y in zip(probabilities, labels) if y == 1]
    normal = [p for p, y in zip(probabilities, labels) if y == 0]
    return {'method': 'mondrian_split_conformal', 'alpha': alpha, 'score': 'one_minus_class_probability',
            'q_fraud': quantile(fraud, alpha), 'q_normal': quantile(normal, alpha),
            'n_fraud': len(fraud), 'n_normal': len(normal),
            'guarantee': f'per-class coverage >= {1 - alpha:.0%} under exchangeability with the calibration split'}


def predict(p, model):
    members = [name for name, ok in (('Fraud', 1 - p <= model['q_fraud']), ('Normal', p <= model['q_normal'])) if ok]
    if len(members) == 1 and (members[0] == 'Fraud') != (p > 0.5):
        # Asymmetric class quantiles can leave only the less likely class; enlarging the set keeps coverage.
        members = ['Fraud', 'Normal']
    return {'set': members, 'prediction': members[0] if len(members) == 1 else 'Unknown',
            'basis': 'conformal_singleton' if len(members) == 1 else 'conformal_ambiguous' if members else 'conformal_empty'}


def evaluate(probabilities, labels, model):
    rows = [(predict(p, model), y) for p, y in zip(probabilities, labels)]
    result = {}
    for label, name in ((1, 'Fraud'), (0, 'Normal')):
        subset = [r for r, y in rows if y == label]
        result[f'coverage_{name.lower()}'] = sum(name in r['set'] for r in subset) / len(subset) if subset else None
    decided = [(r['prediction'], y) for r, y in rows if r['prediction'] != 'Unknown']
    result['unknown_rate'] = 1 - len(decided) / len(rows) if rows else None
    result['accuracy_when_decided'] = (sum((p == 'Fraud') == bool(y) for p, y in decided) / len(decided)) if decided else None
    return result


def adjust_prior(p, sample_prior, deploy_prior):
    """Bayes prior-shift correction: the labeled set is roughly balanced, deployment is not."""
    p = min(max(p, 1e-9), 1 - 1e-9)
    logit = math.log(p / (1 - p)) + math.log(deploy_prior / (1 - deploy_prior)) - math.log(sample_prior / (1 - sample_prior))
    return 1 / (1 + math.exp(-logit))
