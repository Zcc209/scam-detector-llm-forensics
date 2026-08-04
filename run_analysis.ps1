param(
  [string]$Url,
  [string]$Image,
  [string]$Upstream,
  [switch]$AnalyzeImage,
  [string]$Python = "python",
  [string]$StorageState,
  [string]$Output = "artifacts/final_report.json",
  [string]$Report = "artifacts/analysis_report.md",
  [string]$Screenshot = "artifacts/screenshots/page.png"
)

[Console]::InputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$nodeExe = "C:\Users\linzi\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"

if (-not (Test-Path $nodeExe)) {
  throw "Bundled Node runtime not found: $nodeExe"
}

if (([string]::IsNullOrWhiteSpace($Url) -and [string]::IsNullOrWhiteSpace($Image)) -or
    (-not [string]::IsNullOrWhiteSpace($Url) -and -not [string]::IsNullOrWhiteSpace($Image))) {
  throw "Provide exactly one input: -Url or -Image."
}

$nodeArgs = [System.Collections.Generic.List[string]]::new()
$nodeArgs.Add(".\main.js")

if ($Url) {
  $nodeArgs.Add("--url")
  $nodeArgs.Add($Url)
} else {
  $nodeArgs.Add("--image")
  $nodeArgs.Add($Image)
}

$nodeArgs.Add("--output")
$nodeArgs.Add($Output)
$nodeArgs.Add("--report")
$nodeArgs.Add($Report)
$nodeArgs.Add("--screenshot")
$nodeArgs.Add($Screenshot)

if ($Upstream) {
  $nodeArgs.Add("--upstream")
  $nodeArgs.Add($Upstream)
}

if ($AnalyzeImage) {
  if (-not $env:GEMINI_API_KEY) {
    Write-Warning "GEMINI_API_KEY is not set. The result will remain Unknown after OCR."
  }
  if (-not $env:SERPAPI_KEY -or -not $env:IMGBB_API_KEY) {
    Write-Warning "SERPAPI_KEY or IMGBB_API_KEY is not set. Reverse image search will be skipped."
  }
  $pythonCommand = Get-Command $Python -ErrorAction SilentlyContinue
  if ($pythonCommand) {
    $Python = $pythonCommand.Source
  }
  $nodeArgs.Add("--analyze-image")
  $nodeArgs.Add("--python")
  $nodeArgs.Add($Python)
}

if (-not $StorageState -and $Url -match "instagram\.com") {
  $defaultInstagramState = ".\artifacts\auth\instagram-storage-state.json"
  if (Test-Path $defaultInstagramState) {
    $StorageState = $defaultInstagramState
  }
}

if ($StorageState) {
  $nodeArgs.Add("--storage-state")
  $nodeArgs.Add($StorageState)
}

& $nodeExe $nodeArgs
exit $LASTEXITCODE
