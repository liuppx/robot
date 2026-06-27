#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/config/hub.env"
APP_ENV_FILE="$ROOT_DIR/hub/backend/.env"
PID_FILE="$ROOT_DIR/runtime/control-plane/control-plane.pid"
LOG_FILE="$ROOT_DIR/runtime/control-plane/logs/control-plane.out.log"
BIND="127.0.0.1:3900"

echo "[deprecated] scripts/status_full_stack.sh 已降级为兼容入口，建议直接用 health API + starter.sh"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ENV_FILE"
  set +a
  BIND="${HUB_BIND_ADDR:-$BIND}"
elif [[ -f "$APP_ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$APP_ENV_FILE"
  set +a
  BIND="${HUB_BIND_ADDR:-$BIND}"
fi

echo "[info] bind=$BIND"

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE" || true)"
  if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
    echo "[ok] running pid=$PID"
  else
    echo "[warn] pid file exists but process not alive"
  fi
else
  echo "[warn] pid file not found"
fi

echo "[info] health:"
curl -sS "http://$BIND/api/v1/public/health" || true

echo
if [[ -f "$LOG_FILE" ]]; then
  echo "[info] tail log:"
  tail -n 20 "$LOG_FILE"
fi
