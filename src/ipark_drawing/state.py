"""Tiny state file shared between morning and afternoon launchd jobs.

run-morning writes a snapshot of "did the drawing open today?" so run-winners
can decide whether to skip silently (no drawing -> no announcement expected),
to expect an announcement (drawing happened), or to deduplicate a re-trigger.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime

from .comment_bot import CommentResult, CommentStatus
from .config import STATE_DIR

LAST_RUN_PATH = STATE_DIR / "last-run.json"
HISTORY_DIR = STATE_DIR / "history"


@dataclass
class MorningSnapshot:
    date: str                          # ISO YYYY-MM-DD
    drawing_open: bool                 # at least one comment box was reachable
    article_id: str | None             # discovered today's article id
    posted_accounts: list[int] = field(default_factory=list)
    statuses: dict[str, str] = field(default_factory=dict)
    morning_notified: bool = False     # 1차(10시) 알림 발송 완료
    winners_notified: bool = False     # 2차(14:30) 알림 발송 완료


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def write_snapshot(
    article_id: str | None,
    results: list[CommentResult],
) -> MorningSnapshot:
    """Compute and persist this morning's outcome."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    blocked_n = sum(1 for r in results if r.status == CommentStatus.SKIPPED_BLOCKED)
    drawing_open = bool(results) and blocked_n != len(results)
    snap = MorningSnapshot(
        date=_today_str(),
        drawing_open=drawing_open,
        article_id=article_id,
        posted_accounts=[
            r.account_index
            for r in results
            if r.status in (CommentStatus.POSTED, CommentStatus.ALREADY_POSTED)
        ],
        statuses={str(r.account_index): r.status.value for r in results},
    )
    payload = json.dumps(asdict(snap), ensure_ascii=False, indent=2)
    LAST_RUN_PATH.write_text(payload)
    # Audit log — one file per date for retroactive debugging.
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    (HISTORY_DIR / f"{snap.date}.json").write_text(payload)
    return snap


def read_snapshot() -> MorningSnapshot | None:
    if not LAST_RUN_PATH.exists():
        return None
    try:
        raw = json.loads(LAST_RUN_PATH.read_text())
        return MorningSnapshot(**raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        # File corrupted or schema drifted — drop it and fall back to "no state".
        # Caller will treat as "proceed defensively".
        return None


def _mark(field_name: str) -> None:
    snap = read_snapshot()
    if snap is None:
        return
    setattr(snap, field_name, True)
    LAST_RUN_PATH.write_text(json.dumps(asdict(snap), ensure_ascii=False, indent=2))


def mark_morning_notified() -> None:
    _mark("morning_notified")


def mark_winners_notified() -> None:
    _mark("winners_notified")


def is_today(snap: MorningSnapshot | None) -> bool:
    return snap is not None and snap.date == _today_str()
