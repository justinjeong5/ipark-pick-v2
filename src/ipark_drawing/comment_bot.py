from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from patchright.async_api import Frame, Page
from patchright.async_api import TimeoutError as PlaywrightTimeoutError

from .config import (
    CAFE_IFRAME_SELECTOR,
    COMMENT_BLOCKED_HINT_SELECTOR,
    COMMENT_SUBMIT_BUTTON_SELECTOR,
    COMMENT_TEXTAREA_SELECTOR,
    SCREENSHOTS_DIR,
    Account,
    Target,
    ensure_dirs,
)
from .naver_auth import detect_captcha

SleepFn = Callable[[float], Awaitable[None]]
NowFn = Callable[[], float]

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 0.1


class CommentStatus(str, Enum):
    POSTED = "posted"
    SKIPPED_BLOCKED = "skipped_blocked"  # comment box never activated within timeout
    ALREADY_POSTED = "already_posted"
    CAPTCHA = "captcha"
    LOGIN_EXPIRED = "login_expired"
    FAILED = "failed"


@dataclass
class CommentResult:
    status: CommentStatus
    account_index: int
    reason: str
    elapsed_ms: int
    screenshot_path: str | None = None
    comment_text: str | None = None  # populated on POSTED / ALREADY_POSTED


class CommentBoxProbe(Protocol):
    """Abstraction over the Playwright frame so we can unit-test the polling logic."""

    async def is_blocked(self) -> bool: ...
    async def is_active(self) -> bool: ...


class FrameCommentBoxProbe:
    """Production probe — talks to a real Playwright Frame."""

    def __init__(self, frame: Frame) -> None:
        self._frame = frame

    async def is_blocked(self) -> bool:
        hint = self._frame.locator(COMMENT_BLOCKED_HINT_SELECTOR).first
        return await hint.count() > 0

    async def is_active(self) -> bool:
        textarea = self._frame.locator(COMMENT_TEXTAREA_SELECTOR).first
        if await textarea.count() == 0:
            return False
        if await textarea.is_disabled():
            return False
        return await textarea.is_visible()


async def wait_for_comment_box(
    probe: CommentBoxProbe,
    *,
    timeout_s: float,
    sleep: SleepFn = asyncio.sleep,
    now: NowFn = time.monotonic,
) -> bool:
    """Poll until the comment box becomes active or `timeout_s` elapses.

    Returns True if active before timeout, False if timeout reached.
    Returns False immediately if a blocked-hint element is rendered (drawing closed).
    """
    deadline = now() + timeout_s
    while now() < deadline:
        if await probe.is_blocked():
            return False
        if await probe.is_active():
            return True
        await sleep(POLL_INTERVAL_S)
    return False


async def _enter_content_root(page: Page) -> Frame | Page:
    """Return the renderer scope for selectors.

    New SPA (`/f-e/cafes/.../articles/...`) renders directly on the page; the
    legacy layout wraps everything inside `#cafe_main` iframe. Try the iframe
    first with a short timeout; fall back to the page itself.
    """
    try:
        iframe_handle = await page.wait_for_selector(CAFE_IFRAME_SELECTOR, timeout=2000)
    except PlaywrightTimeoutError:
        return page
    frame = await iframe_handle.content_frame()
    return frame or page


async def _already_posted(scope: Frame | Page, account: Account) -> bool:
    """Does our exact comment text appear anywhere on the page?

    Uses body.inner_text rather than per-item locator, so it doesn't break
    when the cafe rev-bumps the comment list class names.
    """
    try:
        body = await scope.locator("body").inner_text()
    except PlaywrightTimeoutError:
        return False
    return account.comment_text in body


async def _submit_comment(scope: Frame | Page, account: Account) -> None:
    textarea = scope.locator(COMMENT_TEXTAREA_SELECTOR).first
    await textarea.click()
    # Mild human-like delay between characters; total ~50-100ms/char.
    await textarea.type(account.comment_text, delay=70)
    await scope.locator(COMMENT_SUBMIT_BUTTON_SELECTOR).first.click()


async def _verify_posted(
    scope: Frame | Page,
    account: Account,
    *,
    timeout_s: float = 5.0,
    sleep: SleepFn = asyncio.sleep,
    now: NowFn = time.monotonic,
) -> bool:
    """Confirm submission by exact-text match on the page.

    Counter increase alone isn't enough — somebody else's comment can bump
    the counter while ours actually failed. We require our `comment_text` to
    appear on the page.
    """
    deadline = now() + timeout_s
    while now() < deadline:
        if await _already_posted(scope, account):
            return True
        await sleep(0.2)
    return False


