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

STATE_DIR="${OPENCLAW_STATE_DIR:-${APP_DIR}/data/openclaw/state}"
PID_FILE="${OPENCLAW_PID_FILE:-${APP_DIR}/data/openclaw/openclaw-gateway.pid}"
LEGACY_STATE_DIR="${APP_DIR}/feishu-state"
if [[ ! -d "${STATE_DIR}" && -d "${LEGACY_STATE_DIR}" ]]; then
  mkdir -p "$(dirname "${STATE_DIR}")"
  mv "${LEGACY_STATE_DIR}" "${STATE_DIR}"
fi

if [[ ! -f "${STATIC_CONFIG_PATH}" ]]; then
  echo "missing OpenClaw config: ${STATIC_CONFIG_PATH}" >&2
  echo "create it from: ${APP_DIR}/config/openclaw.json.template" >&2
  exit 1
fi

if [[ -z "${UV_BIN}" ]]; then
  echo "uv not found. Install uv first or set UV_BIN=/path/to/uv." >&2
  exit 1
fi

mkdir -p "${STATE_DIR}" "${APP_DIR}/data/openclaw/workspace/main" "${APP_DIR}/data/logs" "$(dirname "${PID_FILE}")"
mkdir -p "${UV_CACHE_DIR}"

CONFIG_PATH="$(
  cd "${APP_DIR}"
  OPENCLAW_CONFIG_PATH="${STATIC_CONFIG_PATH}" \
  OPENCLAW_RUNTIME_CONFIG_PATH="${RUNTIME_CONFIG_PATH}" \
  OPENCLAW_STATE_DIR="${STATE_DIR}" \
  CODER_BOT_ENV_FILE="${ENV_FILE}" \
  UV_CACHE_DIR="${UV_CACHE_DIR}" \
    "${UV_BIN}" run --frozen coder-bot --env-file "${ENV_FILE}" prepare-openclaw-runtime
)"
CONFIG_PATH="$(printf '%s\n' "${CONFIG_PATH}" | tail -n 1)"
if [[ -z "${CONFIG_PATH}" ]]; then
  echo "failed to prepare OpenClaw runtime config" >&2
  exit 1
fi

export OPENCLAW_CONFIG_PATH="${CONFIG_PATH}"
export OPENCLAW_RUNTIME_CONFIG_PATH="${RUNTIME_CONFIG_PATH}"
export OPENCLAW_STATE_DIR="${STATE_DIR}"

# systemd 直接运行 run_gateway.sh 时不会经过 start_gateway.sh，这里补写 pid 文件，
# 让 status_gateway.sh 能拿到当前真实进程号。
echo "$$" >"${PID_FILE}"

exec "${APP_DIR}/scripts/openclaw-local" gateway run "$@"
