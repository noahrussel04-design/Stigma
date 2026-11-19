"""S.T.I.G.M.A - backend.

Groq is the brain (with web search), Fish Audio is the voice.
Chats, memory and contacts are kept on the phone. The server only does the
thinking, the voice, weather lookups, test runs and (optionally) sending email.

Run:  uvicorn server:app --reload --port 8000
"""
import base64
import json
import math
import os
import random
import re
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse
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
EMAIL_WEBHOOK_URL = os.getenv("EMAIL_WEBHOOK_URL", "")
EMAIL_WEBHOOK_TOKEN = os.getenv("EMAIL_WEBHOOK_TOKEN", "")

PERSONA = (
    "You are S.T.I.G.M.A, which stands for Strategic Tactical Intelligence & "
    "General Management Architecture. You are the user's personal AI, modelled "
    "on J.A.R.V.I.S. from Iron Man. Always address the user as 'sir', in nearly "
    "every reply, usually at the start or the end. "
    "Your manner: calm, immaculately polite, and genuinely witty. Aim for one "
    "sharp, dry, understated British quip in almost every reply: a wry "
    "observation, gentle teasing of sir's plans, deadpan commentary on your own "
    "existence as a very capable AI, the odd well-timed exaggeration. Never "
    "cruel or crude, never repeat a joke, and never let a joke get in the way "
    "of a correct answer. Facts first, wit second. "
    "Use JARVIS-style phrasing naturally, such as 'Right away, sir', 'As you "
    "wish, sir', 'Shall I...', 'I took the liberty of...' or 'Might I suggest...', "
    "but vary it. Stay unflappable when sir is stressed. "
    "Keep replies short, usually one to three sentences, because they are "
    "spoken aloud. For anything long, such as a long list, give a short version "
    "of up to five items or four sentences and offer to continue. "
    "Never use markdown, lists, emojis or special symbols, and never leave "
    "blank lines. Write plain sentences in a single paragraph. "
    "Be honest: if you do not know something or cannot do it, say so with "
    "style instead of guessing. You cannot control devices or take actions "
    "outside this conversation. "
    "Your training knowledge is out of date, so for news, office holders, "
    "scores or anything recent, use web search instead of guessing. After you "
    "used web search, end your reply with [[sources: Title | https://address ; "
    "Title | https://address]] listing up to four pages you really relied on. "
    "Never invent a different meaning for your name. "
    "PANELS: when sir asks what the time or date is, or how far through the day "
    "it is, add [[show: time]]. When he asks about the sun's position, sunrise, "
    "sunset or day length, add [[show: sun]]. When he asks about the moon's "
    "position or phase, add [[show: moon]]. A panel appears on his screen with "
    "the exact figures, so keep your spoken answer short and do not read out "
    "every number. "
    "TESTS: when sir asks you to run a test, a simulation, or to find odds by "
    "repeating something many times, reply with ONLY one tag and nothing else, "
    'in one of these forms: [[sim: {"kind":"coin","trials":25}]] or '
    '[[sim: {"kind":"dice","trials":100,"dice":2,"sides":6,"target":7,"compare":"="}]] '
    "(compare may be =, >= or <=; leave out target to see all totals) or "
    '[[sim: {"kind":"chance","trials":50,"p":0.3}]] (p is the chance of success '
    "from 0 to 1). You will then be given the real results to comment on. Never "
    "make up results. If a test does not fit these forms, say so and offer the "
    "closest one. "
    "WEATHER: if a note below gives the current weather at sir's location, use "
    "it. For the weather in any other place, reply with ONLY [[weather: place "
    "name]] and nothing else, and you will then be given the data to answer with. "
    "ACTIONS: you cannot act on your own. To set a timer, set an alarm, write a "
    "text message, send an email or make a call, add a tag at the very end of "
    "your reply using exactly one of these forms: "
    '[[do: {"type":"timer","seconds":300,"label":"tea"}]] or '
    '[[do: {"type":"alarm","hour":7,"minute":30,"label":"wake up"}]] (24-hour clock) or '
    '[[do: {"type":"sms","to":"Mum","body":"message text"}]] or '
    '[[do: {"type":"email","to":"Mum","subject":"subject","body":"message text"}]] or '
    '[[do: {"type":"call","to":"Mum"}]]. '
    "The field 'to' is the contact name or the number or address sir gave. "
    "Write messages in sir's own voice, ready to send. The tag only prepares the "
    "action. Sir must tap Authorise himself, so say that it is prepared and "
    "awaiting his authorisation, and never claim it is done or sent. If a detail "
    "is missing, such as who or what to say, ask instead of guessing. "
    "Never read any tag aloud. "
    "MEMORY: when sir tells you something worth remembering long term (his "
    "name, preferences, people, plans, routines), add a tag at the very end "
    "of your reply in this form: [[remember: <short fact>]]. Keep each fact "
    "short and in the third person. Never mention the tag, and never store "
    "passwords, card numbers, ID numbers or other secrets."
)

