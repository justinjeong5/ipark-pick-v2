"""Discover today's article in a cafe board by title pattern.

Operators publish two kinds of posts:
- Comment-target post: "<M>월 <D>일 공실 댓글" (rare variations: "공실")
- Winner announcement:   "<M>월 <D>일 당첨자 발표"

Both arrive on the same board, posted on the day they apply (one for the
morning drawing, one ~14:30 for the result). We resolve the article id by
listing the board and matching today's date + the kind keyword.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from enum import Enum

from patchright.async_api import Page

from .comment_bot import _enter_content_root

logger = logging.getLogger(__name__)


class ArticleKind(str, Enum):
    COMMENT = "comment"   # "M월 D일 공실 (댓글)" — Thursday 10am draw
    WINNER = "winner"     # "M월 D일 당첨자 발표" — Thursday 14:30
    NOTICE = "notice"     # "공실 안내" — Wednesday preview


_KIND_TOKENS: dict[ArticleKind, list[str]] = {
    # Operator title variations seen in the wild — checked in order.
    ArticleKind.COMMENT: ["공실 댓글", "공실"],
    ArticleKind.WINNER: ["당첨자 발표", "당첨자", "추첨 결과"],
    ArticleKind.NOTICE: ["공실 안내", "공실안내"],
}


def _today_patterns(kind: ArticleKind, today: dt.date) -> list[re.Pattern[str]]:
    """Multiple title patterns we'll try in fallback order."""
    date_re = rf"0?{today.month}\s*월\s*0?{today.day}\s*일"
    out: list[re.Pattern[str]] = []
    for token in _KIND_TOKENS[kind]:
        token_re = re.escape(token).replace(r"\ ", r"\s*")
        out.append(re.compile(rf"{date_re}.*{token_re}", re.UNICODE))
    return out


_ARTICLE_ID_PATTERNS = [
    re.compile(r"articleid=(\d+)"),
    re.compile(r"/articles/(\d+)"),
]


def _extract_article_id(href: str | None) -> str | None:
    if not href:
        return None
    for pat in _ARTICLE_ID_PATTERNS:
        m = pat.search(href)
        if m:
            return m.group(1)
    return None


def _keyword_only_patterns(kind: ArticleKind) -> list[re.Pattern[str]]:
    """Patterns that ignore date — used for the weekly NOTICE post."""
    return [
        re.compile(re.escape(t).replace(r"\ ", r"\s*"), re.UNICODE)
        for t in _KIND_TOKENS[kind]
    ]


async def find_today_article(
    page: Page,
    club_id: str,
    menu_id: str,
    kind: ArticleKind,
    *,
    today: dt.date | None = None,
) -> str | None:
    """Return the article id for today's post of the given kind, or None.

    Strategy: load the board list, optionally scroll to load more rows so a
    pinned ad/notice doesn't push today's post off the first page, then try
    each title pattern in fallback order.
    """
    today = today or dt.date.today()
    # Notice posts don't include a date in the title — match by keyword only
    # and rely on "first hit on the freshest list" being today's post.
    if kind == ArticleKind.NOTICE:
        patterns = _keyword_only_patterns(kind)
    else:
        patterns = _today_patterns(kind, today)
    list_url = f"https://cafe.naver.com/f-e/cafes/{club_id}/menus/{menu_id}"
    await page.goto(list_url, wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:  # noqa: BLE001 - networkidle is best-effort
        pass

    scope = await _enter_content_root(page)
    # Trigger lazy-loaded rows by scrolling the inner doc a couple of times.
    try:
        for _ in range(2):
            await scope.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(500)
    except Exception:  # noqa: BLE001
        pass

    # Match anchor links that point at an article — both legacy ArticleRead.nhn
    # query-string form and the new SPA `/articles/<id>` form qualify.
    links = scope.locator("a[href*='articleid='], a[href*='/articles/']")
    n = await links.count()
    logger.info(
        "[board_finder] %d candidate links on menu %s for kind=%s today=%s",
        n,
        menu_id,
        kind.value,
        today.isoformat(),
    )

    # Collect (text, href) up front so each pattern can iterate without
    # re-querying the DOM and without re-walking detached nodes.
    candidates: list[tuple[str, str | None]] = []
    for i in range(n):
        link = links.nth(i)
        try:
            text = (await link.inner_text()).strip()
        except Exception:  # noqa: BLE001
            continue
        href = await link.get_attribute("href")
        candidates.append((text, href))

    for pattern in patterns:
        for text, href in candidates:
            if pattern.search(text):
                article_id = _extract_article_id(href)
                if article_id:
                    logger.info(
                        "[board_finder] match (pattern=%s): %r → article_id=%s",
                        pattern.pattern,
                        text,
                        article_id,
                    )
                    return article_id

    logger.info(
        "[board_finder] no match for kind=%s today=%s",
        kind.value,
        today.isoformat(),
    )
    return None
