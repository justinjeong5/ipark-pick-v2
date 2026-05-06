from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta

from .board_finder import ArticleKind, find_today_article
from .browser import browser_context
from .comment_bot import CommentResult, CommentStatus, post_comment
from .config import (
    Account,
    Target,
    ensure_dirs,
    load_account,
    load_runtime,
    load_target,
    load_telegram,
)
from .inspector import inspect_comment_area
from .naver_auth import LoginExpiredError, assert_logged_in, manual_login_and_save
from .notice_check import fetch_notice, format_notice_message, format_notice_missing_message
from .orchestrator import run_morning
from .telegram import send_message
from .winner_check import find_winners, format_winner_messages

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


async def _run_login(account: Account) -> int:
    await manual_login_and_save(account)
    return 0


async def _run_inspect(account: Account, target: Target) -> int:
    if not account.cookies_path.exists():
        logger.error(
            "쿠키 파일이 없습니다: %s — 먼저 `ipark-drawing login --account %d`",
            account.cookies_path,
            account.index,
        )
        return 2
    runtime = load_runtime()
    async with browser_context(
        headful=runtime.headful,
        storage_state=str(account.cookies_path),
    ) as (_browser, _context, page):
        try:
            await assert_logged_in(page, account)
        except LoginExpiredError as exc:
            logger.error(str(exc))
            return 3
        report_path = await inspect_comment_area(page, account, target)
    logger.info("inspect 결과 저장: %s", report_path)
    logger.info("이 파일을 보고 정확한 selector를 config.py에 반영하세요.")
    return 0


def _seconds_until(target_hms: str, *, now: datetime | None = None) -> float:
    """Return seconds from now until next occurrence of HH:MM:SS (today or tomorrow)."""
    h, m, s = (int(x) for x in target_hms.split(":"))
    cur = now or datetime.now()
    target = cur.replace(hour=h, minute=m, second=s, microsecond=0)
    if target <= cur:
        target += timedelta(days=1)
    return (target - cur).total_seconds()


async def _wait_until(target_hms: str, prelead_s: float) -> None:
    """Sleep until prelead_s seconds before the target HH:MM:SS clock time."""
    delay = _seconds_until(target_hms) - prelead_s
    if delay <= 0:
        return
    logger.info(
        "약속 시간 %s까지 %.1f초 대기 (lead %.1fs 전 페이지 진입)",
        target_hms,
        delay,
        prelead_s,
    )
    await asyncio.sleep(delay)


async def _run_comment(
    account: Account,
    target: Target,
    *,
    at_time: str | None = None,
    prelead_s: float = 10.0,
) -> int:
    runtime = load_runtime()
    if not account.cookies_path.exists():
        logger.error(
            "쿠키 파일이 없습니다: %s — 먼저 `ipark-drawing login --account %d`",
            account.cookies_path,
            account.index,
        )
        return 2

    if at_time:
        await _wait_until(at_time, prelead_s)

    async with browser_context(
        headful=runtime.headful,
        storage_state=str(account.cookies_path),
    ) as (_browser, _context, page):
        try:
            await assert_logged_in(page, account)
        except LoginExpiredError as exc:
            logger.error(str(exc))
            return 3

        result = await post_comment(
            page,
            account,
            target,
            poll_timeout_s=runtime.comment_poll_timeout,
        )

    _print_result(result)
    exit_codes = {
        CommentStatus.POSTED: 0,
        CommentStatus.ALREADY_POSTED: 0,
        CommentStatus.SKIPPED_BLOCKED: 0,
        CommentStatus.CAPTCHA: 4,
        CommentStatus.LOGIN_EXPIRED: 3,
        CommentStatus.FAILED: 1,
    }
    return exit_codes[result.status]


