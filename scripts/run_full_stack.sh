#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[deprecated] scripts/run_full_stack.sh 已降级为兼容入口，请改用 scripts/starter.sh start"
echo "[info] default control plane is Python hub/backend"
exec bash "$ROOT_DIR/scripts/starter.sh" start
