#!/usr/bin/env bash
# Wednesday morning sanity check: read today's '공실 안내' post.
# launchd triggers at 10:00; this script polls every 30 min until 12:00
# (5 attempts: 10:00, 10:30, 11:00, 11:30, 12:00). The first 4 attempts
# stay silent on miss; only the final attempt sends the "직접 확인" message.

set -euo pipefail

cd "$(dirname "$0")/.."
source .venv/bin/activate

# SIGTERM (143) / SIGINT (130) — launchd unload 등 의도된 종료로 간주.
trap 'exit 0' TERM INT
trap 'rc=$?; if [ $rc -ne 0 ] && [ $rc -ne 4 ]; then RC="$rc" python -c "
import asyncio, os
from ipark_drawing.config import load_telegram
from ipark_drawing.telegram import send_message
rc = os.environ[\"RC\"]
asyncio.run(send_message(load_telegram(), f\"🚨 <b>run-notice 비정상 종료</b> (exit={rc})\\n로그: data/notice.err.log\"))
" || true; fi' EXIT

MAX_ATTEMPTS=5
INTERVAL_S=1800   # 30 minutes

for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
  echo "[$(date '+%H:%M:%S')] check-notice 시도 $attempt/$MAX_ATTEMPTS"

  set +e
  if [ "$attempt" -lt "$MAX_ATTEMPTS" ]; then
    ipark-drawing check-notice --account 1 --no-missing-notify
  else
    ipark-drawing check-notice --account 1
  fi
  rc=$?
  set -e

  if [ "$rc" -eq 0 ]; then
    echo "[$(date '+%H:%M:%S')] 공실 안내 발견 + 알림 발송 완료"
    exit 0
  fi
  if [ "$rc" -ne 4 ]; then
    echo "[$(date '+%H:%M:%S')] check-notice 실패 (exit=$rc) — 중단"
    exit "$rc"
  fi
  if [ "$attempt" -eq "$MAX_ATTEMPTS" ]; then
    echo "[$(date '+%H:%M:%S')] $MAX_ATTEMPTS회 시도 후에도 미발견 — '직접 확인' 알림 발송됨"
    exit 0
  fi
  echo "[$(date '+%H:%M:%S')] 미발견 — ${INTERVAL_S}초 후 재시도"
  sleep "$INTERVAL_S"
done
