import json
import logging
import pathlib
import sys

logger = logging.getLogger("xhs.cookies")


def load_cookies(cookie_file: pathlib.Path) -> dict:
    if not cookie_file.exists():
        logger.error("Cookie file not found at %s", cookie_file)
        logger.error("Run:  python3 scrape.py --setup")
        sys.exit(1)
    return json.loads(cookie_file.read_text())


def setup_cookies(cookie_file: pathlib.Path) -> dict:
    """
    Open a visible browser so the user can log in to XHS, then save:
      1. cookies_rednote.json  — for the HTTP session (metadata scraping)
      2. xhs_auth.json         — full browser state for Playwright (enables sub-comments)
    """
    from . import config
    from playwright.sync_api import sync_playwright

    print("""
Cookie setup — a browser window will open.

1. Log in to xiaohongshu.com in the browser (scan QR code or enter your phone + password).
2. Once you can see your feed, come back here and press Enter.
""")
    input("Press Enter to open the browser...")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="zh-CN",
        )
        page = context.new_page()
        page.goto("https://www.xiaohongshu.com")

        input("\nLog in to Xiaohongshu in the browser window, then press Enter here...")

        # Save full browser state (cookies + localStorage) for Playwright sessions
        auth_state_file = config.DEFAULT_AUTH_STATE_FILE
        context.storage_state(path=str(auth_state_file))
        print(f"[✓] Saved full browser auth state to {auth_state_file}")

        # Also extract plain cookies for the HTTP requests session
        raw_cookies = context.cookies()
        browser.close()

    cookie_dict = {
        c["name"]: c["value"]
        for c in raw_cookies
        if "xiaohongshu.com" in c.get("domain", "")
    }
    cookie_file.write_text(json.dumps(cookie_dict, indent=2, ensure_ascii=False))
    print(f"[✓] Saved {len(cookie_dict)} cookies to {cookie_file}")
    print(f"    web_session: {'found ✓' if 'web_session' in cookie_dict else 'MISSING — make sure you are fully logged in before pressing Enter'}")
    return cookie_dict


def to_playwright_cookies(cookies: dict) -> list:
    """Convert flat {name: value} cookie dict to Playwright's required format."""
    return [
        {"name": k, "value": v, "domain": ".xiaohongshu.com", "path": "/"}
        for k, v in cookies.items()
    ]