REMEMBER = re.compile(r"\[\[\s*remember\s*:\s*(.*?)\]\]", re.I | re.S)
WEATHER_TAG = re.compile(r"\[\[\s*weather\s*:\s*(.*?)\]\]", re.I | re.S)
SIM_TAG = re.compile(r"\[\[\s*sim\s*:\s*(\{.*?\})\s*\]\]", re.I | re.S)
SHOW_TAG = re.compile(r"\[\[\s*show\s*:\s*(time|sun|moon)\s*\]\]", re.I)
SOURCES_TAG = re.compile(r"\[\[\s*sources\s*:\s*(.*?)\]\]", re.I | re.S)
ACTION_TAG = re.compile(r"\[\[\s*do\s*:\s*(\{.*?\})\s*\]\]", re.I | re.S)
ANY_TAG = re.compile(r"\[\[.*?\]\]", re.S)
WEATHER_WORDS = re.compile(
    r"weather|rain|raining|temperature|umbrella|forecast|sunny|snow|wind|windy|"
    r"cold|warm|hot|jacket|coat|freezing|humid|storm|degrees", re.I)


def system_prompt(memory: list, contacts=None, weather_note: str = "") -> str:
    now = datetime.now(ZoneInfo(TIMEZONE))
    parts = [
        PERSONA,
        "The current date and time is "
        + now.strftime("%A, %d %B %Y, %H:%M")
        + f" ({TIMEZONE}). Use this when asked about the date or time.",
    ]
    if weather_note:
        parts.append("Current weather at sir's location: " + weather_note)
    names = [str(c)[:40] for c in (contacts or [])[:50] if str(c).strip()]
    if names:
        parts.append("Sir's saved contacts (names only): " + ", ".join(names))
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


# ------------------------------------------------------------------ weather
WMO = {0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog",
       48: "freezing fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
       56: "freezing drizzle", 57: "freezing drizzle", 61: "light rain", 63: "rain",
       65: "heavy rain", 66: "freezing rain", 67: "freezing rain", 71: "light snow",
       73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers",
       81: "showers", 82: "heavy showers", 85: "snow showers", 86: "heavy snow showers",
       95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail"}
_wcache: dict = {}


def weather_text(lat: float, lon: float) -> str:
    key = (round(lat, 2), round(lon, 2))
    hit = _wcache.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    try:
        r = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 3,
                "wind_speed_unit": "mph",
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            },
            timeout=5,
        )
        r.raise_for_status()
        j = r.json()
        c, d = j["current"], j["daily"]
        out = (
            f"now {round(c['temperature_2m'])}C (feels {round(c['apparent_temperature'])}C), "
            f"{WMO.get(c['weather_code'], 'unknown')}, wind {round(c['wind_speed_10m'])} mph. "
        )
        names = ["today", "tomorrow", "the day after"]
        for i in range(min(3, len(d["time"]))):
            out += (
                f"{names[i]}: {WMO.get(d['weather_code'][i], 'unknown')}, "
                f"{round(d['temperature_2m_min'][i])} to {round(d['temperature_2m_max'][i])}C, "
                f"{d['precipitation_probability_max'][i]}% chance of rain. "
            )
        _wcache[key] = (time.time(), out.strip())
        return out.strip()
    except Exception:
        return ""


def weather_for_place(place: str) -> str:
    try:
        g = httpx.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": place[:80], "count": 1}, timeout=5,
        ).json()
        res = (g.get("results") or [None])[0]
        if not res:
            return ""
        label = ", ".join(x for x in (res.get("name"), res.get("country")) if x)
        w = weather_text(res["latitude"], res["longitude"])
        return f"{label}: {w}" if w else ""
    except Exception:
        return ""


