param(
    [int]$GatewayPort = 8001,
    [int]$FrontendPort = 3000
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$deerRoot = Join-Path $projectRoot "deer-flow"
$backendRoot = Join-Path $deerRoot "backend"
$frontendRoot = Join-Path $deerRoot "frontend"
$runtimeRoot = Join-Path $projectRoot "runtime"
$logRoot = Join-Path $runtimeRoot "logs"
$statePath = Join-Path $runtimeRoot "deerflow-windows.json"
$mytorchPython = "D:\conda_envs\mytorch\python.exe"
$uv = (Get-Command uv -ErrorAction Stop).Source

foreach ($port in $GatewayPort, $FrontendPort) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
        throw "Port $port is already in use. Run scripts\stop_deerflow_windows.ps1 or stop the owning process."
    }
}

New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$env:PYTHONPATH = $backendRoot
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:DEER_FLOW_HOME = Join-Path $deerRoot ".deer-flow"
$env:GATEWAY_CORS_ORIGINS = "http://localhost:$FrontendPort,http://127.0.0.1:$FrontendPort"
$env:DEER_FLOW_TRUSTED_ORIGINS = $env:GATEWAY_CORS_ORIGINS

$gateway = Start-Process -FilePath $uv `
    -ArgumentList @("run", "--frozen", "uvicorn", "app.gateway.app:app", "--host", "127.0.0.1", "--port", "$GatewayPort") `
    -WorkingDirectory $backendRoot `
    -RedirectStandardOutput (Join-Path $logRoot "deerflow-gateway.log") `
    -RedirectStandardError (Join-Path $logRoot "deerflow-gateway.err.log") `
    -WindowStyle Hidden -PassThru

$env:PORT = "$FrontendPort"
# Leave NEXT_PUBLIC_* backend URLs unset. DeerFlow auth uses same-origin /api
# paths, and Next.js rewrites those to this internal Gateway URL.
Remove-Item Env:NEXT_PUBLIC_BACKEND_BASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:NEXT_PUBLIC_LANGGRAPH_BASE_URL -ErrorAction SilentlyContinue
$env:DEER_FLOW_INTERNAL_GATEWAY_BASE_URL = "http://localhost:$GatewayPort"
$frontend = Start-Process -FilePath $mytorchPython `
    -ArgumentList @((Join-Path $deerRoot "scripts\pnpm.py"), "run", "dev") `
    -WorkingDirectory $frontendRoot `
    -RedirectStandardOutput (Join-Path $logRoot "deerflow-frontend.log") `
    -RedirectStandardError (Join-Path $logRoot "deerflow-frontend.err.log") `
    -WindowStyle Hidden -PassThru

@{
    gateway_pid = $gateway.Id
    frontend_pid = $frontend.Id
    gateway_port = $GatewayPort
    frontend_port = $FrontendPort
    started_at = (Get-Date).ToString("o")
} | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8

Write-Output "DeerFlow processes started."
Write-Output "Frontend: http://localhost:$FrontendPort"
Write-Output "Gateway:  http://localhost:$GatewayPort/health"
Write-Output "Logs:     $logRoot"
