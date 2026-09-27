from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from account_signals import extract, profile_counts
from alignment import align_ocr, image_only_segments
import conformal
from content_filter import filter_text, filter_viewport
import fusion_model
from llm_evidence import verify


def item(text, x, y, region='content'):
    return {'text': text, 'raw': text, 'region': region,
            'boxes': [[x + 10 * i, y, x + 10 * i + 10, y + 12] for i in range(len(text))]}


class AlignmentTests(unittest.TestCase):
    def capture(self):
        return {'viewport': {'dpr': 2, 'width': 800, 'height': 600},
                'items': [item('Instagram', 0, 0), item('hello', 0, 100)],
                'images': [{'box': [300, 300, 500, 500], 'alt': 'post'}]}

    def ocr(self, text, left, top, right, bottom):  # screenshot pixels (dpr 2)
        return {'text': text, 'confidence': .9, 'accepted': True,
                'bbox': [[left * 2, top * 2], [right * 2, top * 2], [right * 2, bottom * 2], [left * 2, bottom * 2]]}

    def test_duplicate_misread_image_and_unlocated(self):
        result = align_ocr(self.capture(), [
            self.ocr('hello', 0, 98, 52, 114),         # same DOM text
            self.ocr('Instagum', 0, -2, 92, 14),       # DOM text misread by OCR
            self.ocr('年薪約100', 320, 320, 420, 340),   # inside the <img>
            self.ocr('canvas text', 600, 50, 700, 70),  # no DOM, no image
        ])
        self.assertEqual(result['counts'], {'dom_duplicate': 1, 'ocr_variant': 1, 'image_text': 1, 'unlocated_text': 1})
        self.assertEqual(result['ocr_variant'][0]['dom_text'], 'Instagram')
        self.assertEqual(image_only_segments(result), ['年薪約100', 'canvas text'])

    def test_unavailable_without_dpr(self):
        self.assertFalse(align_ocr({'items': []}, [self.ocr('x', 0, 0, 1, 1)])['available'])


class FilterAndSignalTests(unittest.TestCase):
    def test_landmarks_removed_but_risky_text_kept(self):
        capture = {'items': [item('學測模擬測驗', 0, 0, 'menu'), item('加LINE領取 @abc12345', 0, 20, 'header'),
                             item('今天的貼文內容很長', 0, 40)]}
        result = filter_viewport(capture)
        self.assertEqual(result['text'].splitlines(), ['加LINE領取 @abc12345', '今天的貼文內容很長'])
        self.assertEqual(result['removed'][0]['reason'], 'landmark:menu')

    def test_ui_lexicon_and_logo(self):
        removed = {r['text'] for r in filter_text('登入\n註冊\nInstagum\n12.3萬\n年薪約100萬怎麼存錢')['removed']}
        self.assertEqual(removed, {'登入', '註冊', 'Instagum', '12.3萬'})

    def test_account_signals(self):
        result = extract('0 Followers • 5 Threads • 麻煩加我的LINE🆔：@607zqfmb @panther.3569267')
        names = {s['signal'] for s in result['signals']}
        self.assertTrue({'off_platform_contact', 'throwaway_profile', 'random_digit_handle'} <= names)
        self.assertEqual(profile_counts('147.3萬\n位粉絲')['followers'], 1473000)
        self.assertIn('large_audience', {s['signal'] for s in extract('147.3萬 位粉絲')['signals']})


class LLMVerifyTests(unittest.TestCase):
    def test_hallucinated_quotes_are_dropped(self):
        raw = {'speech_act': 'solicitation', 'addresses_reader': True, 'risk': 'high', 'rationale': 'x',
               'tactics': [{'tactic': 'guaranteed_return', 'quote': '保證 獲利'},
                           {'tactic': 'urgency', 'quote': '名額只剩三位'}]}
        result = verify(raw, '老師帶單保證獲利，快加入')
        self.assertEqual([t['tactic'] for t in result['tactics']], ['guaranteed_return'])
        self.assertEqual(len(result['rejected_quotes']), 1)
        self.assertEqual(result['features']['llm_tactic_count'], 1)


class ConformalAndFusionTests(unittest.TestCase):
    def test_conformal_quantile_and_sets(self):
        model = conformal.fit([.9, .8, .7, .95, .6, .2, .1, .3, .4, .05], [1, 1, 1, 1, 1, 0, 0, 0, 0, 0], alpha=.2)
        self.assertEqual(conformal.predict(.97, model)['prediction'], 'Fraud')
        self.assertEqual(conformal.predict(.02, model)['prediction'], 'Normal')
        self.assertEqual(conformal.predict(.5, model)['prediction'], 'Unknown')
        self.assertAlmostEqual(conformal.adjust_prior(.5, .5, .1), .1)

    def test_fusion_fit_and_contributions(self):
        rows, labels = [], []
        for label in (0, 1) * 20:
            evidence = {'text_model': {'status': 'SUCCESS', 'score_provenance': {'logit_difference': 3 if label else -3}},
                        'ocr_model': None, 'account': {'features': {'off_platform_contact': label}}, 'llm': {'status': 'unavailable'}}
            rows.append(fusion_model.vectorize(evidence))
            labels.append(label)
        model = fusion_model.fit(rows, labels, [1] * len(rows), fusion_model.feature_names(['text', 'account', 'llm']))
        model['conformal'] = conformal.fit([fusion_model.score(model, r) for r in rows], labels, .1)
        decision = fusion_model.decide({'text_model': {'status': 'SUCCESS', 'score_provenance': {'logit_difference': 3}},
                                        'account': {'features': {'off_platform_contact': 1}}}, model)
        self.assertEqual(decision['prediction'], 'Fraud')
        self.assertEqual(decision['contributions'][0]['feature'], 'text_logit')


class ImageForensicsTests(unittest.TestCase):
    def test_hash_match_and_ela(self):
        from PIL import Image, ImageDraw
        from image_forensics import analyze, hashes
        with tempfile.TemporaryDirectory() as tmp:
            image = Image.new('RGB', (200, 200), 'white')
            ImageDraw.Draw(image).rectangle([40, 40, 160, 120], fill='red')
            path = Path(tmp) / 'a.jpg'
            image.save(path, quality=85)
            index = [{'case_id': 'x', 'source_url': 'u', **hashes(image)}]
            result = analyze(path, index=index, texts=['中華郵政 包裹通知'], domain='post-tw.top', output_dir=tmp)
            self.assertEqual(result['known_scam_matches'][0]['case_id'], 'x')
            self.assertEqual(result['brand_domain_mismatch'][0]['brand'], '中華郵政')
            self.assertTrue((Path(tmp) / 'ela.png').is_file())
            self.assertEqual(analyze(path, index=[], texts=['中華郵政'], domain='www.instagram.com')['brand_domain_mismatch'], [])


if __name__ == '__main__':
    unittest.main()
