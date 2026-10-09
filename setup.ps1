# 4Health - one-time setup for Windows.
# Run it by double-clicking setup.bat (or: powershell -ExecutionPolicy Bypass -File setup.ps1).
# Safe to run again: every step skips what is already done.

# "Continue": in Windows PowerShell 5.1, "Stop" turns any stderr output of native tools (psql, ollama) into an error.
# Every step checks $LASTEXITCODE explicitly instead.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    OK  $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    !!  $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "`n    XX  $msg`n" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- 1. Python
Step "Checking Python (3.11+ required)"
$python = $null
foreach ($cand in @("py -3", "python", "python3")) {
    try {
        $parts = $cand.Split(" ")
        $exe = $parts[0]; $cargs = @($parts | Select-Object -Skip 1)
        $v = & $exe @cargs -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v -and ([version]$v -ge [version]"3.11")) { $python = $cand; break }
    } catch { }
}
if (-not $python) { Fail "Python 3.11 or newer not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and run setup again." }
Ok "Using '$python' (Python $v)"

# ---------------------------------------------------------------- 2. venv + packages
Step "Creating the virtual environment and installing packages"
$venvPy = Join-Path $PSScriptRoot "api\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    $parts = $python.Split(" ")
    & $parts[0] @($parts | Select-Object -Skip 1) -m venv api\.venv
    if ($LASTEXITCODE -ne 0) { Fail "Could not create api\.venv" }
}
& $venvPy -m pip install --quiet --no-cache-dir --upgrade pip
& $venvPy -m pip install --quiet --no-cache-dir -r api\requirements.txt
if ($LASTEXITCODE -ne 0) { Fail "pip install failed (see the error above)." }
Ok "Packages installed in api\.venv"

# ---------------------------------------------------------------- 3. .env
Step "Configuration file (.env)"
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    # random JWT secret instead of the placeholder
    $secret = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 48 | ForEach-Object { [char]$_ })
    (Get-Content ".env" -Encoding UTF8) -replace '^JWT_SECRET=.*', "JWT_SECRET=$secret" | Set-Content ".env" -Encoding UTF8
    Ok "Created .env from .env.example"
} else { Ok ".env already exists (left unchanged)" }

# ---------------------------------------------------------------- 4. PostgreSQL
Step "PostgreSQL database"
$psql = (Get-Command psql -ErrorAction SilentlyContinue).Source
if (-not $psql) {
    $psql = Get-ChildItem -Path "C:\Program Files\PostgreSQL\*\bin\psql.exe", "D:\PostgreSQL\*\bin\psql.exe", "D:\Program Files\PostgreSQL\*\bin\psql.exe" -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $psql) {
    Fail "PostgreSQL not found. Install it from https://www.postgresql.org/download/windows/ (keep 'Command Line Tools' ticked), then run setup again."
}
Ok "Found psql: $psql"

$env:PGPASSWORD = "health4"
& $psql -U health4 -h localhost -d health4 -tAc "SELECT 1" *> $null
if ($LASTEXITCODE -eq 0) {
    Ok "Database 'health4' already exists"
} else {
    $sec = Read-Host "    Password of the 'postgres' user (chosen when you installed PostgreSQL)" -AsSecureString
    $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
    & $psql -U postgres -h localhost -v ON_ERROR_STOP=1 -q -f api\scripts\setup_db.sql
    if ($LASTEXITCODE -ne 0) { Fail "Could not create the database. Check the postgres password and that the PostgreSQL service is running (services.msc)." }
    Ok "Created user 'health4' and databases 'health4', 'health4_test'"
}
Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue

# ---------------------------------------------------------------- 5. Ollama models
Step "Ollama models"
$llm = "qwen2.5:3b"; $emb = "bge-m3"
foreach ($line in Get-Content ".env" -Encoding UTF8) {
    if ($line -match '^LLM_MODEL=(.+)$')   { $llm = $Matches[1].Trim() }
    if ($line -match '^EMBED_MODEL=(.+)$') { $emb = $Matches[1].Trim() }
}
$ollamaOk = $false
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Warn "Ollama is not installed. The app works without it (no AI answers). Get it from https://ollama.com/download and run setup again."
} else {
    $list = & ollama list 2>$null
    if ($LASTEXITCODE -ne 0) {
        Warn "Ollama is installed but not running. Start the Ollama app (Start menu) and run setup again."
    } else {
        $ollamaOk = $true
        $missing = @($llm, $emb) | Where-Object { -not ($list -match ("^" + [regex]::Escape($_) + "(:latest)?\s")) }
        if ($missing.Count -eq 0) {
            Ok "Models already downloaded: $llm, $emb"
        } else {
            $where = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { "$env:USERPROFILE\.ollama\models" }
            Write-Host "    Models to download: $($missing -join ', ') (about 3 GB) into $where"
            if (-not $env:OLLAMA_MODELS) { Write-Host "    Tip: to keep them off drive C:, set the user environment variable OLLAMA_MODELS (e.g. D:\ollama\models), restart Ollama and rerun setup." }
            $ans = Read-Host "    Download now? [Y/n]"
            if ($ans -notmatch '^[nN]') {
                foreach ($m in $missing) { & ollama pull $m; if ($LASTEXITCODE -ne 0) { Warn "Could not pull $m" } }
            } else { Warn "Skipped. AI features need: ollama pull $llm ; ollama pull $emb" }
        }
    }
}

# ---------------------------------------------------------------- 6. demo data + RAG index
Step "Creating tables, demo data and the knowledge index"
Push-Location api
if ($ollamaOk) { & $venvPy -m app.seed } else { & $venvPy -m app.seed --no-embed }
$seedExit = $LASTEXITCODE
Pop-Location
if ($seedExit -ne 0) { Fail "Seeding failed (see the error above)." }
if (-not $ollamaOk) { Warn "Knowledge index built without embeddings. After Ollama is running: api\.venv\Scripts\python.exe -m app.seed --rag-only (run inside api\)" }

Write-Host ""
Write-Host "  Setup complete." -ForegroundColor Green
Write-Host "  Start the app with start.bat, then open http://localhost:8000"
Write-Host "  Demo logins (password parola123): ion.popescu@4health.ro (patient), dr.andrei.ionescu@4health.ro (doctor)"
Write-Host ""
