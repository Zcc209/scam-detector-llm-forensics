const fs = require("node:fs/promises");
const fsSync = require("node:fs");
const path = require("node:path");
const { URL } = require("node:url");

const BUNDLED_NODE_MODULES =
  "C:\\Users\\linzi\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\node\\node_modules";

function discoverPlaywrightNodePaths() {
  const paths = [BUNDLED_NODE_MODULES];
  const pnpmPath = path.join(BUNDLED_NODE_MODULES, ".pnpm");

  try {
    for (const entry of fsSync.readdirSync(pnpmPath)) {
      if (entry.startsWith("playwright@") || entry.startsWith("playwright-core@")) {
        paths.push(path.join(pnpmPath, entry, "node_modules"));
      }
    }
  } catch {
    // A normal npm install does not have to contain a .pnpm directory.
  }

  return paths;
}

function ensurePlaywrightAvailable() {
  const joined = discoverPlaywrightNodePaths().join(path.delimiter);
  process.env.NODE_PATH = process.env.NODE_PATH
    ? `${joined}${path.delimiter}${process.env.NODE_PATH}`
    : joined;
  require("node:module").Module._initPaths();
  return require("playwright");
}

function resolveBrowserPath() {
  const candidates = [
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  ];

  for (const candidate of candidates) {
    try {
      require("node:fs").accessSync(candidate);
      return candidate;
    } catch {
      continue;
    }
  }

  throw new Error("No supported browser executable was found.");
}

function buildUserAgent() {
  return [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "AppleWebKit/537.36 (KHTML, like Gecko)",
    "Chrome/136.0.0.0 Safari/537.36",
  ].join(" ");
}

async function clickIfVisible(locator) {
  try {
    const count = await locator.count();
    if (count < 1) return false;

    const target = locator.first();
    if (!(await target.isVisible())) return false;

    await target.click({ timeout: 1500 });
    return true;
  } catch {
    return false;
  }
}

async function dismissCommonPopups(page) {
  const dismissed = [];
  const buttonNames = [
    /not now/i,
    /now not/i,
    /close/i,
    /dismiss/i,
    /allow essential/i,
    /only allow essential/i,
    /decline optional cookies/i,
    /現在不要/,
    /稍後再說/,
    /關閉/,
    /僅允許必要/,
    /只允許必要/,
  ];

  for (const pattern of buttonNames) {
    const clicked = await clickIfVisible(page.getByRole("button", { name: pattern }));
    if (clicked) dismissed.push(`button:${pattern}`);
  }

  const selectors = [
    '[aria-label="Close"]',
    '[aria-label="close"]',
    '[aria-label="關閉"]',
    'svg[aria-label="Close"]',
    'svg[aria-label="關閉"]',
    '[data-testid="dialog-close-button"]',
    '[data-cookiebanner="accept_button"]',
    '[data-cookiebanner="decline_button"]',
  ];

  for (const selector of selectors) {
    const clicked = await clickIfVisible(page.locator(selector));
    if (clicked) dismissed.push(`selector:${selector}`);
  }

  try {
    await page.keyboard.press("Escape");
  } catch {
    // Ignore.
  }

  return dismissed;
}

async function stripKnownOverlays(page, hostname) {
  if (!/instagram\.com$|facebook\.com$/.test(hostname)) {
    return false;
  }

  try {
    const removed = await page.evaluate(() => {
      const selectors = [
        'div[role="dialog"]',
        'div[aria-modal="true"]',
        'div[role="presentation"]',
      ];

      let touched = 0;
      for (const selector of selectors) {
        for (const node of document.querySelectorAll(selector)) {
          const text = (node.textContent || "").toLowerCase();
          if (
            text.includes("log in") ||
            text.includes("login") ||
            text.includes("sign up") ||
            text.includes("登入") ||
            text.includes("註冊")
          ) {
            node.remove();
            touched += 1;
          }
        }
      }

      document.documentElement.style.overflow = "auto";
      document.body.style.overflow = "auto";
      return touched;
    });

    return removed > 0;
  } catch {
    return false;
  }
}

