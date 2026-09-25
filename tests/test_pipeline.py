import ast
import asyncio
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from domain_check import analyze_url, normalize_url, require_public_url
from web_capture import capture, is_load_error


class OCRGuardTests(unittest.TestCase):
    def pipeline(self):
        # Load the existing class without importing/downloading ML dependencies.
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'integrated_app.py').read_text(encoding='utf-8'))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        namespace = {'cv2': MagicMock()}
        exec(compile(ast.Module(body=[cls], type_ignores=[]), 'app.py', 'exec'), namespace)
        namespace['cv2'].cvtColor.return_value.mean.return_value = 200
        pipeline = namespace['ScamDetectionPipeline'].__new__(namespace['ScamDetectionPipeline'])
        pipeline.reader = MagicMock()
        pipeline.detector = MagicMock()
        return pipeline

    def test_empty_ocr_does_not_predict(self):
        pipeline = self.pipeline()
        pipeline.reader.readtext.return_value = []
        self.assertEqual(pipeline.process_image('test.png')['status'], 'SKIPPED_OCR_EMPTY')
        pipeline.detector.predict.assert_not_called()

    def test_ocr_error_does_not_predict(self):
        pipeline = self.pipeline()
        pipeline.reader.readtext.side_effect = RuntimeError('OCR unavailable')
        self.assertEqual(pipeline.process_image('test.png')['status'], 'OCR_ERROR')
        pipeline.detector.predict.assert_not_called()

    def test_short_ocr_does_not_predict(self):
        import numpy as np
        pipeline = self.pipeline()
        pipeline.reader.readtext.return_value = [([], '保證獲利', np.float32(0.9))]
        with redirect_stdout(io.StringIO()):
            result = pipeline.process_image('test.png')
        self.assertEqual(result['status'], 'SKIPPED_OCR_LOW_QUALITY')
        json.dumps(result)
        pipeline.detector.predict.assert_not_called()


class CaptureTests(unittest.TestCase):
    def run_fixture(self, password=False, title='Fixture', status=200):
        module = MagicMock()
        browser = MagicMock()
        context = MagicMock()
        page = MagicMock()
        module.async_playwright.return_value.__aenter__.return_value.chromium.launch = AsyncMock(return_value=browser)
        browser.new_context = AsyncMock(return_value=context)
        browser.close = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        context.new_cdp_session = AsyncMock(return_value=MagicMock(send=AsyncMock()))
        context.route_web_socket = AsyncMock()
        page.add_init_script = AsyncMock()
        page.title = AsyncMock(return_value=title)
        page.goto = AsyncMock(return_value=MagicMock(status=status))
        page.wait_for_timeout = AsyncMock()
        page.screenshot = AsyncMock()
        page.screenshot.side_effect = lambda **kwargs: Path(kwargs['path']).write_bytes(b'fixture')
        page.keyboard.press = AsyncMock()
        def locator_for(selector):
            locator = MagicMock()
            locator.first = locator
            locator.count = AsyncMock(return_value=int(password) if 'password' in selector else int(selector == 'body'))
            locator.is_visible = AsyncMock(return_value=False)
            locator.inner_text = AsyncMock(return_value='Some visible content')
            return locator
        page.locator.side_effect = locator_for
        page.url = 'https://www.instagram.com/'
        page.title.return_value = title
        page.goto.return_value.status = status
        with patch.dict(sys.modules, {'playwright.async_api': module}), patch('web_capture.require_public_url'), tempfile.TemporaryDirectory() as output:
            result = capture('https://www.instagram.com/', output)
        browser.close.assert_called_once()
        return result, browser.new_context.return_value

    def test_login_and_error_pages_unusable(self):
        for settings in ({'password': True}, {'title': '無法載入頁面'}, {'title': 'Profile無法顯示 • Instagram'}, {'title': '很抱歉，此頁面無法使用。'}, {'status': 403}):
            self.assertEqual(self.run_fixture(**settings)[0]['status'], 'unusable')

    def test_normal_page_and_redirect_guard(self):
        result, context = self.run_fixture()
        self.assertEqual(result['status'], 'success')
        session = context.new_cdp_session.return_value
        guard = session.on.call_args.args[1]
        event = {'requestId': '1', 'request': {'url': 'https://www.instagram.com/'}, 'resourceType': 'Document'}
        with patch('web_capture.require_public_url'):
            asyncio.run(guard(event))
        session.send.assert_called_with('Fetch.continueRequest', {'requestId': '1'})
        event['request']['url'] = 'http://127.0.0.1/'
        with patch('web_capture.require_public_url', side_effect=ValueError('private redirect')):
            asyncio.run(guard(event))
        session.send.assert_called_with('Fetch.failRequest', {'requestId': '1', 'errorReason': 'BlockedByClient'})

    def test_error_text_in_body(self):
        self.assertTrue(is_load_error('Instagram', '很抱歉，此頁面無法使用。你點擊的連結可能發生故障，或該頁面已遭移除。'))
        self.assertFalse(is_load_error('Instagram', 'JUKSY 街星'))


class DomainTests(unittest.TestCase):
    def test_official_boundary(self):
        self.assertEqual(analyze_url("www.instagram.com/user")['domain_status'], 'official')
        for url in ('instagram.com.evil.test', 'notinstagram.com', 'faceb00k.com'):
            self.assertEqual(analyze_url(url)['domain_status'], 'lookalike')

    def test_credentials_not_confused_with_path(self):
        with self.assertRaises(ValueError):
            analyze_url('https://instagram.com@evil.test')
        self.assertTrue(analyze_url('https://instagram.com/@someone?q=a@b')['capture_allowed'])

    def test_unknown_is_not_automatically_fraud(self):
        result = analyze_url('https://example.com')
        self.assertEqual(result['domain_status'], 'unverified')
        self.assertEqual(result['risk_score'], 0)

    def test_invalid(self):
        for value in ('', 'file:///etc/passwd', 'https://good.com\\@evil.test', 'https://bad..com', 'https://bad.com:99999', 'https://bad.com/\nhello'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_url(value)

    def test_ipv6_and_idna(self):
        self.assertEqual(normalize_url('http://[::1]:8080/')[0], 'http://[::1]:8080/')
        self.assertTrue(analyze_url('https://例子.tw')['hostname'].startswith('xn--'))

    def test_private_and_mixed_dns_blocked(self):
        for addresses in (['127.0.0.1'], ['::1'], ['169.254.169.254'], ['8.8.8.8', '192.168.1.1']):
            records = [(2, 1, 6, '', (address, 443)) for address in addresses]
            with patch('domain_check.socket.getaddrinfo', return_value=records):
                with self.assertRaises(ValueError):
                    require_public_url('https://example.com')

    def test_public_dns(self):
        with patch('domain_check.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('8.8.8.8', 443))]):
            self.assertEqual(require_public_url('https://example.com'), 'https://example.com/')

    def test_domain_only_cli_and_invalid_input(self):
        for url, code in [('instagram.com/user', 0), ('file:///secret', 2)]:
            with tempfile.TemporaryDirectory() as output:
                run = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'run_pipeline.py'), '--url', url, '--domain-only', '--output-dir', output], capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(run.returncode, code)
                report = json.loads(run.stdout)
                self.assertIsNone(report['image_analysis'])
                self.assertTrue(Path(report['report_path']).is_file())


if __name__ == '__main__':
    unittest.main()
