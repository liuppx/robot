#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
ENV_FILE="${ENV_FILE:-${APP_DIR}/.env}"
ENV_TEMPLATE="${ENV_TEMPLATE:-${APP_DIR}/.env.template}"
SERVICE_NAME="${SERVICE_NAME:-coding-bot}"
BOT_USER="${BOT_USER:-$(id -un)}"
UV_BIN="${UV_BIN:-$(command -v uv || true)}"
UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/coding-bot-uv-cache}"
INSTALL_SYSTEMD="${INSTALL_SYSTEMD:-true}"

log() {
  echo "[bootstrap] $*"
}

fail() {
  echo "[bootstrap] ERROR: $*" >&2
  exit 1
}

require_file() {
  local path="$1"
  [[ -f "${path}" ]] || fail "file not found: ${path}"
}

if [[ -z "${UV_BIN}" ]]; then
  fail "uv not found. Install uv first or set UV_BIN=/path/to/uv."
fi

require_file "${APP_DIR}/pyproject.toml"

if [[ ! -f "${ENV_FILE}" ]]; then
  if [[ -f "${ENV_TEMPLATE}" ]]; then
    cp "${ENV_TEMPLATE}" "${ENV_FILE}"
    log "created ${ENV_FILE} from ${ENV_TEMPLATE}"
    log "fill in the required values in ${ENV_FILE}, then rerun bootstrap"
    exit 1
  fi
  fail "env file not found: ${ENV_FILE}"
fi

log "syncing dependencies with uv"
"${UV_BIN}" sync --frozen --directory "${APP_DIR}"

log "checking ${ENV_FILE}"
python3 - "${ENV_FILE}" <<'PY'
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
values: dict[str, str] = {}
for raw_line in env_path.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key.strip()] = value.strip().strip('"').strip("'")

required = [
    "GITHUB_APP_ID",
    "GITHUB_INSTALLATION_ID",
    "GITHUB_PRIVATE_KEY_PATH",
    "GITHUB_FORK_OWNER",
    "ALLOWED_REPOS",
    "CODEX_BIN",
    "CODEX_SOURCE_HOME",
    "GITHUB_CLONE_SSH_KEY_PATH",
]

placeholder_values = {
    "your-github-name",
    "upstream-owner/repo-name",
}

errors: list[str] = []
for key in required:
    value = values.get(key, "").strip()
    if not value:
        errors.append(f"{key} is empty")
    elif value in placeholder_values:
        errors.append(f"{key} still uses template placeholder: {value}")

if values.get("ENABLE_WEBHOOK", "").strip().lower() in {"1", "true", "yes", "on"}:
    if not values.get("GITHUB_WEBHOOK_SECRET", "").strip():
        errors.append("ENABLE_WEBHOOK=true but GITHUB_WEBHOOK_SECRET is empty")

if values.get("ENABLE_WEBHOOK", "").strip().lower() not in {"1", "true", "yes", "on"} and \
   values.get("ENABLE_POLLING", "true").strip().lower() not in {"1", "true", "yes", "on"}:
    errors.append("ENABLE_WEBHOOK and ENABLE_POLLING cannot both be disabled")

if errors:
    print("env check failed:")
    for item in errors:
        print(f"- {item}")
    sys.exit(1)

print("env check passed")
PY

log "running coding-bot doctor"
(
  cd "${APP_DIR}"
  CODING_BOT_ENV_FILE="${ENV_FILE}" UV_CACHE_DIR="${UV_CACHE_DIR}" \
    "${UV_BIN}" run --frozen coding-bot --env-file "${ENV_FILE}" doctor
)

if [[ "${INSTALL_SYSTEMD}" == "true" ]]; then
  log "installing systemd service ${SERVICE_NAME}"
  (
    cd "${APP_DIR}"
    BOT_USER="${BOT_USER}" ENV_FILE="${ENV_FILE}" SERVICE_NAME="${SERVICE_NAME}" \
      UV_BIN="${UV_BIN}" ./scripts/install_systemd.sh
  )
else
  log "skipping systemd install because INSTALL_SYSTEMD=${INSTALL_SYSTEMD}"
fi

log "bootstrap completed"
log "health check: curl -s http://127.0.0.1:9081/health"
log "service status: sudo systemctl status --no-pager ${SERVICE_NAME}"
log "service logs: sudo journalctl -u ${SERVICE_NAME} -f"
