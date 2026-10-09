# 4Health - start the app (Windows). Double-click start.bat.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$venvPy = Join-Path $PSScriptRoot "api\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "Not installed yet - run setup.bat first." -ForegroundColor Red
    exit 1
}

# Open the browser once the server has had a moment to start
Start-Job { Start-Sleep 4; Start-Process "http://localhost:8000" } | Out-Null

Write-Host "4Health is running on http://localhost:8000  (API docs: /docs). Press Ctrl+C to stop." -ForegroundColor Green
Set-Location api
& $venvPy -m uvicorn app.main:app --host 127.0.0.1 --port 8000
