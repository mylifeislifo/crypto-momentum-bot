#!/usr/bin/env bash
# Deploy btcbot as a launchd user agent on macOS (Mac mini M4).
# Run once after cloning:  bash deploy/install.sh [paper|live]
#
# Prerequisites:
#   pip install -e .       (installs `btcbot` CLI)
#   cp .env.example .env   (fill in API keys)
#   mkdir -p logs

set -euo pipefail

MODE="${1:-paper}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_SRC="$SCRIPT_DIR/com.btcbot.$MODE.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.btcbot.$MODE.plist"
LOG_DIR="$(dirname "$SCRIPT_DIR")/logs"

if [[ ! -f "$PLIST_SRC" ]]; then
  echo "ERROR: plist not found: $PLIST_SRC"
  exit 1
fi

# --- substitute YOURUSER placeholder ---
sed "s|YOURUSER|$(whoami)|g" "$PLIST_SRC" > "$PLIST_DST"
echo "Installed: $PLIST_DST"

mkdir -p "$LOG_DIR"
echo "Log directory: $LOG_DIR"

# --- unload previous instance if running ---
launchctl unload "$PLIST_DST" 2>/dev/null || true

# --- load new plist ---
launchctl load "$PLIST_DST"
echo "Loaded launchd agent: com.btcbot.$MODE"
echo ""
echo "Commands:"
echo "  View logs  : tail -f $LOG_DIR/btcbot.log"
echo "  Stop bot   : launchctl unload $PLIST_DST"
echo "  Start bot  : launchctl load $PLIST_DST"
echo "  Status     : launchctl list | grep btcbot"
