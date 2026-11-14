$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$statePath = Join-Path $projectRoot "runtime\deerflow-windows.json"
if (-not (Test-Path -LiteralPath $statePath)) {
    Write-Output "No DeerFlow Windows process state file was found."
    exit 0
}

$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
foreach ($processId in @($state.gateway_pid, $state.frontend_pid)) {
    if (($processId -is [int] -or $processId -match '^\d+$') -and (Get-Process -Id ([int]$processId) -ErrorAction SilentlyContinue)) {
        taskkill.exe /PID ([int]$processId) /T /F 2>$null | Out-Null
    }
}
Remove-Item -LiteralPath $statePath -Force
Write-Output "DeerFlow Windows processes stopped."
