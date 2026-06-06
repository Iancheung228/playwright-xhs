#!/usr/bin/env python3
"""Xiaohongshu post scraper. See README.md for usage."""

import argparse
import json
import logging
import pathlib
import sys
import time

from xhs import config


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scrape",
        description="Scrape a Xiaohongshu post (metadata, images, comments, AI analysis).",
    )
    p.add_argument("url", nargs="?", help="XHS or Rednote post URL to scrape")
    p.add_argument(
        "--analyze",
        action="store_true",
        help="Run Gemini image OCR / video transcription (requires GEMINI_API_KEY env var)",
    )
    p.add_argument(
        "--comments",
        nargs="?",
        const=config.DEFAULT_COMMENTS_N,
        type=int,
        metavar="N",
        help=f"Scrape top N comments sorted by likes (default {config.DEFAULT_COMMENTS_N} when flag is present)",
    )
    p.add_argument(
        "--setup",
        action="store_true",
        help="Interactive cookie setup wizard",
    )
    p.add_argument(
        "--cookie-file",
        default=str(config.DEFAULT_COOKIE_FILE),
        metavar="PATH",
        help=f"Path to cookies JSON file (default: {config.DEFAULT_COOKIE_FILE})",
    )
    p.add_argument(
        "--data-dir",
        default=".",
        metavar="DIR",
        help="Directory where post folders and index.json are written (default: current dir)",
    )
    p.add_argument(
        "--replies",
        nargs="?",
        const=config.DEFAULT_REPLY_K,
        type=int,
        metavar="K",
        help=(
            f"Fetch replies for the most insightful threads: heuristic selects top K candidates "
            f"(default {config.DEFAULT_REPLY_K}), Gemini picks best {config.DEFAULT_REPLY_N} to scrape "
            f"(requires --comments and GEMINI_API_KEY)"
        ),
    )
    p.add_argument(
        "--insights-only",
        action="store_true",
        help=(
            "Generate insights for an already-analyzed post without re-running the vision call. "
            "Skips if insights already exist. Useful when --analyze succeeded but insights "
            "failed due to a token quota error."
        ),
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Save debug_state.json and enable DEBUG logging",
    )
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=log_level, format="[%(levelname)s] %(message)s")

    cookie_file = pathlib.Path(args.cookie_file).expanduser()
    data_dir = pathlib.Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    if args.setup:
        from xhs.cookies import setup_cookies
        setup_cookies(cookie_file)
        return

    if not args.url:
        parser.print_help()
        sys.exit(0)

    # Accept rednote.com URLs by rewriting to xiaohongshu.com
    url = args.url.replace("www.rednote.com", "www.xiaohongshu.com")

    if args.insights_only:
        import re as _re
        from xhs import analysis as gemini
        m = _re.search(r"/explore/([0-9a-f]+)", url)
        post_id = m.group(1) if m else url
        success = gemini.run_insights_for_post(post_id, data_dir)
        sys.exit(0 if success else 1)

    from xhs.cookies import load_cookies
    from xhs.fetcher import fetch_initial_state, make_session
    from xhs.extractor import extract_post
    from xhs.storage import save_post_json, update_index, download_images, save_comments_json
    from xhs.display import print_post
    from xhs import analysis as gemini

    cookies = load_cookies(cookie_file)
    session = make_session(cookies)

    t0 = time.monotonic()

    logging.info("[1/4] Fetching post metadata...")
    t = time.monotonic()
    state = fetch_initial_state(url, session)
    logging.info("      done in %.1fs", time.monotonic() - t)

    if args.debug:
        debug_path = data_dir / "debug_state.json"
        debug_path.write_text(json.dumps(state, indent=2, ensure_ascii=False))
        logging.debug("Raw state saved to %s", debug_path)

    post = extract_post(state)
    if not post:
        logging.error("Could not extract post data. Re-run with --debug to inspect state.")
        sys.exit(1)

    step = 2
    total_steps = 2 + bool(args.analyze) + (args.comments is not None)

    if post["images"]:
        logging.info("[%d/%d] Downloading %d image(s)...", step, total_steps, len(post["images"]))
        t = time.monotonic()
        download_images(post, session, data_dir)
        logging.info("      done in %.1fs", time.monotonic() - t)
    step += 1

    if args.analyze:
        label = "video" if post["type"] == "video" else f"{len(post['images'])} image(s)"
        logging.info("[%d/%d] Running Gemini analysis on %s...", step, total_steps, label)
        t = time.monotonic()
        try:
            if post["type"] == "video" and post["video"]:
                post["analysis"] = gemini.analyze_video(post, data_dir)
            elif post["images"]:
                post["analysis"] = gemini.analyze_images(post, data_dir)
            else:
                logging.warning("--analyze: post has no images or video — skipping.")
            logging.info("      done in %.1fs", time.monotonic() - t)
        except Exception as exc:
            logging.error("Analysis failed: %s — post will be saved without analysis.", exc)
        step += 1

    has_comments = False
    if args.comments is not None:
        from xhs.comments import scrape_comments
        reply_k = args.replies if args.replies is not None else 0
        reply_n = config.DEFAULT_REPLY_N if reply_k > 0 else 0
        if reply_k > 0:
            logging.info("[%d/%d] Scraping top %d comments + smart replies (heuristic K=%d, LLM N=%d)...", step, total_steps, args.comments, reply_k, reply_n)
        else:
            logging.info("[%d/%d] Scraping top %d comments...", step, total_steps, args.comments)
        t = time.monotonic()
        comments = scrape_comments(url, post["id"], args.comments, cookie_file, reply_k=reply_k, reply_n=reply_n)
        save_comments_json(post["id"], comments, args.comments, data_dir)
        has_comments = bool(comments)
        logging.info("      done in %.1fs — %d comment(s) saved", time.monotonic() - t, len(comments))

    print_post(post)
    save_post_json(post, data_dir)
    update_index(post, url, data_dir, has_comments=has_comments)
    logging.info("Total time: %.1fs", time.monotonic() - t0)


if __name__ == "__main__":
    main()
