import datetime
import json
import logging
import pathlib
import urllib.parse

from playwright.sync_api import sync_playwright, Page, Response, TimeoutError as PWTimeout

from . import config
from .cookies import load_cookies, to_playwright_cookies

logger = logging.getLogger("xhs.comments")


def scrape_comments(
    url: str,
    post_id: str,
    max_count: int,
    cookie_file: pathlib.Path,
    reply_k: int = 0,
    reply_n: int = 0,
) -> list[dict]:
    """
    Scrape top-level comments via Playwright response interception.
    If reply_k > 0, also fetch sub-comments for the most insightful threads:
      1. Heuristic: top-K threads by like_count × sub_comment_count
      2. LLM: Gemini picks top-N from those K candidates
      3. Fetch sub-comments for those N threads
    """
    raw_cookies = load_cookies(cookie_file)
    pw_cookies = to_playwright_cookies(raw_cookies)

    xsec_token = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("xsec_token", [""])[0]

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

        try:
            page.wait_for_selector(
                "div.comments-container, .comment-list, [class*='comment']",
                timeout=15_000,
            )
        except PWTimeout:
            logger.warning("Comment section selector not found — proceeding with scroll anyway.")

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

        collected.sort(key=lambda c: c["like_count"], reverse=True)
        top = collected[:max_count]

        if reply_k > 0 and reply_n > 0:
            top = _enrich_with_replies(page, top, post_id, xsec_token, reply_k, reply_n)

        context.close()
        browser.close()

    logger.info("Done. Returning %d comment(s) (sorted by likes).", len(top))
    return top


def _enrich_with_replies(
    page: Page,
    comments: list[dict],
    post_id: str,
    xsec_token: str,
    reply_k: int,
    reply_n: int,
) -> list[dict]:
    """
    Surface the most insightful threads using heuristic + LLM selection.
    XHS pre-loads 1 reply per thread; we use that for the selected threads
    and log the total reply count for the rest.
    """
    candidates = _heuristic_top_k(comments, reply_k)
    if not candidates:
        logger.info("No comments with replies found — skipping reply enrichment.")
        return comments

    logger.info("Heuristic selected %d candidate thread(s) for LLM review.", len(candidates))
    selected_ids = _llm_select_top_n(candidates, min(reply_n, len(candidates)))
    logger.info("LLM selected %d thread(s) as most insightful.", len(selected_ids))

    selected_set = set(selected_ids)
    for comment in comments:
        cid = comment["id"]
        if cid in selected_set:
            replies = comment.get("replies", [])
            logger.info(
                "Thread %s: %d pre-loaded reply, %d total (LLM pick)",
                cid, len(replies), comment.get("sub_comment_count", 0),
            )
            if comment.get("replies_has_more"):
                comment["replies_note"] = f"{comment.get('sub_comment_count', 0)} total replies; showing top pre-loaded reply"
        elif "replies" in comment:
            del comment["replies"]
            comment.pop("replies_has_more", None)

    return comments


def _heuristic_top_k(comments: list[dict], k: int) -> list[dict]:
    """Return top-k comments most likely to have insightful threads."""
    with_replies = [c for c in comments if c["sub_comment_count"] > 0]
    scored = sorted(
        with_replies,
        key=lambda c: c["like_count"] * c["sub_comment_count"],
        reverse=True,
    )
    return scored[:k]


def _llm_select_top_n(candidates: list[dict], n: int) -> list[str]:
    """Use Gemini to pick the n comment IDs most likely to have insightful replies."""
    from .analysis import _make_gemini_client

    client = _make_gemini_client()
    prompt_data = [
        {
            "id": c["id"],
            "content": c["content"],
            "like_count": c["like_count"],
            "reply_count": c["sub_comment_count"],
        }
        for c in candidates
    ]
    prompt = f"""You are analyzing comments from a Xiaohongshu (Chinese social media) post.

Select the {n} comment(s) most likely to have insightful, valuable, or interesting discussion threads underneath. Consider: debate-sparking opinions, questions that others answer, expert takes, emotional reactions that draw community response, or anything that would generate meaningful conversation.

Comments (JSON):
{json.dumps(prompt_data, ensure_ascii=False, indent=2)}

Return a JSON array of exactly {n} comment ID string(s), ordered by priority (best first).
Example: ["id1", "id2"]"""

    schema = {"type": "array", "items": {"type": "string"}}
    try:
        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            config={"response_mime_type": "application/json", "response_schema": schema},
        )
        ids = json.loads(response.text)
        return [i for i in ids if isinstance(i, str)][:n]
    except Exception as e:
        logger.warning("LLM selection failed (%s) — falling back to heuristic top-%d", e, n)
        return [c["id"] for c in candidates[:n]]


def _fetch_sub_comments(page: Page, note_id: str, root_comment_id: str, xsec_token: str) -> list[dict]:
    # XHS's sub-comment API requires cryptographic signing headers generated by their
    # frontend JS and tied to a browser fingerprint. Replicating this outside the
    # user's real Chrome session is not feasible. Sub-comments are instead surfaced
    # from the 1 reply XHS pre-loads per thread in the initial comment response.
    return []


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


def _normalize_comment(raw: dict, include_replies: bool = True) -> dict:
    user = raw.get("user_info") or raw.get("user") or {}
    ts = raw.get("create_time") or raw.get("time") or 0
    posted = (
        datetime.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S")
        if ts else ""
    )
    comment: dict = {
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
    # XHS pre-loads the most recent sub-comment for each thread; extract it.
    if include_replies:
        raw_subs = raw.get("sub_comments") or []
        if raw_subs:
            comment["replies"] = [_normalize_comment(s, include_replies=False) for s in raw_subs]
            comment["replies_has_more"] = bool(raw.get("sub_comment_has_more", False))
    return comment
