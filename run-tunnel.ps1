$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$portableCloudflared = Join-Path $projectRoot ".tools\cloudflared.exe"
$installedCloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue

if (Test-Path -LiteralPath $portableCloudflared) {
    $cloudflaredCommand = $portableCloudflared
} elseif ($installedCloudflared) {
    $cloudflaredCommand = $installedCloudflared.Source
} else {
    throw "cloudflared belum tersedia. Unduh cloudflared.exe ke .tools\cloudflared.exe."
}

Write-Host "Membuka Quick Tunnel untuk pengujian..." -ForegroundColor Yellow
& $cloudflaredCommand tunnel --url http://localhost:8000
