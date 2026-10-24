"""Doodle Duel: a Pictionary game where Gemma 4 guesses your sketches live.

Run locally:
    set GEMINI_API_KEY first, then:  uvicorn main:app --reload --port 8080
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
import random
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
log = logging.getLogger("doodle-duel")

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "static" / "index.html"


def load_dotenv(path: Path = BASE_DIR / ".env") -> None:
    """Minimal .env reader (KEY=value lines) so local runs don't need extra packages.
    Real environment variables always win over the file."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip().strip("\"'")
        if key and not os.environ.get(key):
            os.environ[key] = value


load_dotenv()

PRIMARY_MODEL = os.environ.get("GEMMA_MODEL", "gemma-4-26b-a4b-it")
FALLBACK_MODEL = os.environ.get("GEMMA_FALLBACK_MODEL", "gemma-4-31b-it")
MODELS = [m for m in dict.fromkeys([PRIMARY_MODEL, FALLBACK_MODEL]) if m]

REQUEST_TIMEOUT_S = 35
MAX_IMAGE_BYTES = 4 * 1024 * 1024

# (word, difficulty, accepted aliases) - all things that are fun and doable in 60 seconds.
WORDS: list[tuple[str, str, list[str]]] = [
    ("cat", "easy", ["kitten", "kitty"]),
    ("dog", "easy", ["puppy"]),
    ("fish", "easy", []),
    ("sun", "easy", []),
    ("moon", "easy", ["crescent"]),
    ("star", "easy", []),
    ("tree", "easy", []),
    ("house", "easy", ["home"]),
    ("car", "easy", ["automobile"]),
    ("apple", "easy", []),
    ("banana", "easy", []),
    ("flower", "easy", ["daisy", "tulip"]),
    ("heart", "easy", []),
    ("cloud", "easy", []),
    ("ball", "easy", []),
    ("cup", "easy", ["mug"]),
    ("hat", "easy", []),
    ("boat", "easy", ["ship", "sailboat"]),
    ("bird", "easy", []),
    ("snake", "easy", []),
    ("eye", "easy", []),
    ("key", "easy", []),
    ("book", "easy", []),
    ("chair", "easy", []),
    ("clock", "easy", []),
    ("cake", "easy", ["birthday cake", "cupcake"]),
    ("pizza", "easy", ["pizza slice"]),
    ("ice cream", "easy", ["ice cream cone", "icecream"]),
    ("egg", "easy", []),
    ("rainbow", "easy", []),
    ("snowman", "easy", []),
    ("ghost", "easy", []),
    ("spider", "easy", []),
    ("balloon", "easy", []),
    ("mushroom", "easy", []),
    ("butterfly", "medium", []),
    ("bicycle", "medium", ["bike"]),
    ("airplane", "medium", ["plane", "aeroplane", "jet"]),
    ("rocket", "medium", ["spaceship"]),
    ("guitar", "medium", []),
    ("castle", "medium", []),
    ("lighthouse", "medium", []),
    ("volcano", "medium", []),
    ("octopus", "medium", []),
    ("giraffe", "medium", []),
    ("elephant", "medium", []),
    ("robot", "medium", []),
    ("dinosaur", "medium", ["dino", "t rex", "trex"]),
    ("cactus", "medium", []),
    ("scissors", "medium", []),
    ("glasses", "medium", ["eyeglasses", "spectacles", "sunglasses"]),
    ("umbrella", "medium", []),
    ("hamburger", "medium", ["burger", "cheeseburger"]),
    ("light bulb", "medium", ["lightbulb", "bulb"]),
    ("headphones", "medium", ["headset"]),
    ("crown", "medium", []),
    ("anchor", "medium", []),
    ("kite", "medium", []),
    ("penguin", "medium", []),
    ("turtle", "medium", ["tortoise"]),
    ("snail", "medium", []),
    ("whale", "medium", []),
    ("train", "medium", ["locomotive"]),
    ("camera", "medium", []),
    ("tent", "medium", []),
    ("mountain", "medium", ["mountains"]),
]

