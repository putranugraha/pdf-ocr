$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    throw "Virtual environment belum ada. Jalankan .\setup.ps1 terlebih dahulu."
}

Write-Host "ScanNama aktif di http://127.0.0.1:8000" -ForegroundColor Green
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

