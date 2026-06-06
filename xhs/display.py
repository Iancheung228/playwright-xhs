def print_post(post: dict) -> None:
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
        insights = a.get("insights") or {}
        if isinstance(insights, list):
            qa_pairs, verdict, tldw = insights, "", []
        else:
            qa_pairs = insights.get("questions") or []
            verdict = insights.get("value_verdict", "")
            tldw = insights.get("tldw") or []
        if verdict:
            print(f"    Value verdict: {verdict}")
        if tldw:
            print(f"    TL;DW:")
            for b in tldw:
                print(f"      • {b}")
        if qa_pairs:
            print(f"    Insights ({len(qa_pairs)} questions):")
            for item in qa_pairs:
                print(f"      Q: {item.get('question', '')}")
                answer_preview = (item.get('answer') or '')[:120]
                print(f"      A: {answer_preview}{'...' if len(item.get('answer', '')) > 120 else ''}")
    print(sep)
