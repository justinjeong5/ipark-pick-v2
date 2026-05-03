"""Dump comment-area DOM candidates from the target article page.

Run after `ipark-drawing login --account N`. Result is written to
data/inspect/<account>_<timestamp>.json so the user can paste the relevant
selectors into config.py.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from patchright.async_api import Page

from .config import PROJECT_ROOT, Account, Target

INSPECT_DIR = PROJECT_ROOT / "data" / "inspect"

PROBE_SCRIPT = """
() => {
  const trim = (s, n=400) => (s || '').replace(/\\s+/g,' ').slice(0, n);
  const map = (els) => Array.from(els).slice(0, 5).map(el => ({
    tag: el.tagName.toLowerCase(),
    id: el.id || null,
    class: el.className || null,
    placeholder: el.getAttribute('placeholder'),
    role: el.getAttribute('role'),
    name: el.getAttribute('name'),
    type: el.getAttribute('type'),
    text: trim(el.innerText, 100),
    outerHTML: trim(el.outerHTML, 600),
  }));
  return {
    url: location.href,
    title: document.title,
    iframes: Array.from(document.querySelectorAll('iframe'))
      .map(f => ({id: f.id, name: f.name, src: f.src})),
    textareas: map(document.querySelectorAll('textarea')),
    contenteditables: map(document.querySelectorAll('[contenteditable="true"]')),
    role_textboxes: map(document.querySelectorAll('[role="textbox"]')),
    register_buttons: map(
      Array.from(document.querySelectorAll('button, a'))
        .filter(b => /등록|작성|확인/.test(b.innerText || ''))
    ),
    comment_area: map(document.querySelectorAll(
      '[class*="CommentBox"], [class*="comment_area"],'
      + ' [class*="CommentInput"], [class*="comment_inbox"]'
    )),
    comment_items: map(document.querySelectorAll(
      'li.CommentItem, [class*="CommentItem"], [class*="comment_item"]'
    )),
    blocked_hints: map(document.querySelectorAll(
      '.comment_inbox_block, [class*="comment_block"],'
      + ' [class*="CommentBlocked"], [class*="disabled"]'
    )),
  };
}
"""


async def inspect_comment_area(page: Page, account: Account, target: Target) -> Path:
    INSPECT_DIR.mkdir(parents=True, exist_ok=True)

    await page.goto(target.article_url, wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:  # noqa: BLE001 - networkidle is best-effort
        pass

    report = await page.evaluate(PROBE_SCRIPT)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = INSPECT_DIR / f"account_{account.index}_{stamp}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return out_path
