param(
  [string]$Output = "artifacts/auth/instagram-storage-state.json"
)

[Console]::InputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$nodeExe = "C:\Users\linzi\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"

if (-not (Test-Path $nodeExe)) {
  throw "Bundled Node runtime not found: $nodeExe"
}

& $nodeExe ".\setup_instagram_session.js" --output $Output
exit $LASTEXITCODE
