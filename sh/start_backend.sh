#!/usr/bin/env bash
# start_backend.sh — sets up and starts the governance layer API on port 8000.
#
# Usage:
#   ./sh/start_backend.sh                    # uses policy.yaml or policy.example.yaml
#   ./sh/start_backend.sh --config my.yaml   # explicit config file
#   ./sh/start_backend.sh --port 9000        # custom port
#   ./sh/start_backend.sh --help
#
# The script mirrors the production setup:
#   1. Creates/updates the virtualenv with all required extras
#   2. Loads .env if present
#   3. Reads the active config file and validates provider requirements
#   4. Warns on missing API keys or unreachable local servers
#   5. Starts uvicorn on the configured port
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ── helpers ────────────────────────────────────────────────────────────────
ok()   { printf '  \033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[1;33m⚠\033[0m  %s\n' "$*"; }
step() { printf '\n\033[1;34m▶ %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
info() { printf '  \033[0;37m%s\033[0m\n' "$*"; }

CONFIG_FILE=""
PORT=8000

# ── args ───────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config|-c) CONFIG_FILE="$2"; shift 2 ;;
    --port|-p)   PORT="$2";        shift 2 ;;
    --help|-h)
      sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) fail "Unknown option: $1. Run with --help for usage." ;;
  esac
done

# ── prerequisites ──────────────────────────────────────────────────────────
step "Checking prerequisites"

command -v uv >/dev/null 2>&1 \
  && ok "uv $(uv --version 2>&1 | head -1)" \
  || fail "uv not found — install it: https://docs.astral.sh/uv/getting-started/installation/"

command -v python3 >/dev/null 2>&1 \
  && ok "python $(python3 --version 2>&1)" \
  || fail "python3 not found"

# ── venv + deps ────────────────────────────────────────────────────────────
step "Python environment"

if [ ! -d "$ROOT/.venv" ]; then
  uv venv "$ROOT/.venv"
  ok "venv created"
else
  ok "venv already exists"
fi

# Install all optional extras so every provider kind works out of the box
uv pip install -e "$ROOT[server,anthropic,openai,local,yaml,dotenv]" -q
ok "pygola installed (server, anthropic, openai, local, yaml, dotenv)"

PYTHON="$ROOT/.venv/bin/python"

# ── env vars ───────────────────────────────────────────────────────────────
step "Environment"

if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck source=/dev/null
  source "$ROOT/.env"
  set +a
  ok ".env loaded"
elif [ -f "$ROOT/.env.example" ]; then
  warn ".env not found — copying .env.example to .env"
  cp "$ROOT/.env.example" "$ROOT/.env"
  warn "Edit $ROOT/.env and add your real API keys, then re-run this script."
  # Load it anyway so the rest of the script can inspect vars
  set -a
  source "$ROOT/.env"
  set +a
else
  info "No .env file found — relying on shell environment variables"
fi

# ── config file resolution ─────────────────────────────────────────────────
step "Config"

if [ -n "$CONFIG_FILE" ]; then
  [ -f "$CONFIG_FILE" ] || fail "Config file not found: $CONFIG_FILE"
  ok "Using config: $CONFIG_FILE"
elif [ -f "$ROOT/policy.yaml" ]; then
  CONFIG_FILE="$ROOT/policy.yaml"
  ok "Using config: policy.yaml"
elif [ -f "$ROOT/policy.example.yaml" ]; then
  CONFIG_FILE="$ROOT/policy.example.yaml"
  warn "Using policy.example.yaml (copy to policy.yaml and customise for your environment)"
else
  warn "No config file found — server will start with default mock providers"
fi

