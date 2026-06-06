# xhs-scraper

Scrape Xiaohongshu (小红书 / Rednote) posts from the command line. Extracts post metadata, downloads images, scrapes comments via Playwright, runs AI analysis (OCR, transcription, insights) via Gemini, and includes a local chat interface for Q&A over any scraped post.

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

**Add AI analysis (image OCR / video transcription + insights):**
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

**All options together:**
```bash
python3 scrape.py "<url>" --analyze --comments 50 --replies 10
```

**Backfill insights on an already-analyzed post:**
```bash
python3 scrape.py "<url>" --insights-only
```
Skips if insights already exist. Useful when `--analyze` succeeded but the insights call failed due to a token quota error (see [Rate limits](#rate-limits) below).

**Save to a specific directory:**
```bash
python3 scrape.py "<url>" --data-dir /path/to/data
```

---

## Data flow & LLM calls

Understanding what happens at each step and when Gemini is called:

### Without any flags (metadata only)
```
HTTP GET post URL
  → extract window.__INITIAL_STATE__ from HTML
  → parse title, body, author, tags, engagement stats, image URLs, video URL
  → download images to {post_id}/images/
  → save post.json, update index.json
```
**LLM calls: 0**

---

### `--analyze` (image post)
```
[existing images on disk]
  → Call 1 — multimodal (images + text prompt) → Gemini
      extracts: full_text (OCR across all images), per-image descriptions,
                summary, content_type
  → Call 2 — text only → Gemini (_run_insights)
      input: full_text from Call 1
      produces: 5 deep Q&A pairs, value_verdict, TL;DW bullets
  → all results saved to post.json under analysis{}
```
**LLM calls: 2** (1 multimodal vision + 1 text)

The multimodal call sees the actual images. Everything after that — including insights — works from the extracted text, not the images directly.

---

### `--analyze` (video post)
```
video URL
  → download video to temp file
  → upload to Gemini Files API, poll until PROCESSING complete
  → Call 1 — multimodal (video file + text prompt) → Gemini
      extracts: timestamped transcription (speaker, text, translation),
                onscreen_text, visual description, summary, content_type
  → temp file deleted immediately
  → Call 2 — text only → Gemini (_run_insights)
      input: joined transcript lines from Call 1
      produces: 5 deep Q&A pairs, value_verdict, TL;DW bullets
  → all results saved to post.json under analysis{}
```
**LLM calls: 2** (1 multimodal vision + 1 text)

Note: the insights call works from the transcript (text), not the video itself. The multimodal call is used to handle visual elements like on-screen text overlays, speaker identification, and scene description — not just audio transcription.

---

### `--comments N --replies K`
```
Playwright browser (authenticated)
  → intercept /api/sns/web/v2/comment/page API responses while scrolling
  → collect up to N top-level comments, sort by likes
  → if --replies K:
      heuristic: score comments by (likes × reply_count), take top K
      Call 1 — text only → Gemini (_llm_select_top_n)
          input: K candidate comments as JSON
          picks: which threads have the most insightful discussion
      surface pre-loaded replies for selected threads
  → save comments.json
```
**LLM calls: 0** without `--replies`, **1 text call** with `--replies K`

---

### `insights` block in `post.json`

The `analysis.insights` field produced by `_run_insights` has three parts:

```json
"insights": {
  "questions": [
    { "question": "What is the central argument?", "answer": "..." },
    ...
  ],
  "value_verdict": "The core idea is a repackaging of the creator economy thesis — the consumption vs. creation framing is not novel, but the application to attention economics adds some practical specificity.",
  "tldw": [
    "Consuming content transfers your attention (and money) to creators; creating content is the only way to be on the receiving end.",
    "Writing is framed as the highest-leverage creative act because it compounds — one piece captures attention repeatedly.",
    "The post is motivational rather than instructional; it argues for a mindset shift but offers limited concrete steps."
  ]
}
```

The `value_verdict` is intentionally critical — it assesses whether the content adds genuine intellectual value or recycles familiar ideas. The `tldw` bullets are opinionated, not neutral summaries. Together they act as a gatekeeper: enough information to decide whether to actually watch or read the full post.

All three fields are passed to the chat system prompt, giving the chatbot a critical foundation for intellectually substantive conversation about the post.

---

## Chat interface

A local web app for Q&A over any scraped post.

**Start the server:**
```bash
python3 -m uvicorn serve:app --reload
```

Then open [http://localhost:8000](http://localhost:8000).

### How chat works

```
User selects a post
  → build_system_prompt() assembles context from post.json + comments.json:
      POST metadata (title, body, author, tags, stats)
      VIDEO TRANSCRIPT or EXTRACTED IMAGE TEXT  (if --analyze was run)
      VALUE VERDICT + TL;DW + KEY INSIGHTS      (if --analyze was run)
      TOP COMMENTS                              (if --comments was run)
  → context sent as Gemini system instruction (set once per session)
  → each user message appended to conversation history
  → Gemini responds grounded in the assembled context
```

**LLM calls: 1 per message** (text only — images are not re-sent during chat, only the extracted text from `--analyze`)

The sidebar shows `analyzed` / `comments` / `video` badges so you can see at a glance what context is available for each post. Posts without `--analyze` can still be chatted with, but image/video content will be invisible to the model.

---

## Output

Each scraped post is saved to a folder named by post ID:

```
<post_id>/
  post.json        # metadata + Gemini analysis + insights
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

**Image posts:**
```json
"analysis": {
  "content_type": "text carousel",
  "summary": "...",
  "image_analysis": [
    { "index": 1, "extracted_text": "INTJ与INTP | INTJ and INTP...", "description": "..." }
  ],
  "full_text": "INTJ与INTP\n天才差距\n...",
  "insights": {
    "questions": [{ "question": "...", "answer": "..." }],
    "value_verdict": "...",
    "tldw": ["...", "...", "..."]
  },
  "model": "gemini-2.5-flash",
  "analyzed_at": "2026-06-01 21:30:00"
}
```

**Video posts:**
```json
"analysis": {
  "content_type": "motivational clip",
  "summary": "...",
  "transcription": [
    { "time": "00:00", "speaker": "Jensen Huang", "text": "...", "translation": "..." }
  ],
  "onscreen_text": ["那个30岁的人不听任何人的话"],
  "description": "An older man in a black leather jacket speaking...",
  "insights": {
    "questions": [{ "question": "...", "answer": "..." }],
    "value_verdict": "...",
    "tldw": ["...", "...", "..."]
  },
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

---

## Gemini API key

Get a free key at [aistudio.google.com](https://aistudio.google.com) → Get API key.

The free tier supports video and image input with no billing required (rate-capped at 500 requests/day).

Add it to your shell profile for persistence:
```bash
echo 'export GEMINI_API_KEY=your_key_here' >> ~/.zshrc
source ~/.zshrc
```

## Rate limits

The Gemini free tier enforces a **250k input token/minute** cap per model. This rarely affects image posts, but large video analyses can exhaust the budget: a 96 MB video produces ~187 transcript lines, which when fed into the insights call immediately after can trigger a `RESOURCE_EXHAUSTED` 429 error.

**How we handle it**: `_run_insights` parses the `retryDelay` from the 429 response and does one automatic retry after that delay. If the retry also fails, insights are skipped and the post is saved without them — you can regenerate later with `--insights-only`.

**System design strategies for this limit:**

| Strategy | Notes |
|---|---|
| **Retry with suggested delay** | Implemented — the API error includes the exact seconds to wait |
| **Decouple the calls** | `--insights-only` separates the expensive vision call from the text-only insights call, spreading token usage across time |
| **Lighter model for text-only calls** | `_run_insights` and `_llm_select_top_n` are text-only and don't need `gemini-2.5-flash`; `gemini-2.0-flash` has a higher free-tier RPM limit and would reduce contention |
| **Truncate insight inputs** | Insight quality doesn't scale linearly with transcript length — truncating to the first ~8k characters would reduce token usage with minimal quality loss |
| **Upgrade to paid tier** | Paid tier is 4M input tokens/min vs 250k/min free — eliminates the problem entirely |

## Notes

- **Cookies expire** — if scraping stops working, re-run `python3 scrape.py --setup` to refresh them
- **Video analysis** downloads the video to a temp file, uploads it to Gemini, then deletes it immediately — no permanent disk usage
- **Image analysis** uses already-downloaded images — no extra network requests
- **`--debug`** saves `debug_state.json` with the raw `__INITIAL_STATE__` blob — useful if extraction fails
- **Existing posts** analyzed before `--analyze` was added will not have an `insights` block; re-run `--analyze` to generate one
