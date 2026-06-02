import datetime
import logging
import pathlib

from playwright.sync_api import sync_playwright, Response, TimeoutError as PWTimeout

from . import config
from .cookies import load_cookies, to_playwright_cookies

logger = logging.getLogger("xhs.comments")


def scrape_comments(
    url: str,
    post_id: str,
    max_count: int,
    cookie_file: pathlib.Path,
) -> list[dict]:
    """
    Launch headless Chromium, navigate to the XHS post, intercept
    /api/sns/web/v2/comment/page responses, and return up to max_count
    comments sorted by like_count descending.
    """
    raw_cookies = load_cookies(cookie_file)
    pw_cookies = to_playwright_cookies(raw_cookies)

    collected: list[dict] = []
    seen_ids: set[str] = set()
    has_more = True

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=config.HEADERS["User-Agent"],
            locale="zh-CN",
        )
        context.add_cookies(pw_cookies)
        page = context.new_page()

        def _on_response(response: Response) -> None:
            nonlocal has_more
            if config.COMMENT_API_PATH not in response.url:
                return
            try:
                data = response.json()
            except Exception:
                return
            if data.get("code") != 0:
                logger.warning("Comment API returned code %s: %s", data.get("code"), data.get("msg"))
                return
            payload = data.get("data", {})
            if not payload:
                logger.warning("Comment API returned empty data — cookies may be missing web_session. Re-run: python3 scrape.py --setup")
                return
            for raw in payload.get("comments", []):
                cid = raw.get("id")
                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    collected.append(_normalize_comment(raw))
            has_more = payload.get("has_more", False)
            logger.info("Collected %d comment(s) so far...", len(collected))

        page.on("response", _on_response)

        logger.info("Navigating to %s", url)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except PWTimeout:
            logger.error("Page load timed out. Check your internet connection or cookie validity.")
            context.close()
            browser.close()
            return []

        # Wait for comment section — non-fatal if selector changes
        try:
            page.wait_for_selector(
                "div.comments-container, .comment-list, [class*='comment']",
                timeout=15_000,
            )
        except PWTimeout:
            logger.warning("Comment section selector not found — proceeding with scroll anyway.")

        # Scroll loop to trigger comment pagination
        prev_count = -1
        stall_rounds = 0
        MAX_STALL = 3

        while len(collected) < max_count and has_more and stall_rounds < MAX_STALL:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1800)

            if len(collected) == prev_count:
                stall_rounds += 1
                logger.debug("No new comments (stall round %d/%d)", stall_rounds, MAX_STALL)
            else:
                stall_rounds = 0
            prev_count = len(collected)

        context.close()
        browser.close()

    collected.sort(key=lambda c: c["like_count"], reverse=True)
    top = collected[:max_count]
    logger.info("Done. Returning %d comment(s) (sorted by likes).", len(top))
    return top


def _parse_count(value) -> int:
    """Parse XHS count strings like '10+', '1.2k', '2w' into integers."""
    if value is None:
        return 0
    s = str(value).strip().lower().rstrip("+").replace(",", "")
    try:
        if s.endswith("w"):
            return int(float(s[:-1]) * 10_000)
        if s.endswith("k"):
            return int(float(s[:-1]) * 1_000)
        return int(float(s)) if s else 0
    except (ValueError, TypeError):
        return 0


def _normalize_comment(raw: dict) -> dict:
    user = raw.get("user_info") or raw.get("user") or {}
    ts = raw.get("create_time") or raw.get("time") or 0
    posted = (
        datetime.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S")
        if ts else ""
    )
    return {
        "id": raw.get("id", ""),
        "content": raw.get("content", ""),
        "user_id": user.get("user_id", ""),
        "nickname": user.get("nickname", ""),
        "avatar_url": user.get("image", ""),
        "like_count": _parse_count(raw.get("like_count", 0)),
        "posted_at": posted,
        "ip_location": raw.get("ip_location", ""),
        "sub_comment_count": _parse_count(raw.get("sub_comment_count", 0)),
        "has_images": bool(raw.get("pictures")),
    }
