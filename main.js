const fs = require("node:fs/promises");
const path = require("node:path");
const { execFile } = require("node:child_process");
const { promisify } = require("node:util");

const { capturePage } = require("./src/browserCapture");
const { mergeAssessment } = require("./src/mergeReport");
const { createMarkdownReport } = require("./src/reportWriter");

const execFileAsync = promisify(execFile);

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const current = argv[i];
    const next = argv[i + 1];
    if (!current.startsWith("--")) continue;

    const key = current.slice(2);
    if (!next || next.startsWith("--")) {
      args[key] = true;
      continue;
    }

    args[key] = next;
    i += 1;
  }
  return args;
}

async function readJson(filePath) {
  const content = await fs.readFile(filePath, "utf8");
  return JSON.parse(content);
}

function parseJsonOutput(stdout) {
  const text = String(stdout || "").trim();
  if (!text) throw new Error("Image analyzer returned no JSON output.");
  return JSON.parse(text);
}

async function runImageAnalyzer(imagePath, args) {
  const python = args.python || process.env.PYTHON_EXECUTABLE || "python";
  const script = path.resolve("image_analyzer.py");
  const output = path.resolve(args["image-output"] || "artifacts/image_analysis.json");
  const pythonArgs = [script, "--image", imagePath, "--output", output];

  if (args["yolo-model"]) {
    pythonArgs.push("--yolo-model", args["yolo-model"]);
  }

  await fs.rm(output, { force: true });

  let processResult;
  let processError;
  try {
    processResult = await execFileAsync(python, pythonArgs, {
      cwd: process.cwd(),
      encoding: "utf8",
      maxBuffer: 10 * 1024 * 1024,
      env: {
        ...process.env,
        PYTHONIOENCODING: "utf-8",
        PYTHONUTF8: "1",
      },
    });
  } catch (error) {
    processError = error;
  }

  const stderr = processResult?.stderr || processError?.stderr;
  if (stderr) process.stderr.write(stderr);

  try {
    return await readJson(output);
  } catch {
    try {
      return parseJsonOutput(processResult?.stdout || processError?.stdout);
    } catch {
      return {
        status: "error",
        risk_level: "Unknown",
        reason: "影像分析程式無法啟動，目前無法判斷風險。",
        repaired_text: [],
        message: processError?.message || "Image analyzer produced no valid JSON file.",
      };
    }
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const url = args.url ? String(args.url).trim() : null;
  const imagePath = args.image ? path.resolve(args.image) : null;
  const outputPath = path.resolve(args.output || "artifacts/final_report.json");
  const reportPath = path.resolve(args.report || "artifacts/analysis_report.md");
  const screenshotPath = path.resolve(args.screenshot || "artifacts/screenshots/page.png");
  const upstreamPath = args.upstream ? path.resolve(args.upstream) : null;

  if ((!url && !imagePath) || (url && imagePath)) {
    throw new Error("Provide exactly one input: --url <target> or --image <file>.");
  }

  let browserCapture = null;
  let evidenceImagePath = imagePath;

  if (url) {
    browserCapture = await capturePage(url, screenshotPath, {
      storageStatePath: args["storage-state"],
    });
    evidenceImagePath = screenshotPath;
  }

  let imageAnalysis = null;
  if (upstreamPath) {
    imageAnalysis = await readJson(upstreamPath);
  } else if (args["analyze-image"]) {
    if (!evidenceImagePath) {
      throw new Error("Image analysis requires a local image or a captured screenshot.");
    }
    imageAnalysis = await runImageAnalyzer(evidenceImagePath, args);
  }

  const combinedAssessment = mergeAssessment(imageAnalysis, browserCapture);

  const report = {
    status: "success",
    generated_at: new Date().toISOString(),
    input: {
      type: url ? "url" : "image",
      url,
      image_path: imagePath,
      evidence_image_path: evidenceImagePath,
      upstream_image_analysis: upstreamPath,
    },
    browser_capture: browserCapture,
    image_analysis: imageAnalysis,
    combined_assessment: combinedAssessment,
  };

  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  await fs.mkdir(path.dirname(reportPath), { recursive: true });
  await fs.writeFile(outputPath, JSON.stringify(report, null, 2), "utf8");
  await fs.writeFile(reportPath, createMarkdownReport(report), "utf8");

  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
}

main().catch((error) => {
  process.stderr.write(
    `${JSON.stringify({ status: "error", message: error.message }, null, 2)}\n`
  );
  process.exitCode = 1;
});
