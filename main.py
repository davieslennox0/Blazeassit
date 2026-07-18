"""BlazeAssit — AI co-host for Blaze streamers.

Built for the Blaze Builder Challenge. Listens to a channel's EventSub feed,
auto-answers viewer questions with Groq, marks hype moments, nudges the
streamer when engagement dips, and writes the post-stream recap pack.
"""

import asyncio
import logging
import random
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from app import blaze, bot, oauth
from app.config import DASH_KEY
from app.engine import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("blazeassit")

STATIC = Path(__file__).parent / "static"
_demo_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine.loop = asyncio.get_running_loop()
    task = asyncio.create_task(bot.run())
    yield
    task.cancel()


app = FastAPI(title="BlazeAssit", lifespan=lifespan)


def _check_key(request: Request):
    if DASH_KEY and request.headers.get("x-dash-key") != DASH_KEY:
        raise HTTPException(401, "bad dashboard key")


@app.get("/")
def index():
    return FileResponse(STATIC / "landing.html")


@app.get("/dashboard")
def dashboard():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/login")
async def login():
    url = await oauth.generate_auth_url()
    return RedirectResponse(url)


@app.get("/callback")
async def callback(code: str = "", state: str = ""):
    if not code:
        raise HTTPException(400, "missing code")
    await oauth.exchange_code(code, state)
    return RedirectResponse("/dashboard?authorized=1")


@app.get("/api/state")
def state():
    s = engine.state()
    s["authorized"] = oauth.load_tokens() is not None
    s["demo_running"] = _demo_task is not None and not _demo_task.done()
    return s


@app.post("/api/settings")
async def update_settings(request: Request):
    _check_key(request)
    body = await request.json()
    allowed = {"channel_slug", "streamer_name", "category", "faq", "bot_enabled", "hype_callouts"}
    for k, v in body.items():
        if k in allowed:
            engine.settings[k] = v
    slug = engine.settings.get("channel_slug")
    if slug and body.get("channel_slug"):
        try:
            ch = await blaze.resolve_channel(slug)
            if ch:
                engine.settings["channel_id"] = ch.get("id") or ch.get("channelId") or ""
                engine.settings["streamer_name"] = (
                    engine.settings.get("streamer_name")
                    or ch.get("displayName")
                    or ch.get("name")
                    or slug
                )
        except Exception as e:
            log.warning("channel resolve failed: %s", e)
    from app.engine import save_settings

    save_settings(engine.settings)
    return {"ok": True, "channel_id": engine.settings.get("channel_id")}


_last_recap = 0.0


@app.post("/api/recap")
async def make_recap():
    global _last_recap
    if time.time() - _last_recap < 60:
        raise HTTPException(429, "recap was just generated — try again in a minute")
    _last_recap = time.time()
    return await asyncio.to_thread(engine.build_recap)


# ---------------- demo mode ----------------
# Feeds synthetic viewers through the exact same pipeline as real EventSub
# traffic so the co-host can be demonstrated without a live audience.

DEMO_CHAT = [
    ("nova_kid", "yo this stream is fire"), ("pix3l", "what game is this?"),
    ("markus", "how long have you been streaming?"), ("jjboss", "LMAOOO"),
    ("tessa", "W streamer"), ("crow", "what's your setup? camera looks clean"),
    ("nova_kid", "when is the next stream?"), ("pix3l", "no way he hit that"),
    ("markus", "clip it clip it"), ("tessa", "do you have a discord?"),
    ("jjboss", "!ask what days do you stream"), ("crow", "GG"),
    ("l1am", "first time here, this is great"), ("sofia", "how do I sub on blaze?"),
]


async def _demo(minutes: float):
    engine.demo_mode = True
    engine.on_stream(True)
    engine.viewers = random.randint(20, 40)
    end = time.time() + minutes * 60
    try:
        while time.time() < end:
            user, text = random.choice(DEMO_CHAT)
            await asyncio.to_thread(engine.on_chat, f"{user}", text, None, None)
            if random.random() < 0.12:
                engine.on_signal("follows", 2, f"{random.choice(DEMO_CHAT)[0]} followed")
            if random.random() < 0.05:
                engine.on_signal("tips", 5, f"{random.choice(DEMO_CHAT)[0]} sent a tip")
            # occasional burst to trigger hype detection
            if random.random() < 0.08:
                for _ in range(random.randint(8, 14)):
                    u, t = random.choice(DEMO_CHAT)
                    await asyncio.to_thread(engine.on_chat, u, t, None, None)
                    await asyncio.sleep(0.3)
            engine.viewers = max(5, engine.viewers + random.randint(-3, 3))
            await asyncio.sleep(random.uniform(2, 7))
        await asyncio.to_thread(engine.build_recap)
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("demo run failed")
    finally:
        # Whatever happens, the demo must not leave the dashboard stuck on LIVE.
        engine.demo_mode = False
        engine.live = False
        engine.viewers = 0


@app.post("/api/demo")
async def demo(request: Request):
    global _demo_task
    body = await request.json()
    if _demo_task and not _demo_task.done():
        _demo_task.cancel()
        _demo_task = None
        return {"ok": True, "demo": "stopped"}
    minutes = min(float(body.get("minutes", 3)), 5)
    _demo_task = asyncio.create_task(_demo(minutes))
    return {"ok": True, "demo": "started"}
