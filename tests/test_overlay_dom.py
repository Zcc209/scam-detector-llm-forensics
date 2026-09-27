"""Opt-in Chromium DOM checks; no external navigation or account access."""
import asyncio
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web_capture import OVERLAY_SCRIPT
from alignment import VIEWPORT_SCRIPT


@unittest.skipUnless(os.environ.get("RUN_BROWSER_TESTS") == "1", "Set RUN_BROWSER_TESTS=1 for local Chromium fixtures")
class OverlayDOMTests(unittest.TestCase):
    def test_viewport_excludes_hidden_offscreen_and_covered_text(self):
        async def check():
            from playwright.async_api import async_playwright
            async with async_playwright() as pw:
                browser = await pw.chromium.launch()
                try:
                    page = await browser.new_page(viewport={'width':800,'height':600})
                    await page.set_content('''<p>visible words</p><p style="display:none">hidden words</p>
                      <p style="position:absolute;top:1000px">offscreen words</p>
                      <div style="position:absolute;top:100px;left:0">covered words</div>
                      <div style="position:absolute;top:90px;left:0;width:400px;height:50px;background:white;z-index:10">cover</div>''')
                    result = await page.evaluate(VIEWPORT_SCRIPT)
                    self.assertIn('visible words', result['text'])
                    for value in ('hidden words','offscreen words','covered words'):
                        self.assertNotIn(value, result['text'])
                    self.assertFalse(result['truncated'])
                finally:
                    await browser.close()
        asyncio.run(check())

    def test_blank_visible_hidden_and_nonsemantic_modals(self):
        async def check():
            from playwright.async_api import async_playwright
            async with async_playwright() as pw:
                browser = await pw.chromium.launch()
                try:
                    page = await browser.new_page(viewport={"width": 1440, "height": 1080})
                    fixtures = [
                        ('<main>Public profile</main>', False),
                        ('<div role="dialog" style="width:600px;height:400px"> </div>', True),
                        ('<div role="dialog" style="display:none;width:600px;height:400px"></div>', False),
                        ('<div style="position:fixed;inset:0;z-index:100;background:#0008"><div style="margin:200px;width:600px;height:400px;background:white"></div></div>', True),
                    ]
                    for html, expected in fixtures:
                        await page.set_content(html)
                        self.assertEqual((await page.evaluate(OVERLAY_SCRIPT))["obstructed"], expected)
                finally:
                    await browser.close()
        asyncio.run(check())
