#!/usr/bin/env python3
"""
Rednote post scraper using __INITIAL_STATE__ extraction.
No browser needed — just cookies + one HTTP request.

Usage:
    python3 scrape.py <post_url>
    python3 scrape.py <post_url> --analyze   # also transcribe/describe video via Gemini
    python3 scrape.py --setup                # walk through cookie setup

Environment:
    GEMINI_API_KEY   required for --analyze
"""

import sys
import re
import json
import os
import pathlib
import datetime
import tempfile
import time

COOKIE_FILE = pathlib.Path.home() / "cookies_rednote.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.xiaohongshu.com/",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


# ── Cookie helpers ────────────────────────────────────────────────────────────

def load_cookies() -> dict:
    if not COOKIE_FILE.exists():
        print(f"[!] Cookie file not found at {COOKIE_FILE}")
        print("    Run:  python3 scrape.py --setup")
        sys.exit(1)
    return json.loads(COOKIE_FILE.read_text())


def setup_cookies():
    print("""
Cookie setup (30 seconds):

1. Open Chrome and go to:  https://www.xiaohongshu.com
   Make sure you're logged in.

2. Open DevTools Console (F12 → Console tab)

3. Paste this and press Enter:
   copy(document.cookie)

4. Come back here and paste the cookie string below, then press Enter twice.
""")
    lines = []
    while True:
        line = input()
        if line == "" and lines:
            break
        lines.append(line)

    raw = " ".join(lines).strip()
    cookies = {}
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            cookies[k.strip()] = v.strip()

    COOKIE_FILE.write_text(json.dumps(cookies, indent=2, ensure_ascii=False))
    print(f"\n[✓] Saved {len(cookies)} cookies to {COOKIE_FILE}")
    return cookies


# ── Core extraction ───────────────────────────────────────────────────────────

def fetch_initial_state(url: str, cookies: dict) -> dict:
    import requests

    resp = requests.get(url, headers=HEADERS, cookies=cookies, timeout=15)
    resp.raise_for_status()

    # The pattern: window.__INITIAL_STATE__=<json>;
    # Rednote sometimes uses undefined for missing fields, so we patch that.
    match = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})(?:;|\s*</script>)", resp.text, re.DOTALL)
    if not match:
        print("[!] Could not find __INITIAL_STATE__ in page. Are your cookies valid?")
        print(f"    HTTP status: {resp.status_code}")
        print(f"    Page snippet: {resp.text[:500]}")
        sys.exit(1)

    raw_json = match.group(1)
    # Replace JS `undefined` with null so Python's json parser accepts it
    raw_json = re.sub(r'\bundefined\b', 'null', raw_json)
    return json.loads(raw_json)


def extract_post(state: dict) -> dict:
    """Pull the relevant fields out of the raw __INITIAL_STATE__ blob."""
    note_map = (
        state
        .get("note", {})
        .get("noteDetailMap", {})
    )
    if not note_map:
        return {}

    # There's usually one key in noteDetailMap — the note ID
    note_id = next(iter(note_map))
    raw = note_map[note_id].get("note", {})

    interact = raw.get("interactInfo") or {}
    user = raw.get("user") or {}
    ts = raw.get("time")

    post = {
        "id": note_id,
        "title": raw.get("title", ""),
        "body": raw.get("desc", ""),
        "type": raw.get("type", "normal"),   # "normal" | "video"
        "author": user.get("nickname", ""),
        "author_id": user.get("userId", ""),
        "posted_at": datetime.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S") if ts else "",
        "ip_location": raw.get("ipLocation", ""),
        "likes": interact.get("likedCount", "?"),
        "collects": interact.get("collectedCount", "?"),
        "comments": interact.get("commentCount", "?"),
        "shares": interact.get("shareCount", "?"),
        "tags": [t.get("name", "") for t in (raw.get("tagList") or [])],
        "images": [],
        "video": None,
        "analysis": None,
    }

    # Images
    for img in raw.get("imageList") or []:
        url = (img.get("urlDefault") or img.get("url") or "")
        if url:
            post["images"].append(url)

    # Video
    video = raw.get("video") or {}
    media = video.get("media") or {}
    stream = media.get("stream") or {}
    for quality in ("h264", "h265", "av1"):
        segments = stream.get(quality) or []
        if segments:
            post["video"] = segments[0].get("masterUrl") or segments[0].get("backupUrls", [None])[0]
            break

    return post


