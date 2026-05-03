"""Wednesday-evening sanity check: read the operator's '공실 안내' post and
relay its contents so the user knows whether tomorrow's drawing is ON or OFF.

Body structure (verified against real posts on 2026-04-22 and 2026-04-29):

  Has vacancies (article 706):
    "Q : ...(general FAQ block)..."
    "4월 23일 공실 4세대"
    table: 당첨매칭번호 / 타입 / 층수 / 보증금 / 임대료
      1 74A  1  112,000,000  510,000
      2 74B  10 73,000,000   638,000
      …
    "◆ 추첨 일정"

  No vacancies (article 711):
    "4월 30일 공실 없습니다."
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from patchright.async_api import Page

from .comment_bot import _enter_content_root

logger = logging.getLogger(__name__)


@dataclass
class VacancyUnit:
    match_no: str   # 당첨매칭번호 (1, 2, 3, …)
    type_: str      # "74A"
    floor: str      # "1"
    deposit: str    # "112,000,000"
    rent: str       # "510,000"


@dataclass
class NoticeReadResult:
    article_url: str
    article_title: str
    likely_drawing: bool                   # best-effort prediction
    drawing_date: str | None = None        # "4월 23일"
    vacancy_count: int | None = None       # 4 → 4세대
    units: list[VacancyUnit] = field(default_factory=list)
    raw_excerpt: str = ""                  # short fallback text for unknown layouts


_NO_VACANCY_RE = re.compile(r"공실\s*없", re.UNICODE)
_VACANCY_HEADER_RE = re.compile(
    r"(\d+\s*월\s*\d+\s*일)\s*공실\s*(\d+)\s*세대",
    re.UNICODE,
)
# A row of 5 whitespace-separated tokens: matchNo / type / floor / deposit / rent.
# Inner_text inserts blank lines between cells so \s+ covers everything.
_VACANCY_ROW_RE = re.compile(
    r"(\d+)\s+([0-9]{2,3}[A-Z])\s+(\d+)\s+([\d,]+)\s+([\d,]+)",
    re.UNICODE,
)


def _parse_vacancies(body: str) -> tuple[str | None, int | None, list[VacancyUnit]]:
    header = _VACANCY_HEADER_RE.search(body)
    if not header:
        return None, None, []
    drawing_date = re.sub(r"\s+", " ", header.group(1))
    count = int(header.group(2))
    # Look for rows AFTER the header to avoid matching unrelated tables.
    tail = body[header.end():]
    units = [
        VacancyUnit(*m.groups())
        for m in _VACANCY_ROW_RE.finditer(tail)
    ][:count]  # safety cap
    return drawing_date, count, units


async def fetch_notice(page: Page, article_url: str) -> NoticeReadResult:
    await page.goto(article_url, wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:  # noqa: BLE001
        pass

    scope = await _enter_content_root(page)
    body = await scope.locator("body").inner_text()
    title = ""
    title_loc = scope.locator(".title_text, h3.title_text").first
    if await title_loc.count() > 0:
        title = (await title_loc.inner_text()).strip()

    no_vacancy = bool(_NO_VACANCY_RE.search(body))
    drawing_date, vacancy_count, units = _parse_vacancies(body)
    likely = (not no_vacancy) and bool(units)

    excerpt = re.sub(r"\n{2,}", "\n", body).strip()[:600]

    return NoticeReadResult(
        article_url=article_url,
        article_title=title,
        likely_drawing=likely,
        drawing_date=drawing_date,
        vacancy_count=vacancy_count,
        units=units,
        raw_excerpt=excerpt,
    )


def _format_unit(u: VacancyUnit) -> str:
    return (
        f"  • 매칭 #{u.match_no}: {u.type_}형 / {u.floor}층 / "
        f"보증금 {u.deposit}원 / 월세 {u.rent}원"
    )


def format_notice_message(result: NoticeReadResult) -> str:
    if not result.likely_drawing:
        return (
            "🟡 <b>내일 공실 추첨 없음</b>\n\n"
            f"📋 <b>{result.article_title or '공실 안내'}</b>\n"
            f'<a href="{result.article_url}">→ 공지 게시글 보기</a>'
        )

    lines = ["🟢 <b>내일 공실 추첨 있습니다!</b>", ""]
    if result.drawing_date and result.vacancy_count is not None:
        lines.append(
            f"📅 <b>{result.drawing_date}</b> "
            f"공실 <b>{result.vacancy_count}세대</b>"
        )
        lines.append("")
    if result.units:
        lines.extend(_format_unit(u) for u in result.units)
    else:
        # Parse failed — fall back to excerpt so the user still gets info.
        lines.append("(상세 표를 추출하지 못했습니다 — 본문 일부)")
        lines.append(f"<pre>{result.raw_excerpt}</pre>")
    lines.extend([
        "",
        "🕙 내일 10:00:00 ~ 10:00:59 동안 봇이 자동으로 댓글을 등록합니다.",
        "",
        f'📋 <a href="{result.article_url}">공지 게시글 보기</a>',
    ])
    return "\n".join(lines)


def format_notice_missing_message() -> str:
    return (
        "📭 <b>오늘 '공실 안내' 글이 게시되지 않았습니다</b>\n\n"
        "수요일 안내 글이 아직 안 올라왔거나 형식이 변경됐을 수 있습니다.\n"
        "📌 카페 공지사항을 직접 확인해 주세요."
    )
