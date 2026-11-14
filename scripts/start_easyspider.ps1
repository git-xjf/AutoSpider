$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$releaseRoot = Join-Path $projectRoot "EasySpider-release\app\EasySpider_windows_x64"
$launcher = Join-Path $releaseRoot "EasySpider\EasySpider.exe"
if (-not (Test-Path -LiteralPath $launcher)) {
    throw "EasySpider launcher not found: $launcher"
}

# EasySpider is an interactive desktop application, so its window stays visible.
# VS Code/Codex commonly exports this variable for its own Electron subprocesses;
# EasySpider must not inherit it or Electron exits as a Node CLI process.
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
Start-Process -FilePath $launcher -WorkingDirectory $releaseRoot
Write-Output "EasySpider started. Wait for http://127.0.0.1:8074 before running a crawl."
