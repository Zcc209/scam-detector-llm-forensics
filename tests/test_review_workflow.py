import copy
import json
from pathlib import Path
import tempfile
import unittest

from alignment import compare_texts
from review_workflow import enqueue, build, validate_rows, write_json
from train_reviewed import training_rows


def row(identifier='a', **values):
    return dict({'case_id': identifier, 'group_id': identifier, 'split': 'train', 'label': 'Normal',
                 'label_source': 'fixture evidence', 'reviewer': 'tester', 'permission': 'fixture',
                 'notes': 'fixture only', 'status': 'reviewed', 'synthetic': False, 'scope': 'content',
                 'texts': {'dom': 'unique text '+identifier}}, **values)


class ReviewWorkflowTests(unittest.TestCase):
    def test_group_pair_and_text_leaks(self):
        for change in ({'group_id':'a'}, {'pair_id':'pair'}, {'texts':{'dom':'unique text a'}}):
            a = row(pair_id='pair')
            b = row('b', split='test', **change)
            with self.assertRaises(ValueError):
                validate_rows([a,b])

    def test_pending_synthetic_and_account_labels_rejected(self):
        for change in ({'status':'pending'}, {'synthetic':True}, {'scope':'account'}, {'label':'Unresolved'}):
            with self.assertRaises(ValueError):
                validate_rows([row(**change)])

    def test_training_requires_both_labels_in_validation(self):
        with self.assertRaises(ValueError):
            training_rows([row()])

    def test_enqueue_idempotent_and_no_automatic_label(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = root/'report.json'
            write_json(report, {'evidence': {'dom': {'text':'visible content', 'model':{'status':'SUCCESS'}}}})
            target = enqueue(report, root/'queue')
            self.assertEqual(enqueue(report, root/'queue'), target)
            self.assertEqual(json.loads(target.read_text())['label'], 'Unresolved')
            with self.assertRaisesRegex(ValueError, 'No reviewed'):
                build(root/'queue', root/'corpus.jsonl')
            reviewed = json.loads(target.read_text())
            reviewed.update(row())
            write_json(target, reviewed)
            self.assertEqual(build(root/'queue', root/'corpus.jsonl'), 1)
            with self.assertRaisesRegex(ValueError, 'immutable'):
                build(root/'queue', root/'corpus.jsonl')
            write_json(report, {'changed':True})
            with self.assertRaisesRegex(ValueError, 'changed'):
                build(root/'queue', root/'v2.jsonl')

    def test_alignment_diagnostic_does_not_set_risk(self):
        result = compare_texts('Dcard', 'dcard')
        self.assertEqual(result['character_overlap'], 1.)
        self.assertNotIn('risk_score', result)
        self.assertEqual(compare_texts(None, 'image')['scope'], 'image_only')
