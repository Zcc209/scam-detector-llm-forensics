import asyncio
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from domain_check import analyze_url
from run_pipeline import main, domain_assessment
from web_capture import capture
import test_pipeline


class GateTests(unittest.TestCase):
    def test_rejected_before_browser_in_all_modes(self):
        for url in ('http://www.intagram.com', 'https://www.intagram.com'):
            for mode in ([], ['--capture-only'], ['--domain-only']):
                with self.subTest(url=url, mode=mode), tempfile.TemporaryDirectory() as output:
                    args = ['run_pipeline.py', '--url', url, '--output-dir', output, *mode]
                    with patch.object(sys, 'argv', args), patch('web_capture.capture') as browser, redirect_stdout(io.StringIO()):
                        self.assertEqual(main(), 2)
                    browser.assert_not_called()
                    report = json.loads((Path(output) / 'report.json').read_text(encoding='utf-8'))
                    self.assertEqual(report['status'], 'blocked')
                    self.assertIsNone(report['image_analysis'])
                    self.assertIsNone(report['browser_capture']['final_domain_analysis'])
                    self.assertFalse((Path(output) / 'page.png').exists())

    def test_direct_capture_does_not_resolve_or_launch(self):
        with patch('web_capture.require_public_url') as dns:
            result = capture('https://www.intagram.com', '.')
        dns.assert_not_called()
        self.assertEqual(result['status'], 'blocked')

    def test_official_destination_never_lowers_initial_score(self):
        initial = analyze_url('http://www.intagram.com')
        final = analyze_url('https://www.instagram.com/pokemongoapp/')
        result = domain_assessment(initial, {'final_domain_analysis': final})
        self.assertEqual(result['domain_risk_score'], 80)
        self.assertEqual(result['domain_risk_level'], 'High')
        self.assertFalse(result['domain_check_passed'])

    def test_bad_redirect_is_aborted_before_dns_and_remains_evidence(self):
        result, context = test_pipeline.CaptureTests().run_fixture()
        session = context.new_cdp_session.return_value
        guard = session.on.call_args.args[1]
        event = {'requestId': 'redirect', 'resourceType': 'Document',
                 'request': {'url': 'https://www.intagram.com'}}
        with patch('web_capture.require_public_url') as dns:
            asyncio.run(guard(event))
        dns.assert_not_called()
        session.send.assert_called_with('Fetch.failRequest', {'requestId': 'redirect', 'errorReason': 'BlockedByClient'})
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['navigation_checks'][-1]['domain_status'], 'lookalike')

    def test_cdn_assets_do_not_need_to_be_social_domains(self):
        _, context = test_pipeline.CaptureTests().run_fixture()
        session = context.new_cdp_session.return_value
        guard = session.on.call_args.args[1]
        event = {'requestId': 'image', 'resourceType': 'Image', 'request': {'url': 'https://cdn.example.com/a.png'}}
        with patch('web_capture.require_public_url'):
            asyncio.run(guard(event))
        session.send.assert_called_with('Fetch.continueRequest', {'requestId': 'image'})


if __name__ == '__main__':
    unittest.main()