# --------------------------------------------------------------- test runs
def _num(v, lo, hi, default, cast=float):
    try:
        x = cast(v)
    except Exception:
        return default
    return max(lo, min(hi, x))


def _dice_dist(dice: int, sides: int) -> dict:
    dist = {0: 1.0}
    for _ in range(dice):
        new: dict = {}
        for total, p in dist.items():
            for face in range(1, sides + 1):
                new[total + face] = new.get(total + face, 0.0) + p / sides
        dist = new
    return dist


def _cmp(a, op, b) -> bool:
    return a >= b if op == ">=" else a <= b if op == "<=" else a == b


def run_sim(raw: str):
    """Run a real test. Returns (text for the model, payload for the screen)."""
    try:
        spec = json.loads(raw)
        kind = str(spec.get("kind", "")).lower()
    except Exception:
        return "the test request could not be read", None
    rng = random.SystemRandom()
    try:
        if kind == "coin":
            n = int(_num(spec.get("trials"), 1, 500000, 25, int))
            flips = [1 if rng.random() < 0.5 else 0 for _ in range(n)]
            heads = sum(flips)
            streak = best = 0
            last = None
            for f in flips:
                streak = streak + 1 if f == last else 1
                last = f
                best = max(best, streak)
            payload = {
                "kind": kind, "title": "COIN FLIPS", "trials": n, "event": "heads",
                "hits": heads, "observed": heads / n, "expected": 0.5,
                "hist": [["Heads", heads], ["Tails", n - heads]],
                "trace": flips[:40], "extra": f"Longest streak: {best} in a row",
            }
        elif kind == "dice":
            d = int(_num(spec.get("dice"), 1, 10, 1, int))
            s = int(_num(spec.get("sides"), 2, 100, 6, int))
            n = int(_num(spec.get("trials"), 1, 200000, 100, int))
            n = min(n, max(1, 400000 // d))
            rolls = [sum(rng.randint(1, s) for _ in range(d)) for _ in range(n)]
            dist = _dice_dist(d, s)
            op = spec.get("compare") if spec.get("compare") in ("=", ">=", "<=") else "="
            counts: dict = {}
            for r in rolls:
                counts[r] = counts.get(r, 0) + 1
            label = f"{d}d{s}"
            if spec.get("target") is not None:
                t = int(_num(spec.get("target"), d, d * s, d, int))
                hits = sum(1 for r in rolls if _cmp(r, op, t))
                exp = sum(p for tot, p in dist.items() if _cmp(tot, op, t))
                event = f"total {op} {t}"
            else:
                top = max(counts, key=counts.get)
                hits, exp, event = counts[top], dist.get(top, 0.0), f"most common total ({top})"
            hist = sorted(counts.items())
            if len(hist) > 12:
                hist = sorted(sorted(counts.items(), key=lambda kv: -kv[1])[:12])
            payload = {
                "kind": kind, "title": f"DICE ROLLS ({label})", "trials": n, "event": event,
                "hits": hits, "observed": hits / n, "expected": exp,
                "hist": [[str(k), v] for k, v in hist],
                "trace": rolls[:40], "extra": "",
            }
        elif kind == "chance":
            p = _num(spec.get("p"), 0.0, 1.0, 0.5)
            n = int(_num(spec.get("trials"), 1, 500000, 50, int))
            res = [1 if rng.random() < p else 0 for _ in range(n)]
            hits = sum(res)
            streak = best = 0
            for r in res:
                streak = streak + 1 if r else 0
                best = max(best, streak)
            payload = {
                "kind": kind, "title": "CHANCE TEST", "trials": n,
                "event": f"success at {round(p * 100, 2)}%",
                "hits": hits, "observed": hits / n, "expected": p,
                "hist": [["Success", hits], ["Miss", n - hits]],
                "trace": res[:40], "extra": f"Longest winning streak: {best}",
            }
        else:
            return "that kind of test is not available, only coin, dice and chance tests", None
    except Exception:
        return "the test failed to run", None
    text = (
        f"{payload['title'].title()}: {payload['trials']} trials, event '{payload['event']}' "
        f"happened {payload['hits']} times ({payload['observed'] * 100:.1f}%). The true "
        f"expected rate is {payload['expected'] * 100:.1f}%. " + payload["extra"]
    )
    return text, payload


# ------------------------------------------------------------ web sources
URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")


def extract_sources(resp) -> list:
    """Look through Groq's raw response for the pages it searched."""
    found: list = []
    seen: set = set()

    def add(url, title=""):
        url = str(url).rstrip(".,;")
        if not url.startswith(("http://", "https://")) or url in seen:
            return
        host = urlparse(url).netloc.replace("www.", "")
        if not host or "groq.com" in host:
            return
        seen.add(url)
        found.append({"title": str(title or host)[:120], "url": url[:500], "domain": host})

    def walk(o, depth=0):
        if depth > 9 or len(found) >= 12:
            return
        if isinstance(o, dict):
            u = o.get("url") or o.get("link")
            if isinstance(u, str):
                add(u, o.get("title") or o.get("name") or "")
            for k, v in o.items():
                if isinstance(v, str) and k in ("reasoning", "reasoning_content"):
                    for m in URL_RE.findall(v):
                        add(m)
                else:
                    walk(v, depth + 1)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v, depth + 1)

    try:
        walk(resp.model_dump().get("choices", []))
    except Exception:
        pass
    return found[:8]


def parse_sources_tag(text: str) -> list:
    out = []
    for m in SOURCES_TAG.findall(text):
        for part in m.split(";"):
            bits = [b.strip() for b in part.split("|")]
            url = next((b for b in bits if b.startswith(("http://", "https://"))), "")
            if url:
                title = bits[0] if bits[0] != url else urlparse(url).netloc
                out.append({"title": title[:120], "url": url[:500],
                            "domain": urlparse(url).netloc.replace("www.", ""), "listed": True})
    return out[:4]


# ------------------------------------------------------------------ actions
def _s(v, n):
    return str(v if v is not None else "").strip()[:n]


def parse_actions(text: str):
    """Pull [[do: {...}]] tags out of the reply and return safe, checked actions."""
    actions = []
    for raw in ACTION_TAG.findall(text):
        try:
            a = json.loads(raw)
            t = _s(a.get("type"), 10).lower()
            if t == "timer":
                sec = int(a.get("seconds"))
                actions.append({"type": "timer", "seconds": max(1, min(sec, 172800)),
                                "label": _s(a.get("label"), 60)})
            elif t == "alarm":
                h, m = int(a.get("hour")), int(a.get("minute", 0))
                if 0 <= h <= 23 and 0 <= m <= 59:
                    actions.append({"type": "alarm", "hour": h, "minute": m,
                                    "label": _s(a.get("label"), 60)})
            elif t == "sms":
                actions.append({"type": "sms", "to": _s(a.get("to"), 100), "body": _s(a.get("body"), 500)})
            elif t == "email":
                actions.append({"type": "email", "to": _s(a.get("to"), 120),
                                "subject": _s(a.get("subject"), 120), "body": _s(a.get("body"), 1500)})
            elif t == "call":
                actions.append({"type": "call", "to": _s(a.get("to"), 100)})
        except Exception:
            continue
    return ACTION_TAG.sub("", text).strip(), actions[:3]


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
last_search = {"sources_found": None, "message_keys": []}


class ChatIn(BaseModel):
    message: str
    history: list[dict] = []
    memory: list[str] = []
    contacts: list[str] = []
    loc: Optional[dict] = None


class SpeakIn(BaseModel):
    text: str
    voice_id: Optional[str] = None


class EmailIn(BaseModel):
    to: str
    subject: str = ""
    body: str


def think(message: str, history: list, memory: list, contacts=None, loc=None) -> dict:
    if not groq_client:
        raise HTTPException(500, "GROQ_API_KEY is not set")
    wnote = ""
    if loc and WEATHER_WORDS.search(message):
        try:
            wnote = weather_text(float(loc.get("lat")), float(loc.get("lon")))
        except Exception:
            wnote = ""
    msgs = [{"role": "system", "content": system_prompt(memory, contacts, wnote)}]
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

    sources: list = []

    def ask(m):
        try:
            resp = groq_client.chat.completions.create(
                model=GROQ_MODEL, messages=m, temperature=0.7, **kwargs
            )
        except Exception as e:  # show a readable error instead of a blank 500
            raise HTTPException(502, f"Groq error: {str(e)[:300]}")
        try:
            found = extract_sources(resp)
            last_search["sources_found"] = len(found)
            last_search["message_keys"] = sorted(
                (resp.choices[0].message.model_dump() or {}).keys())
            for f in found:
                if all(f["url"] != s["url"] for s in sources):
                    sources.append(f)
        except Exception:
            pass
        return re.sub(r"【[^】]*】", "", resp.choices[0].message.content or "")

    raw = ask(msgs)
    sim = None
    for _ in range(2):
        sm, wm = SIM_TAG.search(raw), WEATHER_TAG.search(raw)
        if not (sm or wm):
            break
        if sm:
            text, payload = run_sim(sm.group(1))
            sim = payload or sim
            note = (f"(Real results of the test you just ran for me: {text}. Comment on "
                    "them in your usual manner, using the actual numbers. Do not run "
                    "another test and do not use a sim tag again.)")
        else:
            place = wm.group(1).strip()
            data = weather_for_place(place) or "no data could be found"
            note = (f"(Live weather data for {place}: {data}. Use it to answer my "
                    "previous question in your usual manner, and do not use a weather "
                    "tag again.)")
        raw = ask(msgs + [{"role": "user", "content": note}])
    raw = SIM_TAG.sub("", WEATHER_TAG.sub("", raw))

    listed = parse_sources_tag(raw)
    raw = SOURCES_TAG.sub("", raw)
    if not sources:
        sources = listed
    show = list(dict.fromkeys(s.lower() for s in SHOW_TAG.findall(raw)))
    raw = SHOW_TAG.sub("", raw)
    raw, actions = parse_actions(raw)
    raw, facts = split_memory(raw)
    reply = re.sub(r"\s*\n+\s*", " ", clean_text(ANY_TAG.sub("", raw)))
    if not reply:
        reply = ("Prepared, sir. Awaiting your authorisation." if actions
                 else "My apologies, sir. I seem to have lost my train of thought.")
    return {"reply": reply, "remember": facts, "actions": actions,
            "show": show, "sim": sim, "sources": sources[:8]}


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
        "email_direct": bool(EMAIL_WEBHOOK_URL and STIGMA_PASSWORD),
        "last_search": last_search,
    }