GUESS_PROMPT = (
    "You are a playful Pictionary player watching someone draw. "
    "Look at this (possibly unfinished) sketch.{history}\n"
    "If the canvas just has a few lines, still make your best guess with low confidence. "
    "If someone simply wrote letters instead of drawing, tease them for cheating.\n"
    'Reply ONLY JSON: {{"guess": "one or two words", "confidence": 0-100, '
    '"comment": "one short funny sentence reacting to the drawing"}}'
)

JUDGE_PROMPT = (
    "You are a witty, kind-hearted art critic judging a Pictionary sketch. "
    'The player was challenged to draw: "{word}".\n'
    "Score 0-10 how recognisable the drawing is as that thing "
    "(10 = instantly recognisable, 5 = you can tell with squinting, 0 = blank or unrelated). "
    "If they just wrote the word as text instead of drawing it, score it 2 at most and call them out.\n"
    'Reply ONLY JSON: {{"score": integer 0-10, "critique": "1-2 witty sentences about the drawing"}}'
)

app = FastAPI(title="Doodle Duel", version="1.0.0")


# --------------------------------------------------------------------------- errors


class GemmaError(Exception):
    """An error with a message that is safe and friendly to show to players."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@app.exception_handler(RequestValidationError)
async def on_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    log.info("Bad request: %s", exc.errors()[:3])
    return JSONResponse(
        status_code=400,
        content={"ok": False, "error": "That request looked a bit scribbly. Try drawing again!"},
    )


@app.exception_handler(Exception)
async def on_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    log.exception("Unexpected error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"ok": False, "error": "Oops, Gemma tripped over a crayon. Please try again."},
    )


# --------------------------------------------------------------------------- Gemma helpers

_client: genai.Client | None = None


def api_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "").strip()


def get_client() -> genai.Client:
    global _client
    if _client is None:
        key = api_key()
        if not key:
            raise GemmaError("The server has no GEMINI_API_KEY set, so Gemma can't see your drawing yet.")
        _client = genai.Client(api_key=key)
    return _client


def decode_image(data: str) -> tuple[bytes, str]:
    """Accepts raw base64 or a data: URL. Returns (bytes, mime type)."""
    if data.startswith("data:"):
        data = data.partition(",")[2]
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", data), validate=True)
    except (binascii.Error, ValueError):
        raise GemmaError("That drawing didn't arrive in one piece. Try again!") from None
    if not raw:
        raise GemmaError("The canvas looks empty. Draw something first!")
    if len(raw) > MAX_IMAGE_BYTES:
        raise GemmaError("That drawing is too big for Gemma to look at.")
    if raw.startswith(b"\x89PNG"):
        return raw, "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return raw, "image/jpeg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return raw, "image/webp"
    raise GemmaError("That doesn't look like a PNG drawing.")


def parse_json(text: str) -> dict[str, Any] | None:
    """Pulls a JSON object out of a model reply, tolerating code fences and chatter."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    candidates = [text]
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(obj, list) and obj and isinstance(obj[0], dict):
            obj = obj[0]
        if isinstance(obj, dict):
            return {str(k).lower(): v for k, v in obj.items()}
    return None


def friendly_api_error(exc: Exception | None) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "Gemma is thinking really hard... too hard. Try again in a moment!"
    if isinstance(exc, genai_errors.APIError):
        if exc.code == 429:
            return "Gemma needs a breather (rate limit hit). Try again in a few seconds."
        if exc.code and exc.code >= 500:
            return "Gemma's servers are having a moment. Try again shortly."
    return "Gemma couldn't make sense of that one. Keep drawing!"


def is_json_mode_error(exc: genai_errors.APIError) -> bool:
    msg = str(exc).lower()
    return exc.code == 400 and any(s in msg for s in ("json", "mime", "response_mime_type"))


