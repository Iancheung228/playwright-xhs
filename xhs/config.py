import pathlib

GEMINI_MODEL = "gemini-2.5-flash"

DEFAULT_COOKIE_FILE = pathlib.Path.home() / "cookies_rednote.json"
DEFAULT_AUTH_STATE_FILE = pathlib.Path.home() / "xhs_auth.json"
DEFAULT_COMMENTS_N = 50
DEFAULT_REPLY_K = 10   # heuristic pool size
DEFAULT_REPLY_N = 3    # LLM picks this many threads to actually fetch

XHS_BASE = "https://www.xiaohongshu.com"
COMMENT_API_PATH = "/api/sns/web/v2/comment/page"
SUB_COMMENT_API_PATH = "/api/sns/web/v2/comment/sub/page"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.xiaohongshu.com/",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
