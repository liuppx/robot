#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="/root/code/bot/example/example_feishuGitIssue"
INSTANCE_DIR="$BASE_DIR/.openclaw-feishu-gitissue-gpt54"
CONFIG_PATH="$INSTANCE_DIR/openclaw.json"
STATE_DIR="$INSTANCE_DIR/state"
LOG_PATH="$INSTANCE_DIR/gateway.out"
PID_FILE="$INSTANCE_DIR/openclaw.pid"
PORT=18890
START_TIMEOUT_SEC=90
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

find_running_pids() {
  for p in $(pgrep -x openclaw 2>/dev/null || true); do
    if [[ -r "/proc/$p/environ" ]] && tr '\0' '\n' < "/proc/$p/environ" | grep -q "^OPENCLAW_CONFIG_PATH=$CONFIG_PATH$"; then
      echo "$p"
    fi
  done
}

is_port_ready() {
  ss -lntp 2>/dev/null | grep -qE "127.0.0.1:$PORT|\[::1\]:$PORT"
}

OPENCLAW_BIN="$(detect_openclaw_bin || true)"
if [[ -z "$OPENCLAW_BIN" ]]; then
  echo "openclaw binary not found"
  exit 1
fi

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "missing config: $CONFIG_PATH"
  echo "run: bash $BASE_DIR/scripts/init_gitissue_gpt54.sh"
  exit 1
fi

pids="$(find_running_pids || true)"
if [[ -n "$pids" ]] && is_port_ready; then
  echo "already running: pids=$pids"
  exit 0
fi

if [[ -n "$pids" ]]; then
  for p in $pids; do
    kill "$p" 2>/dev/null || true
  done
  sleep 1
fi

load_github_env
mkdir -p "$STATE_DIR"
nohup env OPENCLAW_CONFIG_PATH="$CONFIG_PATH" OPENCLAW_STATE_DIR="$STATE_DIR" \
  "$OPENCLAW_BIN" gateway run --port "$PORT" >> "$LOG_PATH" 2>&1 &

echo $! > "$PID_FILE"

elapsed=0
while [[ "$elapsed" -lt "$START_TIMEOUT_SEC" ]]; do
  sleep 2
  elapsed=$((elapsed + 2))
  now="$(find_running_pids || true)"
  if [[ -n "$now" ]] && is_port_ready; then
    echo "started: pids=$now (wait=${elapsed}s)"
    exit 0
  fi
done

echo "start failed after ${START_TIMEOUT_SEC}s, tail log:"
tail -n 200 "$LOG_PATH" || true
exit 1
