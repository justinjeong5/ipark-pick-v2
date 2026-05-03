from __future__ import annotations

import logging
import os
from pathlib import Path

from patchright.async_api import Page

from .browser import browser_context
from .config import CAPTCHA_SELECTOR, Account, ensure_dirs

logger = logging.getLogger(__name__)

NAVER_LOGIN_URL = "https://nid.naver.com/nidlogin.login"
NAVER_HOME_URL = "https://www.naver.com"


class LoginExpiredError(RuntimeError):
    """Raised when stored cookies no longer carry an authenticated session."""


class CaptchaDetectedError(RuntimeError):
    """Raised when a captcha is rendered — automation cannot proceed safely."""


async def detect_captcha(page: Page) -> bool:
    return await page.locator(CAPTCHA_SELECTOR).count() > 0


NAVER_AUTH_COOKIE_NAMES = {"NID_AUT", "NID_SES"}


async def is_logged_in(page: Page) -> bool:
    """Decide auth purely from cookies — no DOM probing, no navigation.

    Naver issues NID_AUT (auth token) and NID_SES (session) on successful login.
    Both are httpOnly cookies on `.naver.com`, so they're present whether the
    browser is on naver.com, cafe.naver.com, or anywhere else in the domain.
    """
    cookies = await page.context.cookies("https://www.naver.com")
    names = {c["name"] for c in cookies}
    return NAVER_AUTH_COOKIE_NAMES.issubset(names)


async def manual_login_and_save(account: Account) -> Path:
    """Open a headful browser, let the user log in by hand, then persist cookies."""
    ensure_dirs()
    async with browser_context(headful=True) as (_browser, context, page):
        await page.goto(NAVER_LOGIN_URL, wait_until="domcontentloaded")
        logger.info(
            "[account %d] 브라우저에서 직접 로그인하세요. 완료 후 터미널에서 Enter.",
            account.index,
        )
        # Block on the user — keep this a synchronous prompt so the browser stays open.
        input("로그인 완료 후 Enter를 누르세요... ")

        if await detect_captcha(page):
            raise CaptchaDetectedError("캡챠 페이지가 감지되었습니다. 잠시 후 다시 시도해주세요.")

        # User just confirmed they logged in. Auto-detection is best-effort; if
        # it fails, we still save cookies but warn so the user can re-check.
        if not await is_logged_in(page):
            logger.warning(
                "[account %d] 로그인 상태 자동 감지 실패 (현재 URL: %s). "
                "쿠키를 저장하지만, comment 단계에서 만료 에러가 나면 다시 로그인하세요.",
                account.index,
                page.url,
            )

        await context.storage_state(path=str(account.cookies_path))
        # Cookies hold an authenticated session — restrict to user-only.
        try:
            os.chmod(account.cookies_path, 0o600)
        except OSError:
            pass
        logger.info("[account %d] 쿠키 저장: %s", account.index, account.cookies_path)
        return account.cookies_path


async def assert_logged_in(page: Page, account: Account) -> None:
    if not await is_logged_in(page):
        raise LoginExpiredError(
            f"account {account.index} 세션 만료. `ipark-drawing login --account "
            f"{account.index}`로 재로그인하세요."
        )