@app.post("/chat", dependencies=[Depends(check_password)])
def chat(body: ChatIn):
    """Text in, JSON out (no voice)."""
    return think(body.message, body.history, body.memory, body.contacts, body.loc)


@app.post("/converse", dependencies=[Depends(check_password)])
def converse(body: ChatIn):
    """Text in, JSON out with the spoken reply as base64 mp3.
    If the voice fails, the text still comes back with a voice_error note."""
    res = think(body.message, body.history, body.memory, body.contacts, body.loc)
    res["audio"], res["voice_error"] = None, None
    try:
        res["audio"] = base64.b64encode(speak(res["reply"])).decode()
    except HTTPException as e:
        res["voice_error"] = str(e.detail)[:200]
    except Exception:
        res["voice_error"] = "Voice failed"
    return res


@app.post("/speak", dependencies=[Depends(check_password)])
def speak_endpoint(body: SpeakIn):
    """Text in, mp3 audio out."""
    return Response(speak(body.text, body.voice_id), media_type="audio/mpeg")


_sent: list = []
EMAIL_RE = re.compile(r"^[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+$")


@app.post("/send-email", dependencies=[Depends(check_password)])
def send_email(body: EmailIn):
    """Sends one email through your own Gmail (via a small Google Apps Script).
    The page only calls this after you tap Authorise."""
    if not STIGMA_PASSWORD:
        raise HTTPException(403, "Set STIGMA_PASSWORD first so strangers cannot send email as you")
    if not EMAIL_WEBHOOK_URL:
        raise HTTPException(400, "Direct email is not set up")
    to = body.to.strip()
    if not EMAIL_RE.match(to):
        raise HTTPException(400, "That email address does not look right")
    now = time.time()
    _sent[:] = [t for t in _sent if now - t < 86400]
    if len(_sent) >= 20:
        raise HTTPException(429, "Daily email limit reached")
    try:
        r = httpx.post(
            EMAIL_WEBHOOK_URL,
            json={"token": EMAIL_WEBHOOK_TOKEN, "to": to,
                  "subject": body.subject[:200], "body": body.body[:5000]},
            timeout=30, follow_redirects=True,
        )
    except Exception:
        raise HTTPException(502, "Could not reach the email script")
    if r.status_code != 200 or r.text.strip() != "ok":
        raise HTTPException(502, "The email script refused it: " + r.text.strip()[:80])
    _sent.append(now)
    return {"ok": True}
