# xhs-scraper

Scrape Xiaohongshu (小红书 / Rednote) posts from the command line. Extracts post metadata, downloads images, scrapes comments via Playwright, runs AI analysis (OCR, transcription) via Gemini, and includes a local chat interface for Q&A over any scraped post.

## How it works

When Xiaohongshu serves a post page, it embeds the full post data as `window.__INITIAL_STATE__` in the HTML. The scraper fetches that HTML, extracts the JSON blob, and parses it — no headless browser required for metadata. Playwright is used only for comment scraping (XHS's comment API is only accessible from an authenticated browser session).

## Setup

**1. Install dependencies**
```bash
pip3 install -r requirements.txt
playwright install chromium
```

**2. Set up cookies**

Run the interactive setup wizard:
```bash
python3 scrape.py --setup
```

This will instruct you to:
1. Open Chrome and go to `https://www.xiaohongshu.com`
2. Open DevTools Console (`F12` → Console tab)
3. Run `copy(document.cookie)` to copy your session cookies
4. Paste into the terminal and press Enter twice

Cookies are saved to `~/cookies_rednote.json`.

## Usage

Always wrap URLs in quotes — the `&` in query strings will break the shell otherwise. URLs from `rednote.com` are also accepted.

**Scrape metadata + images:**
```bash
python3 scrape.py "https://www.xiaohongshu.com/explore/<post_id>?..."
```

**Add AI analysis (image OCR / video transcription):**
```bash
python3 scrape.py "<url>" --analyze
```

**Scrape top comments (sorted by likes):**
```bash
python3 scrape.py "<url>" --comments 50
```

**Scrape comments + enrich top reply threads:**
```bash
python3 scrape.py "<url>" --comments 50 --replies 10
```
`--replies K` uses a heuristic to find the K most promising threads, then Gemini picks the best ones to surface pre-loaded replies for.

**All options together:**
```bash
python3 scrape.py "<url>" --analyze --comments 50 --replies 10
```

**Save to a specific directory:**
```bash
python3 scrape.py "<url>" --data-dir /path/to/data
```

## Output

Each scraped post is saved to a folder named by post ID:

```
<post_id>/
  post.json        # metadata + Gemini analysis
  comments.json    # top comments (if --comments was used)
  images/
    01.jpg
    02.jpg
    ...

index.json         # registry of all scraped posts
```

### post.json structure

```json
{
  "id": "6a1c492f0000000007013fae",
  "title": "INTJ与INTP天才差距",
  "body": "...",
  "type": "normal",
  "author": "阿涂",
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
  "onscreen_text": ["那个30岁的人不听任何人的话"],
  "description": "An older man in a black leather jacket speaking...",
  "model": "gemini-2.5-flash",
  "analyzed_at": "2026-06-01 20:00:00"
}
```

### comments.json structure

```json
{
  "post_id": "6a1c492f0000000007013fae",
  "scraped_at": "2026-06-03 10:00:00",
  "total_scraped": 42,
  "sort": "like_count_desc",
  "comments": [
    {
      "id": "...",
      "content": "这个太有共鸣了",
      "nickname": "用户A",
      "like_count": 128,
      "posted_at": "2026-06-01 09:15:00",
      "ip_location": "上海",
      "sub_comment_count": 5,
      "replies": [...]
    }
  ]
}
```

## Chat interface

A local web app for Q&A over any scraped post — ask about post content, comments, image text, or video timestamps.

**Start the server:**
```bash
python3 -m uvicorn serve:app --reload
```

Then open [http://localhost:8000](http://localhost:8000).

The sidebar lists all scraped posts with badges showing what data is available (`analyzed`, `comments`, `video`). Select a post and start asking questions. The chat context is built from `post.json` + `comments.json` and sent to Gemini as a system prompt, so the model only answers from what was actually scraped.

Requires `GEMINI_API_KEY` to be set (same key as `--analyze`).

## Gemini API key

Get a free key at [aistudio.google.com](https://aistudio.google.com) → Get API key.

The free tier supports video and image input with no billing required (rate-capped at 500 requests/day).

Add it to your shell profile for persistence:
```bash
echo 'export GEMINI_API_KEY=your_key_here' >> ~/.zshrc
source ~/.zshrc
```

## Notes

- **Cookies expire** — if scraping stops working, re-run `python3 scrape.py --setup` to refresh them
- **Video analysis** downloads the video to a temp file, uploads it to Gemini, then deletes it immediately — no permanent disk usage
- **Image analysis** uses already-downloaded images, so no extra network requests
- **`--debug`** saves `debug_state.json` with the raw `__INITIAL_STATE__` blob — useful if extraction fails
