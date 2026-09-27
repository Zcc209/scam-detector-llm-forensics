import unittest
from calibration import apply_candidate, fit_candidates, fit_mapping, mapped_score, statistics


class CalibrationTests(unittest.TestCase):
    def test_isotonic_ties_monotonicity_and_clipping(self):
        mapping = fit_mapping([(-2, 0, 1), (-1, 1, 1), (0, 0, 1), (1, 1, 1)], 'isotonic')
        self.assertEqual(mapping['y'], [0, .5, .5, 1])
        self.assertEqual(mapped_score(mapping, -99), 0)
        self.assertEqual(mapped_score(mapping, 99), 1)
        ties = fit_mapping([(0, 0, 1), (0, 1, 1)], 'isotonic')
        self.assertEqual(mapped_score(ties, 0), .5)

    def test_platt_is_finite_and_monotonic(self):
        mapping = fit_mapping([(-4, 0, 1), (-2, 0, 1), (2, 1, 1), (4, 1, 1)], 'platt')
        self.assertGreaterEqual(mapping['slope'], 0)
        self.assertLess(mapped_score(mapping, -2), mapped_score(mapping, 2))
        self.assertGreater(mapped_score(mapping, -2), 0)

    def test_class_and_parameter_guards(self):
        with self.assertRaises(ValueError):
            fit_mapping([(1, 1, 1)], 'platt')
        with self.assertRaises(ValueError):
            mapped_score({'method':'platt','slope':float('nan'),'intercept':0}, 1)
        with self.assertRaises(ValueError):
            mapped_score({'method':'isotonic','x':[1,0],'y':[0,1]}, 1)

    def cases(self):
        identity = {'model.safetensors': 'fixture-not-real-model'}
        rows = []
        for split in ('calibration', 'test'):
            for i, (x, label) in enumerate([(-2, 'Normal'), (-1, 'Normal'), (1, 'Fraud'), (2, 'Fraud')]):
                model = {'status':'SUCCESS','score_provenance':{'model_files_sha256':identity,'logit_difference':x}}
                rows.append({'split':split,'label':label,'group_id':split+str(i),'report':{'evidence':{'dom':{'model':model}}}})
        return rows, identity

    def test_held_out_labels_do_not_fit_and_raw_is_retained(self):
        rows, identity = self.cases()
        artifact = fit_candidates(rows, identity, 'isotonic', 2)
        mapping = artifact['sources']['dom']['mapping']
        for row in rows:
            if row['split'] == 'test':
                row['label'] = 'Normal' if row['label'] == 'Fraud' else 'Fraud'
        changed = fit_candidates(rows, identity, 'isotonic', 2)
        self.assertEqual(mapping, changed['sources']['dom']['mapping'])
        original = {'status':'SUCCESS','fraud_confidence':.8,'score_provenance':{'logit_difference':1}}
        result = apply_candidate(original, 'dom', artifact, identity)
        self.assertEqual(result['fraud_confidence'], .8)
        self.assertFalse(result['calibration']['deployment_validated'])
        with self.assertRaises(ValueError):
            apply_candidate(original, 'dom', artifact, {})

    def test_insufficient_groups_do_not_create_mapping(self):
        rows, identity = self.cases()
        artifact = fit_candidates(rows, identity, 'platt')
        self.assertFalse(artifact['sources'])

    def test_weighted_metrics(self):
        result = statistics([(.8, 1, 1), (.2, 0, 1)])
        self.assertAlmostEqual(result['brier'], .04)
        self.assertAlmostEqual(result['ece'], .2)
