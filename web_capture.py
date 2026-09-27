"""Capture a public page, reporting inaccessible content instead of scoring it."""
from pathlib import Path
import asyncio
import sys
from urllib.parse import urlsplit
from domain_check import analyze_url, require_public_url


def is_load_error(title, body):
    text = (title + "\n" + body).lower()
    return any(s in text for s in (
        "無法載入頁面", "頁面無法使用", "此頁面無法使用", "很抱歉，此頁面無法使用", "profile無法顯示",
        "profile 無法顯示", "個人檔案無法顯示", "該頁面已遭移除",
        "this page isn't available", "this page is not available",
        "profile isn't available", "profile is not available",
        "page could not be loaded", "access denied", "verify you are human",
    ))


def classify_page(title, body, final_url, http_status, has_password=False, overlay=None):
    """Return a stable reason for pages that cannot provide content evidence."""
    if http_status is None or http_status >= 400:
        return "http_error"
    if is_load_error(title, body):
        return "load_error"
    lowered = (title + "\n" + body).lower()
    if (has_password or "/login" in final_url.lower() or "/accounts/login" in final_url.lower()
            or "登入後繼續" in lowered or "log in to continue" in lowered):
        return "login_wall"
    if overlay and overlay.get("obstructed"):
        return "obstructing_overlay"
    if not body.strip():
        return "empty_page"
    return None


OVERLAY_SCRIPT = """() => {
    const visible = el => {
        const r = el.getBoundingClientRect(), s = getComputedStyle(el);
        return r.width > 80 && r.height > 80 && s.display !== 'none' &&
            s.visibility !== 'hidden' && Number(s.opacity) > 0 &&
            r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
    };
    const dialogs = [...document.querySelectorAll('[role="dialog"], [aria-modal="true"]')].filter(visible);
    const candidates = new Set(dialogs);
    for (const hit of document.elementsFromPoint(innerWidth / 2, innerHeight / 2)) {
        for (let el = hit; el && el !== document.body; el = el.parentElement) {
            if (!visible(el)) continue;
            const s = getComputedStyle(el), r = el.getBoundingClientRect();
            const area = r.width * r.height / (innerWidth * innerHeight);
            if (s.position === 'fixed' && area >= 0.15 &&
                (Number(s.zIndex) > 0 || dialogs.length > 0)) candidates.add(el);
        }
    }
    return {obstructed: candidates.size > 0, visible_dialogs: dialogs.length,
        overlay_count: candidates.size};
}"""


LINKS_SCRIPT = """() => [...document.querySelectorAll('a[href]')].map(a => {
    const r = a.getBoundingClientRect();
    return {href: a.href, text: (a.innerText || a.getAttribute('aria-label') || '').trim().slice(0, 80),
            visible: r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight};
}).filter(x => /^https?:/.test(x.href)).slice(0, 400)"""


async def settle_overlays(page, attempts=10):
    """Wait for late modal hydration; only click explicitly labeled close controls."""
    dismissed = []
    state = {"obstructed": False, "visible_dialogs": 0, "overlay_count": 0}
    clear_count = 0
    for attempt in range(attempts):
        state = await page.evaluate(OVERLAY_SCRIPT)
        if state.get("obstructed"):
            clear_count = 0
            for selector in ('button[aria-label="Close"]', 'button[aria-label="關閉"]',
                             '[role="button"][aria-label="Close"]', '[role="button"][aria-label="關閉"]',
                             '[role="dialog"] svg[aria-label="Close"]', '[role="dialog"] svg[aria-label="關閉"]'):
                button = page.locator(selector).first
                if await button.count() and await button.is_visible():
                    try:
                        await button.click(timeout=1000)
                        dismissed.append(selector)
                        break
                    except Exception:
                        continue
            await page.keyboard.press("Escape")
        else:
            clear_count += 1
            if clear_count >= 3:
                return state, dismissed, attempt + 1
        await page.wait_for_timeout(500)
    return await page.evaluate(OVERLAY_SCRIPT), dismissed, attempts


