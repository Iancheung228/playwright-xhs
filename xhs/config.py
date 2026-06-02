import pathlib

GEMINI_MODEL = "gemini-2.5-flash"

DEFAULT_COOKIE_FILE = pathlib.Path.home() / "cookies_rednote.json"
DEFAULT_COMMENTS_N = 50

XHS_BASE = "https://www.xiaohongshu.com"
COMMENT_API_PATH = "/api/sns/web/v2/comment/page"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.xiaohongshu.com/",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
