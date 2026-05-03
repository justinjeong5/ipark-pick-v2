from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COOKIES_DIR = PROJECT_ROOT / "data" / "cookies"
SCREENSHOTS_DIR = PROJECT_ROOT / "data" / "screenshots"
STATE_DIR = PROJECT_ROOT / "data" / "state"

load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Account:
    index: int
    naver_id: str
    comment_text: str

    @property
    def cookies_path(self) -> Path:
        return COOKIES_DIR / f"account_{self.index}.json"


@dataclass(frozen=True)
class Target:
    cafe_url: str
    club_id: str
    article_id: str
    menu_id: str
    list_menu_id: str  # board to scan for date-pattern auto-discovery (0 = all)

    @property
    def article_url(self) -> str:
        # /f-e/ is the outer SPA shell; the real article DOM lives inside the
        # `iframe#cafe_main` it injects. We enter via /f-e/ and let
        # comment_bot's iframe-fallback step pick up the inner document.
        return (
            f"https://cafe.naver.com/f-e/cafes/{self.club_id}"
            f"/articles/{self.article_id}"
            f"?boardtype=W&menuid={self.menu_id}&referrerAllArticles=false"
        )


@dataclass(frozen=True)
class RuntimeConfig:
    headful: bool
    comment_poll_timeout: float


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: str
    enabled: bool


# DOM selectors — verified against the live new-SPA cafe page on 2026-05-03.
# The /f-e/ SPA injects an `iframe#cafe_main` whose document holds the real
# article + comment widgets. Selectors below match that inner document.
CAFE_IFRAME_SELECTOR = "iframe#cafe_main"
COMMENT_TEXTAREA_SELECTOR = "textarea.comment_inbox_text"
# Submit is rendered as <a class="btn_register"> with role="button" — not a <button>.
COMMENT_SUBMIT_BUTTON_SELECTOR = "a.btn_register"
COMMENT_BLOCKED_HINT_SELECTOR = (
    ".comment_inbox_block, "
    "[class*='comment_block'], "
    "[class*='CommentBlocked'], "
    "[class*='disabled_comment']"
)
# #gnb_logout_button is rendered only when authenticated; it's the cleanest
# signal we have. Fall back to #gnb_my for older GNB layouts.
LOGIN_INDICATOR_SELECTOR = "#gnb_logout_button, #gnb_my, a.gnb_my"
CAPTCHA_SELECTOR = "#captcha, .captcha_wrap, img[src*='captcha']"


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Environment variable {name} is required (set it in .env)")
    return value


def _optional(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def load_account(index: int) -> Account:
    if index not in (1, 2, 3):
        raise ValueError(f"account index must be 1, 2, or 3 (got {index})")
    return Account(
        index=index,
        naver_id=_require(f"NAVER_ACCOUNT_{index}_ID"),
        comment_text=_require(f"NAVER_ACCOUNT_{index}_COMMENT"),
    )


def load_target() -> Target:
    return Target(
        cafe_url=_optional("TARGET_CAFE_URL", "https://cafe.naver.com/redn47ad"),
        club_id=_optional("TARGET_CLUB_ID", "30796368"),
        article_id=_optional("TARGET_ARTICLE_ID", "21"),
        menu_id=_optional("TARGET_MENU_ID", "2"),
        # 0 = all-articles board; safe default for auto-discovery on big cafes.
        list_menu_id=_optional("TARGET_LIST_MENU_ID", "0"),
    )


def load_runtime() -> RuntimeConfig:
    return RuntimeConfig(
        headful=_optional("HEADFUL", "false").lower() == "true",
        comment_poll_timeout=float(_optional("COMMENT_POLL_TIMEOUT", "60")),
    )


def load_telegram() -> TelegramConfig:
    token = _optional("TELEGRAM_BOT_TOKEN")
    chat_id = _optional("TELEGRAM_CHAT_ID")
    return TelegramConfig(
        bot_token=token,
        chat_id=chat_id,
        enabled=bool(token and chat_id),
    )


def ensure_dirs() -> None:
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
