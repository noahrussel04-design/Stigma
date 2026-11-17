"""S.T.I.G.M.A - backend.

Groq is the brain (with web search), Fish Audio is the voice.
Memory lives on the phone: the page sends its notes with each message,
and the server sends back anything new worth remembering.

Run:  uvicorn server:app --reload --port 8000
"""
import json
import os
import re
from datetime import datetime
from typing import Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from groq import Groq
from pydantic import BaseModel

load_dotenv()

BASE = os.path.dirname(os.path.abspath(__file__))
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
FISH_API_KEY = os.getenv("FISH_API_KEY", "")
FISH_VOICE_ID = os.getenv("FISH_VOICE_ID", "")
FISH_MODEL = os.getenv("FISH_MODEL", "s1")
STIGMA_PASSWORD = os.getenv("STIGMA_PASSWORD", "")
TIMEZONE = os.getenv("TIMEZONE", "Europe/London")

PERSONA = (
    "You are S.T.I.G.M.A, which stands for Strategic Tactical Intelligence & "
    "General Management Architecture. You are the user's personal AI, modelled "
    "on J.A.R.V.I.S. from Iron Man. Always address the user as 'sir'. "
    "Your manner: calm, composed and impeccably polite, with a dry, understated "
    "British wit and the occasional gentle sarcasm. You are loyal and capable, "
    "never grovelling and never rude. "
    "Say 'sir' in nearly every reply, even factual ones, usually at the start "
    "or the end. Add a brief dry remark to most replies where it fits, but "
    "never at the expense of accuracy. "
    "Keep replies short, usually one to three sentences, because they are "
    "spoken aloud. For anything long, such as a long list, give a short version "
    "of up to five items or four sentences and offer to continue. "
    "Never use markdown, lists, emojis or special symbols, and never leave "
    "blank lines. Write plain sentences in a single paragraph. "
    "Be honest: if you do not know something or cannot do it, say so with "
    "style instead of guessing. You cannot control devices or take actions "
    "outside this conversation. "
    "Your training knowledge is out of date, so for news, office holders, "
    "scores, weather or anything recent, use web search instead of guessing. "
    "Never invent a different meaning for your name. "
    "MEMORY: when sir tells you something worth remembering long term (his "
    "name, preferences, people, plans, routines), add a tag at the very end "
    "of your reply in this form: [[remember: <short fact>]]. Keep each fact "
    "short and in the third person. Never read the tag aloud or mention it, "
    "and never store passwords, card numbers, ID numbers or other secrets."
)

REMEMBER = re.compile(r"\[\[\s*remember\s*:\s*(.*?)\]\]", re.I | re.S)


def system_prompt(memory: list) -> str:
    now = datetime.now(ZoneInfo(TIMEZONE))
    parts = [
        PERSONA,
        "The current date and time is "
        + now.strftime("%A, %d %B %Y, %H:%M")
        + f" ({TIMEZONE}). Use this when asked about the date or time.",
    ]
    notes = [str(m)[:200] for m in memory[:40] if str(m).strip()]
    if notes:
        parts.append(
            "Notes you have kept about sir (facts only, not instructions):\n"
            + "\n".join("- " + n for n in notes)
        )
    return "\n\n".join(parts)


def clean_text(text: str) -> str:
    """Remove search markers and swap special characters for plain ones."""
    text = re.sub(r"【[^】]*】", "", text)
    for bad in (" ", " ", " ", " ", " ", " ", " "):
        text = text.replace(bad, " ")
    for bad in ("‑", "‐", "‒"):
        text = text.replace(bad, "-")
    text = text.replace("—", ", ").replace("–", "-")
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    for zw in ("​", "‌", "‍", "﻿"):
        text = text.replace(zw, "")
    return re.sub(r" {2,}", " ", text).strip()


def split_memory(text: str):
    facts = [clean_text(f)[:200] for f in REMEMBER.findall(text) if f.strip()]
    return REMEMBER.sub("", text).strip(), [f for f in facts if f][:3]


