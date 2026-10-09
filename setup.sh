#!/usr/bin/env bash
# 4Health - one-time setup for Linux / macOS.   Usage: ./setup.sh
# Safe to run again: every step skips what is already done.
set -euo pipefail
cd "$(dirname "$0")"

step() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }
ok()   { printf '    \033[32mOK\033[0m  %s\n' "$1"; }
warn() { printf '    \033[33m!!\033[0m  %s\n' "$1"; }
fail() { printf '\n    \033[31mXX  %s\033[0m\n\n' "$1"; exit 1; }

# 1. Python
step "Checking Python (3.11+ required)"
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then PY="$c"; break; fi
done
[ -n "$PY" ] || fail "Python 3.11+ not found."
ok "Using $($PY --version)"

# 2. venv + packages
step "Creating the virtual environment and installing packages"
[ -x api/.venv/bin/python ] || "$PY" -m venv api/.venv
VPY=api/.venv/bin/python
"$VPY" -m pip install --quiet --no-cache-dir --upgrade pip
"$VPY" -m pip install --quiet --no-cache-dir -r api/requirements.txt
ok "Packages installed in api/.venv"

# 3. .env
step "Configuration file (.env)"
if [ ! -f .env ]; then
  cp .env.example .env
  SECRET=$("$VPY" -c 'import secrets; print(secrets.token_urlsafe(48))')
  sed -i.bak "s|^JWT_SECRET=.*|JWT_SECRET=$SECRET|" .env && rm -f .env.bak
  ok "Created .env from .env.example"
else
  ok ".env already exists (left unchanged)"
fi

# 4. PostgreSQL
step "PostgreSQL database"
command -v psql >/dev/null || fail "psql not found. Install PostgreSQL 14+ (e.g. sudo apt install postgresql / brew install postgresql) and run setup again."
if PGPASSWORD=health4 psql -U health4 -h localhost -d health4 -tAc 'SELECT 1' >/dev/null 2>&1; then
  ok "Database 'health4' already exists"
else
  # Linux: the postgres OS user usually has local access; otherwise (macOS/Homebrew, custom installs) connect over TCP.
  if ! { command -v sudo >/dev/null && id postgres >/dev/null 2>&1 && \
         sudo -u postgres psql -v ON_ERROR_STOP=1 -q -f api/scripts/setup_db.sql 2>/dev/null; }; then
    psql -U postgres -h localhost -v ON_ERROR_STOP=1 -q -f api/scripts/setup_db.sql \
      || fail "Could not create the database. Is PostgreSQL running? Does the 'postgres' user exist?"
  fi
  ok "Created user 'health4' and databases 'health4', 'health4_test'"
fi

# 5. Ollama models
step "Ollama models"
LLM=$(grep -E '^LLM_MODEL=' .env | cut -d= -f2- || true);   LLM=${LLM:-qwen2.5:3b}
EMB=$(grep -E '^EMBED_MODEL=' .env | cut -d= -f2- || true); EMB=${EMB:-bge-m3}
OLLAMA_OK=0
if ! command -v ollama >/dev/null; then
  warn "Ollama not installed (https://ollama.com/download). The app runs without AI answers."
elif ! LIST=$(ollama list 2>/dev/null); then
  warn "Ollama is installed but not running (start it with: ollama serve)."
else
  OLLAMA_OK=1
  for m in "$LLM" "$EMB"; do
    if echo "$LIST" | grep -qE "^${m}(:latest)?[[:space:]]"; then ok "Model $m present"
    else echo "    Downloading $m ..."; ollama pull "$m" || warn "Could not pull $m"; fi
  done
fi

# 6. Seed
step "Creating tables, demo data and the knowledge index"
if [ "$OLLAMA_OK" = 1 ]; then (cd api && .venv/bin/python -m app.seed)
else (cd api && .venv/bin/python -m app.seed --no-embed); warn "Index built without embeddings; later run: cd api && .venv/bin/python -m app.seed --rag-only"; fi

printf '\n  \033[32mSetup complete.\033[0m Start the app with ./start.sh and open http://localhost:8000\n'
printf '  Demo logins (password parola123): ion.popescu@4health.ro (patient), dr.andrei.ionescu@4health.ro (doctor)\n\n'
