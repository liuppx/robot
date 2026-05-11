#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-${APP_DIR}/config/coder-bot.env}"
USER_HOME="${HOME:-$(getent passwd "$(id -un)" | cut -d: -f6)}"

load_env_file() {
  local path="$1"
  python3 - "$path" <<'PY'
import shlex
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(0)

for raw_line in path.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    value = value.strip()
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        value = value[1:-1]
    print(f"export {key}={shlex.quote(value)}")
PY
}

if [[ -f "${ENV_FILE}" ]]; then
  eval "$(load_env_file "${ENV_FILE}")"
fi
RUNNER="${APP_DIR}/scripts/openclaw-local"
UV_BIN="${UV_BIN:-$(command -v uv || true)}"
if [[ -z "${UV_BIN}" ]]; then
  UV_BIN="${USER_HOME}/.local/bin/uv"
fi
UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/coder-bot-uv-cache}"
STATIC_CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-${APP_DIR}/config/openclaw.json}"
RUNTIME_CONFIG_PATH="${OPENCLAW_RUNTIME_CONFIG_PATH:-${APP_DIR}/data/openclaw/runtime/openclaw.runtime.json}"
STATE_DIR="${OPENCLAW_STATE_DIR:-${APP_DIR}/data/openclaw/state}"
LEGACY_STATE_DIR="${APP_DIR}/feishu-state"
if [[ ! -d "${STATE_DIR}" && -d "${LEGACY_STATE_DIR}" ]]; then
  STATE_DIR="${LEGACY_STATE_DIR}"
fi
LOG_PATH="${OPENCLAW_LOG_PATH:-${APP_DIR}/data/logs/openclaw-gateway.log}"
PID_FILE="${OPENCLAW_PID_FILE:-${APP_DIR}/data/openclaw/openclaw-gateway.pid}"

CONFIG_PATH="${RUNTIME_CONFIG_PATH}"
if [[ ! -f "${CONFIG_PATH}" ]]; then
  CONFIG_PATH="${STATIC_CONFIG_PATH}"
fi

if [[ -n "${UV_BIN}" ]]; then
  mkdir -p "${UV_CACHE_DIR}"
  if prepared="$(
    cd "${APP_DIR}"
    OPENCLAW_CONFIG_PATH="${STATIC_CONFIG_PATH}" \
    OPENCLAW_RUNTIME_CONFIG_PATH="${RUNTIME_CONFIG_PATH}" \
    OPENCLAW_STATE_DIR="${STATE_DIR}" \
    CODER_BOT_ENV_FILE="${ENV_FILE}" \
    UV_CACHE_DIR="${UV_CACHE_DIR}" \
      "${UV_BIN}" run --frozen coder-bot --env-file "${ENV_FILE}" prepare-openclaw-runtime 2>/dev/null
  )"; then
    CONFIG_PATH="$(printf '%s\n' "${prepared}" | tail -n 1)"
  fi
fi

PORT="$(
  python3 - "${CONFIG_PATH}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    print("18831")
    raise SystemExit(0)
payload = json.loads(path.read_text(encoding="utf-8"))
gateway = payload.get("gateway") or {}
print(gateway.get("port") or 18831)
PY
)"

echo "instance_dir=${APP_DIR}"
echo "static_config_path=${STATIC_CONFIG_PATH}"
echo "runtime_config_path=${RUNTIME_CONFIG_PATH}"
echo "effective_config_path=${CONFIG_PATH}"
echo "state_dir=${STATE_DIR}"

echo "== process =="
if [[ -f "${PID_FILE}" ]]; then
  pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
  if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
    echo "openclaw pid=${pid}"
    ps -fp "${pid}" || true
  else
    echo "pid file exists but process is not running"
  fi
else
  echo "pid file not found"
fi

echo "== port =="
ss -lnt 2>/dev/null | awk '{print $4}' | grep -q ":${PORT}$" \
  && ss -lntp 2>/dev/null | grep ":${PORT}" \
  || echo "gateway port ${PORT} is not listening"

echo "== agents =="
OPENCLAW_CONFIG_PATH="${CONFIG_PATH}" OPENCLAW_STATE_DIR="${STATE_DIR}" \
  "${RUNNER}" agents list --json 2>/dev/null || true

echo "== key config =="
OPENCLAW_CONFIG_PATH="${CONFIG_PATH}" OPENCLAW_STATE_DIR="${STATE_DIR}" \
  "${RUNNER}" config get gateway.port 2>/dev/null || true
OPENCLAW_CONFIG_PATH="${CONFIG_PATH}" OPENCLAW_STATE_DIR="${STATE_DIR}" \
  "${RUNNER}" config get agents.defaults.model.primary 2>/dev/null || true
OPENCLAW_CONFIG_PATH="${CONFIG_PATH}" OPENCLAW_STATE_DIR="${STATE_DIR}" \
  "${RUNNER}" config get channels.feishu.groupAllowFrom 2>/dev/null || true

echo "== recent log =="
if [[ ! -f "${LOG_PATH}" ]]; then
  fallback_log="$(ls -1t /tmp/openclaw/openclaw-*.log 2>/dev/null | head -n 1 || true)"
  if [[ -n "${fallback_log}" ]]; then
    LOG_PATH="${fallback_log}"
  fi
fi
tail -n 40 "${LOG_PATH}" 2>/dev/null || echo "log file not found: ${LOG_PATH}"