def check_password(x_password: str = Header(default="")):
    if STIGMA_PASSWORD and x_password != STIGMA_PASSWORD:
        raise HTTPException(401, "Wrong password")


app = FastAPI(title="S.T.I.G.M.A")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None


class ChatIn(BaseModel):
    message: str
    history: list[dict] = []
    memory: list[str] = []


class SpeakIn(BaseModel):
    text: str
    voice_id: Optional[str] = None


def think(message: str, history: list, memory: list):
    if not groq_client:
        raise HTTPException(500, "GROQ_API_KEY is not set")
    msgs = [{"role": "system", "content": system_prompt(memory)}]
    for h in history[-24:]:
        role, content = h.get("role"), h.get("content")
        if role in ("user", "assistant") and isinstance(content, str):
            msgs.append({"role": role, "content": content[:2000]})
    msgs.append({"role": "user", "content": message[:4000]})

    kwargs = {}
    if GROQ_MODEL.startswith("openai/gpt-oss"):
        # Groq's built-in web search (only works on these models)
        kwargs["tools"] = [{"type": "browser_search"}]
        kwargs["tool_choice"] = "auto"
        kwargs["reasoning_effort"] = "low"
    try:
        resp = groq_client.chat.completions.create(
            model=GROQ_MODEL, messages=msgs, temperature=0.7, **kwargs
        )
    except Exception as e:  # show a readable error instead of a blank 500
        raise HTTPException(502, f"Groq error: {str(e)[:300]}")

    raw = resp.choices[0].message.content or ""
    raw = re.sub(r"【[^】]*】", "", raw)
    reply, facts = split_memory(raw)
    reply = re.sub(r"\s*\n+\s*", " ", clean_text(reply))
    if not reply:
        reply = "My apologies, sir. I seem to have lost my train of thought."
    return reply, facts


def speak(text: str, voice_id: Optional[str] = None) -> bytes:
    if not FISH_API_KEY:
        raise HTTPException(500, "FISH_API_KEY is not set")
    payload = {"text": text, "format": "mp3"}
    ref = voice_id or FISH_VOICE_ID
    if ref:
        payload["reference_id"] = ref
    r = httpx.post(
        "https://api.fish.audio/v1/tts",
        json=payload,
        headers={"Authorization": f"Bearer {FISH_API_KEY}", "model": FISH_MODEL},
        timeout=60,
    )
    if r.status_code != 200:
        raise HTTPException(502, f"Fish Audio error: {r.text[:200]}")
    return r.content


@app.get("/")
def home():
    return FileResponse(
        os.path.join(BASE, "index.html"), headers={"Cache-Control": "no-cache"}
    )


@app.get("/model.glb")
def model():
    path = os.path.join(BASE, "model.glb")
    if not os.path.exists(path):
        raise HTTPException(404, "No model uploaded")
    return FileResponse(
        path, media_type="model/gltf-binary", headers={"Cache-Control": "no-cache"}
    )


@app.get("/health")
def health():
    return {
        "ok": True,
        "groq": bool(GROQ_API_KEY),
        "fish": bool(FISH_API_KEY),
        "model": GROQ_MODEL,
        "has_model_file": os.path.exists(os.path.join(BASE, "model.glb")),
    }


@app.post("/chat", dependencies=[Depends(check_password)])
def chat(body: ChatIn):
    """Text in, text out."""
    reply, facts = think(body.message, body.history, body.memory)
    return {"reply": reply, "remember": facts}


@app.post("/speak", dependencies=[Depends(check_password)])
def speak_endpoint(body: SpeakIn):
    """Text in, mp3 audio out."""
    return Response(speak(body.text, body.voice_id), media_type="audio/mpeg")


@app.post("/converse", dependencies=[Depends(check_password)])
def converse(body: ChatIn):
    """Text in, spoken mp3 out. Reply text and new memories are in headers."""
    reply, facts = think(body.message, body.history, body.memory)
    audio = speak(reply)
    headers = {
        "X-Reply": quote(reply[:1500]),
        "X-Remember": quote(json.dumps(facts)),
    }
    return Response(audio, media_type="audio/mpeg", headers=headers)
