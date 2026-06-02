import json
import logging
import re
import sys

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import config

logger = logging.getLogger("xhs.fetcher")


def make_session(cookies: dict) -> requests.Session:
    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update(config.HEADERS)
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def fetch_initial_state(url: str, session: requests.Session) -> dict:
    resp = session.get(url, timeout=15)
    resp.raise_for_status()

    match = re.search(
        r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})(?:;|\s*</script>)",
        resp.text,
        re.DOTALL,
    )
    if not match:
        logger.error("Could not find __INITIAL_STATE__ in page. Are your cookies valid?")
        logger.error("HTTP status: %s", resp.status_code)
        logger.error("Page snippet: %s", resp.text[:500])
        sys.exit(1)

    raw_json = match.group(1)
    raw_json = re.sub(r"\bundefined\b", "null", raw_json)
    return json.loads(raw_json)
