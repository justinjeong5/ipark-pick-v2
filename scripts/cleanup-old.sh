#!/usr/bin/env bash
# Trim audit logs and screenshots older than 30 days.
# Run manually or wire into launchd as a low-priority weekly job.

set -euo pipefail
cd "$(dirname "$0")/.."

# History snapshots
find data/state/history -type f -name '*.json' -mtime +30 -print -delete 2>/dev/null || true
# Screenshots
find data/screenshots -type f \( -name '*.png' -o -name '*.jpg' \) -mtime +30 -print -delete 2>/dev/null || true
# Stale logs
find data -maxdepth 1 -type f \( -name '*.out.log' -o -name '*.err.log' \) -size +10M -print -delete 2>/dev/null || true

echo "cleanup done"
