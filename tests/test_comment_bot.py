from __future__ import annotations

from collections.abc import Sequence

import pytest

from ipark_drawing.comment_bot import wait_for_comment_box


class FakeProbe:
    """Replays scripted (blocked, active) tuples on each (is_blocked/is_active) pair."""

    def __init__(self, script: Sequence[tuple[bool, bool]]) -> None:
        self._script = list(script)
        self._idx = 0

    def _step(self) -> tuple[bool, bool]:
        if self._idx >= len(self._script):
            return self._script[-1]
        item = self._script[self._idx]
        self._idx += 1
        return item

    async def is_blocked(self) -> bool:
        self._current = self._step()
        return self._current[0]

    async def is_active(self) -> bool:
        return self._current[1]


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def now_fn(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def test_returns_true_when_box_activates_immediately():
    probe = FakeProbe([(False, True)])
    clock = FakeClock()

    result = await wait_for_comment_box(
        probe, timeout_s=10.0, sleep=clock.sleep, now=clock.now_fn
    )

    assert result is True
    assert clock.sleeps == []  # no waiting needed


async def test_returns_true_when_box_activates_after_some_polling():
    probe = FakeProbe([(False, False), (False, False), (False, True)])
    clock = FakeClock()

    result = await wait_for_comment_box(
        probe, timeout_s=10.0, sleep=clock.sleep, now=clock.now_fn
    )

    assert result is True
    assert len(clock.sleeps) == 2


async def test_returns_false_when_explicitly_blocked():
    probe = FakeProbe([(True, False)])
    clock = FakeClock()

    result = await wait_for_comment_box(
        probe, timeout_s=10.0, sleep=clock.sleep, now=clock.now_fn
    )

    assert result is False
    assert clock.sleeps == []  # short-circuit on blocked hint


async def test_returns_false_on_timeout_without_activation():
    # Box never activates and never explicitly blocked — pure timeout path.
    probe = FakeProbe([(False, False)])
    clock = FakeClock()

    result = await wait_for_comment_box(
        probe, timeout_s=0.5, sleep=clock.sleep, now=clock.now_fn
    )

    assert result is False
    assert clock.now >= 0.5


async def test_treats_blocked_during_polling_as_immediate_skip():
    # Activates "later" but blocked-hint shows up first → must skip.
    probe = FakeProbe([(False, False), (True, False), (False, True)])
    clock = FakeClock()

    result = await wait_for_comment_box(
        probe, timeout_s=10.0, sleep=clock.sleep, now=clock.now_fn
    )

    assert result is False


async def test_comment_status_enum_includes_captcha():
    """Captcha detection must surface as a distinct status, not 'failed'."""
    from ipark_drawing.comment_bot import CommentStatus

    assert CommentStatus.CAPTCHA.value == "captcha"
    assert CommentStatus.SKIPPED_BLOCKED.value == "skipped_blocked"
    # Distinguishing skip-blocked from captcha matters for downstream alerting.
    assert CommentStatus.CAPTCHA != CommentStatus.SKIPPED_BLOCKED


def test_telegram_normalize_chat_id_converts_t_me_url():
    from ipark_drawing.telegram import _normalize_chat_id

    assert _normalize_chat_id("https://t.me/c/3271858551?boost") == "-1003271858551"
    assert _normalize_chat_id("t.me/c/3271858551") == "-1003271858551"


def test_telegram_normalize_chat_id_passes_through_numeric():
    from ipark_drawing.telegram import _normalize_chat_id

    assert _normalize_chat_id("-1003271858551") == "-1003271858551"
    assert _normalize_chat_id("12345") == "12345"


def test_winner_match_unmasked():
    from ipark_drawing.winner_check import matches_in_body

    body = "당첨자\n1 정창우1125\n2 김희0524"
    assert matches_in_body(body, "정창우1125") is True
    assert matches_in_body(body, "김희0524") is True
    assert matches_in_body(body, "박지원0101") is False


def test_winner_match_with_middle_char_masked():
    from ipark_drawing.winner_check import matches_in_body

    # Real-world layout from cafe 30020276: "김*희0524", "박*진0123"
    body = "예비 순위\t당첨자\n1\t김*희0524\n2\t박*진0123\n3\t정*우1125"
    assert matches_in_body(body, "정창우1125") is True
    assert matches_in_body(body, "김희0524") is True   # would be "김*희0524"
    # Different birthday on a masked name → no false positive.
    assert matches_in_body(body, "정창우9999") is False


def test_winner_match_four_letter_name_masked():
    from ipark_drawing.winner_check import matches_in_body

    # 4-letter name with one mask char in middle: 남궁민수 -> 남*민수
    body = "남*민수1125 당첨"
    assert matches_in_body(body, "남궁민수1125") is True


_REAL_WINNER_BODY = """\
당첨자

타입

층수

보증금

임대료


전*미0913

74A

1

112,000,000

510,000


최*내0901

74B

10

73,000,000

638,000


예비 순위

당첨자


1

김*희0524

2

박*진0123
"""


def test_parse_winner_tables_extracts_main_and_reserve():
    from ipark_drawing.winner_check import parse_winner_tables

    entries = parse_winner_tables(_REAL_WINNER_BODY)
    main = [e for e in entries if e.rank_type == "main"]
    reserve = [e for e in entries if e.rank_type == "reserve"]

    assert len(main) == 2
    assert main[0].masked_name == "전*미0913"
    assert main[0].type_ == "74A"
    assert main[0].floor == "1"
    assert main[0].deposit == "112,000,000"
    assert main[0].rent == "510,000"

    assert len(reserve) == 2
    assert reserve[0].rank == 1
    assert reserve[0].masked_name == "김*희0524"


def test_match_candidates_distinguishes_main_vs_reserve():
    from ipark_drawing.winner_check import match_candidates

    matches = match_candidates(
        _REAL_WINNER_BODY,
        [(1, "전미미0913"), (2, "김희0524"), (3, "정창우9999")],
    )

    by_idx = {m.account_index: m for m in matches}
    assert 1 in by_idx and by_idx[1].entry.rank_type == "main"
    assert by_idx[1].entry.deposit == "112,000,000"
    assert 2 in by_idx and by_idx[2].entry.rank_type == "reserve"
    assert by_idx[2].entry.rank == 1
    assert 3 not in by_idx  # 정창우9999 — 매치 없어야


def test_board_finder_today_pattern_matches_with_zero_padding():
    import datetime as dt

    from ipark_drawing.board_finder import ArticleKind, _today_patterns

    today = dt.date(2026, 4, 23)
    patterns = _today_patterns(ArticleKind.COMMENT, today)
    # Variations seen in the wild
    assert any(p.search("4월 23일 공실 댓글") for p in patterns)
    assert any(p.search("04월 23일 공실 댓글") for p in patterns)
    assert any(p.search("4월 23일 공실") for p in patterns)  # fallback
    # Wrong day shouldn't match.
    assert not any(p.search("4월 24일 공실 댓글") for p in patterns)


def test_state_snapshot_drawing_open_when_any_posted(tmp_path, monkeypatch):
    from ipark_drawing import config, state
    from ipark_drawing.comment_bot import CommentResult, CommentStatus

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state, "LAST_RUN_PATH", tmp_path / "last-run.json")

    snap = state.write_snapshot(article_id="42", results=[
        CommentResult(CommentStatus.POSTED, 1, "ok", 100, comment_text="X"),
        CommentResult(CommentStatus.SKIPPED_BLOCKED, 2, "blocked", 100),
    ])
    assert snap.drawing_open is True
    assert snap.posted_accounts == [1]


