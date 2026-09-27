import math
import unittest
from attribution import explain_segments
from fusion import fuse, source_decision


class Detector:
    def predict(self, text):
        if not text:
            return {'status': 'SKIPPED_OCR_EMPTY'}
        score = .8 if 'trigger' in text else .2
        return {'status': 'SUCCESS', 'fraud_confidence': score, 'prediction': 'Fraud' if score > .5 else 'Normal'}


class AttributionTests(unittest.TestCase):
    def test_uniform_sampling_and_explicit_pair_budget(self):
        progress = []
        result = explain_segments(Detector(), ['neutral']*10 + ['trigger'], limit=3, pair_budget=1,
                                  callback=lambda done, total: progress.append((done,total)))
        self.assertEqual(result['tested_indices'], [0,5,10])
        self.assertEqual(result['inference_calls'], 5)
        self.assertEqual(progress[-1], (5,5))
        self.assertEqual(len(result['pairs']), 1)

    def test_pair_nonadditivity_is_recorded(self):
        class JointDetector:
            def predict(self, text):
                return {'status':'SUCCESS','fraud_confidence':.9 if 'A' in text and 'B' in text else .1}
        result = explain_segments(JointDetector(), ['A','B','neutral'], pair_budget=1)
        self.assertAlmostEqual(result['pairs'][0]['nonadditivity'], -.8)

    def test_deletion_delta_and_coverage(self):
        result = explain_segments(Detector(), ['neutral', 'trigger'])
        self.assertTrue(result['coverage_complete'])
        self.assertAlmostEqual(result['segments'][1]['delta_fraud_score'], .6)
        partial = explain_segments(Detector(), ['neutral', 'trigger'], limit=1)
        self.assertFalse(partial['coverage_complete'])

    def test_empty_perturbation_is_not_zero_probability(self):
        result = explain_segments(Detector(), ['trigger'])
        self.assertIsNone(result['segments'][0]['delta_fraud_score'])

    def test_argmax_not_custom_abstention_band(self):
        for score, expected in [(.1,'Normal'),(.49,'Normal'),(.51,'Fraud'),(.6,'Fraud'),(.5,'Uncertain')]:
            result = {'status':'SUCCESS','prediction':expected if expected != 'Uncertain' else 'Normal', 'fraud_confidence':score}
            self.assertEqual(source_decision(result), expected)
        self.assertNotIn('fraud_min', fuse()['decision_policy'])

    def test_explanatory_softmax_examples(self):
        for probability in (.6, .1):
            difference = math.log(probability / (1-probability))
            self.assertAlmostEqual(1/(1+math.exp(-difference)), probability)
