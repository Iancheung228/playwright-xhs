import datetime
import json
import logging
import os
import pathlib
import tempfile

import requests

from . import config

logger = logging.getLogger("xhs.storage")


def _atomic_write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as tmp:
        tmp.write(text)
        tmp_path = tmp.name
    os.replace(tmp_path, path)


def save_post_json(post: dict, data_dir: pathlib.Path) -> None:
    dest = data_dir / post["id"] / "post.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(dest, json.dumps(post, indent=2, ensure_ascii=False))
    logger.info("Saved post to %s", dest)


def download_images(post: dict, session: requests.Session, data_dir: pathlib.Path) -> None:
    folder = data_dir / post["id"] / "images"
    folder.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading %d image(s) to %s/", len(post["images"]), folder)
    for i, url in enumerate(post["images"]):
        dest = folder / f"{i+1:02d}.jpg"
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        logger.info("  [%d] saved %s", i + 1, dest)


def save_comments_json(
    post_id: str,
    comments: list,
    max_requested: int,
    data_dir: pathlib.Path,
) -> None:
    dest = data_dir / post_id / "comments.json"
    payload = {
        "post_id": post_id,
        "scraped_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_scraped": len(comments),
        "max_requested": max_requested,
        "sort": "like_count_desc",
        "comments": comments,
    }
    _atomic_write(dest, json.dumps(payload, indent=2, ensure_ascii=False))
    logger.info("Saved %d comments to %s", len(comments), dest)


def update_index(
    post: dict,
    url: str,
    data_dir: pathlib.Path,
    has_comments: bool = False,
) -> None:
    index_file = data_dir / "index.json"
    entries = json.loads(index_file.read_text()) if index_file.exists() else []

    ids = [e["post_id"] for e in entries]
    analysis = post.get("analysis") or {}
    entry = {
        "post_id": post["id"],
        "url": url,
        "title": post["title"],
        "type": post["type"],
        "author": post["author"],
        "author_id": post["author_id"],
        "posted_at": post["posted_at"],
        "ip_location": post["ip_location"],
        "likes": post["likes"],
        "collects": post["collects"],
        "comments": post["comments"],
        "shares": post["shares"],
        "tags": post["tags"],
        "images_count": len(post["images"]),
        "has_video": bool(post["video"]),
        "has_analysis": bool(analysis),
        "has_comments": has_comments,
        "content_type": analysis.get("content_type", ""),
        "scraped_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if post["id"] in ids:
        entries[ids.index(post["id"])] = entry
    else:
        entries.append(entry)

    _atomic_write(index_file, json.dumps(entries, indent=2, ensure_ascii=False))
    logger.info("Index updated: %s (%d posts)", index_file, len(entries))