def test_state_dedup_marks_morning_notified(tmp_path, monkeypatch):
    from ipark_drawing import config, state
    from ipark_drawing.comment_bot import CommentResult, CommentStatus

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state, "LAST_RUN_PATH", tmp_path / "last-run.json")

    state.write_snapshot(article_id="42", results=[
        CommentResult(CommentStatus.POSTED, 1, "ok", 100),
    ])
    snap = state.read_snapshot()
    assert snap is not None and snap.morning_notified is False

    state.mark_morning_notified()
    snap = state.read_snapshot()
    assert snap is not None and snap.morning_notified is True


def test_locking_single_instance_blocks_concurrent(tmp_path, monkeypatch):
    from ipark_drawing import locking
    from ipark_drawing.locking import AlreadyRunningError, single_instance

    monkeypatch.setattr(locking, "LOCK_DIR", tmp_path)
    holder = single_instance("test-job")
    holder.__enter__()
    try:
        with pytest.raises(AlreadyRunningError):
            second = single_instance("test-job")
            second.__enter__()
    finally:
        holder.__exit__(None, None, None)
    # After release, a new acquirer should succeed.
    with single_instance("test-job"):
        pass


def test_state_snapshot_no_drawing_when_all_blocked(tmp_path, monkeypatch):
    from ipark_drawing import config, state
    from ipark_drawing.comment_bot import CommentResult, CommentStatus

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state, "LAST_RUN_PATH", tmp_path / "last-run.json")

    snap = state.write_snapshot(article_id=None, results=[
        CommentResult(CommentStatus.SKIPPED_BLOCKED, 1, "x", 100),
        CommentResult(CommentStatus.SKIPPED_BLOCKED, 2, "x", 100),
        CommentResult(CommentStatus.SKIPPED_BLOCKED, 3, "x", 100),
    ])
    assert snap.drawing_open is False


def test_account_loader_rejects_invalid_index():
    from ipark_drawing.config import load_account

    with pytest.raises(ValueError):
        load_account(0)
    with pytest.raises(ValueError):
        load_account(4)


def test_target_article_url_uses_fe_spa_path(monkeypatch):
    from ipark_drawing.config import load_target

    monkeypatch.setenv("TARGET_CAFE_URL", "https://cafe.naver.com/redn47ad")
    monkeypatch.setenv("TARGET_CLUB_ID", "30796368")
    monkeypatch.setenv("TARGET_ARTICLE_ID", "21")
    monkeypatch.setenv("TARGET_MENU_ID", "2")

    url = load_target().article_url

    # New SPA shell — comment_bot enters via this URL and traverses #cafe_main.
    assert url.startswith("https://cafe.naver.com/f-e/cafes/30796368/articles/21")
    assert "menuid=2" in url
    assert "referrerAllArticles=false" in url
