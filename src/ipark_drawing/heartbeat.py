"""Pre-flight diagnostics: confirm we can run today before 10:00.

Sent every Thursday morning at 09:55 (just before the real run).
"""
from __future__ import annotations

import asyncio
import logging

from .config import load_account, load_telegram
from .telegram import send_message

logger = logging.getLogger(__name__)


def _account_status() -> list[str]:
    parts = []
    for idx in (1, 2, 3):
        try:
            acc = load_account(idx)
        except RuntimeError:
            parts.append(f"account {idx}: ❌ .env 누락")
            continue
        if not acc.cookies_path.exists():
            parts.append(f"account {idx}: ❌ 쿠키 없음 — login 필요")
        else:
            parts.append(f"account {idx}: ✅ 쿠키 존재 ({acc.naver_id})")
    return parts


async def _send_heartbeat() -> None:
    parts = ["🟢 <b>시스템 깨어있음 — 곧 10시 추첨 시도</b>", ""]
    parts.extend(_account_status())
    parts.append("")
    parts.append("⏳ 10:00:00 전후 1차 결과 알림이 갈 예정입니다.")
    await send_message(load_telegram(), "\n".join(parts))


def main() -> int:
    asyncio.run(_send_heartbeat())
    return 0