# ── provider validation ─────────────────────────────────────────────────────
if [ -n "$CONFIG_FILE" ]; then
  step "Validating providers"

  # Extract provider kinds and settings from the config using Python + PyYAML
  PROVIDER_INFO=$("$PYTHON" - "$CONFIG_FILE" <<'EOF'
import sys, json
try:
    import yaml
except ImportError:
    print(json.dumps({"error": "PyYAML not available"}))
    sys.exit(0)

path = sys.argv[1]
try:
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
except Exception as e:
    print(json.dumps({"error": str(e)}))
    sys.exit(0)

setup = cfg.get("setup", {})
result = {}
for role in ("trusted_provider", "commercial_provider"):
    p = setup.get(role, {})
    result[role] = {
        "kind":        p.get("kind", "mock"),
        "model":       p.get("model", "mock-model"),
        "api_key_env": p.get("api_key_env", ""),
        "base_url":    p.get("base_url", ""),
    }
print(json.dumps(result))
EOF
  )

  PARSE_ERROR=$("$PYTHON" -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('error',''))" "$PROVIDER_INFO" 2>/dev/null || echo "")
  if [ -n "$PARSE_ERROR" ]; then
    warn "Could not parse config for validation: $PARSE_ERROR"
  else
    for ROLE in trusted_provider commercial_provider; do
      KIND=$("$PYTHON" -c "import json,sys; d=json.loads(sys.argv[1]); print(d['$ROLE']['kind'])" "$PROVIDER_INFO")
      MODEL=$("$PYTHON" -c "import json,sys; d=json.loads(sys.argv[1]); print(d['$ROLE']['model'])" "$PROVIDER_INFO")
      API_KEY_ENV=$("$PYTHON" -c "import json,sys; d=json.loads(sys.argv[1]); print(d['$ROLE']['api_key_env'])" "$PROVIDER_INFO")
      BASE_URL=$("$PYTHON" -c "import json,sys; d=json.loads(sys.argv[1]); print(d['$ROLE']['base_url'])" "$PROVIDER_INFO")

      LABEL="${ROLE//_/ }"

      case "$KIND" in
        mock)
          ok "$LABEL: mock / $MODEL (no API key needed)"
          ;;
        anthropic|openai)
          if [ -z "$API_KEY_ENV" ]; then
            warn "$LABEL: $KIND / $MODEL — api_key_env not set in config"
          elif [ -z "${!API_KEY_ENV:-}" ]; then
            warn "$LABEL: $KIND / $MODEL — \$$API_KEY_ENV is not set (export it or add to .env)"
          else
            KEY_PREVIEW="${!API_KEY_ENV}"
            ok "$LABEL: $KIND / $MODEL — \$$API_KEY_ENV=${KEY_PREVIEW:0:8}..."
          fi
          ;;
        local)
          EFFECTIVE_URL="${BASE_URL:-http://localhost:11434/v1}"
          HOST_PORT=$(echo "$EFFECTIVE_URL" | "$PYTHON" -c "
import sys, urllib.parse
u = urllib.parse.urlparse(sys.stdin.read().strip())
print(u.hostname or 'localhost', u.port or 80)
")
          LOCAL_HOST=$(echo "$HOST_PORT" | awk '{print $1}')
          LOCAL_PORT=$(echo "$HOST_PORT" | awk '{print $2}')

          if "$PYTHON" -c "
import socket, sys
try:
    s = socket.create_connection(('$LOCAL_HOST', $LOCAL_PORT), timeout=2)
    s.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
            ok "$LABEL: local / $MODEL — server reachable at $EFFECTIVE_URL"
          else
            warn "$LABEL: local / $MODEL — server NOT reachable at $EFFECTIVE_URL"
            warn "  Start your local LLM server before sending requests:"
            if echo "$EFFECTIVE_URL" | grep -q "11434"; then
              info "  Ollama:    ollama serve && ollama pull $MODEL"
            elif echo "$EFFECTIVE_URL" | grep -q "1234"; then
              info "  LM Studio: open the app and start the local server"
            else
              info "  vLLM:      python -m vllm.entrypoints.openai.api_server --model $MODEL"
            fi
            info "  The API server will still start — requests will fail until the LLM is up."
          fi
          ;;
        *)
          warn "$LABEL: unknown provider kind '$KIND'"
          ;;
      esac
    done
  fi
fi

# ── summary ────────────────────────────────────────────────────────────────
step "Starting server"

printf '\n'
printf '  \033[1mURL\033[0m     http://localhost:%s\n' "$PORT"
printf '  \033[1mDocs\033[0m    http://localhost:%s/docs\n' "$PORT"
[ -n "$CONFIG_FILE" ] && printf '  \033[1mConfig\033[0m  %s\n' "$CONFIG_FILE"
printf '\n  Press Ctrl-C to stop.\n\n'

# ── launch ──────────────────────────────────────────────────────────────────
SERVE_ARGS=(serve --host 0.0.0.0 --port "$PORT")
[ -n "$CONFIG_FILE" ] && SERVE_ARGS+=(--config "$CONFIG_FILE")

exec "$ROOT/.venv/bin/pygola" "${SERVE_ARGS[@]}"
