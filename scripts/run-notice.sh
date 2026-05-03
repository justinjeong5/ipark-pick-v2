#!/usr/bin/env bash
# Wednesday-evening sanity check: read tomorrow's drawing notice.
# Invoked by com.ipark-drawing.notice.plist on Wed at 17:00.

set -euo pipefail

cd "$(dirname "$0")/.."
source .venv/bin/activate

trap 'rc=$?; if [ $rc -ne 0 ] && [ $rc -ne 4 ]; then python -c "
import asyncio
from ipark_drawing.config import load_telegram
from ipark_drawing.telegram import send_message
asyncio.run(send_message(load_telegram(), f\"🚨 <b>run-notice 비정상 종료</b> (exit=$rc)\\n로그: data/notice.err.log\"))
" || true; fi' EXIT

ipark-drawing check-notice --account 1
