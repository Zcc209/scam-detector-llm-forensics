const fs = require("node:fs/promises");
const path = require("node:path");
const readline = require("node:readline");

const {
  buildUserAgent,
  ensurePlaywrightAvailable,
  resolveBrowserPath,
} = require("./src/browserCapture");

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    if (!argv[i].startsWith("--")) continue;
    const key = argv[i].slice(2);
    const value = argv[i + 1];
    if (value && !value.startsWith("--")) {
      args[key] = value;
      i += 1;
    } else {
      args[key] = true;
    }
  }
  return args;
}

function waitForEnter() {
  const terminal = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });
  return new Promise((resolve) => {
    terminal.question("Login completed? Press Enter to save the session...", () => {
      terminal.close();
      resolve();
    });
  });
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const outputPath = path.resolve(
    args.output || "artifacts/auth/instagram-storage-state.json"
  );
  const { chromium } = ensurePlaywrightAvailable();
  const browser = await chromium.launch({
    headless: false,
    executablePath: resolveBrowserPath(),
  });
  const context = await browser.newContext({
    locale: "zh-TW",
    userAgent: buildUserAgent(),
    viewport: { width: 1280, height: 900 },
  });
  const page = await context.newPage();

  try {
    await page.goto("https://www.instagram.com/accounts/login/", {
      waitUntil: "domcontentloaded",
      timeout: 30000,
    });
    process.stdout.write(
      "Complete Instagram login in the opened Chrome window. Do not enter credentials in this terminal.\n"
    );
    await waitForEnter();

    const cookies = await context.cookies("https://www.instagram.com");
    const hasSession = cookies.some((cookie) => cookie.name === "sessionid");
    if (!hasSession) {
      throw new Error("Instagram session cookie was not found. Finish login before pressing Enter.");
    }

    await fs.mkdir(path.dirname(outputPath), { recursive: true });
    await context.storageState({ path: outputPath, indexedDB: true });
    process.stdout.write(`Session saved: ${outputPath}\n`);
  } finally {
    await context.close();
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
});
