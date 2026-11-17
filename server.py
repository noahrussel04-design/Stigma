"""S.T.I.G.M.A - backend core.

Groq handles the thinking, Fish Audio handles the voice.
The interface is separate: it just talks to these endpoints.

Run:  uvicorn server:app --reload --port 8000
"""
import os
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from groq import Groq
from pydantic import BaseModel

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
FISH_API_KEY = os.getenv("FISH_API_KEY", "")
FISH_VOICE_ID = os.getenv("FISH_VOICE_ID", "")  # reference_id of your Fish voice model
FISH_MODEL = os.getenv("FISH_MODEL", "s1")
STIGMA_PASSWORD = os.getenv("STIGMA_PASSWORD", "")  # protects your keys when hosted online

SYSTEM_PROMPT = (
    "You are S.T.I.G.M.A, a personal AI assistant in the spirit of JARVIS: "
    "calm, sharp, dryly witty, and loyal. Address the user respectfully, keep "
    "replies concise (they may be spoken aloud), and never use markdown, "
    "emojis or lists in replies unless asked."
)

def check_password(x_password: str = Header(default="")):
    if STIGMA_PASSWORD and x_password != STIGMA_PASSWORD:
        raise HTTPException(401, "Wrong password")


app = FastAPI(title="S.T.I.G.M.A")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# In-memory conversation history, keyed by session id.
sessions: dict[str, list[dict]] = {}
MAX_TURNS = 20


class ChatIn(BaseModel):
    message: str
    session_id: str = "default"


class SpeakIn(BaseModel):
    text: str
    voice_id: Optional[str] = None


def think(session_id: str, message: str) -> str:
    if not groq_client:
        raise HTTPException(500, "GROQ_API_KEY is not set")
    history = sessions.setdefault(session_id, [])
    history.append({"role": "user", "content": message})
    del history[:-MAX_TURNS * 2]
    resp = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, *history],
        temperature=0.7,
    )
    reply = resp.choices[0].message.content or ""
    history.append({"role": "assistant", "content": reply})
    return reply


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
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))


@app.get("/health")
def health():
    return {"ok": True, "groq": bool(GROQ_API_KEY), "fish": bool(FISH_API_KEY)}


@app.post("/chat", dependencies=[Depends(check_password)])
def chat(body: ChatIn):
    """Text in, text out."""
    return {"reply": think(body.session_id, body.message)}


@app.post("/speak", dependencies=[Depends(check_password)])
def speak_endpoint(body: SpeakIn):
    """Text in, mp3 audio out."""
    return Response(speak(body.text, body.voice_id), media_type="audio/mpeg")


@app.post("/converse", dependencies=[Depends(check_password)])
def converse(body: ChatIn):
    """Text in, mp3 of the spoken reply out (reply text in X-Reply header)."""
    reply = think(body.session_id, body.message)
    audio = speak(reply)
    safe = reply.replace("\n", " ")[:1000].encode("ascii", "ignore").decode()
    return Response(audio, media_type="audio/mpeg", headers={"X-Reply": safe})


@app.delete("/session/{session_id}")
def reset(session_id: str):
    sessions.pop(session_id, None)
    return {"ok": True}
