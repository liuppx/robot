#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOCK_FILE="$ROOT_DIR/scripts/setup/openclaw.versions.lock"

if [[ -f "$LOCK_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$LOCK_FILE"
fi

NODE_VERSION="${NODE_VERSION:-22.22.0}"
NODE_DIST="${NODE_DIST:-node-v${NODE_VERSION}-linux-x64}"
OPENCLAW_VERSION="${OPENCLAW_VERSION:-2026.2.26}"
NODE_URL="https://npmmirror.com/mirrors/node/v${NODE_VERSION}/${NODE_DIST}.tar.xz"

ROUTER_BASE_URL="${ROUTER_BASE_URL:-https://test-router.yeying.pub/v1}"
ROUTER_MODEL="${ROUTER_MODEL:-gpt-5.3-codex}"
OPENCLAW_GATEWAY_TOKEN="${OPENCLAW_GATEWAY_TOKEN:-}"

usage() {
  cat <<USAGE
Usage: $(basename "$0") [install|configure|patch|all]

install    Install Node + OpenClaw (version locked)
configure  Configure OpenClaw profile for Router + channels
patch      Apply WhatsApp 405 compatibility patch
all        Run install + configure + patch (default)
USAGE
}

ensure_node_ready() {
  if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    return 0
  fi
  echo "[error] node/npm not found. run install stage first." >&2
  exit 2
}

link_openclaw_bin() {
  local src="$1"
  if command -v sudo >/dev/null 2>&1; then
    sudo ln -sf "$src" /usr/local/bin/openclaw
  else
    ln -sf "$src" /usr/local/bin/openclaw
  fi
}

ensure_openclaw_ready() {
  ensure_node_ready

  if command -v openclaw >/dev/null 2>&1; then
    return 0
  fi

  local npm_prefix=""
  local npm_candidate=""
  local dist_candidate="/usr/local/${NODE_DIST}/bin/openclaw"

  npm_prefix="$(npm config get prefix 2>/dev/null || true)"
  if [[ -n "$npm_prefix" && "$npm_prefix" != "undefined" ]]; then
    npm_candidate="${npm_prefix%/}/bin/openclaw"
    if [[ -x "$npm_candidate" ]]; then
      echo "[fix] link openclaw from npm prefix: $npm_candidate"
      link_openclaw_bin "$npm_candidate"
    fi
  fi

  if ! command -v openclaw >/dev/null 2>&1 && [[ -x "$dist_candidate" ]]; then
    echo "[fix] link openclaw from node dist: $dist_candidate"
    link_openclaw_bin "$dist_candidate"
  fi

  hash -r || true

  if command -v openclaw >/dev/null 2>&1; then
    return 0
  fi

  echo "[error] openclaw binary not in PATH after install." >&2
  echo "[hint] npm prefix: ${npm_prefix:-<empty>}" >&2
  echo "[hint] try: sudo ln -sf \"$(npm config get prefix)/bin/openclaw\" /usr/local/bin/openclaw" >&2
  return 1
}

install_stage() {
  echo "[step] install Node ${NODE_VERSION} and OpenClaw ${OPENCLAW_VERSION}"

  sudo apt-get update -y
  sudo apt-get install -y curl xz-utils ca-certificates

  local tmp_dir
  tmp_dir="$(mktemp -d)"
  trap 'rm -rf "$tmp_dir"' RETURN

  cd "$tmp_dir"
  curl -fsSLO "$NODE_URL"
  sudo tar -xJf "${NODE_DIST}.tar.xz" -C /usr/local
  sudo ln -sf "/usr/local/${NODE_DIST}/bin/node" /usr/local/bin/node
  sudo ln -sf "/usr/local/${NODE_DIST}/bin/npm" /usr/local/bin/npm
  sudo ln -sf "/usr/local/${NODE_DIST}/bin/npx" /usr/local/bin/npx

  node -v
  npm -v

  npm i -g "openclaw@${OPENCLAW_VERSION}"

  if ! ensure_openclaw_ready; then
    exit 3
  fi

  openclaw --version
  echo "[ok] install done"
}

configure_stage() {
  if ! ensure_openclaw_ready; then
    echo "[error] openclaw not found. run install stage first." >&2
    exit 4
  fi
  : "${ROUTER_API_KEY:?ROUTER_API_KEY is required for configure stage}"

  echo "[step] configure OpenClaw with Router + WhatsApp defaults"
  openclaw config set models.providers.router.baseUrl "$ROUTER_BASE_URL"
  openclaw config set models.providers.router.auth "api-key"
  openclaw config set models.providers.router.apiKey "$ROUTER_API_KEY"
  openclaw config set models.providers.router.api "openai-responses"
  openclaw config set models.providers.router.models "[{\"id\":\"${ROUTER_MODEL}\",\"name\":\"${ROUTER_MODEL}\"}]"
  openclaw config set agents.defaults.model.primary "router/${ROUTER_MODEL}"

  openclaw plugins enable whatsapp || true
  openclaw channels add --channel whatsapp || true

  openclaw config set channels.whatsapp.groupPolicy open
  openclaw config set channels.whatsapp.accounts.default.groupPolicy open
  openclaw config set channels.whatsapp.dmPolicy pairing
  openclaw config set channels.whatsapp.accounts.default.dmPolicy pairing
  openclaw config set messages.groupChat.mentionPatterns '[".*"]'
  openclaw config set messages.groupChat.historyLimit 30

  if [[ -n "$OPENCLAW_GATEWAY_TOKEN" ]]; then
    openclaw config set gateway.auth.mode token
    openclaw config set gateway.auth.token "$OPENCLAW_GATEWAY_TOKEN"
  fi

  echo "[ok] configure done"
}

patch_stage() {
  if ! ensure_openclaw_ready; then
    echo "[error] openclaw not found. run install stage first." >&2
    exit 5
  fi

  local npm_root
  npm_root="$(npm root -g 2>/dev/null || true)"
  local root="${npm_root}/openclaw/dist"
  local plugin_root="${root}/plugin-sdk"

  if [[ ! -d "$root" ]]; then
    root="/usr/local/${NODE_DIST}/lib/node_modules/openclaw/dist"
    plugin_root="${root}/plugin-sdk"
  fi

  local old='browser: ["openclaw", "cli", VERSION]'
  local new='browser: ["Ubuntu", "Chrome", "122.0.0.0"]'

  local targets=(
    "${root}/session-Dugoy7rd.js"
    "${root}/session-C_T4icrY.js"
    "${root}/session-CmithsSM.js"
    "${root}/session-B5tdmbsr.js"
    "${plugin_root}/session-DNHC6iPh.js"
  )

  echo "[step] apply WhatsApp compatibility patch"
  local patched=0
  local f
  for f in "${targets[@]}"; do
    if [[ ! -f "$f" ]]; then
      echo "[warn] target missing, skip: $f"
      continue
    fi

    if grep -Fq "$new" "$f"; then
      echo "[skip] already patched: $f"
      continue
    fi

    if grep -Fq "$old" "$f"; then
      sudo cp "$f" "$f.bak.$(date +%Y%m%d%H%M%S)"
      sudo sed -i "s/${old//\//\/}/${new//\//\/}/g" "$f"
      echo "[ok] patched: $f"
      patched=$((patched + 1))
    else
      echo "[warn] pattern not found: $f"
    fi
  done

  echo "[info] verify patch markers"
  grep -RIn 'browser: \["Ubuntu", "Chrome", "122.0.0.0"\]' "$root"/session-*.js "$plugin_root"/session-*.js 2>/dev/null || true
  echo "[ok] patch stage done, changed files=$patched"
}

action="${1:-all}"
case "$action" in
  install)
    install_stage
    ;;
  configure)
    configure_stage
    ;;
  patch)
    patch_stage
    ;;
  all)
    install_stage
    configure_stage
    patch_stage
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage
    echo "[error] unknown action: $action" >&2
    exit 1
    ;;
esac