def _print_result(result: CommentResult) -> None:
    logger.info(
        "[account %d] status=%s elapsed=%dms reason=%s",
        result.account_index,
        result.status.value,
        result.elapsed_ms,
        result.reason,
    )
    if result.screenshot_path:
        logger.info("스크린샷: %s", result.screenshot_path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ipark-drawing")
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="브라우저를 띄워 수동 로그인 후 쿠키 저장")
    login.add_argument("--account", type=int, required=True, choices=[1, 2, 3])

    comment = sub.add_parser("comment", help="저장된 쿠키로 댓글 작성")
    comment.add_argument("--account", type=int, required=True, choices=[1, 2, 3])
    comment.add_argument(
        "--at",
        metavar="HH:MM:SS",
        help="약속 시간까지 자동 대기 후 페이지 진입 (오늘 시각이 이미 지났으면 내일로 해석)",
    )
    comment.add_argument(
        "--prelead-s",
        type=float,
        default=10.0,
        help="약속 시간보다 이만큼 초 전에 페이지를 미리 띄움 (기본 10.0초)",
    )

    inspect = sub.add_parser(
        "inspect",
        help="게시글 페이지의 댓글 영역 selector 후보를 JSON으로 추출",
    )
    inspect.add_argument("--account", type=int, required=True, choices=[1, 2, 3])

    morning = sub.add_parser(
        "run-morning",
        help="목요일 10시 운영용: 여러 계정을 병렬 실행 + 텔레그램 알림",
    )
    morning.add_argument(
        "--accounts",
        default="1,2,3",
        help="콤마로 구분한 account 인덱스 (기본 1,2,3)",
    )
    morning.add_argument(
        "--at",
        metavar="HH:MM:SS",
        help="약속 시간까지 자동 대기 후 실행",
    )
    morning.add_argument(
        "--prelead-s",
        type=float,
        default=10.0,
        help="약속 시간보다 이만큼 초 전에 시작 (기본 10.0초)",
    )
    morning.add_argument(
        "--no-notify",
        action="store_true",
        help="텔레그램 알림 비활성화 (디버깅용)",
    )
    morning.add_argument(
        "--article-id",
        help="자동 탐색된 article_id를 명시적으로 전달 (없으면 .env의 TARGET_ARTICLE_ID 사용)",
    )

    winners = sub.add_parser(
        "check-winners",
        help="당첨자 발표 게시글에서 우리 댓글 매치 검색 + 텔레그램 알림",
    )
    winners.add_argument(
        "--result-url",
        required=True,
        help="당첨자 발표 게시글 URL",
    )
    winners.add_argument("--account", type=int, required=True, choices=[1, 2, 3])
    winners.add_argument(
        "--at",
        metavar="HH:MM:SS",
        help="발표 시각까지 자동 대기 후 조회 (보통 14:30:00)",
    )
    winners.add_argument(
        "--prelead-s",
        type=float,
        default=10.0,
        help="발표 시각보다 이만큼 초 전에 페이지를 띄움 (기본 10.0초)",
    )
    winners.add_argument(
        "--no-notify",
        action="store_true",
        help="텔레그램 알림 비활성화 (디버깅용)",
    )

    discover = sub.add_parser(
        "discover",
        help="오늘 날짜의 공실/당첨자/안내 글 article_id를 stdout에 출력",
    )
    discover.add_argument("--account", type=int, required=True, choices=[1, 2, 3])
    discover.add_argument(
        "--kind",
        required=True,
        choices=[k.value for k in ArticleKind],
        help="comment / winner / notice",
    )

    notice = sub.add_parser(
        "check-notice",
        help="수요일 '공실 안내' 글을 찾아 내일 추첨 여부 예측 + 텔레그램 알림",
    )
    notice.add_argument("--account", type=int, required=True, choices=[1, 2, 3])
    notice.add_argument(
        "--no-notify",
        action="store_true",
        help="텔레그램 알림 비활성화 (디버깅용)",
    )
    notice.add_argument(
        "--no-missing-notify",
        action="store_true",
        help="안내 글 미발견 시 '직접 확인' 알림 비활성화 (폴링 중간 시도용)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    ensure_dirs()
    args = _build_parser().parse_args(argv)

    if args.command == "run-morning":
        return asyncio.run(_run_morning_cli(args))

    account = load_account(args.account)
    if args.command == "login":
        return asyncio.run(_run_login(account))
    if args.command == "comment":
        return asyncio.run(
            _run_comment(
                account,
                load_target(),
                at_time=args.at,
                prelead_s=args.prelead_s,
            )
        )
    if args.command == "inspect":
        return asyncio.run(_run_inspect(account, load_target()))
    if args.command == "check-winners":
        return asyncio.run(_run_check_winners(account, args))
    if args.command == "discover":
        return asyncio.run(_run_discover(account, args))
    if args.command == "check-notice":
        return asyncio.run(_run_check_notice(account, args))
    return 1


async def _run_check_notice(account: Account, args: argparse.Namespace) -> int:
    if not account.cookies_path.exists():
        logger.error(
            "쿠키 파일이 없습니다 — 먼저 `ipark-drawing login --account %d`",
            account.index,
        )
        return 2
    runtime = load_runtime()
    target = load_target()
    telegram = load_telegram()

    async with browser_context(
        headful=runtime.headful,
        storage_state=str(account.cookies_path),
    ) as (_browser, _context, page):
        try:
            await assert_logged_in(page, account)
        except LoginExpiredError as exc:
            logger.error(str(exc))
            return 3
        article_id = await find_today_article(
            page, target.club_id, target.list_menu_id, ArticleKind.NOTICE,
        )
        if article_id is None:
            logger.info("공실 안내 글 미발견")
            if not args.no_notify and not args.no_missing_notify:
                try:
                    await send_message(telegram, format_notice_missing_message())
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Telegram 알림 실패: %s", exc)
            return 4
        article_url = (
            f"https://cafe.naver.com/f-e/cafes/{target.club_id}"
            f"/articles/{article_id}?boardtype=L"
        )
        result = await fetch_notice(page, article_url)

    logger.info(
        "공실 안내 발견: %s (likely_drawing=%s)",
        result.article_title or "(제목 없음)",
        result.likely_drawing,
    )
    if not args.no_notify:
        try:
            await send_message(telegram, format_notice_message(result))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Telegram 알림 실패: %s", exc)
    return 0


async def _run_discover(account: Account, args: argparse.Namespace) -> int:
    if not account.cookies_path.exists():
        logger.error(
            "쿠키 파일이 없습니다 — 먼저 `ipark-drawing login --account %d`",
            account.index,
        )
        return 2
    runtime = load_runtime()
    target = load_target()
    kind = ArticleKind(args.kind)

    async with browser_context(
        headful=runtime.headful,
        storage_state=str(account.cookies_path),
    ) as (_browser, _context, page):
        try:
            await assert_logged_in(page, account)
        except LoginExpiredError as exc:
            logger.error(str(exc))
            return 3
        article_id = await find_today_article(
            page, target.club_id, target.list_menu_id, kind
        )

    if article_id is None:
        logger.info("오늘 날짜의 %s 게시글을 찾지 못함", kind.value)
        return 4
    print(article_id)
    return 0


async def _run_check_winners(account: Account, args: argparse.Namespace) -> int:
    from .locking import AlreadyRunningError, single_instance

    if not account.cookies_path.exists():
        logger.error(
            "쿠키 파일이 없습니다: %s — 먼저 `ipark-drawing login --account %d`",
            account.cookies_path,
            account.index,
        )
        return 2

    try:
        with single_instance("winners"):
            return await _do_check_winners(account, args)
    except AlreadyRunningError as exc:
        logger.warning("동시 실행 차단: %s", exc)
        return 0


async def _do_check_winners(account: Account, args: argparse.Namespace) -> int:
    if args.at:
        await _wait_until(args.at, args.prelead_s)

    runtime = load_runtime()
    telegram = load_telegram()
    # All three accounts' comment texts are candidates — any of them might win.
    candidates: list[tuple[int, str]] = []
    for idx in (1, 2, 3):
        try:
            candidates.append((idx, load_account(idx).comment_text))
        except RuntimeError:
            continue

    async with browser_context(
        headful=runtime.headful,
        storage_state=str(account.cookies_path),
    ) as (_browser, _context, page):
        try:
            await assert_logged_in(page, account)
        except LoginExpiredError as exc:
            logger.error(str(exc))
            return 3
        result = await find_winners(page, args.result_url, candidates)

    logger.info(
        "당첨 확인: 매치 %d건 (제목: %s)",
        len(result.matches),
        result.article_title or "(제목 없음)",
    )
    if not args.no_notify:
        for msg in format_winner_messages(result):
            try:
                await send_message(telegram, msg)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Telegram 알림 실패: %s", exc)

    return 0 if result.matches else 4


async def _run_morning_cli(args: argparse.Namespace) -> int:
    indices = [int(x) for x in args.accounts.split(",") if x.strip()]
    accounts = [load_account(i) for i in indices]
    target = load_target()
    if args.article_id:
        target = Target(
            cafe_url=target.cafe_url,
            club_id=target.club_id,
            article_id=args.article_id,
            menu_id=target.menu_id,
            list_menu_id=target.list_menu_id,
        )
    runtime = load_runtime()
    telegram = load_telegram()

    if args.at:
        await _wait_until(args.at, args.prelead_s)

    results = await run_morning(
        accounts,
        target,
        runtime,
        telegram,
        notify=not args.no_notify,
        article_id=target.article_id,
    )
    for r in results:
        _print_result(r)

    failed = [r for r in results if r.status == CommentStatus.FAILED]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