async def _save_screenshot(page: Page, account: Account, suffix: str) -> str:
    ensure_dirs()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SCREENSHOTS_DIR / f"account_{account.index}_{stamp}_{suffix}.png"
    await page.screenshot(path=str(path), full_page=True)
    return str(path)


RELOAD_CYCLE_S = 5.0


async def post_comment(
    page: Page,
    account: Account,
    target: Target,
    *,
    poll_timeout_s: float,
) -> CommentResult:
    """Navigate to the article and keep reloading until the comment box opens.

    Naver cafe is a SPA that does NOT push state changes when an article's
    comment toggle flips from blocked → open. We must reload the page to see
    the new state. So the loop is:

      goto → check captcha/already-posted → short poll for activation
      → if not active, reload → repeat → until activated or timeout.
    """
    started = time.monotonic()

    def elapsed_ms() -> int:
        return int((time.monotonic() - started) * 1000)

    await page.goto(target.article_url, wait_until="domcontentloaded")

    if await detect_captcha(page):
        screenshot = await _save_screenshot(page, account, "captcha")
        return CommentResult(
            status=CommentStatus.CAPTCHA,
            account_index=account.index,
            reason="캡챠 페이지 감지 — 자동화 중단",
            elapsed_ms=elapsed_ms(),
            screenshot_path=screenshot,
        )

    deadline = started + poll_timeout_s
    reloads = 0

    while time.monotonic() < deadline:
        scope = await _enter_content_root(page)

        if await _already_posted(scope, account):
            return CommentResult(
                status=CommentStatus.ALREADY_POSTED,
                account_index=account.index,
                reason="동일 댓글이 이미 등록됨",
                elapsed_ms=elapsed_ms(),
                comment_text=account.comment_text,
            )

        cycle_remaining = min(RELOAD_CYCLE_S, deadline - time.monotonic())
        if cycle_remaining <= 0:
            break
        probe = FrameCommentBoxProbe(scope)
        activated = await wait_for_comment_box(probe, timeout_s=cycle_remaining)
        if activated:
            return await _submit_and_verify(page, scope, account, started)

        # Comment box still inactive in this cycle. Hard-reload so a fresh
        # server-rendered DOM can reflect the operator's toggle change.
        if time.monotonic() >= deadline:
            break
        try:
            await page.reload(wait_until="domcontentloaded")
            reloads += 1
        except Exception as exc:  # noqa: BLE001 - reload failures are recoverable
            logger.warning("[account %d] reload 실패: %s", account.index, exc)

    screenshot = await _save_screenshot(page, account, "blocked")
    return CommentResult(
        status=CommentStatus.SKIPPED_BLOCKED,
        account_index=account.index,
        reason=(
            f"댓글창 비활성 ({poll_timeout_s:.0f}초 윈도우, "
            f"{reloads}회 새로고침 후) — 추첨 없음으로 판정"
        ),
        elapsed_ms=elapsed_ms(),
        screenshot_path=screenshot,
    )


async def _submit_and_verify(
    page: Page,
    scope: Frame | Page,
    account: Account,
    started: float,
) -> CommentResult:
    def elapsed_ms() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        await _submit_comment(scope, account)
    except Exception as exc:
        screenshot = await _save_screenshot(page, account, "submit_failed")
        return CommentResult(
            status=CommentStatus.FAILED,
            account_index=account.index,
            reason=f"제출 실패: {exc}",
            elapsed_ms=elapsed_ms(),
            screenshot_path=screenshot,
        )

    if not await _verify_posted(scope, account):
        screenshot = await _save_screenshot(page, account, "verify_failed")
        return CommentResult(
            status=CommentStatus.FAILED,
            account_index=account.index,
            reason="제출 후 댓글이 카운터·목록 어디에도 반영되지 않음",
            elapsed_ms=elapsed_ms(),
            screenshot_path=screenshot,
        )

    screenshot = await _save_screenshot(page, account, "posted")
    return CommentResult(
        status=CommentStatus.POSTED,
        account_index=account.index,
        reason="댓글 작성 완료",
        elapsed_ms=elapsed_ms(),
        screenshot_path=screenshot,
        comment_text=account.comment_text,
    )
