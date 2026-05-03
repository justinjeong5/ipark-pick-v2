"""Read the day's winner-announcement article and see if any of our comments hit.

Run after the cafe operator posts the announcement (~14:30 on drawing days).
The operator masks names in the result list: "정창우" appears as "정*우" while
the 4-digit birthday is left intact ("1125"). We match BOTH the unmasked form
(in case a particular cafe doesn't mask) and the masked form.

The page typically has TWO ranking tables:

    당첨자                     ← main winners (with type/floor/deposit/rent)
    타입 층수 보증금 임대료
    전*미0913  74A  1   112,000,000  510,000
    …

    예비 순위 / 당첨자          ← reserve ranks (rank + masked name only)
    1  김*희0524
    2  박*진0123
    …
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Literal

from patchright.async_api import Page

from .comment_bot import _enter_content_root

logger = logging.getLogger(__name__)

_NAME_BIRTH_RE = re.compile(r"^([가-힣]+)(\d{4})$")
# Masking glyphs we've seen in the wild: *, O/0-circle look-alikes.
_MASK_CHARS = "*○●○Oo·\\-_"
# A masked or unmasked Korean-name + 4-digit-birthday token.
_NAME_TOKEN_RE = rf"[가-힣{re.escape(_MASK_CHARS)}]+\d{{4}}"


_MAIN_ROW_RE = re.compile(
    rf"({_NAME_TOKEN_RE})\s+([A-Za-z0-9]+)\s+(\d+)\s+([\d,]+)\s+([\d,]+)"
)
_RESERVE_ROW_RE = re.compile(
    rf"^\s*(\d+)\s+({_NAME_TOKEN_RE})\s*$",
    re.MULTILINE,
)


def parse_winner_tables(body: str) -> list[WinnerEntry]:
    """Extract main + reserve entries from a winner-announcement page text."""
    entries: list[WinnerEntry] = []

    # Split body around the "예비 순위" header so a reserve row never gets
    # mistaken for a main one (and vice versa).
    reserve_idx = body.find("예비 순위")
    main_section = body[:reserve_idx] if reserve_idx >= 0 else body
    reserve_section = body[reserve_idx:] if reserve_idx >= 0 else ""

    for i, m in enumerate(_MAIN_ROW_RE.finditer(main_section), start=1):
        entries.append(
            WinnerEntry(
                rank_type="main",
                rank=i,
                masked_name=m.group(1),
                type_=m.group(2),
                floor=m.group(3),
                deposit=m.group(4),
                rent=m.group(5),
            )
        )

    if reserve_section:
        for m in _RESERVE_ROW_RE.finditer(reserve_section):
            entries.append(
                WinnerEntry(
                    rank_type="reserve",
                    rank=int(m.group(1)),
                    masked_name=m.group(2),
                )
            )

    return entries


def matches_in_body(body: str, comment_text: str) -> bool:
    """True if `comment_text` (unmasked or masked) appears in `body`."""
    if comment_text in body:
        return True
    m = _NAME_BIRTH_RE.match(comment_text)
    if not m:
        return False
    name, birth = m.group(1), m.group(2)
    if len(name) < 2:
        # 1-char name — too ambiguous to fuzzy match; require birthday adjacent
        # to the single letter.
        return bool(re.search(rf"{re.escape(name)}[{_MASK_CHARS}]*\s*{re.escape(birth)}", body))
    first, last = name[0], name[-1]
    # Operator masks middle character(s); mask glyph + remaining real letters
    # may total 1-3 chars. Use a generic non-greedy `.` so we accept either
    # purely-masked ("정*우") or partially-masked ("남*민수") forms.
    pattern = re.compile(
        rf"{re.escape(first)}.{{1,3}}?{re.escape(last)}\s*{re.escape(birth)}"
    )
    return bool(pattern.search(body))


@dataclass
class WinnerEntry:
    """One row from the announcement, masked-name form preserved."""
    rank_type: Literal["main", "reserve"]
    rank: int                # 1-based within its table
    masked_name: str         # e.g. "전*미0913"
    type_: str | None = None     # main only — "74A"
    floor: str | None = None     # main only — "1"
    deposit: str | None = None   # main only — "112,000,000"
    rent: str | None = None      # main only — "510,000"


@dataclass
class WinnerMatch:
    account_index: int
    comment_text: str        # what we submitted ("정창우1125")
    entry: WinnerEntry       # the row we matched against


@dataclass
class WinnerCheckResult:
    article_url: str
    article_title: str
    matches: list[WinnerMatch] = field(default_factory=list)
    body_excerpt: str = ""


def _entry_matches_text(entry: WinnerEntry, comment_text: str) -> bool:
    """Does `comment_text` (unmasked) correspond to the masked `entry.masked_name`?"""
    if entry.masked_name == comment_text:
        return True
    # Reuse the body-matcher: treat the entry's masked label as a 1-line "body".
    return matches_in_body(entry.masked_name, comment_text)


def match_candidates(
    body: str, candidates: list[tuple[int, str]]
) -> list[WinnerMatch]:
    """Match each (account, comment_text) against parsed entries; keep first hit."""
    entries = parse_winner_tables(body)
    matches: list[WinnerMatch] = []
    for idx, text in candidates:
        if not text:
            continue
        for entry in entries:
            if _entry_matches_text(entry, text):
                matches.append(WinnerMatch(idx, text, entry))
                break
        else:
            # Operator's table parsing might have failed (layout change). Fall
            # back to a body-wide match so we don't silently miss a winner.
            if matches_in_body(body, text):
                matches.append(
                    WinnerMatch(
                        idx, text,
                        WinnerEntry(rank_type="main", rank=0, masked_name=text),
                    )
                )
    return matches


async def find_winners(
    page: Page,
    article_url: str,
    candidates: list[tuple[int, str]],
) -> WinnerCheckResult:
    """Open the announcement page and resolve each candidate to a WinnerMatch."""
    await page.goto(article_url, wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:  # noqa: BLE001 — networkidle is best-effort
        pass

    scope = await _enter_content_root(page)
    body = await scope.locator("body").inner_text()
    title = ""
    title_loc = scope.locator(".title_text, h3.title_text").first
    if await title_loc.count() > 0:
        title = (await title_loc.inner_text()).strip()

    return WinnerCheckResult(
        article_url=article_url,
        article_title=title,
        matches=match_candidates(body, candidates),
        body_excerpt=body[:500],
    )


_MEDALS = ["🥇", "🥈", "🥉"]


def _entry_details_block(entry: WinnerEntry) -> list[str]:
    if entry.rank_type != "main" or entry.type_ is None:
        return []
    return [
        f"   📐 {entry.type_}형 / {entry.floor}층",
        f"   💰 보증금 {entry.deposit}원",
        f"   📆 월세 {entry.rent}원",
    ]


def _footer_lines(result: WinnerCheckResult) -> list[str]:
    out: list[str] = []
    if result.article_title:
        out.append(f"\n📋 <b>{result.article_title}</b>")
    out.append(f'<a href="{result.article_url}">→ 공지 게시글 보기</a>')
    return out


def format_winner_messages(result: WinnerCheckResult) -> list[str]:
    """One Telegram message per category (main / reserve / no-match)."""
    main = [m for m in result.matches if m.entry.rank_type == "main"]
    reserve = [m for m in result.matches if m.entry.rank_type == "reserve"]

    messages: list[str] = []

    if main:
        lines = [
            "🎉🎊✨🎊🎉🎊✨🎊🎉",
            "🏆 <b>본 선 당 첨!</b> 🏆",
            "🎉🎊✨🎊🎉🎊✨🎊🎉",
            "",
        ]
        for i, m in enumerate(main):
            medal = _MEDALS[i] if i < len(_MEDALS) else "🎯"
            lines.append(
                f"{medal} <b>account {m.account_index}</b>:  "
                f"<code>{m.comment_text}</code>  (본선 {m.entry.rank}순위)"
            )
            lines.extend(_entry_details_block(m.entry))
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━",
            "📌 <b>꼭 확인하세요</b>",
            "• 당일 <b>16:50</b>까지 임대사업센터로 <u>직접</u> 연락 필수",
            "• 미연락 시 자동 포기 처리됩니다",
            "• ☎️ <code>031-374-9514</code>",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "🍀 좋은 결정 되시길 바랍니다! 🍀",
        ])
        lines.extend(_footer_lines(result))
        messages.append("\n".join(lines))

    if reserve:
        lines = [
            "✨🎈 <b>예비 순위 당첨</b> 🎈✨",
            "",
            "본선 당첨자가 미연락하면 순차로 호출됩니다.",
            "",
        ]
        for m in reserve:
            lines.append(
                f"🎟️ <b>account {m.account_index}</b>:  "
                f"<code>{m.comment_text}</code>  (예비 {m.entry.rank}순위)"
            )
        lines.extend([
            "",
            "📞 <code>031-374-9514</code>로 본선 통화 결과를 문의할 수 있습니다.",
        ])
        lines.extend(_footer_lines(result))
        messages.append("\n".join(lines))

    if not messages:
        lines = ["📭 당첨자 명단 확인 — 일치 없음"]
        lines.extend(_footer_lines(result))
        messages.append("\n".join(lines))

    return messages


def format_winner_summary(result: WinnerCheckResult) -> str:
    """Backward-compatible single-string view (joins all category messages)."""
    return "\n\n──────────\n\n".join(format_winner_messages(result))
