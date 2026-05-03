"""Telegram notifier — single bot, single chat, plain HTTP via Bot API.

Reuses an existing bot token; the bot only needs to be a member of the chat.
For private supergroups, the chat_id must be the `-100<id>` form.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable

import httpx

from .comment_bot import CommentResult, CommentStatus
from .config import TelegramConfig

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
HTTP_TIMEOUT_S = 10.0


class TelegramError(RuntimeError):
    pass


def _normalize_chat_id(raw: str) -> str:
    """Accept `-100123...`, `123...`, or `t.me/c/123...?...` — return API form."""
    raw = raw.strip()
    if raw.startswith("https://t.me/c/") or raw.startswith("t.me/c/"):
        # https://t.me/c/<id>?... → -100<id>
        tail = raw.split("t.me/c/", 1)[1]
        digits = tail.split("?", 1)[0].split("/", 1)[0]
        return f"-100{digits}"
    if raw.lstrip("-").isdigit():
        return raw
    return raw  # username like @channel — left as-is


async def send_message(cfg: TelegramConfig, text: str) -> None:
    if not cfg.enabled:
        logger.info("[telegram] disabled (token/chat_id 미설정) — 알림 스킵")
        return
    chat_id = _normalize_chat_id(cfg.chat_id)
    url = f"{API_BASE}/bot{cfg.bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
        resp = await client.post(url, json=payload)
    if resp.status_code != 200:
        raise TelegramError(f"Telegram API {resp.status_code}: {resp.text}")
    body = resp.json()
    if not body.get("ok"):
        raise TelegramError(f"Telegram API ok=false: {body}")


def _emoji(status: CommentStatus) -> str:
    return {
        CommentStatus.POSTED: "✅",
        CommentStatus.ALREADY_POSTED: "↩️",
        CommentStatus.SKIPPED_BLOCKED: "⏸️",
        CommentStatus.CAPTCHA: "🛑",
        CommentStatus.LOGIN_EXPIRED: "🔑",
        CommentStatus.FAILED: "❌",
    }.get(status, "❓")


def _format_one(r: CommentResult) -> str:
    elapsed_s = r.elapsed_ms / 1000
    if r.status == CommentStatus.POSTED:
        return (
            f"✅ <b>account {r.account_index}</b>: 댓글 작성 완료 ({elapsed_s:.1f}초)\n"
            f"     → <code>{r.comment_text or ''}</code>"
        )
    if r.status == CommentStatus.ALREADY_POSTED:
        return (
            f"↩️ <b>account {r.account_index}</b>: 이미 등록되어 있음 "
            f"({elapsed_s:.1f}초)\n     → <code>{r.comment_text or ''}</code>"
        )
    if r.status == CommentStatus.SKIPPED_BLOCKED:
        return f"⏸️ <b>account {r.account_index}</b>: 댓글창 비활성 — {r.reason}"
    if r.status == CommentStatus.CAPTCHA:
        return f"🛑 <b>account {r.account_index}</b>: 캡챠 감지 — 자동화 중단"
    if r.status == CommentStatus.LOGIN_EXPIRED:
        return (
            f"🔑 <b>account {r.account_index}</b>: 세션 만료 — "
            f"<code>ipark-drawing login --account {r.account_index}</code> 필요"
        )
    return f"❌ <b>account {r.account_index}</b>: {r.reason}"


def format_run_summary(results: Iterable[CommentResult], header: str) -> str:
    """Render the daily morning summary as a Telegram-friendly HTML string."""
    results = list(results)
    n = len(results)
    posted = [r for r in results if r.status == CommentStatus.POSTED]
    already = [r for r in results if r.status == CommentStatus.ALREADY_POSTED]
    blocked = [r for r in results if r.status == CommentStatus.SKIPPED_BLOCKED]
    expired = [r for r in results if r.status == CommentStatus.LOGIN_EXPIRED]
    captcha = [r for r in results if r.status == CommentStatus.CAPTCHA]
    failed = [
        r for r in results
        if r.status in (CommentStatus.FAILED, CommentStatus.CAPTCHA, CommentStatus.LOGIN_EXPIRED)
    ]
    success_n = len(posted) + len(already)

    # All-blocked: collapse into a single summary line (계정별 같은 문구 반복 X).
    if n > 0 and len(blocked) == n:
        return f"<b>{header}</b>\n\n📭 <b>오늘은 추첨이 열리지 않았습니다.</b>"

    lines = [f"<b>{header}</b>", ""]
    lines.extend(_format_one(r) for r in results)
    lines.append("")

    # Login-expired and captcha need operator action — surface them above other failures.
    if expired:
        ids = ",".join(str(r.account_index) for r in expired)
        lines.append(
            f"🔑 <b>로그인 갱신 필요</b> (account {ids}) — "
            "쿠키 만료 시 추첨 참여 불가, 즉시 재로그인하세요"
        )
    elif captcha:
        ids = ",".join(str(r.account_index) for r in captcha)
        lines.append(f"🛑 <b>캡챠 감지</b> (account {ids}) — 봇 탐지 의심, 잠시 후 재시도")
    elif failed and success_n == 0:
        lines.append("🚨 <b>전부 실패</b> — 즉시 점검 필요")
    elif failed:
        lines.append(
            f"⚠️ <b>{success_n}/{n} 성공</b> ({len(failed)}건 실패) — 실패 계정 확인 필요"
        )
    elif success_n == n and n > 0:
        lines.append(
            f"🎯 <b>{success_n}/{n} 모두 등록 완료!</b>  14:30 발표를 기다려요 🍀"
        )
    return "\n".join(lines)
