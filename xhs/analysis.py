import base64
import datetime
import json
import logging
import os
import pathlib
import re
import sys
import tempfile
import time

from . import config

logger = logging.getLogger("xhs.analysis")


def _make_gemini_client():
    from google import genai
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY env var not set. Export it and retry.")
        sys.exit(1)
    return genai.Client(api_key=api_key)


def repair_json(s: str) -> str:
    """Escape literal newlines/carriage returns inside JSON string values."""
    result = []
    in_string = False
    i = 0
    while i < len(s):
        c = s[i]
        if c == '"' and (i == 0 or s[i - 1] != "\\"):
            in_string = not in_string
        if in_string and c == "\n":
            result.append("\\n")
        elif in_string and c == "\r":
            result.append("\\r")
        else:
            result.append(c)
        i += 1
    return "".join(result)


def parse_gemini_json(raw_text: str) -> dict:
    raw_text = raw_text.strip()
    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
    raw_text = re.sub(r"\s*```$", "", raw_text)
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


def analyze_video(post: dict, data_dir: pathlib.Path) -> dict:
    import requests as req
    client = _make_gemini_client()

    video_url = post["video"]
    if not video_url:
        logger.warning("No video URL found in post.")
        return {}

    logger.info("Downloading video for analysis (temp, will be deleted)...")
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_path = tmp.name
        session = req.Session()
        session.headers.update(config.HEADERS)
        resp = session.get(video_url, stream=True, timeout=60)
        resp.raise_for_status()
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            tmp.write(chunk)

    size_mb = pathlib.Path(tmp_path).stat().st_size / 1024 / 1024
    logger.info("Downloaded %.1f MB — uploading to Gemini Files API...", size_mb)

    try:
        with open(tmp_path, "rb") as f:
            video_file = client.files.upload(file=f, config={"mime_type": "video/mp4"})

        while video_file.state.name == "PROCESSING":
            logger.info("Waiting for Gemini to process video...")
            time.sleep(3)
            video_file = client.files.get(name=video_file.name)

        if video_file.state.name == "FAILED":
            logger.error("Gemini file processing failed.")
            return {}

        MAX_RETRIES = 6
        RETRY_DELAYS = [15, 30, 60, 120, 180, 300]
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
        from google.genai import errors as genai_errors
        FALLBACK_MODELS = [config.GEMINI_MODEL, "gemini-1.5-flash-002"]
        response = None
        for model_name in FALLBACK_MODELS:
            for attempt in range(MAX_RETRIES):
                try:
                    logger.info("Analyzing with %s (attempt %d)...", model_name, attempt + 1)
                    response = client.models.generate_content(
                        model=model_name,
                        contents=[
                            {"role": "user", "parts": [
                                {"file_data": {"file_uri": video_file.uri, "mime_type": "video/mp4"}},
                                {"text": prompt},
                            ]},
                        ],
                        config={"response_mime_type": "application/json", "response_schema": video_schema},
                    )
                    break
                except (genai_errors.ServerError, genai_errors.ClientError) as exc:
                    is_quota = isinstance(exc, genai_errors.ClientError) and "RESOURCE_EXHAUSTED" in str(exc)
                    if is_quota:
                        logger.warning("Model %s: quota exhausted on free tier — skipping.", model_name)
                        break
                    if attempt == MAX_RETRIES - 1:
                        logger.warning("Model %s exhausted retries — trying next model.", model_name)
                    else:
                        delay = RETRY_DELAYS[attempt]
                        logger.warning("Gemini 503 on %s (attempt %d/%d) — retrying in %ds...", model_name, attempt + 1, MAX_RETRIES, delay)
                        time.sleep(delay)
            if response is not None:
                break
        if response is None:
            logger.error("All models failed. Video analysis skipped — run --analyze again later.")
            return {}
        result = parse_gemini_json(response.text)
        result["model"] = config.GEMINI_MODEL
        result["analyzed_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        client.files.delete(name=video_file.name)

        transcript_text = "\n".join(
            f"[{t.get('time', '?')}] {t.get('speaker', 'Speaker')}: {t.get('text', '')}"
            for t in result.get("transcription") or []
        )
        input_text = transcript_text or post.get("body", "")
        logger.info("Generating insights...")
        result["insights"] = _run_insights(input_text, client)

    finally:
        os.unlink(tmp_path)
        logger.info("Temp video file deleted.")

    return result


def _run_insights(text: str, client) -> dict:
    """
    Deep analysis of post text: 5 Q&A pairs, a first-principles value verdict,
    and a 3-bullet TL;DW summary.
    """
    if not text or not text.strip():
        return {}

    prompt = f"""You are a rigorous intellectual analyst and gatekeeper. A user is deciding whether this content is worth their time. Your job is to evaluate it honestly and critically — most content recycles familiar ideas in new packaging, and you should say so plainly when that's the case.

Analyze the following text in three parts:

PART 1 — 5 Essential Questions
Generate exactly 5 deep questions that together capture the full intellectual substance of this content. Go beyond surface-level summaries. Good questions expose the author's assumptions, test the strength of their argument, surface what's actually novel vs. well-known, and draw out implications the author may not have stated explicitly. Answer each question in detail, drawing only from the provided text.

PART 2 — Value Verdict
In 2-3 sentences, assess from first principles: what is the genuine intellectual value of this content? Is the core idea truly novel, or is it a repackaging of existing concepts dressed up in new language? Is the reasoning rigorous or mostly rhetorical? Be direct and honest — a verdict of "low original value" is a valid and useful output.

PART 3 — TL;DW (Too Long; Didn't Watch)
Provide exactly 3 bullet points that capture the ultimate takeaways. These should be decisive and opinionated — not just neutral summaries, but the 3 things that most determine whether this content deserves someone's attention.

PART 4 — Key Quotes
Select exactly 3 verbatim phrases or sentences copied directly from the text above that best encapsulate the essence of the content. These must be literal extracts — do not paraphrase or rewrite. Choose lines that a reader could use as a standalone representation of what this content is fundamentally about.

Text:
{text}"""

    schema = {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "answer": {"type": "string"},
                    },
                },
            },
            "value_verdict": {"type": "string"},
            "tldw": {
                "type": "array",
                "items": {"type": "string"},
            },
            "key_quotes": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
    }
    # The insights call is text-only but still token-heavy (long transcripts).
    # On the free tier (250k input tokens/min) it can hit RESOURCE_EXHAUSTED
    # right after a large video analysis call. We parse the retryDelay from the
    # error and do one retry rather than failing silently.
    def _call():
        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            config={"response_mime_type": "application/json", "response_schema": schema},
        )
        result = json.loads(response.text)
        return result if isinstance(result, dict) else {}

    try:
        return _call()
    except Exception as exc:
        exc_str = str(exc)
        if "RESOURCE_EXHAUSTED" in exc_str:
            match = re.search(r"retry in (\d+)", exc_str)
            wait = int(match.group(1)) + 5 if match else 35
            logger.warning("Token quota hit — retrying insights in %ds...", wait)
            time.sleep(wait)
            try:
                return _call()
            except Exception as exc2:
                logger.warning("Insights generation failed after retry: %s", exc2)
                return {}
        logger.warning("Insights generation failed: %s", exc)
        return {}


