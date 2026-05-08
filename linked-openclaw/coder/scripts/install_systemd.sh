#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
ENV_FILE="${ENV_FILE:-${APP_DIR}/config/coder-bot.env}"
OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-${APP_DIR}/config/openclaw.json}"
OPENCLAW_RUNTIME_CONFIG_PATH="${OPENCLAW_RUNTIME_CONFIG_PATH:-${APP_DIR}/data/openclaw/runtime/openclaw.runtime.json}"
SERVICE_NAME="${SERVICE_NAME:-coder-bot}"
GATEWAY_SERVICE_NAME="${GATEWAY_SERVICE_NAME:-openclaw-gateway}"
BOT_USER="${BOT_USER:-${SUDO_USER:-$(id -un)}}"
UV_BIN="${UV_BIN:-$(command -v uv || true)}"

if [[ -z "${UV_BIN}" ]]; then
  echo "uv not found. Install uv first or set UV_BIN=/path/to/uv." >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "env file not found: ${ENV_FILE}" >&2
  exit 1
fi

if [[ ! -f "${APP_DIR}/pyproject.toml" ]]; then
  echo "APP_DIR does not look like coder-bot: ${APP_DIR}" >&2
  exit 1
fi

if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=()
else
  SUDO=(sudo)
fi

TMP_SERVICE="$(mktemp)"
TMP_GATEWAY_SERVICE="$(mktemp)"

cat >"${TMP_GATEWAY_SERVICE}" <<EOF
[Unit]
Description=OpenClaw Gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${BOT_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
Environment=ENV_FILE=${ENV_FILE}
Environment=UV_BIN=${UV_BIN}
Environment=UV_CACHE_DIR=/tmp/coder-bot-uv-cache
Environment=OPENCLAW_CONFIG_PATH=${OPENCLAW_CONFIG_PATH}
Environment=OPENCLAW_RUNTIME_CONFIG_PATH=${OPENCLAW_RUNTIME_CONFIG_PATH}
Environment=OPENCLAW_STATE_DIR=${APP_DIR}/data/openclaw/state
Environment=OPENCLAW_LOG_PATH=${APP_DIR}/data/logs/openclaw-gateway.log
ExecStart=${APP_DIR}/scripts/run_gateway.sh
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat >"${TMP_SERVICE}" <<EOF
[Unit]
Description=Coder Bot
Requires=${GATEWAY_SERVICE_NAME}.service
After=${GATEWAY_SERVICE_NAME}.service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${BOT_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
Environment=CODER_BOT_ENV_FILE=${ENV_FILE}
Environment=UV_CACHE_DIR=/tmp/coder-bot-uv-cache
ExecStart=${UV_BIN} run --frozen gunicorn -c ${APP_DIR}/gunicorn.conf.py issue_bot_service:APP
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

"${SUDO[@]}" install -m 0644 "${TMP_GATEWAY_SERVICE}" "/etc/systemd/system/${GATEWAY_SERVICE_NAME}.service"
"${SUDO[@]}" install -m 0644 "${TMP_SERVICE}" "/etc/systemd/system/${SERVICE_NAME}.service"
rm -f "${TMP_GATEWAY_SERVICE}"
rm -f "${TMP_SERVICE}"

"${SUDO[@]}" systemctl daemon-reload
"${SUDO[@]}" systemctl enable "${GATEWAY_SERVICE_NAME}"
"${SUDO[@]}" systemctl enable "${SERVICE_NAME}"
"${SUDO[@]}" systemctl restart "${GATEWAY_SERVICE_NAME}"
"${SUDO[@]}" systemctl restart "${SERVICE_NAME}"
"${SUDO[@]}" systemctl status --no-pager "${GATEWAY_SERVICE_NAME}"
"${SUDO[@]}" systemctl status --no-pager "${SERVICE_NAME}"
