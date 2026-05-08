#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="/root/code/bot/example/example_feishuGitIssue"
INSTANCE_DIR="$BASE_DIR/.openclaw-feishu-gitissue-gpt54"
CONFIG_PATH="$INSTANCE_DIR/openclaw.json"
STATE_DIR="$INSTANCE_DIR/state"
LOG_PATH="$INSTANCE_DIR/gateway.out"
PORT=18890
GITHUB_ENV_FILE="${GITHUB_ENV_FILE:-/root/.config/openclaw/github-app/config.env}"

detect_openclaw_bin() {
  if [[ -n "${OPENCLAW_BIN:-}" && -x "$OPENCLAW_BIN" ]]; then
    echo "$OPENCLAW_BIN"
    return 0
  fi
  for candidate in /usr/local/bin/openclaw /usr/local/node-v22.22.0-linux-x64/bin/openclaw; do
    if [[ -x "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

load_github_env() {
  if [[ -f "$GITHUB_ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    . "$GITHUB_ENV_FILE"
    set +a
  fi

  if [[ -z "${GITHUB_APP_PRIVATE_KEY_PATH:-}" && -n "${GITHUB_APP_PRIVATE_KEY:-}" && -f "${GITHUB_APP_PRIVATE_KEY}" ]]; then
    export GITHUB_APP_PRIVATE_KEY_PATH="$GITHUB_APP_PRIVATE_KEY"
  fi
}

OPENCLAW_BIN="$(detect_openclaw_bin || true)"
load_github_env

echo "instance_dir=$INSTANCE_DIR"
echo "config_path=$CONFIG_PATH"
echo "state_dir=$STATE_DIR"

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "missing config: $CONFIG_PATH"
  echo "run: bash $BASE_DIR/scripts/init_gitissue_gpt54.sh"
  exit 1
fi

echo "== process (by config path) =="
found=0
for p in $(pgrep -x openclaw 2>/dev/null || true); do
  if [[ -r "/proc/$p/environ" ]] && tr '\0' '\n' < "/proc/$p/environ" | grep -q "^OPENCLAW_CONFIG_PATH=$CONFIG_PATH$"; then
    found=1
    echo "openclaw pid=$p"
    ps -fp "$p" || true
  fi
done
if [[ "$found" -eq 0 ]]; then
  echo "openclaw not running"
fi

echo "== port =="
ss -lntp | grep -E "127.0.0.1:$PORT|\[::1\]:$PORT" || true

echo "== github app env =="
echo "github_env_file=$GITHUB_ENV_FILE"
echo "GITHUB_APP_ID=${GITHUB_APP_ID:-<unset>}"
echo "GITHUB_APP_INSTALLATION_ID=${GITHUB_APP_INSTALLATION_ID:-<unset>}"
echo "GITHUB_DEFAULT_OWNER=${GITHUB_DEFAULT_OWNER:-${GITHUB_OWNER:-<unset>}}"
echo "GITHUB_DEFAULT_REPO=${GITHUB_DEFAULT_REPO:-${GITHUB_REPO:-<unset>}}"
echo "GITHUB_APP_PRIVATE_KEY_PATH=${GITHUB_APP_PRIVATE_KEY_PATH:-<unset>}"

if [[ -n "$OPENCLAW_BIN" ]]; then
  echo "== hooks =="
  OPENCLAW_CONFIG_PATH="$CONFIG_PATH" OPENCLAW_STATE_DIR="$STATE_DIR" "$OPENCLAW_BIN" hooks list 2>/dev/null || true
fi

echo "== recent log =="
tail -n 120 "$LOG_PATH" 2>/dev/null || true
