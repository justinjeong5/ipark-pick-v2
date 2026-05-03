from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

# patchright is a drop-in fork of playwright with stronger anti-bot patches
# applied at the Chromium driver level — same async API.
from patchright.async_api import Browser, BrowserContext, Page, async_playwright


@asynccontextmanager
async def browser_context(
    *,
    headful: bool,
    storage_state: str | None = None,
) -> AsyncIterator[tuple[Browser, BrowserContext, Page]]:
    """Yield a stealthed (browser, context, page) using patchright.

    `storage_state` is the path to a previously saved cookies/localStorage file.
    Pass None for the very first login.
    """
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=not headful,
            args=["--lang=ko-KR"],
        )
        context = await browser.new_context(
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1366, "height": 900},
            storage_state=storage_state,
            extra_http_headers={
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            },
        )
        page = await context.new_page()
        try:
            yield browser, context, page
        finally:
            await context.close()
            await browser.close()
