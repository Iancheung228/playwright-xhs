import json
import pathlib


def build_system_prompt(post_id: str, data_dir: pathlib.Path) -> str:
    post_path = data_dir / post_id / "post.json"
    if not post_path.exists():
        raise FileNotFoundError(f"No post found for ID: {post_id}")

    post = json.loads(post_path.read_text())
    sections = []

    sections.append(
        "You are an AI assistant helping the user explore and understand a Xiaohongshu "
        "(RedNote / Little Red Book) post. Answer questions accurately using only the "
        "information provided below. If something is not in the context, say so clearly."
    )

    tags = ", ".join(post.get("tags", [])) or "none"
    sections.append(f"""
=== POST ===
Title: {post.get("title", "")}
Author: {post.get("author", "")} | Posted: {post.get("posted_at", "")} | Location: {post.get("ip_location", "")}
Likes: {post.get("likes", "?")} | Collects: {post.get("collects", "?")} | Comments: {post.get("comments", "?")} | Shares: {post.get("shares", "?")}
Tags: {tags}

Body:
{post.get("body", "(no body text)")}""")

    analysis = post.get("analysis") or {}
    if analysis:
        if post.get("type") == "video":
            transcript = analysis.get("transcription") or []
            if transcript:
                lines = "\n".join(
                    f"[{t.get('time', '?')}] {t.get('speaker', 'Speaker')}: {t.get('text', '')}"
                    + (
                        f" | {t.get('translation', '')}"
                        if t.get("translation") and t.get("translation") != t.get("text")
                        else ""
                    )
                    for t in transcript
                )
                sections.append(f"\n=== VIDEO TRANSCRIPT ===\n{lines}")
            onscreen = analysis.get("onscreen_text") or []
            if onscreen:
                sections.append("\n=== ON-SCREEN TEXT ===\n" + "\n".join(onscreen))
            if analysis.get("summary"):
                sections.append(f"\n=== VIDEO SUMMARY ===\n{analysis['summary']}")
        else:
            if analysis.get("full_text"):
                sections.append(f"\n=== EXTRACTED IMAGE TEXT ===\n{analysis['full_text']}")
            for img in analysis.get("image_analysis") or []:
                pass  # full_text already covers content; per-image descriptions below
            img_analysis = analysis.get("image_analysis") or []
            if img_analysis:
                img_lines = [
                    f"Image {img.get('index', '?')}: {img.get('description', '')} | Text: {img.get('extracted_text', '')}"
                    for img in img_analysis
                ]
                sections.append("\n=== IMAGE DESCRIPTIONS ===\n" + "\n".join(img_lines))
            if analysis.get("summary"):
                sections.append(f"\n=== POST SUMMARY ===\n{analysis['summary']}")
    else:
        sections.append(
            "\n(No Gemini analysis available — image/video content cannot be described. "
            "Re-run scrape with --analyze to enable.)"
        )

    comments_path = data_dir / post_id / "comments.json"
    if comments_path.exists():
        data = json.loads(comments_path.read_text())
        comments = data.get("comments", [])
        if comments:
            comment_lines = []
            for i, c in enumerate(comments, 1):
                comment_lines.append(
                    f"{i}. @{c.get('nickname', '?')} ({c.get('like_count', 0)} likes): {c.get('content', '')}"
                )
                for reply in c.get("replies") or []:
                    comment_lines.append(f"   └ @{reply.get('nickname', '?')}: {reply.get('content', '')}")
            sections.append("\n=== TOP COMMENTS ===\n" + "\n".join(comment_lines))
    else:
        sections.append(
            "\n(No comments scraped for this post. Re-run scrape with --comments to enable.)"
        )

    return "\n".join(sections)
