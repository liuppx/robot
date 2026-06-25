#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT_DIR/dashboard"

if [[ -f "$HOME/.cargo/env" ]]; then
  # shellcheck disable=SC1090
  source "$HOME/.cargo/env"
fi

if ! command -v cargo >/dev/null 2>&1; then
  echo "[error] cargo not found. Install Rust first: curl https://sh.rustup.rs -sSf | sh -s -- -y" >&2
  exit 1
fi

if [[ -f "$APP_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$APP_DIR/.env"
  set +a
fi

echo "[run] bot-hub control-plane on ${BOT_HUB_BIND_ADDR:-127.0.0.1:3900}"
cd "$APP_DIR"
exec cargo run
