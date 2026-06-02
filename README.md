# xhs-scraper

Scrape Xiaohongshu (小红书 / Rednote) posts from the command line. Extracts post metadata, downloads images, and optionally runs AI analysis (transcription, OCR, description) via Google Gemini.

No browser or Playwright needed — works with a single HTTP request using your browser cookies.

## How it works

When Xiaohongshu serves a post page, it embeds the full post data as `window.__INITIAL_STATE__` in the HTML (server-side rendering). The script fetches that HTML, extracts the JSON blob, and parses it — no headless browser required.

## Setup

**1. Install dependencies**
```bash
pip3 install requests

# Only needed for --analyze
pip3 install google-genai
```

**2. Set up cookies**

You must be logged in to Xiaohongshu. Run the interactive setup:
```bash
python3 scrape.py --setup
```

This will instruct you to:
1. Open Chrome and go to `https://www.xiaohongshu.com`
2. Open DevTools Console (`F12` → Console tab)
3. Run `copy(document.cookie)` — this copies your cookies to clipboard
4. Paste the cookie string into the terminal and press Enter twice

Cookies are saved to `~/cookies_rednote.json`.

## Usage

**Scrape a post:**
```bash
python3 scrape.py "https://www.xiaohongshu.com/explore/<post_id>?..."
```

Always wrap the URL in quotes — the `&` in query strings will break the shell otherwise.

**Scrape with AI analysis:**
```bash
GEMINI_API_KEY=your_key python3 scrape.py "https://www.xiaohongshu.com/explore/<post_id>?..." --analyze
```

> Note: URLs from `rednote.com` work too — the script accepts them but internally uses `xiaohongshu.com`. Copy URLs directly from the browser address bar to avoid line-break issues.

## Output

Each scraped post is saved to a folder named by post ID:

```
<post_id>/
  post.json     # all metadata + analysis
  01.jpg        # downloaded images
  02.jpg
  ...
```

### post.json structure

```json
{
  "id": "6a1c492f0000000007013fae",
  "title": "INTJ与INTP天才差距",
  "body": "...",
  "type": "normal",
  "author": "阿涂",
  "author_id": "5cc7c410000000001201e604",
  "posted_at": "2026-05-31 10:43:59",
  "ip_location": "广东",
  "likes": "324",
  "collects": "221",
  "comments": "40",
  "shares": "73",
  "tags": ["MBTI", "INTJ"],
  "images": ["https://..."],
  "video": null,
  "analysis": { ... }
}
```

### analysis block (with --analyze)

**Image posts** — OCR and visual description of each image:
```json
"analysis": {
  "content_type": "text carousel",
  "summary": "This post compares INTJ and INTP personality types...",
  "image_analysis": [
    {
      "index": 1,
      "extracted_text": "INTJ与INTP | INTJ and INTP...",
      "description": "Cover image with travel-themed background..."
    }
  ],
  "full_text": "INTJ与INTP\n天才差距\n...",
  "model": "gemini-2.5-flash",
  "analyzed_at": "2026-06-01 21:30:00"
}
```

**Video posts** — speech transcription with timestamps and speaker labels:
```json
"analysis": {
  "content_type": "motivational clip",
  "summary": "Jensen Huang reflects on advice for his 30-year-old self...",
  "transcription": [
    {
      "time": "00:00",
      "speaker": "Jensen Huang",
      "text": "First of all, that 30-year-old isn't listening to anybody.",
      "translation": "First of all, that 30-year-old isn't listening to anybody."
    }
  ],
  "onscreen_text": ["那个30岁的人不听任何人的话", "你做得对"],
  "description": "An older man in a black leather jacket speaking...",
  "model": "gemini-2.5-flash",
  "analyzed_at": "2026-06-01 20:00:00"
}
```

## Gemini API key

Get a free key at [aistudio.google.com](https://aistudio.google.com) → Get API key.

The free tier supports video and image input with no billing required (hard rate-capped at 500 requests/day). Note that free tier usage may be used to improve Google's models.

Export the key before running:
```bash
export GEMINI_API_KEY=your_key_here
python3 scrape.py "<url>" --analyze
```

## Notes

- **Cookies expire** — if scraping stops working, re-run `python3 scrape.py --setup` to refresh them
- **Video analysis** downloads the video to a temp file, uploads it to Gemini, then deletes the temp file immediately — no permanent disk usage from video
- **Image analysis** uses already-downloaded images, so no extra downloads
- `debug_state.json` is written on every run with the raw `__INITIAL_STATE__` blob — useful if extraction fails
