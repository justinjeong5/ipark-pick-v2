#!/usr/bin/env bash
# Discover today's "당첨자 발표" article and run the winner check.
# Invoked by com.ipark-drawing.winners.plist on Thursday at 14:30.

set -euo pipefail

cd "$(dirname "$0")/.."
source .venv/bin/activate

trap 'rc=$?; if [ $rc -ne 0 ]; then python -c "
import asyncio
from ipark_drawing.config import load_telegram
from ipark_drawing.telegram import send_message
asyncio.run(send_message(load_telegram(), f\"🚨 <b>run-winners 비정상 종료</b> (exit=$rc)\\n로그: data/winners.err.log\"))
" || true; fi' EXIT

# 0) Decide whether to run at all by reading the morning snapshot.
SHOULD_RUN=$(python -c "
from ipark_drawing.state import read_snapshot, is_today
snap = read_snapshot()
if not is_today(snap):
    # No morning run today, or stale file — proceed defensively.
    print('proceed')
elif snap.winners_notified:
    print('already_notified')
elif not snap.drawing_open:
    print('skip_no_drawing')
else:
    print('proceed')
")

case "$SHOULD_RUN" in
  skip_no_drawing)
    echo "[$(date '+%H:%M:%S')] 오전에 추첨이 열리지 않았음 — winners skip"
    exit 0
    ;;
  already_notified)
    echo "[$(date '+%H:%M:%S')] 오늘 이미 winners 알림 발송 — 중복 방지로 skip"
    exit 0
    ;;
  proceed)
    ;;
esac

MAX_ATTEMPTS=7
INTERVAL_S=300
ARTICLE_ID=""

# 1) Polling loop: 14:30, 14:35, ..., 15:00 (7 attempts).
for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
  echo "[$(date '+%H:%M:%S')] discover 시도 $attempt/$MAX_ATTEMPTS"
  if ARTICLE_ID=$(ipark-drawing discover --account 1 --kind winner 2>/dev/null); then
    echo "[$(date '+%H:%M:%S')] 발견: article_id=$ARTICLE_ID"
    break
  fi
  rc=$?
  if [ "$rc" -ne 4 ]; then
    echo "[$(date '+%H:%M:%S')] discover 실패 (exit=$rc) — 중단"
    exit "$rc"
  fi
  if [ "$attempt" -eq "$MAX_ATTEMPTS" ]; then
    echo "[$(date '+%H:%M:%S')] $MAX_ATTEMPTS회 시도 후에도 발표 글 미발견 — 알림 발송"
    python -c "
import asyncio
from ipark_drawing.config import load_telegram
from ipark_drawing.state import mark_winners_notified
from ipark_drawing.telegram import send_message
asyncio.run(send_message(load_telegram(), '⚠️ <b>당첨자 발표 글이 아직 게시되지 않았습니다</b>\n\n오전에 댓글을 등록한 추첨이지만 14:30~15:00 사이에도 발표 글이 올라오지 않았습니다.\n📌 카페 공지사항을 직접 확인해 주세요.'))
mark_winners_notified()
" || true
    exit 0
  fi
  echo "[$(date '+%H:%M:%S')] 미발견 — ${INTERVAL_S}초 후 재시도"
  sleep "$INTERVAL_S"
done

# 2) Found — assemble URL and run the check.
CLUB_ID=$(grep -E '^TARGET_CLUB_ID=' .env | cut -d= -f2)
RESULT_URL="https://cafe.naver.com/f-e/cafes/${CLUB_ID}/articles/${ARTICLE_ID}?boardtype=L"
echo "[$(date '+%H:%M:%S')] check-winners 실행: $RESULT_URL"

ipark-drawing check-winners \
  --account 1 \
  --result-url "$RESULT_URL"

# 3) Mark notified so any re-trigger today is silent.
python -c "from ipark_drawing.state import mark_winners_notified; mark_winners_notified()" || true
