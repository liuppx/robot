#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PID_FILE="${CODER_BOT_PID_FILE:-${APP_DIR}/data/coder-bot.pid}"
NULL_FILE="${CODER_BOT_NULL_FILE:-/tmp/coder-bot.null}"

: >"${NULL_FILE}"

if [[ ! -f "${PID_FILE}" ]]; then
  echo "not running"
  exit 0
fi

pid="$(cat "${PID_FILE}" 2>"${NULL_FILE}" || true)"
if [[ -z "${pid}" ]]; then
  rm -f "${PID_FILE}"
  echo "removed empty pid file"
  exit 0
fi

if kill -0 "${pid}" 2>"${NULL_FILE}"; then
  kill "${pid}"
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    if ! kill -0 "${pid}" 2>"${NULL_FILE}"; then
      rm -f "${PID_FILE}"
      echo "stopped: pid=${pid}"
      exit 0
    fi
  done
  kill -9 "${pid}" 2>"${NULL_FILE}" || true
fi

rm -f "${PID_FILE}"
echo "stopped: pid=${pid}"
