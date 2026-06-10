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
UV_BIN="${UV_BIN:-$(command -v uv || true)}"
if [[ -z "${UV_BIN}" ]]; then
  UV_BIN="${USER_HOME}/.local/bin/uv"
fi
UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/coder-bot-uv-cache}"
STATIC_CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-${APP_DIR}/config/openclaw.json}"
RUNTIME_CONFIG_PATH="${OPENCLAW_RUNTIME_CONFIG_PATH:-${APP_DIR}/data/openclaw/runtime/openclaw.runtime.json}"
LOG_DIR="${APP_DIR}/data/logs"
LOG_PATH="${OPENCLAW_LOG_PATH:-${LOG_DIR}/openclaw-gateway.log}"
PID_FILE="${OPENCLAW_PID_FILE:-${APP_DIR}/data/openclaw/openclaw-gateway.pid}"
NULL_FILE="${OPENCLAW_NULL_FILE:-/tmp/openclaw-gateway.null}"

if [[ -z "${UV_BIN}" ]]; then
  echo "uv not found. Install uv first or set UV_BIN=/path/to/uv." >&2
  exit 1
fi

mkdir -p "${UV_CACHE_DIR}"
: >"${NULL_FILE}"

CONFIG_PATH="$(
  cd "${APP_DIR}"
  OPENCLAW_CONFIG_PATH="${STATIC_CONFIG_PATH}" \
  OPENCLAW_RUNTIME_CONFIG_PATH="${RUNTIME_CONFIG_PATH}" \
  CODER_BOT_ENV_FILE="${ENV_FILE}" \
  UV_CACHE_DIR="${UV_CACHE_DIR}" \
    "${UV_BIN}" run --frozen coder-bot --env-file "${ENV_FILE}" prepare-openclaw-runtime
)"
CONFIG_PATH="$(printf '%s\n' "${CONFIG_PATH}" | tail -n 1)"
if [[ -z "${CONFIG_PATH}" ]]; then
  echo "failed to prepare OpenClaw runtime config" >&2
  exit 1
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

mkdir -p "$(dirname "${PID_FILE}")" "${LOG_DIR}"

if [[ -f "${PID_FILE}" ]]; then
  old_pid="$(cat "${PID_FILE}" 2>"${NULL_FILE}" || true)"
  if [[ -n "${old_pid}" ]] && kill -0 "${old_pid}" 2>"${NULL_FILE}"; then
    echo "already running: pid=${old_pid}"
    exit 0
  fi
  rm -f "${PID_FILE}"
fi

if command -v setsid >"${NULL_FILE}" 2>&1; then
  setsid "${SCRIPT_DIR}/run_gateway.sh" <"${NULL_FILE}" >>"${LOG_PATH}" 2>&1 &
else
  "${SCRIPT_DIR}/run_gateway.sh" <"${NULL_FILE}" >>"${LOG_PATH}" 2>&1 &
fi
echo $! >"${PID_FILE}"

for _ in 1 2 3 4 5 6 7 8 9 10; do
  sleep 1
  if [[ -f "${PID_FILE}" ]]; then
    pid="$(cat "${PID_FILE}" 2>"${NULL_FILE}" || true)"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>"${NULL_FILE}" && ss -lnt 2>"${NULL_FILE}" | awk '{print $4}' | grep -q ":${PORT}$"; then
      echo "started: pid=${pid}"
      exit 0
    fi
  fi
done

echo "start failed; tail log:" >&2
tail -n 140 "${LOG_PATH}" >&2 || true
exit 1
