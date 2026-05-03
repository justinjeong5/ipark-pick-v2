#!/usr/bin/env bash
# Install launchd jobs for daily comment + winner check.
# Run this once after `pip install -e .` and `playwright install chromium`.

set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$LAUNCH_AGENTS"

for name in heartbeat notice morning winners; do
  src="$PROJECT_ROOT/scripts/com.ipark-drawing.$name.plist"
  dst="$LAUNCH_AGENTS/com.ipark-drawing.$name.plist"
  cp "$src" "$dst"
  # Reload (unload first if already loaded; ignore errors).
  launchctl unload "$dst" 2>/dev/null || true
  launchctl load "$dst"
  echo "loaded: $dst"
done

echo
echo "Verify:  launchctl list | grep ipark-drawing"
echo "Logs:    tail -f $PROJECT_ROOT/data/{morning,winners}.{out,err}.log"