def run_insights_for_post(post_id: str, data_dir: pathlib.Path) -> bool:
    """
    Generate insights for an already-analyzed post without re-running the
    expensive vision call. Skips silently if insights already exist.
    Returns True if insights were written, False otherwise.
    """
    post_path = data_dir / post_id / "post.json"
    if not post_path.exists():
        logger.error("No post.json found for %s — scrape the post first.", post_id)
        return False

    post = json.loads(post_path.read_text())
    analysis = post.get("analysis") or {}

    if not analysis:
        logger.error("No analysis found for %s — run --analyze first.", post_id)
        return False

    existing = analysis.get("insights")
    if existing and (isinstance(existing, dict) and existing.get("questions") or
                     isinstance(existing, list) and existing):
        logger.info("Insights already present for %s — skipping.", post_id)
        return False

    if post.get("type") == "video":
        transcript_text = "\n".join(
            f"[{t.get('time', '?')}] {t.get('speaker', 'Speaker')}: {t.get('text', '')}"
            for t in analysis.get("transcription") or []
        )
        input_text = transcript_text or post.get("body", "")
    else:
        input_text = analysis.get("full_text") or post.get("body", "")

    if not input_text:
        logger.error("No text content found for %s to generate insights from.", post_id)
        return False

    client = _make_gemini_client()
    logger.info("Generating insights for %s...", post_id)
    insights = _run_insights(input_text, client)

    if not insights:
        logger.error("Insights generation failed for %s.", post_id)
        return False

    analysis["insights"] = insights
    post["analysis"] = analysis

    import tempfile, os as _os
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=post_path.parent, delete=False, suffix=".tmp"
    ) as tmp:
        tmp.write(json.dumps(post, indent=2, ensure_ascii=False))
        tmp_path = tmp.name
    _os.replace(tmp_path, post_path)
    logger.info("Insights saved to %s", post_path)
    return True


def analyze_images(post: dict, data_dir: pathlib.Path) -> dict:
    client = _make_gemini_client()

    folder = data_dir / post["id"] / "images"
    image_paths = sorted(folder.glob("*.jpg"))
    if not image_paths:
        logger.warning("No downloaded images found in %s", folder)
        return {}

    logger.info("Analyzing %d image(s) with %s...", len(image_paths), config.GEMINI_MODEL)

    parts = []
    for i, path in enumerate(image_paths):
        data = base64.b64encode(path.read_bytes()).decode()
        parts.append({"text": f"Image {i+1}:"})
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": data}})

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
        model=config.GEMINI_MODEL,
        contents=[{"role": "user", "parts": parts}],
        config={"response_mime_type": "application/json", "response_schema": image_schema},
    )
    result = parse_gemini_json(response.text)
    result["model"] = config.GEMINI_MODEL
    result["analyzed_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    input_text = result.get("full_text") or post.get("body", "")
    logger.info("Generating insights...")
    result["insights"] = _run_insights(input_text, client)

    return result