async def ask_gemma(prompt: str, image: bytes, mime: str, temperature: float) -> tuple[dict[str, Any], str]:
    """Sends image + prompt to Gemma 4. Tries JSON mode first, then plain text, then the fallback model."""
    client = get_client()
    last_error: Exception | None = None

    for model in MODELS:
        for json_mode in (True, False):
            config = types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=1024,
                response_mime_type="application/json" if json_mode else None,
            )
            try:
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=model,
                        contents=[types.Part.from_bytes(data=image, mime_type=mime), prompt],
                        config=config,
                    ),
                    timeout=REQUEST_TIMEOUT_S,
                )
            except genai_errors.APIError as exc:
                last_error = exc
                log.warning("Gemma call failed (model=%s, json=%s): %s", model, json_mode, exc)
                if exc.code in (401, 403) or "api key" in str(exc).lower():
                    raise GemmaError("Gemma rejected the API key. Check GEMINI_API_KEY on the server.") from None
                if json_mode and is_json_mode_error(exc):
                    continue  # this model doesn't do JSON mode; ask for plain text instead
                break  # try the fallback model
            except Exception as exc:  # timeouts, network errors, SDK surprises
                last_error = exc
                log.warning("Gemma call failed (model=%s, json=%s): %r", model, json_mode, exc)
                break

            try:
                text = response.text or ""
            except Exception:  # some SDK versions raise when there are no candidates
                text = ""
            data = parse_json(text)
            if data is not None:
                return data, model
            log.warning("Unparseable reply from %s: %.200r", model, text)

    raise GemmaError(friendly_api_error(last_error))


def clean_text(value: Any, max_len: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_len].rstrip()


def to_number(value: Any) -> float | None:
    try:
        return float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- API


class GuessRequest(BaseModel):
    image: str = Field(..., min_length=10, max_length=8_000_000)
    history: list[str] = Field(default_factory=list, max_length=100)


class JudgeRequest(BaseModel):
    image: str = Field(..., min_length=10, max_length=8_000_000)
    word: str = Field(..., min_length=1, max_length=60)


@app.get("/", include_in_schema=False)
async def index():
    if INDEX_FILE.exists():
        return FileResponse(INDEX_FILE, media_type="text/html")
    return HTMLResponse("<h1>Doodle Duel</h1><p>static/index.html is missing.</p>", status_code=500)


@app.get("/health")
async def health():
    return {"ok": True, "models": MODELS, "key_configured": bool(api_key())}


@app.get("/challenge")
async def challenge(avoid: str | None = None):
    pool = [w for w in WORDS if w[0] != (avoid or "").strip().lower()] or WORDS
    word, difficulty, aliases = random.choice(pool)
    return {"ok": True, "word": word, "difficulty": difficulty, "aliases": aliases}


@app.post("/guess")
async def guess(req: GuessRequest):
    history = [clean_text(h, 40) for h in req.history if clean_text(h, 40)][-10:]
    history_line = ""
    if history:
        history_line = (
            f" Your previous guesses were: {', '.join(history)}. The drawing may have grown since then - "
            "repeat a guess only if it still fits best, otherwise try something new."
        )
    try:
        image, mime = decode_image(req.image)
        data, model = await ask_gemma(GUESS_PROMPT.format(history=history_line), image, mime, temperature=0.7)
    except GemmaError as exc:
        return {"ok": False, "error": exc.message, "guess": "", "confidence": 0, "comment": exc.message}

    guess_text = clean_text(data.get("guess"), 60).strip(" .!?\"'")
    guess_text = " ".join(guess_text.split()[:3]) or "hmm..."
    confidence = to_number(data.get("confidence"))
    if confidence is not None and 0 < confidence <= 1 and "." in str(data.get("confidence")):
        confidence *= 100  # model answered 0.8 instead of 80
    confidence = int(round(min(100, max(0, confidence if confidence is not None else 30))))
    comment = clean_text(data.get("comment"), 200) or "I'm squinting really hard at this one."

    return {"ok": True, "guess": guess_text, "confidence": confidence, "comment": comment, "model": model}


@app.post("/judge")
async def judge(req: JudgeRequest):
    word = clean_text(req.word, 40).replace('"', "")
    try:
        image, mime = decode_image(req.image)
        data, model = await ask_gemma(JUDGE_PROMPT.format(word=word), image, mime, temperature=0.6)
    except GemmaError as exc:
        return {"ok": False, "error": exc.message, "score": 0, "critique": exc.message}

    score = to_number(data.get("score"))
    if score is not None and 10 < score <= 100:
        score /= 10  # model answered on a 0-100 scale
    score = int(round(min(10, max(0, score if score is not None else 5))))
    critique = clean_text(data.get("critique"), 400) or "A bold piece. I have no words, only feelings."

    return {"ok": True, "score": score, "critique": critique, "model": model}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
