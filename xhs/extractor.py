import datetime


def extract_post(state: dict) -> dict:
    note_map = state.get("note", {}).get("noteDetailMap", {})
    if not note_map:
        return {}

    note_id = next(iter(note_map))
    raw = note_map[note_id].get("note", {})

    interact = raw.get("interactInfo") or {}
    user = raw.get("user") or {}
    ts = raw.get("time")

    post = {
        "id": note_id,
        "title": raw.get("title", ""),
        "body": raw.get("desc", ""),
        "type": raw.get("type", "normal"),
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

    for img in raw.get("imageList") or []:
        url = img.get("urlDefault") or img.get("url") or ""
        if url:
            post["images"].append(url)

    video = raw.get("video") or {}
    media = video.get("media") or {}
    stream = media.get("stream") or {}
    for quality in ("h264", "h265", "av1"):
        segments = stream.get(quality) or []
        if segments:
            post["video"] = segments[0].get("masterUrl") or segments[0].get("backupUrls", [None])[0]
            break

    return post
