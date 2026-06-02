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
    """Read cookies directly from Chrome (must be logged in to xiaohongshu.com in Chrome)."""
    print("Reading cookies from Chrome — make sure you're logged in to xiaohongshu.com in Chrome...")
    try:
        from pycookiecheat import chrome_cookies
        cookies = chrome_cookies("https://www.xiaohongshu.com")
    except Exception as e:
        logger.error("Could not read Chrome cookies: %s", e)
        logger.error("Make sure Chrome is installed and you are logged in to xiaohongshu.com")
        sys.exit(1)

    if not cookies:
        logger.error("No cookies found for xiaohongshu.com — are you logged in on Chrome?")
        sys.exit(1)

    cookie_file.write_text(json.dumps(cookies, indent=2, ensure_ascii=False))
    print(f"\n[✓] Saved {len(cookies)} cookies to {cookie_file}")
    print(f"    web_session: {'found ✓' if 'web_session' in cookies else 'MISSING — log in to XHS in Chrome first'}")
    return cookies


def to_playwright_cookies(cookies: dict) -> list:
    """Convert flat {name: value} cookie dict to Playwright's required format."""
    return [
        {"name": k, "value": v, "domain": ".xiaohongshu.com", "path": "/"}
        for k, v in cookies.items()
    ]
