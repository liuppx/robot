#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT_DIR/hub/backend"
ENV_FILE="$ROOT_DIR/config/hub.env"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ENV_FILE"
  set +a
elif [[ -f "$APP_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$APP_DIR/.env"
  set +a
fi

echo "[run] robot control plane on ${HUB_BIND_ADDR:-127.0.0.1:3900}"
if [[ -x "$APP_DIR/.venv/bin/robot-control-plane" ]]; then
  exec "$APP_DIR/.venv/bin/robot-control-plane"
fi

if command -v uv >/dev/null 2>&1; then
  cd "$APP_DIR"
  exec uv run robot-control-plane
fi

echo "[error] python control plane is not prepared. Run scripts/bootstrap_full_stack.sh first." >&2
exit 1
