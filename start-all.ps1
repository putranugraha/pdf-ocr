$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$runtimeDir = Join-Path $projectRoot ".runtime"
$serverPidFile = Join-Path $runtimeDir "server.pid"
$tunnelPidFile = Join-Path $runtimeDir "tunnel.pid"
$tunnelOutFile = Join-Path $runtimeDir "cloudflared.out.log"
$tunnelErrorFile = Join-Path $runtimeDir "cloudflared.error.log"
$tunnelUrlFile = Join-Path $runtimeDir "cloudflare-url.txt"
$localUrl = "http://127.0.0.1:8000"

Set-Location -LiteralPath $projectRoot
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python proyek belum siap. Jalankan setup.ps1 satu kali terlebih dahulu."
}

$portableCloudflared = Join-Path $projectRoot ".tools\cloudflared.exe"
$installedCloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $portableCloudflared) {
    $cloudflaredPath = $portableCloudflared
} elseif ($installedCloudflared) {
    $cloudflaredPath = $installedCloudflared.Source
} else {
    throw "cloudflared belum tersedia. Instal cloudflared atau letakkan cloudflared.exe di folder .tools."
}

Write-Host "Menyalakan ScanNama PDF..." -ForegroundColor Cyan

$listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" -ErrorAction SilentlyContinue
    if (-not $owner -or $owner.CommandLine -notmatch "uvicorn\s+app\.main:app") {
        throw "Port 8000 sedang dipakai program lain. Tutup program tersebut lalu coba lagi."
    }
    Write-Host "Server OCR sudah aktif; memakai server yang ada." -ForegroundColor DarkYellow
} else {
    $serverProcess = Start-Process `
        -FilePath $pythonPath `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -LiteralPath $serverPidFile -Value $serverProcess.Id -Encoding ASCII
}

$serverReady = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri "$localUrl/api/health" -TimeoutSec 3
        if ($health.status -eq "ok") {
            $serverReady = $true
            break
        }
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
if (-not $serverReady) {
    throw "Server OCR tidak berhasil aktif. Jalankan BERHENTI.bat lalu coba MULAI.bat lagi."
}

$existingTunnel = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match "^cloudflared(\.exe)?$" -and
    $_.CommandLine -match "tunnel" -and
    $_.CommandLine -match "localhost:8000"
} | Select-Object -First 1

$tunnelUrl = ""
if ($existingTunnel -and (Test-Path -LiteralPath $tunnelUrlFile)) {
    $tunnelUrl = (Get-Content -LiteralPath $tunnelUrlFile -Raw).Trim()
    Write-Host "Cloudflare Tunnel sudah aktif; memakai tunnel yang ada." -ForegroundColor DarkYellow
} elseif ($existingTunnel) {
    throw "Cloudflare Tunnel sudah aktif tetapi URL-nya tidak ditemukan. Jalankan BERHENTI.bat, lalu MULAI.bat."
} else {
    foreach ($logFile in @($tunnelOutFile, $tunnelErrorFile, $tunnelUrlFile)) {
        if (Test-Path -LiteralPath $logFile) {
            Remove-Item -LiteralPath $logFile -Force
        }
    }

    $tunnelProcess = Start-Process `
        -FilePath $cloudflaredPath `
        -ArgumentList @("tunnel", "--url", "http://localhost:8000") `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $tunnelOutFile `
        -RedirectStandardError $tunnelErrorFile `
        -PassThru
    Set-Content -LiteralPath $tunnelPidFile -Value $tunnelProcess.Id -Encoding ASCII

    for ($attempt = 0; $attempt -lt 100; $attempt++) {
        Start-Sleep -Milliseconds 250
        $logText = ""
        if (Test-Path -LiteralPath $tunnelOutFile) {
            $logText += Get-Content -LiteralPath $tunnelOutFile -Raw
        }
        if (Test-Path -LiteralPath $tunnelErrorFile) {
            $logText += Get-Content -LiteralPath $tunnelErrorFile -Raw
        }
        $urlMatch = [regex]::Match($logText, "https://[a-z0-9-]+\.trycloudflare\.com")
        if ($urlMatch.Success) {
            $tunnelUrl = $urlMatch.Value
            break
        }
        if ($tunnelProcess.HasExited) {
            break
        }
    }

    if (-not $tunnelUrl) {
        throw "Cloudflare gagal membuat URL. Periksa .runtime\cloudflared.error.log."
    }
    Set-Content -LiteralPath $tunnelUrlFile -Value $tunnelUrl -Encoding UTF8
}

try {
    Set-Clipboard -Value $tunnelUrl
    $clipboardMessage = "URL Cloudflare sudah disalin ke clipboard."
} catch {
    $clipboardMessage = "Salin URL Cloudflare secara manual dari layar ini."
}

Write-Host ""
Write-Host "SEMUA SUDAH AKTIF" -ForegroundColor Green
Write-Host "Scanner + OCR : $localUrl/" -ForegroundColor White
Write-Host "Gabungkan PDF : $localUrl/merge" -ForegroundColor White
Write-Host "Akses dari HP : $tunnelUrl" -ForegroundColor Yellow
Write-Host $clipboardMessage -ForegroundColor Cyan
Write-Host ""
Write-Host "PERINGATAN: URL Quick Tunnel dapat diakses siapa pun yang mengetahui URL tersebut." -ForegroundColor Red
Write-Host "Untuk berhenti, klik BERHENTI.bat." -ForegroundColor White

Start-Process "$localUrl/"
Read-Host "Tekan Enter untuk menutup jendela ini (server tetap aktif)"
