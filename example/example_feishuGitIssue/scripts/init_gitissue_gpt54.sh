#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTANCE_DIR="$ROOT_DIR/.openclaw-feishu-gitissue-gpt54"
WORKSPACE_DIR="$INSTANCE_DIR/workspace-larkbot"
STATE_DIR="$INSTANCE_DIR/state"
TARGET_CONFIG="$INSTANCE_DIR/openclaw.json"
TEMPLATE_CONFIG="$ROOT_DIR/openclaw.example.json"
FORCE="${FORCE:-0}"

mkdir -p "$INSTANCE_DIR" "$STATE_DIR"

bash "$ROOT_DIR/scripts/sync_to_feishu_workspace.sh" "$WORKSPACE_DIR"

if [[ ! -f "$TEMPLATE_CONFIG" ]]; then
  echo "missing template config: $TEMPLATE_CONFIG"
  exit 1
fi

if [[ -f "$TARGET_CONFIG" && "$FORCE" != "1" ]]; then
  echo "keep existing config: $TARGET_CONFIG"
else
  cp "$TEMPLATE_CONFIG" "$TARGET_CONFIG"
  echo "wrote config template: $TARGET_CONFIG"
fi

touch "$INSTANCE_DIR/gateway.out"

echo "init complete"
echo "next:"
echo "  1. edit /root/.config/openclaw/github-app/config.env"
echo "  2. edit $TARGET_CONFIG"
echo "  3. edit $WORKSPACE_DIR/config/policy.json"
echo "  4. start with: bash $ROOT_DIR/start_gitissue_gpt54.sh"
