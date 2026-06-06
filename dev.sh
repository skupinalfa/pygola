#!/usr/bin/env bash
# dev.sh — sets up the project and starts API + UI.
# Usage: ./dev.sh
set -euo pipefail

cd "$(dirname "$0")"

# ── helpers ────────────────────────────────────────────────────────────────
bold()  { printf '\033[1m%s\033[0m\n'       "$*"; }
step()  { printf '\n\033[1;34m▶ %s\033[0m\n' "$*"; }
ok()    { printf '  \033[1;32m✓\033[0m %s\n'  "$*"; }
skip()  { printf '  \033[90m– %s\033[0m\n'    "$*"; }
fail()  { printf '\n\033[1;31m✗ %s\033[0m\n'  "$*" >&2; exit 1; }

# ── prerequisites ──────────────────────────────────────────────────────────
step "Checking prerequisites"

command -v uv   >/dev/null 2>&1 \
  && ok "uv $(uv --version 2>&1 | head -1)" \
  || fail "uv not found — install it: https://docs.astral.sh/uv/getting-started/installation/"

command -v node >/dev/null 2>&1 \
  && ok "node $(node --version)" \
  || fail "node not found — install it: https://nodejs.org"

command -v npm >/dev/null 2>&1 \
  && ok "npm $(npm --version)" \
  || fail "npm not found — install it: https://nodejs.org"

# ── python venv ────────────────────────────────────────────────────────────
step "Python environment"

if [ -d .venv ]; then
  skip "venv already exists"
else
  uv venv .venv
  ok "venv created"
fi

step "Installing Python packages"
uv pip install -e ".[dev,server]" -q
ok "pygola (editable, with server extras)"

# ── node ───────────────────────────────────────────────────────────────────
step "Installing Node.js packages"

if [ -d ui/node_modules ]; then
  (cd ui && npm install --silent)
  skip "node_modules already present, updated if needed"
else
  (cd ui && npm install --silent)
  ok "node_modules installed"
fi

# ── start ──────────────────────────────────────────────────────────────────
echo ""
bold "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
bold " Everything is ready — starting servers"
bold "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
printf "\n  UI   →  \033[1;4mhttp://localhost:3000\033[0m\n"
printf   "  API  →  \033[1;4mhttp://localhost:8000\033[0m"
printf   "  (docs: \033[4mhttp://localhost:8000/docs\033[0m)\n\n"
printf   "  Press Ctrl-C to stop both servers.\n\n"

# Kill both child processes on exit (Ctrl-C or error).
cleanup() {
  echo ""
  bold "Stopping servers…"
  kill "$PYTHON_PID" "$NEXT_PID" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

PYTHONUNBUFFERED=1 .venv/bin/uvicorn pygola.server:app --reload --port 8000 &
PYTHON_PID=$!

(cd ui && npm run dev) &
NEXT_PID=$!

wait "$PYTHON_PID" "$NEXT_PID"
