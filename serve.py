#!/usr/bin/env python3
"""XHS Chat server. Run with: uvicorn serve:app --reload"""

import json
import os
import pathlib
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from xhs import config

DATA_DIR = pathlib.Path(os.environ.get("XHS_DATA_DIR", "."))
STATIC_DIR = pathlib.Path(__file__).parent / "static"

app = FastAPI(title="XHS Chat")

# session_id -> {"post_id": str, "history": list[dict], "system": str}
_sessions: dict[str, dict] = {}


class ChatRequest(BaseModel):
    post_id: str
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    session_id: str


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "chat.html")


@app.get("/posts")
def list_posts():
    index_path = DATA_DIR / "index.json"
    if not index_path.exists():
        return []
    return json.loads(index_path.read_text())


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(500, "GEMINI_API_KEY environment variable not set")

    from google import genai
    from xhs.chat import build_system_prompt

    client = genai.Client(api_key=api_key)

    session_id = req.session_id or str(uuid.uuid4())
    session = _sessions.get(session_id)

    if session is None or session["post_id"] != req.post_id:
        try:
            system = build_system_prompt(req.post_id, DATA_DIR)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc))
        session = {"post_id": req.post_id, "history": [], "system": system}
        _sessions[session_id] = session

    session["history"].append({"role": "user", "parts": [{"text": req.message}]})

    try:
        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=session["history"],
            config={"system_instruction": session["system"]},
        )
        reply_text = response.text
    except Exception as exc:
        session["history"].pop()  # roll back the user turn on failure
        raise HTTPException(502, f"Gemini error: {exc}")

    session["history"].append({"role": "model", "parts": [{"text": reply_text}]})

    return ChatResponse(reply=reply_text, session_id=session_id)


@app.delete("/chat/{session_id}")
def clear_session(session_id: str):
    _sessions.pop(session_id, None)
    return {"ok": True}
