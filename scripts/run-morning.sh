#!/usr/bin/env bash
# Discover today's "공실 댓글" article and run the morning posting flow.
# Invoked by com.ipark-drawing.morning.plist on Thursday at 09:57.

set -euo pipefail

cd "$(dirname "$0")/.."
source .venv/bin/activate

# Notify on unexpected non-zero exit (anything we didn't `exit 0` from).
trap 'rc=$?; if [ $rc -ne 0 ]; then python -c "
import asyncio
from ipark_drawing.config import load_telegram
from ipark_drawing.telegram import send_message
asyncio.run(send_message(load_telegram(), f\"🚨 <b>run-morning 비정상 종료</b> (exit=$rc)\\n로그: data/morning.err.log\"))
" || true; fi' EXIT

# 1) Discover today's article. Exit code 4 = no match (no drawing today).
if ! ARTICLE_ID=$(ipark-drawing discover --account 1 --kind comment 2>/dev/null); then
  rc=$?
  if [ "$rc" -eq 4 ]; then
    echo "[$(date '+%H:%M:%S')] 오늘은 공실 추첨 글이 없음 — 정상 종료"
    # Persist a snapshot so run-winners can decide to skip silently.
    python -c "
import json
from datetime import datetime
from pathlib import Path
state = Path('data/state'); state.mkdir(parents=True, exist_ok=True)
(state / 'last-run.json').write_text(json.dumps({
    'date': datetime.now().strftime('%Y-%m-%d'),
    'drawing_open': False,
    'article_id': None,
    'posted_accounts': [],
    'statuses': {},
    'winners_notified': False,
}, ensure_ascii=False, indent=2))
"
    # Reassuring 'system OK, no vacancy' notification.
    python -c "
import asyncio
from ipark_drawing.config import load_telegram
from ipark_drawing.telegram import send_message
asyncio.run(send_message(load_telegram(), '🟢 <b>오늘은 공실이 없습니다</b>\n\n오전 10시 추첨 글이 게시되지 않아 시도하지 않았습니다. 시스템은 정상 동작 중입니다.'))
" || true
    exit 0
  fi
  exit "$rc"
fi

echo "[$(date '+%H:%M:%S')] discovered article_id=$ARTICLE_ID"

# 2) Run the posting flow.
ipark-drawing run-morning \
  --accounts 1,2,3 \
  --article-id "$ARTICLE_ID" \
  --at 10:00:00
