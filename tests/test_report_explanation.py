import copy
import unittest
from report_explanation import annotate, spatial_coverage, markdown


class ExplanationTests(unittest.TestCase):
    def report(self):
        return {'status':'incomplete', 'assessment':{'risk_level':'Unknown'},
                'content_analysis':{'status':'SUCCESS','basis':'modality_conflict'},
                'browser_capture':{'status':'success','viewport_evidence':{
                    'viewport':{'dpr':2}, 'items':[{'boxes':[[0,0,50,20]]}]}},
                'evidence':{'dom':{'text':'native','model':{'status':'SUCCESS','prediction':'Normal','fraud_confidence':.45}},
                            'screenshot_ocr':{'model':{'status':'SUCCESS','prediction':'Fraud','fraud_confidence':.71},
                                'items':[{'text':'native','accepted':True,'bbox':[[0,0],[100,0],[100,40],[0,40]]},
                                         {'text':'image text','accepted':True,'bbox':[[0,100],[100,100],[100,140],[0,140]]}]}}}

    def test_completed_is_distinct_from_unknown_and_scores_unchanged(self):
        report = self.report()
        before = copy.deepcopy(report['evidence'])
        annotate(report)
        self.assertEqual(report['status'], 'success')
        self.assertEqual(report['decision_status'], 'needs_review')
        self.assertEqual(report['assessment']['risk_level'], 'Unknown')
        self.assertEqual(report['evidence'], before)
        self.assertEqual(report['report_summary']['content_equivalence'], 'not_established')

    def test_dpr_geometry_and_no_coordinates_fallback(self):
        report = self.report()
        coverage = spatial_coverage(report)
        self.assertEqual([r['text'] for r in coverage['matched']], ['native'])
        self.assertEqual([r['text'] for r in coverage['unmatched']], ['image text'])
        report['browser_capture'] = None
        self.assertFalse(spatial_coverage(report)['available'])

    def test_errors_not_reported_as_success(self):
        report = self.report()
        report.update(status='error',error='failure')
        annotate(report)
        self.assertEqual(report['status'],'error')
        self.assertEqual(report['analysis_status'],'stopped')
        self.assertIn('不是帳號詐騙機率', markdown(report))