# ── Image download ────────────────────────────────────────────────────────────

def download_images(post: dict):
    import requests
    folder = pathlib.Path(post["id"])
    folder.mkdir(exist_ok=True)
    print(f"[→] Downloading {len(post['images'])} image(s) to ./{post['id']}/")
    for i, url in enumerate(post["images"]):
        dest = folder / f"{i+1:02d}.jpg"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        print(f"    [{i+1}] saved {dest}")


# ── Gemini video analysis ─────────────────────────────────────────────────────

def analyze_video(post: dict) -> dict:
    """Download video to a temp file, upload to Gemini, return analysis dict."""
    import requests
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[!] GEMINI_API_KEY env var not set. Export it and retry.")
        sys.exit(1)

    video_url = post["video"]
    if not video_url:
        print("[!] No video URL found in this post.")
        return {}

    client = genai.Client(api_key=api_key)

    # Download to temp file
    print(f"[→] Downloading video for analysis (temp, will be deleted)...")
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_path = tmp.name
        resp = requests.get(video_url, headers=HEADERS, stream=True, timeout=60)
        resp.raise_for_status()
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            tmp.write(chunk)
    size_mb = pathlib.Path(tmp_path).stat().st_size / 1024 / 1024
    print(f"[→] Downloaded {size_mb:.1f} MB — uploading to Gemini Files API...")

    try:
        # Upload to Gemini Files API
        with open(tmp_path, "rb") as f:
            video_file = client.files.upload(
                file=f,
                config={"mime_type": "video/mp4"},
            )

        # Wait for Gemini to process the file
        while video_file.state.name == "PROCESSING":
            print("[→] Waiting for Gemini to process video...")
            time.sleep(3)
            video_file = client.files.get(name=video_file.name)

        if video_file.state.name == "FAILED":
            print("[!] Gemini file processing failed.")
            return {}

        print("[→] Analyzing with Gemini 2.5 Flash...")
        prompt = """This is a video from Xiaohongshu (Little Red Book), a Chinese social media platform.

Analyze the video and return a JSON object with exactly these keys:

"transcription": array of objects, one per speaker turn, each with:
  - "time": timestamp in MM:SS format (when this line starts)
  - "speaker": name of the speaker if identifiable from visuals, otherwise "Speaker 1", "Speaker 2", etc.
  - "text": the spoken words in the original language
  - "translation": English translation (if already English, repeat the same text)

"summary": a 2-3 sentence summary in English of what the video is about and its main message

"content_type": a short label for the type of video, e.g. "motivational clip", "beauty tutorial", "food vlog", "interview", "product review", "travel vlog", "comedy skit", etc.

"onscreen_text": array of strings — any text visible on screen (subtitles, titles, captions, text overlays), in the order they appear. Include both Chinese and English if both are shown.

"description": a detailed visual description of scenes, actions, people, and setting.

Return only valid JSON with no markdown formatting."""

        video_schema = {
            "type": "object",
            "properties": {
                "content_type": {"type": "string"},
                "summary": {"type": "string"},
                "transcription": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "time": {"type": "string"},
                            "speaker": {"type": "string"},
                            "text": {"type": "string"},
                            "translation": {"type": "string"},
                        },
                    },
                },
                "onscreen_text": {"type": "array", "items": {"type": "string"}},
                "description": {"type": "string"},
            },
        }
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                {"role": "user", "parts": [
                    {"file_data": {"file_uri": video_file.uri, "mime_type": "video/mp4"}},
                    {"text": prompt},
                ]},
            ],
            config={"response_mime_type": "application/json", "response_schema": video_schema},
        )

        result = parse_gemini_json(response.text)

        result["model"] = "gemini-2.5-flash"
        result["analyzed_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Clean up uploaded file from Gemini
        client.files.delete(name=video_file.name)

    finally:
        os.unlink(tmp_path)
        print(f"[→] Temp video file deleted.")

    return result


# ── JSON repair helper ───────────────────────────────────────────────────────

def repair_json(s: str) -> str:
    """Escape literal newlines/carriage returns inside JSON string values."""
    result = []
    in_string = False
    i = 0
    while i < len(s):
        c = s[i]
        if c == '"' and (i == 0 or s[i - 1] != '\\'):
            in_string = not in_string
        if in_string and c == '\n':
            result.append('\\n')
        elif in_string and c == '\r':
            result.append('\\r')
        else:
            result.append(c)
        i += 1
    return ''.join(result)


def parse_gemini_json(raw_text: str) -> dict:
    raw_text = raw_text.strip()
    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
    raw_text = re.sub(r"\s*```$", "", raw_text)
    # Gemini sometimes double-escapes quotes inside strings: \\" → \"
    repairs = [
        lambda s: s,
        lambda s: s.replace('\\\\"', '\\"'),
        repair_json,
        lambda s: repair_json(s.replace('\\\\"', '\\"')),
    ]
    for repair in repairs:
        try:
            return json.loads(repair(raw_text))
        except json.JSONDecodeError:
            continue
    return {"raw": raw_text}


# ── Gemini image analysis ─────────────────────────────────────────────────────

def analyze_images(post: dict) -> dict:
    """Send downloaded images to Gemini, extract text and descriptions."""
    import base64
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[!] GEMINI_API_KEY env var not set. Export it and retry.")
        sys.exit(1)

    folder = pathlib.Path(post["id"])
    image_paths = sorted(folder.glob("*.jpg"))
    if not image_paths:
        print("[!] No downloaded images found — run without --analyze first or images failed to download.")
        return {}

    client = genai.Client(api_key=api_key)
    print(f"[→] Analyzing {len(image_paths)} image(s) with Gemini 2.5 Flash...")

    # Build parts: one instruction block, then all images labelled by index
    parts = []
    for i, path in enumerate(image_paths):
        data = base64.b64encode(path.read_bytes()).decode()
        parts.append({
            "text": f"Image {i+1}:"
        })
        parts.append({
            "inline_data": {"mime_type": "image/jpeg", "data": data}
        })

    prompt = f"""This post has {len(image_paths)} image(s) from Xiaohongshu (Little Red Book), a Chinese social media platform.

For each image, extract and analyze its content. Return a JSON object with:

"content_type": a short label for what kind of post this is, e.g. "text carousel", "photo album", "infographic", "recipe", "tutorial", "meme", etc.

"summary": a 2-3 sentence summary in English of the overall post content and main message across all images.

"image_analysis": array of objects (one per image, in order), each with:
  - "index": image number (1-based)
  - "extracted_text": ALL text visible in the image — titles, body text, captions, watermarks, labels. Preserve original language. If Chinese, also provide English translation separated by " | ". If no text, use empty string.
  - "description": brief description of what the image shows visually (layout, colors, people, objects, etc.)

"full_text": a single string concatenating all extracted text across all images in order, separated by newlines. This should read like the full written content of the post.

Return only valid JSON with no markdown formatting."""

    parts.append({"text": prompt})

    image_schema = {
        "type": "object",
        "properties": {
            "content_type": {"type": "string"},
            "summary": {"type": "string"},
            "image_analysis": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "extracted_text": {"type": "string"},
                        "description": {"type": "string"},
                    },
                },
            },
            "full_text": {"type": "string"},
        },
    }
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[{"role": "user", "parts": parts}],
        config={"response_mime_type": "application/json", "response_schema": image_schema},
    )

    result = parse_gemini_json(response.text)
    result["model"] = "gemini-2.5-flash"
    result["analyzed_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return result


# ── Output ────────────────────────────────────────────────────────────────────

def save_post_json(post: dict):
    folder = pathlib.Path(post["id"])
    folder.mkdir(exist_ok=True)
    dest = folder / "post.json"
    dest.write_text(json.dumps(post, indent=2, ensure_ascii=False))
    print(f"[→] Saved metadata to {dest}")


def log_url(post: dict, url: str):
    log_file = pathlib.Path("scraped_links.json")
    entries = json.loads(log_file.read_text()) if log_file.exists() else []

    # Update existing entry if post ID already present, otherwise append
    ids = [e["post_id"] for e in entries]
    entry = {
        "url": url,
        "post_id": post["id"],
        "title": post["title"],
        "type": post["type"],
        "author": post["author"],
        "scraped_at": datetime.datetime.now().strftime("%Y-%m-%d"),
    }
    if post["id"] in ids:
        entries[ids.index(post["id"])] = entry
    else:
        entries.append(entry)

    log_file.write_text(json.dumps(entries, indent=2, ensure_ascii=False))
    print(f"[→] Logged to scraped_links.json ({len(entries)} total)")


def print_post(post: dict):
    sep = "─" * 60
    print(f"\n{sep}")
    print(f"  ID:        {post['id']}")
    print(f"  Type:      {post['type']}")
    print(f"  Author:    {post['author']} ({post['author_id']})")
    print(f"  Posted:    {post['posted_at']}  [{post['ip_location']}]")
    print(f"  Title:     {post['title']}")
    print(f"  Likes:     {post['likes']}  Collects: {post['collects']}  Comments: {post['comments']}  Shares: {post['shares']}")
    print(f"  Tags:      {', '.join(post['tags'])}")
    print(f"\n  Body:\n  {post['body']}")
    print(f"\n  Images ({len(post['images'])}):")
    for i, u in enumerate(post["images"]):
        print(f"    [{i+1}] {u[:90]}...")
    if post["video"]:
        print(f"\n  Video URL:\n    {post['video'][:100]}...")
    if post["analysis"]:
        a = post["analysis"]
        print(f"\n  Analysis (Gemini):")
        if "content_type" in a:
            print(f"    Type:        {a['content_type']}")
        if "summary" in a:
            print(f"    Summary:     {a['summary']}")
        if "transcription" in a and isinstance(a["transcription"], list):
            print(f"    Transcription ({len(a['transcription'])} lines):")
            for line in a["transcription"][:5]:
                print(f"      [{line.get('time','?')}] {line.get('speaker','?')}: {line.get('text','')[:80]}")
            if len(a["transcription"]) > 5:
                print(f"      ... and {len(a['transcription']) - 5} more lines")
        if "onscreen_text" in a and a["onscreen_text"]:
            print(f"    On-screen text ({len(a['onscreen_text'])} items): {a['onscreen_text'][0][:60]}...")
        if "image_analysis" in a and isinstance(a["image_analysis"], list):
            print(f"    Image text ({len(a['image_analysis'])} image(s)):")
            for img in a["image_analysis"]:
                text_preview = (img.get("extracted_text") or "")[:80]
                print(f"      [Image {img.get('index')}] {text_preview}{'...' if len(img.get('extracted_text','')) > 80 else ''}")
        if "full_text" in a and a["full_text"]:
            print(f"    Full text preview: {a['full_text'][:200]}...")
    print(sep)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if not args or args[0] == "--setup":
        if not args:
            print("Usage: python3 scrape.py <rednote_post_url> [--analyze]")
            print("       python3 scrape.py --setup")
            sys.exit(0)
        setup_cookies()
        return

    analyze = "--analyze" in args
    url = next(a for a in args if not a.startswith("--"))
    cookies = load_cookies()

    print(f"[→] Fetching: {url}")
    state = fetch_initial_state(url, cookies)

    # Dump raw state to file for inspection
    debug_file = pathlib.Path("debug_state.json")
    debug_file.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    print(f"[→] Raw state saved to {debug_file} (for debugging)")

    post = extract_post(state)
    if not post:
        print("[!] Could not extract post data. Inspect debug_state.json to see what the state looks like.")
        sys.exit(1)

    if post["images"]:
        download_images(post)

    if analyze:
        if post["type"] == "video" and post["video"]:
            post["analysis"] = analyze_video(post)
        elif post["images"]:
            post["analysis"] = analyze_images(post)
        else:
            print("[!] --analyze was passed but this post has no images or video — skipping analysis.")

    print_post(post)
    save_post_json(post)
    log_url(post, url)


if __name__ == "__main__":
    main()
