#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/config/bot-hub.env"
LEGACY_ENV_FILE="$ROOT_DIR/dashboard/.env"

bash "$ROOT_DIR/scripts/bootstrap_full_stack.sh"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1091
  source "$ENV_FILE" || true
elif [[ -f "$LEGACY_ENV_FILE" ]]; then
  # shellcheck disable=SC1091
  source "$LEGACY_ENV_FILE" || true
fi

if [[ -z "${ROUTER_API_KEY:-}" ]]; then
  echo "[warn] ROUTER_API_KEY is empty in $ENV_FILE"
  echo "[warn] Web UI can start, but Router model list and model calls may fail until key is configured."
fi

bash "$ROOT_DIR/scripts/starter.sh" start

echo "[step] runtime status"
bash "$ROOT_DIR/scripts/status_full_stack.sh"

echo "[step] doctor checks"
if ! bash "$ROOT_DIR/scripts/doctor_full_stack.sh"; then
  echo "[warn] doctor reported issues; review output above"
fi

echo "[done] Open in browser: http://${BOT_HUB_BIND_ADDR:-127.0.0.1:3900}/"