async function detectLoginWall(page, hostname) {
  try {
    if (/instagram\.com$/.test(hostname)) {
      const publicPosts = page.locator('a[href*="/p/"], a[href*="/reel/"]');
      if ((await publicPosts.count()) >= 3) return false;
    }

    const dialogs = page.locator('[role="dialog"]:visible, [aria-modal="true"]:visible');
    for (let index = 0; index < (await dialogs.count()); index += 1) {
      const dialogText = (await dialogs.nth(index).innerText()).toLowerCase();
      if (/log in|login|sign up|登入|註冊/.test(dialogText)) return true;
    }

    const text = (await page.locator("body").innerText()).toLowerCase();
    const patterns = [
      "log in",
      "login",
      "sign up",
      "登入",
      "註冊",
      "查看內容請先登入",
      "see instagram photos and videos",
      "查看貼文前請先登入",
    ];

    return text.length < 1000 && patterns.some((pattern) => text.includes(pattern));
  } catch {
    return false;
  }
}

async function detectLoadError(page) {
  try {
    const title = (await page.title()).toLowerCase();
    const text = (await page.locator("body").innerText()).toLowerCase();
    const patterns = [
      "無法載入頁面",
      "發生錯誤",
      "重新載入頁面",
      "page isn't available",
      "something went wrong",
      "reload page",
    ];
    return patterns.some((pattern) => title.includes(pattern) || text.includes(pattern));
  } catch {
    return false;
  }
}

async function retryFailedPage(page) {
  let retries = 0;
  if (!(await detectLoadError(page))) return retries;

  for (let attempt = 0; attempt < 2; attempt += 1) {
    retries += 1;
    const clicked = await clickIfVisible(
      page.getByRole("button", { name: /重新載入頁面|reload page|try again/i })
    );

    if (!clicked) {
      try {
        await page.reload({ waitUntil: "domcontentloaded", timeout: 25000 });
      } catch {
        // Keep the current page so the caller can record the failed state.
      }
    }

    await page.waitForTimeout(3000);
    if (!(await detectLoadError(page))) break;
  }

  return retries;
}

async function capturePage(url, outputPath, options = {}) {
  const { chromium } = ensurePlaywrightAvailable();
  const browserPath = resolveBrowserPath();
  const hostname = new URL(url).hostname.toLowerCase();
  const launchOptions = {
    headless: options.headless !== false,
    executablePath: browserPath,
  };

  await fs.mkdir(path.dirname(outputPath), { recursive: true });

  const browser = await chromium.launch(launchOptions);
  const storageStatePath = options.storageStatePath
    ? path.resolve(options.storageStatePath)
    : null;
  const storageStateUsed = Boolean(
    storageStatePath && fsSync.existsSync(storageStatePath)
  );
  const context = await browser.newContext({
    viewport: {
      width: options.width || 1440,
      height: options.height || 2200,
    },
    userAgent: options.userAgent || buildUserAgent(),
    locale: options.locale || "zh-TW",
    storageState: storageStateUsed ? storageStatePath : undefined,
  });
  const page = await context.newPage();

  try {
    let response = await page.goto(url, {
      waitUntil: "domcontentloaded",
      timeout: options.timeoutMs || 25000,
    });

    await page.waitForTimeout(options.postLoadDelayMs || 2500);
    let reloadAttempts = await retryFailedPage(page);
    const dismissedPopups = await dismissCommonPopups(page);
    let strippedOverlay = await stripKnownOverlays(page, hostname);
    await page.waitForTimeout(1200);
    reloadAttempts += await retryFailedPage(page);
    if (reloadAttempts > 0) {
      dismissedPopups.push(...(await dismissCommonPopups(page)));
      strippedOverlay =
        (await stripKnownOverlays(page, hostname)) || strippedOverlay;
      await page.waitForTimeout(1200);
    }
    const loginWallDetected = await detectLoginWall(page, hostname);
    const loadErrorDetected = await detectLoadError(page);

    await page.screenshot({
      path: outputPath,
      fullPage: true,
    });

    return {
      status: loadErrorDetected ? "partial" : "success",
      screenshot_path: outputPath,
      final_url: page.url(),
      page_title: await page.title(),
      http_status: response ? response.status() : null,
      browser_path: browserPath,
      dismissed_popups: dismissedPopups,
      stripped_overlay: strippedOverlay,
      login_wall_detected: loginWallDetected,
      load_error_detected: loadErrorDetected,
      reload_attempts: reloadAttempts,
      storage_state_used: storageStateUsed,
    };
  } finally {
    await page.close();
    await context.close();
    await browser.close();
  }
}

module.exports = {
  buildUserAgent,
  capturePage,
  ensurePlaywrightAvailable,
  resolveBrowserPath,
};
