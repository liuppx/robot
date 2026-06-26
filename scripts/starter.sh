#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-start}"

usage() {
  cat <<USAGE
Usage: $(basename "$0") [start|stop|restart]

No argument defaults to: start
USAGE
}

load_env() {
  local primary="$ROOT_DIR/config/bot-hub.env"
  local fallback="$ROOT_DIR/dashboard/.env"
  local template="$ROOT_DIR/config/bot-hub.env.template"
  local loaded=""

  if [[ -f "$primary" ]]; then
    loaded="$primary"
  elif [[ -f "$fallback" ]]; then
    loaded="$fallback"
  fi

  if [[ -n "$loaded" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$loaded"
    set +a
  else
    if [[ -f "$template" ]]; then
      echo "[warn] config file not found: $primary"
      echo "[warn] copy template and edit it: cp $template $primary"
    else
      echo "[warn] config file not found, and template missing: $template"
    fi
  fi

  export BOT_HUB_REPO_ROOT="${BOT_HUB_REPO_ROOT:-$ROOT_DIR}"
  export BOT_HUB_RUNTIME_DIR="${BOT_HUB_RUNTIME_DIR:-$ROOT_DIR/runtime/control-plane}"
  export BOT_HUB_INSTANCES_ROOT="${BOT_HUB_INSTANCES_ROOT:-$ROOT_DIR/runtime/instances}"
  export BOT_HUB_BIND_ADDR="${BOT_HUB_BIND_ADDR:-127.0.0.1:3900}"
}

resolve_bin() {
  local candidates=()

  if [[ -n "${BOT_HUB_BIN:-}" ]]; then
    candidates+=("$BOT_HUB_BIN")
  fi

  candidates+=(
    "$ROOT_DIR/build/bot-hub-control-plane"
    "$ROOT_DIR/dashboard/target/release/bot-hub-control-plane"
    "$ROOT_DIR/dashboard/target/debug/bot-hub-control-plane"
  )

  local p
  for p in "${candidates[@]}"; do
    if [[ -x "$p" ]]; then
      echo "$p"
      return 0
    fi
  done

  echo "[error] bot-hub binary not found. Tried:" >&2
  for p in "${candidates[@]}"; do
    echo "  - $p" >&2
  done
  exit 1
}

runtime_pid_file() {
  echo "${BOT_HUB_RUNTIME_DIR}/control-plane.pid"
}

runtime_log_file() {
  echo "${BOT_HUB_RUNTIME_DIR}/logs/control-plane.out.log"
}

ensure_runtime_dirs() {
  mkdir -p "${BOT_HUB_RUNTIME_DIR}/logs" "${BOT_HUB_INSTANCES_ROOT}"
}

is_pid_alive() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

wait_pid_exit() {
  local pid="$1"
  local max_wait="${2:-10}"
  local i
  for ((i=0; i<max_wait; i++)); do
    if ! is_pid_alive "$pid"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

start_service() {
  load_env
  ensure_runtime_dirs

  local bin
  bin="$(resolve_bin)"

  local pid_file
  pid_file="$(runtime_pid_file)"

  local log_file
  log_file="$(runtime_log_file)"

  if [[ -f "$pid_file" ]]; then
    local old_pid
    old_pid="$(cat "$pid_file" || true)"
    if is_pid_alive "$old_pid"; then
      echo "[ok] already running, pid=$old_pid"
      echo "[url] http://${BOT_HUB_BIND_ADDR}/"
      return 0
    fi
  fi

  local existing_pid
  existing_pid="$(pgrep -f "$bin" | head -n 1 || true)"
  if is_pid_alive "$existing_pid"; then
    echo "$existing_pid" > "$pid_file"
    echo "[ok] already running, recovered pid=$existing_pid"
    echo "[url] http://${BOT_HUB_BIND_ADDR}/"
    return 0
  fi

  local port
  port="${BOT_HUB_BIND_ADDR##*:}"
  if ss -lnt | awk '{print $4}' | grep -E "[:.]${port}$" >/dev/null 2>&1; then
    echo "[error] port $port is already in use; set BOT_HUB_BIND_ADDR or stop conflicting process" >&2
    return 1
  fi

  echo "[info] starting bot-hub: $bin"
  nohup "$bin" > "$log_file" 2>&1 &
  local pid=$!
  echo "$pid" > "$pid_file"

  sleep 1
  if is_pid_alive "$pid"; then
    echo "[ok] started pid=$pid bind=${BOT_HUB_BIND_ADDR}"
    echo "[log] $log_file"
    echo "[url] http://${BOT_HUB_BIND_ADDR}/"
    echo "[health] curl -sS http://${BOT_HUB_BIND_ADDR}/api/v1/public/health"
  else
    echo "[error] start failed, see log: $log_file" >&2
    return 1
  fi
}

stop_service() {
  load_env

  local pid_file
  pid_file="$(runtime_pid_file)"

  if [[ ! -f "$pid_file" ]]; then
    echo "[ok] no pid file, already stopped"
    return 0
  fi

  local pid
  pid="$(cat "$pid_file" || true)"

  if ! is_pid_alive "$pid"; then
    rm -f "$pid_file"
    echo "[ok] process already gone"
    return 0
  fi

  kill "$pid" || true
  if ! wait_pid_exit "$pid" 8; then
    echo "[warn] graceful stop timeout, force killing pid=$pid"
    kill -9 "$pid" || true
  fi

  rm -f "$pid_file"
  echo "[ok] stopped pid=$pid"
}

restart_service() {
  stop_service
  start_service
}

case "$ACTION" in
  start)
    start_service
    ;;
  stop)
    stop_service
    ;;
  restart)
    restart_service
    ;;
  *)
    usage
    exit 1
    ;;
esac
