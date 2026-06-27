#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT_DIR/hub/backend"
CONFIG_DIR="$ROOT_DIR/config"
ENV_TEMPLATE="$CONFIG_DIR/hub.env.template"
ENV_FILE="$CONFIG_DIR/hub.env"
APP_ENV_FILE="$APP_DIR/.env"

SKIP_OPENCLAW_INSTALL="${SKIP_OPENCLAW_INSTALL:-0}"

echo "[step] bootstrap hub"
if [[ -x "$APP_DIR/.venv/bin/robot-hub" ]]; then
  echo "[ok] python hub already prepared: $APP_DIR/.venv/bin/robot-hub"
elif command -v uv >/dev/null 2>&1; then
  echo "[step] install python control plane dependencies"
  (cd "$APP_DIR" && uv sync)
else
  echo "[error] uv not found. Install uv first: https://docs.astral.sh/uv/" >&2
  exit 1
fi

if [[ ! -f "$ENV_TEMPLATE" ]]; then
  cp "$APP_DIR/.env.example" "$ENV_TEMPLATE"
  echo "[info] created $ENV_TEMPLATE from hub/backend/.env.example"
fi

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ENV_TEMPLATE" "$ENV_FILE"
  echo "[info] created $ENV_FILE from template"
fi

if ! command -v openclaw >/dev/null 2>&1; then
  if [[ "$SKIP_OPENCLAW_INSTALL" == "1" ]]; then
    echo "[warn] openclaw missing and SKIP_OPENCLAW_INSTALL=1, skip install"
  else
    echo "[step] install openclaw (node + cli)"
    bash "$ROOT_DIR/scripts/setup/openclaw_prepare.sh" install
  fi
fi

if command -v openclaw >/dev/null 2>&1; then
  echo "[ok] openclaw: $(openclaw --version 2>/dev/null || true)"
else
  echo "[warn] openclaw still missing; WhatsApp/DingTalk instances cannot be started until installed"
fi

if [[ -n "${ROUTER_API_KEY:-}" ]]; then
  if grep -q '^ROUTER_API_KEY=' "$ENV_FILE"; then
    sed -i "s|^ROUTER_API_KEY=.*$|ROUTER_API_KEY=${ROUTER_API_KEY}|" "$ENV_FILE"
  else
    echo "ROUTER_API_KEY=${ROUTER_API_KEY}" >> "$ENV_FILE"
  fi
  echo "[ok] injected ROUTER_API_KEY into $ENV_FILE from current shell env"
fi

if [[ ! -f "$APP_ENV_FILE" ]]; then
  cp "$ENV_FILE" "$APP_ENV_FILE"
  echo "[info] created app env: $APP_ENV_FILE"
fi

echo "[next] if needed, edit: $ENV_FILE"
echo "[next] start control plane: bash $ROOT_DIR/scripts/starter.sh start"
