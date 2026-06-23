#!/bin/zsh
set -eu

ROOT="$HOME/.claracore/gateway"
ENV_FILE="$ROOT/gateway.env"
PROJECT_DIR="${0:A:h}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

export CLARACORE_AGENT_ID="${CLARACORE_AGENT_ID:-default}"
PYTHON="${CLARACORE_PYTHON:-$(command -v python3)}"

exec "$PYTHON" "$PROJECT_DIR/server/mcp_entry.py"
