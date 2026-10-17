$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtimeDir = Join-Path $projectRoot ".runtime"
$serverPidFile = Join-Path $runtimeDir "server.pid"
$tunnelPidFile = Join-Path $runtimeDir "tunnel.pid"
$tunnelUrlFile = Join-Path $runtimeDir "cloudflare-url.txt"

function Stop-ProcessTree {
    param(
        [int]$ProcessId,
        [string]$ExpectedPattern
    )

    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if (-not $process -or $process.CommandLine -notmatch $ExpectedPattern) {
        return
    }
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        Stop-ProcessTree -ProcessId $child.ProcessId -ExpectedPattern $ExpectedPattern
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

if (Test-Path -LiteralPath $tunnelPidFile) {
    $savedTunnelPid = [int](Get-Content -LiteralPath $tunnelPidFile -Raw)
    Stop-ProcessTree -ProcessId $savedTunnelPid -ExpectedPattern "cloudflared"
}
if (Test-Path -LiteralPath $serverPidFile) {
    $savedServerPid = [int](Get-Content -LiteralPath $serverPidFile -Raw)
    Stop-ProcessTree -ProcessId $savedServerPid -ExpectedPattern "uvicorn\s+app\.main:app"
}

# Also stop validated child processes in case the Windows Python launcher or
# cloudflared outlived the PID originally stored by the launcher.
$projectPattern = [regex]::Escape($projectRoot)
$remaining = Get-CimInstance Win32_Process | Where-Object {
    ($_.CommandLine -match "uvicorn\s+app\.main:app" -and $_.CommandLine -match $projectPattern) -or
    ($_.Name -match "^cloudflared(\.exe)?$" -and $_.CommandLine -match "tunnel" -and $_.CommandLine -match "localhost:8000")
}
foreach ($process in $remaining) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}

foreach ($stateFile in @($serverPidFile, $tunnelPidFile, $tunnelUrlFile)) {
    if (Test-Path -LiteralPath $stateFile) {
        Remove-Item -LiteralPath $stateFile -Force
    }
}

Write-Host "Server OCR dan Cloudflare Tunnel sudah dihentikan." -ForegroundColor Green
