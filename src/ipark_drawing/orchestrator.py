"""Run the comment-posting flow for multiple accounts in parallel.

Each account gets its own BrowserContext (its own cookie file). asyncio.gather
runs them concurrently so all three submit within the same ~5s after the
comment box opens — important because the live drawing window is only 1 minute.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from datetime import datetime
from zoneinfo import ZoneInfo

from .browser import browser_context
from .comment_bot import CommentResult, CommentStatus, post_comment
from .config import Account, RuntimeConfig, Target, TelegramConfig
from .locking import AlreadyRunningError, single_instance
from .naver_auth import LoginExpiredError, assert_logged_in
from .state import is_today, mark_morning_notified, read_snapshot, write_snapshot
from .telegram import format_run_summary, send_message

KST = ZoneInfo("Asia/Seoul")

logger = logging.getLogger(__name__)


async def _run_one(
    account: Account,
    target: Target,
    runtime: RuntimeConfig,
) -> CommentResult:
    if not account.cookies_path.exists():
        return CommentResult(
            status=CommentStatus.FAILED,
            account_index=account.index,
            reason=(
                f"쿠키 파일 없음: {account.cookies_path} — "
                f"`ipark-drawing login --account {account.index}` 먼저"
            ),
            elapsed_ms=0,
        )

    # Hard wall-clock cap so a hung browser/network can't run past the
    # 1-minute drawing window. poll_timeout + login + reload margin = +60s.
    hard_timeout_s = runtime.comment_poll_timeout + 60

    async def _do() -> CommentResult:
        async with browser_context(
            headful=runtime.headful,
            storage_state=str(account.cookies_path),
        ) as (_browser, _context, page):
            try:
                await assert_logged_in(page, account)
            except LoginExpiredError as exc:
                return CommentResult(
                    status=CommentStatus.LOGIN_EXPIRED,
                    account_index=account.index,
                    reason=str(exc),
                    elapsed_ms=0,
                )
            return await post_comment(
                page,
                account,
                target,
                poll_timeout_s=runtime.comment_poll_timeout,
            )

    try:
        return await asyncio.wait_for(_do(), timeout=hard_timeout_s)
    except asyncio.TimeoutError:
        return CommentResult(
            status=CommentStatus.FAILED,
            account_index=account.index,
            reason=f"전체 흐름이 {hard_timeout_s:.0f}초를 초과 — 강제 종료",
            elapsed_ms=int(hard_timeout_s * 1000),
        )


async def run_morning(
    accounts: Iterable[Account],
    target: Target,
    runtime: RuntimeConfig,
    telegram: TelegramConfig,
    *,
    notify: bool = True,
    article_id: str | None = None,
) -> list[CommentResult]:
    """Run all accounts concurrently, then send a single summary to Telegram."""
    accounts = list(accounts)

    # Same-day re-trigger dedup: if morning has already been notified today,
    # skip the whole flow (silent) so launchd retry doesn't double-post.
    snap = read_snapshot()
    if is_today(snap) and snap is not None and snap.morning_notified:
        logger.info("morning_notified=True — 같은 날 중복 실행으로 판단, skip")
        return []

    try:
        with single_instance("morning"):
            return await _run_morning_locked(
                accounts, target, runtime, telegram,
                notify=notify, article_id=article_id,
            )
    except AlreadyRunningError as exc:
        logger.warning("동시 실행 차단: %s", exc)
        return []


async def _run_morning_locked(
    accounts: list[Account],
    target: Target,
    runtime: RuntimeConfig,
    telegram: TelegramConfig,
    *,
    notify: bool,
    article_id: str | None,
) -> list[CommentResult]:
    logger.info("morning run 시작 — accounts=%s", [a.index for a in accounts])

    results = await asyncio.gather(
        *(_run_one(a, target, runtime) for a in accounts),
        return_exceptions=True,
    )

    # Surface unexpected exceptions as FAILED results so the summary still ships.
    final: list[CommentResult] = []
    for acc, r in zip(accounts, results):
        if isinstance(r, BaseException):
            logger.exception("[account %d] 예외 발생", acc.index, exc_info=r)
            final.append(
                CommentResult(
                    status=CommentStatus.FAILED,
                    account_index=acc.index,
                    reason=f"예외: {type(r).__name__}: {r}",
                    elapsed_ms=0,
                )
            )
        else:
            final.append(r)

    write_snapshot(article_id=article_id, results=final)

    if notify:
        header = f"🌅 {datetime.now(KST):%Y-%m-%d %H:%M} 추첨 댓글 결과 (KST)"
        try:
            await send_message(telegram, format_run_summary(final, header))
            mark_morning_notified()
        except Exception as exc:  # noqa: BLE001 - don't crash the run on a delivery failure
            logger.error(
                "🚨 Telegram 알림 발송 실패: %s — 결과는 data/state/last-run.json에 저장됨",
                exc,
            )

    return final
