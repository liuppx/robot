#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[deprecated] scripts/run_full_stack.sh 已降级为兼容入口，请改用 scripts/starter.sh start"

if [[ ! -x "$ROOT_DIR/build/bot-hub-control-plane" && ! -x "$ROOT_DIR/dashboard/target/release/bot-hub-control-plane" && ! -x "$ROOT_DIR/dashboard/target/debug/bot-hub-control-plane" ]]; then
  echo "[info] binary missing, bootstrap once..."
  bash "$ROOT_DIR/scripts/bootstrap_rust_control_plane.sh"
fi

exec bash "$ROOT_DIR/scripts/starter.sh" start
