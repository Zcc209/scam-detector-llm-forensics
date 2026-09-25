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


def classify_page(title, body, final_url, http_status, has_password=False):
    """Return a stable reason for pages that cannot provide content evidence."""
    if http_status is None or http_status >= 400:
        return "http_error"
    if is_load_error(title, body):
        return "load_error"
    lowered = (title + "\n" + body).lower()
    if (has_password or "/login" in final_url.lower() or "/accounts/login" in final_url.lower()
            or "登入後繼續" in lowered or "log in to continue" in lowered):
        return "login_wall"
    if not body.strip():
        return "empty_page"
    return None


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

            await page.wait_for_timeout(3000)

            # 安全清除常見關閉按鈕與彈窗
            try:
                close_selectors = [
                    'svg[aria-label="關閉"]', 'svg[aria-label="Close"]',
                    'div[role="dialog"] button:has(svg)', 'button[aria-label="Close"]'
                ]
                clicked = False
                for selector in close_selectors:
                    btn = page.locator(selector).first
                    if await btn.count() > 0 and await btn.is_visible():
                        await btn.click(timeout=1500)
                        clicked = True
                        break
                if not clicked:
                    await page.keyboard.press("Escape")
                await page.wait_for_timeout(1000)
            except Exception:
                pass

            if result["status"] == "blocked":
                return result

            # 最終頁面狀態評估
            final_url = page.url
            final_check = analyze_url(final_url)
            if not final_check["capture_allowed"]:
                result.update(status="blocked", error=final_check["block_reason"], final_domain_analysis=final_check)
                return result

            title = await page.title()
            body = await page.locator("body").inner_text(timeout=5000) if await page.locator("body").count() > 0 else ""
            has_pw = await page.locator('input[type="password"]:visible').count() > 0

            screenshot.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(screenshot), full_page=False, timeout=10000)

            http_status = response.status if response else None
            unusable_reason = classify_page(title, body, final_url, http_status, has_pw)
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
                final_domain_analysis=final_check,
                page_text=body.strip(),
            )

        except Exception as exc:
            result.setdefault("error", f"{type(exc).__name__}: {exc}")
        finally:
            await browser.close()

    return result


async def capture_async(url, output_dir, *, headed=False, storage_state=None, channel=None):
    """Do not silently switch to a logged-in or visible browser session."""
    return await _execute_capture(url, output_dir, headed=headed, storage_state=storage_state, channel=channel)
