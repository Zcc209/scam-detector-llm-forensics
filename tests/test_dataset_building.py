from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from build_dataset import groups, split_groups
from collect_fraudbuster import label_from_timeline, parse_detail
from score_dataset import image_only_lines


class DatasetTests(unittest.TestCase):
    def test_timeline_labels(self):
        self.assertEqual(label_from_timeline(['民眾舉報', '內政部已經確認，這是詐騙訊息，已通知Meta移除。']), 'Fraud')
        self.assertEqual(label_from_timeline(['經數發部確認，這並不是詐騙訊息。']), 'Normal')
        self.assertIsNone(label_from_timeline(['經金管會確認高風險訊息，請謹慎評估']))
        self.assertIsNone(label_from_timeline(['已通知 內政部，確認中']))

    def test_parse_detail(self):
        page = ('<img src="x" id="imgPic" alt="金融投資"><ul class="socialIcons" aria-label="出現平台"><li><img alt="Threads"></li></ul>'
                '<div class="summary-content"> <p>加LINE領飆股</p>'
                '<ol class="timeline"><li><time datetime="2026/09/25 23:52">t</time><p>民眾舉報</p></li></ol>'
                '<ol class="timeline"><li><time datetime="2026/09/26 08:58">t</time><p>內政部已經確認，這是詐騙訊息。</p></li></ol>')
        row = parse_detail(page, 'a' * 24)
        self.assertEqual((row['label'], row['category'], row['platforms'], row['text']), ('Fraud', '金融投資', ['Threads'], '加LINE領飆股'))

    def test_shared_contact_and_near_duplicates_group_together(self):
        cases = [{'case_id': '1', 'text': '飆股群組 加LINE: abcd1234 立即領取', 'label': 'Fraud'},
                 {'case_id': '2', 'text': '完全不同的文案，但一樣請加 line id: ABCD1234', 'label': 'Fraud'},
                 {'case_id': '3', 'text': '週年慶全館八折，歡迎蒞臨門市選購新品，數量有限售完為止', 'label': 'Normal'},
                 {'case_id': '4', 'text': '週年慶全館八折！歡迎蒞臨門市選購新品，數量有限售完為止喔', 'label': 'Normal'},
                 {'case_id': '5', 'text': '今天天氣很好，我們去爬山吧，山上的風景非常漂亮', 'label': 'Normal'}]
        groups(cases)
        ids = {c['case_id']: c['group_id'] for c in cases}
        self.assertEqual(ids['1'], ids['2'])
        self.assertEqual(ids['3'], ids['4'])
        self.assertNotEqual(ids['3'], ids['5'])
        split_groups(cases, seed=1)
        by_group = {}
        for case in cases:
            by_group.setdefault(case['group_id'], set()).add(case['split'])
        self.assertTrue(all(len(splits) == 1 for splits in by_group.values()))

    def test_split_keeps_every_platform_in_test(self):
        # Stratified by (label, platform): a small platform must not end up entirely in train.
        cases = [{'case_id': f'{p}{i}', 'group_id': f'{p}{i}', 'label': 'Fraud', 'platforms': [p]}
                 for p, n in (('Threads', 50), ('LINE', 10), ('Web', 10)) for i in range(n)]
        split_groups(cases, seed=1)
        for platform in ('Threads', 'LINE', 'Web'):
            with self.subTest(platform=platform):
                self.assertIn('test', {c['split'] for c in cases if c['platforms'] == [platform]})

    def test_restore_images_downloads_only_missing_files(self):
        import tempfile
        from collect_fraudbuster import restore_images
        with tempfile.TemporaryDirectory() as tmp:
            kept = Path(tmp) / 'images' / 'a.jpg'
            kept.parent.mkdir()
            kept.write_bytes(b'already here')
            cases = [{'case_id': 'a', 'image_path': str(kept)}, {'case_id': 'b', 'image_path': str(Path(tmp) / 'images' / 'b.jpg')},
                     {'case_id': 'c', 'image_path': None}]
            calls = []

            class Crawler:
                def get(self, url, binary=False):
                    calls.append(url)
                    return b'x' * 3000

            self.assertEqual(restore_images(cases, Crawler(), workers=1), 1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(kept.read_bytes(), b'already here')
            self.assertTrue((Path(tmp) / 'images' / 'b.jpg').is_file())

    def test_image_only_lines_tolerate_ocr_errors(self):
        text = '限時免費領取熱搜名單，加入官方LINE立即領取'
        self.assertEqual(image_only_lines(['限時免費領取熱搜名單', '加入官方LlNE立即領取', '保證月入十萬'], text), ['保證月入十萬'])


if __name__ == '__main__':
    unittest.main()
