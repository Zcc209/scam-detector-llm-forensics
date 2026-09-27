from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from link_tracer import candidates, judge, trace, unwrap


def hop(url, **extra):
    from urllib.parse import urlsplit
    return {'normalized_url': url, 'hostname': urlsplit(url).hostname, 'listed_165': None,
            'possible_impersonated_platform': None, **extra}


class LinkTracerTests(unittest.TestCase):
    def test_unwrap_and_candidate_priority(self):
        self.assertEqual(unwrap('https://l.instagram.com/?u=https%3A%2F%2Fshop.example%2Fa&e=x'), 'https://shop.example/a')
        picked = candidates('https://www.instagram.com/acct/',
                            [{'href': 'https://l.instagram.com/?u=https%3A%2F%2Fbit.ly%2Fabc', 'visible': True},
                             {'href': 'https://www.instagram.com/explore/', 'visible': True},
                             {'href': 'https://hidden.example/', 'visible': False}],
                            [('screenshot_text', '點我 lin.ee/AbCd12 或 deal.shop/p/1，信箱 a@b.com')])
        self.assertEqual([p['url'] for p in picked], ['https://bit.ly/abc', 'https://lin.ee/AbCd12', 'https://deal.shop/p/1'])
        self.assertEqual(picked[1]['source'], 'screenshot_text')

    def test_email_domains_are_not_followed(self):
        # OCR often splits "business@dcard.cc" into "business@ dcard.CC"; that is contact info, not a link.
        picked = candidates(None, [], [('screenshot_text', '合作請洽 business@ dcard.CC\nContact: pr@brand.com.tw 或 shop.example.top/buy')])
        self.assertEqual([p['url'] for p in picked], ['https://shop.example.top/buy'])

    def test_line_group_via_shortener_is_medium(self):
        result = judge('https://bit.ly/x', {'status': 'success', 'final_url': 'https://line.me/R/ti/g/abc',
                                            'navigation_checks': [hop('https://bit.ly/x'), hop('https://go.track-me.xyz/y'), hop('https://line.me/R/ti/g/abc')],
                                            'full_page_text': '加入群組'})
        names = {f['signal'] for f in result['flags']}
        self.assertTrue({'shortener', 'multi_hop', 'private_chat'} <= names)
        self.assertEqual(result['risk'], 'medium')
        self.assertEqual(result['chain'][-1], 'https://line.me/R/ti/g/abc')

    def test_line_official_account_hops_stay_within_one_site(self):
        # A business bio link: lin.ee -> line.me -> page.line.me is all LINE, not a multi-site bounce.
        result = judge('https://lin.ee/CRfrzbf', {'status': 'success', 'final_url': 'https://page.line.me/959qrnwi',
                                                  'navigation_checks': [hop('https://lin.ee/CRfrzbf'), hop('https://line.me/R/ti/p/@959qrnwi'),
                                                                        hop('https://page.line.me/959qrnwi')]})
        self.assertNotIn('multi_hop', {f['signal'] for f in result['flags']})
        self.assertEqual(result['risk'], 'low')

    def test_one_page_shop_and_165_hop(self):
        shop = judge('https://a.example', {'status': 'success', 'final_url': 'https://super-deal.shop/',
                                           'navigation_checks': [hop('https://a.example'), hop('https://super-deal.shop/')],
                                           'full_page_text': '限時特價 原價 3990 貨到付款 僅剩 5 件 立即購買'})
        self.assertIn('one_page_shop', {f['signal'] for f in shop['flags']})
        self.assertEqual(shop['risk'], 'medium')
        company = judge('https://a.example', {'status': 'success', 'final_url': 'https://brand.com.tw/',
                                              'navigation_checks': [hop('https://brand.com.tw/')],
                                              'full_page_text': '限時特價 原價 貨到付款 統一編號 12345678 退貨政策'})
        self.assertEqual(company['risk'], 'none')
        blocked = judge('https://bit.ly/z', {'status': 'blocked', 'navigation_checks': [
            hop('https://bit.ly/z'), hop('https://bbhhshf.cc/', listed_165='11412')]})
        self.assertEqual(blocked['risk'], 'high')
        self.assertIsNone(blocked['screenshot_path'])

    def test_fake_myship_domain_is_high_but_ocr_misread_official_is_not(self):
        unreachable = {'status': 'unusable', 'unusable_reason': 'unreachable', 'navigation_checks': []}
        fake = judge('https://11j.twglo.sbs', {**unreachable, 'final_url': 'https://11j.twglo.sbs'}, 'myship7 11j.twglo.sbs', 'screenshot_text')
        self.assertEqual(fake['risk'], 'high')
        self.assertTrue({'brand_impersonation', 'random_host', 'risky_tld', 'unreachable'} <= {f['signal'] for f in fake['flags']})
        misread = judge('https://myship.T-llcom.tw', {**unreachable, 'final_url': 'https://myship.t-llcom.tw/'},
                        '請至 myship.T-llcom.tw查詢物流進度', 'screenshot_text')
        self.assertNotEqual(misread['risk'], 'high')
        self.assertIn('ocr_misread', {f['signal'] for f in misread['flags']})
        generic = judge('https://myblog.com/post/1', {'status': 'success', 'final_url': 'https://myblog.com/post/1', 'navigation_checks': []},
                        '新文章 post myblog.com', 'screenshot_text')
        self.assertEqual(generic['risk'], 'none')

    def test_brand_mismatch_uses_screenshot_domains(self):
        from image_forensics import brand_mismatch
        self.assertEqual(brand_mismatch(['myship7 11j.twglo.sbs'], ['11j.twglo.sbs'])[0]['brand'], 'myship')
        self.assertEqual(brand_mismatch(['賣貨便 myship.T-llcom.tw'], ['myship.t-llcom.tw']), [])
        self.assertEqual(brand_mismatch(['賣貨便'], ['bit.ly']), [])

    def test_listed_first_hop_is_never_captured(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp, patch('link_tracer.analyze_url', lambda url: {
                'capture_allowed': False, 'block_reason': 'Listed in 165 fraud-website data', **hop(url, listed_165='11412')}):
            result = trace(None, [], [('screenshot_text', 'https://bbhhshf.cc/')], tmp, capture=lambda *a: calls.append(a))
        self.assertEqual(calls, [])
        self.assertEqual(result['worst_risk'], 'high')


if __name__ == '__main__':
    unittest.main()
