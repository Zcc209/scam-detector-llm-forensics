"""Tiny random BERT plumbing test, never a fraud accuracy benchmark."""
import json
import os
from pathlib import Path
import tempfile
import unittest


@unittest.skipUnless(os.environ.get('RUN_TRAINING_TESTS') == '1', 'Opt-in local tiny-BERT training smoke test')
class TrainingSmokeTests(unittest.TestCase):
    def test_train_save_reload_score_and_calibrate(self):
        from transformers import BertConfig, BertForSequenceClassification, BertTokenizerFast
        from review_workflow import write_json, score_corpus
        from train_reviewed import train
        from calibration import fit_candidates
        from attribution import fingerprint
        from evaluate import load_cases, evaluate
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = root/'base'; base.mkdir()
            vocab = ['[PAD]','[UNK]','[CLS]','[SEP]','[MASK]','normal','fraud','text']+[str(i) for i in range(16)]
            (base/'vocab.txt').write_text('\n'.join(vocab), encoding='utf-8')
            tokenizer = BertTokenizerFast(vocab_file=str(base/'vocab.txt'))
            tokenizer.save_pretrained(base)
            config = BertConfig(vocab_size=len(vocab), hidden_size=16, num_hidden_layers=2,
                                num_attention_heads=2, intermediate_size=32,
                                id2label={0:'Normal',1:'Fraud'}, label2id={'Normal':0,'Fraud':1})
            BertForSequenceClassification(config).save_pretrained(base)
            identity = fingerprint(base)
            rows = []
            for index in range(16):
                report = root/f'{index}.json'
                write_json(report, {})
                label = 'Fraud' if index % 2 else 'Normal'
                text = f'{label.lower()} text {index}'
                # These fixture metadata are scoped to a temporary directory only.
                rows.append(dict(case_id=str(index), group_id=str(index), split=('train','validation','calibration','test')[index//4],
                                 label=label, label_source='unit test fixture', reviewer='test', permission='test', notes='test only',
                                 status='reviewed', synthetic=False, scope='content', report_path=str(report),
                                 texts={'dom':text,'screenshot_ocr':text}))
            corpus = root/'corpus.jsonl'
            corpus.write_text(''.join(json.dumps(row)+'\n' for row in rows), encoding='utf-8')
            candidate = train(corpus, base, root/'candidate', epochs=1, layers=1)
            self.assertEqual(identity, fingerprint(base))
            score_corpus(corpus, candidate, root/'scores')
            cases = load_cases(root/'scores'/'manifest.csv')
            self.assertEqual(evaluate(cases)['test_count'], 4)
            mapping = fit_candidates(cases, fingerprint(candidate), 'platt', minimum=2)
            self.assertEqual(set(mapping['sources']), {'dom','screenshot_ocr'})
            write_json(root/'mapping.json', mapping)
            score_corpus(corpus, candidate, root/'calibrated', root/'mapping.json')
            self.assertEqual(len(load_cases(root/'calibrated'/'manifest.csv')), 12)