def capture(url, output_dir, *, headed=False, storage_state=None, channel=None):
    return asyncio.run(capture_async(url, output_dir, headed=headed, storage_state=storage_state, channel=channel))


async def _execute_capture(url, output_dir, *, headed=False, storage_state=None, channel=None):
    """核心實際執行抓取的內部函式"""
    initial = analyze_url(url)
    if not initial["capture_allowed"]:
        return {
            "status": "blocked",
            "error": initial["block_reason"],
            "navigation_checks": [initial],
            "final_domain_analysis": None,
        }

    from playwright.async_api import async_playwright

    # 執行 SSRF 檢查
    try:
        require_public_url(url)
    except Exception as e:
        return {"status": "blocked", "error": f"SSRF blocked: {e}", "navigation_checks": [initial]}

    result = {
        "status": "error",
        "navigation_checks": [initial],
        "blocked_requests": [],
        "storage_state_used": bool(storage_state),
        "transport": "chromium_native",
        "headless": not headed,
    }

    screenshot = Path(output_dir).resolve() / "page.png"

    async with async_playwright() as pw:
        options = {
            "headless": not headed,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
                "--disable-dev-shm-usage",
            ],
        }
        if channel:
            options["channel"] = channel

        browser = await pw.chromium.launch(**options)
        try:
            context = await browser.new_context(
                viewport={"width": 1440, "height": 1080},
                device_scale_factor=2.0,
                locale="zh-TW",
                timezone_id="Asia/Taipei",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                accept_downloads=False,
                service_workers="block",
                storage_state=storage_state if (storage_state and Path(storage_state).exists()) else None,
            )

            page = await context.new_page()
            await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            session = await context.new_cdp_session(page)
            checked_hosts = set()

            async def guard(event):
                request_id = event["requestId"]
                req_url = event["request"]["url"]

                # 忽略非 HTTP(S) 請求 (如 data:, blob:)
                if not req_url.startswith(("http://", "https://")):
                    await session.send("Fetch.continueRequest", {"requestId": request_id})
                    return

                try:
                    parsed_req = urlsplit(req_url)
                    if event.get("resourceType") == "Document":
                        check = analyze_url(req_url)
                        result["navigation_checks"].append(check)
                        if not check["capture_allowed"]:
                            result["status"] = "blocked"
                            result["error"] = check["block_reason"]
                            await session.send("Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"})
                            return

                    # SSRF 本地快取驗證
                    key = (parsed_req.scheme, parsed_req.netloc)
                    if key not in checked_hosts:
                        await asyncio.wait_for(asyncio.to_thread(require_public_url, req_url), timeout=3)
                        checked_hosts.add(key)

                    await session.send("Fetch.continueRequest", {"requestId": request_id})
                except Exception:
                    result["blocked_requests"].append(req_url)
                    await session.send("Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"})

            session.on("Fetch.requestPaused", guard)
            await session.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})

            # 防彈跳視窗與下載阻擋
            async def close_socket(ws): await ws.close()
            async def close_popup(p): await p.close()
            async def cancel_download(d): await d.cancel()

            await context.route_web_socket("**/*", close_socket)
            context.on("page", close_popup)
            page.on("download", cancel_download)

            # 導航至目標頁面
            response = None
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as nav_e:
                print(f"[Warning] 導航超時或警示，嘗試繼續擷取內容: {nav_e}", file=sys.stderr)
                if any(code in str(nav_e) for code in ("ERR_NAME_NOT_RESOLVED", "ERR_CONNECTION_REFUSED", "ERR_CONNECTION_TIMED_OUT",
                                                        "ERR_ADDRESS_UNREACHABLE", "ERR_CONNECTION_RESET", "ERR_CERT_")):
                    # Nothing was loaded (domain gone, sinkholed or refusing): report it, do not score an error page.
                    result.update(status="unusable", unusable_reason="unreachable", error=str(nav_e).splitlines()[0][:200])
                    return result

            await page.wait_for_timeout(3000)

            overlay, dismissed, settle_attempts = await settle_overlays(page)
            try:
                # Post thumbnails carry most image text; wait (bounded) until visible images have decoded.
                await page.wait_for_function(
                    "() => [...document.images].filter(i => { const r = i.getBoundingClientRect();"
                    " return r.width >= 48 && r.top < innerHeight && r.bottom > 0; })"
                    ".every(i => i.complete && i.naturalWidth > 0)", timeout=8000)
            except Exception:
                pass

            if result["status"] == "blocked":
                return result

            # 最終頁面狀態評估
            final_url = page.url
            if not final_url.startswith(("http://", "https://")):  # e.g. chrome-error://chromewebdata/
                result.update(status="unusable", unusable_reason="unreachable", error=f"Navigation ended on {final_url.split('/')[0]}")
                return result
            final_check = analyze_url(final_url)
            if not final_check["capture_allowed"]:
                result.update(status="blocked", error=final_check["block_reason"], final_domain_analysis=final_check)
                return result

            title = await page.title()
            body = await page.locator("body").inner_text(timeout=5000) if await page.locator("body").count() > 0 else ""
            has_pw = await page.locator('input[type="password"]:visible').count() > 0

            screenshot.parent.mkdir(parents=True, exist_ok=True)
            before_screenshot = await page.evaluate(OVERLAY_SCRIPT)
            from alignment import VIEWPORT_SCRIPT
            viewport_before = await page.evaluate(VIEWPORT_SCRIPT)
            outbound_links = await page.evaluate(LINKS_SCRIPT)
            await page.screenshot(path=str(screenshot), full_page=False, animations="disabled", timeout=10000)
            viewport_after = await page.evaluate(VIEWPORT_SCRIPT)
            viewport_stable = (viewport_before.get("text") == viewport_after.get("text")
                               and viewport_before.get("viewport") == viewport_after.get("viewport")
                               and viewport_before.get("items") == viewport_after.get("items")
                               and not viewport_before.get("truncated") and not viewport_after.get("truncated"))

            http_status = response.status if response else None
            overlay = await page.evaluate(OVERLAY_SCRIPT)
            if before_screenshot.get("obstructed"):
                overlay = before_screenshot
            unusable_reason = classify_page(title, body, final_url, http_status, has_pw, overlay)
            if not screenshot.is_file():
                unusable_reason = unusable_reason or "screenshot_missing"

            result.update(
                status="unusable" if unusable_reason else "success",
                screenshot_path=str(screenshot.resolve()),
                final_url=final_url,
                page_title=title,
                http_status=http_status,
                login_wall_detected=unusable_reason == "login_wall",
                load_error_detected=unusable_reason == "load_error",
                unusable_reason=unusable_reason,
                overlay_detected=overlay.get("obstructed", False),
                overlay_state=overlay,
                dismissed_popups=dismissed,
                settle_attempts=settle_attempts,
                final_domain_analysis=final_check,
                page_text=viewport_before.get("text", "") if viewport_stable else "",
                full_page_text=body.strip(),
                outbound_links=outbound_links,
                viewport_evidence=viewport_before,
                viewport_stable=viewport_stable,
                text_scope="viewport",
            )

        except Exception as exc:
            result.setdefault("error", f"{type(exc).__name__}: {exc}")
        finally:
            await browser.close()

    return result


CAPTURE_TIMEOUT = 90


async def capture_async(url, output_dir, *, headed=False, storage_state=None, channel=None):
    """Do not silently switch to a logged-in or visible browser session. Hard time limit so a stalled site cannot hang a job."""
    try:
        return await asyncio.wait_for(_execute_capture(url, output_dir, headed=headed, storage_state=storage_state, channel=channel),
                                      timeout=CAPTURE_TIMEOUT)
    except asyncio.TimeoutError:
        return {"status": "error", "error": f"Capture exceeded {CAPTURE_TIMEOUT}s", "navigation_checks": [], "final_domain_analysis": None}
