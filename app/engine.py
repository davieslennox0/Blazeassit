"""Session state: chat velocity, hype detection, engagement watch, recap data."""

import json
import logging
import time
from collections import deque
from datetime import datetime, timezone

from app import blaze, llm
from app.config import EVENTS_FILE, SETTINGS_FILE

log = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    "channel_slug": "",
    "channel_id": "",
    "streamer_name": "",
    "category": "",
    "faq": "",
    "bot_enabled": True,
    "hype_callouts": True,
}


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        return {**DEFAULT_SETTINGS, **json.loads(SETTINGS_FILE.read_text())}
    return dict(DEFAULT_SETTINGS)


def save_settings(s: dict):
    SETTINGS_FILE.write_text(json.dumps(s, indent=2))


class Engine:
    def __init__(self):
        self.loop = None  # main asyncio loop, set at startup (handlers run in threads)
        self.demo_mode = False  # while True, never post to the real Blaze chat
        self.settings = load_settings()
        self.connected = False
        self.session_id = None
        self.live = False
        self.session_start = None
        self.viewers = 0
        self.chat_times = deque(maxlen=2000)      # timestamps of chat messages
        self.recent_chat = deque(maxlen=60)       # (time, user, text)
        self.baseline = 0.0                       # EMA of chat msgs/min
        self.peak_rate = 0.0
        self.hype_moments = []                    # {t, score, reason}
        self.suggestions = []                     # {t, text}
        self.answers = []                         # {t, user, q, a}
        self.counts = {"chat": 0, "follows": 0, "subs": 0, "tips": 0, "raids": 0}
        self.recap = None
        self.last_answer_at = 0.0
        self.last_suggestion_at = 0.0
        self.last_hype_at = 0.0
        self.sent_minute = deque(maxlen=20)       # our sends, for the 20/30s API limit

    # ---------- persistence ----------
    def log_event(self, kind: str, data: dict):
        row = {"t": datetime.now(timezone.utc).isoformat(), "kind": kind, **data}
        with EVENTS_FILE.open("a") as f:
            f.write(json.dumps(row) + "\n")

    # ---------- metrics ----------
    def chat_rate(self, window=60.0) -> float:
        """Messages per minute over the trailing window."""
        cutoff = time.time() - window
        n = sum(1 for t in self.chat_times if t > cutoff)
        return n * (60.0 / window)

    def _mark_hype(self, score: float, reason: str):
        now = time.time()
        if now - self.last_hype_at < 120:
            return
        self.last_hype_at = now
        moment = {"t": self._clock(), "score": round(score, 1), "reason": reason}
        self.hype_moments.append(moment)
        self.log_event("hype", moment)
        if self.settings.get("hype_callouts") and self.settings.get("bot_enabled"):
            self._queue_chat(f"🔥 Chat is ON FIRE — clip this moment! ({reason})")

    def _clock(self) -> str:
        if self.session_start:
            mins = int((time.time() - self.session_start) // 60)
            return f"{mins // 60:02d}:{mins % 60:02d}"
        return datetime.now(timezone.utc).strftime("%H:%M")

    # ---------- chat sending (rate-limited fire and forget) ----------
    def _can_send(self) -> bool:
        cutoff = time.time() - 30
        return sum(1 for t in self.sent_minute if t > cutoff) < 18

    def _queue_chat(self, text: str, reply_to: str | None = None):
        import asyncio

        if self.demo_mode or not self.settings.get("channel_id") or not self._can_send() or self.loop is None:
            return
        self.sent_minute.append(time.time())

        async def _send():
            try:
                await blaze.send_chat(self.settings["channel_id"], text, reply_to)
            except Exception as e:
                log.warning("chat send failed: %s", e)

        # Handlers run in worker threads (LLM calls block); hop back to the main loop.
        asyncio.run_coroutine_threadsafe(_send(), self.loop)

    # ---------- event handlers ----------
    def on_chat(self, user: str, text: str, message_id: str | None, user_id: str | None):
        now = time.time()
        self.chat_times.append(now)
        self.recent_chat.append((self._clock(), user, text))
        self.counts["chat"] += 1
        self.log_event("chat", {"user": user, "text": text})

        rate = self.chat_rate(30)
        self.baseline = 0.98 * self.baseline + 0.02 * rate if self.baseline else rate
        self.peak_rate = max(self.peak_rate, rate)
        if rate > 12 and self.baseline > 0 and rate > 3 * self.baseline:
            self._mark_hype(rate, f"chat surge {int(rate)} msgs/min")

        if self.settings.get("bot_enabled") and now - self.last_answer_at > 15:
            q = text.strip()
            if q.lower().startswith("!ask"):
                q = q[4:].strip()
            if llm.should_answer(text, self.settings.get("faq", "")):
                self.last_answer_at = now
                answer = llm.answer_viewer(
                    q,
                    self.settings.get("streamer_name") or self.settings.get("channel_slug"),
                    self.settings.get("faq", ""),
                    f"live={self.live}, viewers={self.viewers}, "
                    f"category={self.settings.get('category')}",
                )
                if answer:
                    self.answers.append({"t": self._clock(), "user": user, "q": text, "a": answer})
                    self.log_event("answer", {"user": user, "q": text, "a": answer})
                    self._queue_chat(f"@{user} {answer}", reply_to=message_id)

    def on_signal(self, kind: str, weight: float, detail: str):
        """Follows, subs, tips, raids — all feed the hype score."""
        self.counts[kind] = self.counts.get(kind, 0) + 1
        self.log_event(kind, {"detail": detail})
        rate = self.chat_rate(30) + weight * 6
        if weight >= 5:
            self._mark_hype(rate, detail)

    def on_stream(self, online: bool):
        self.live = online
        self.log_event("stream", {"online": online})
        if online:
            self.session_start = time.time()
            self.hype_moments, self.suggestions, self.answers = [], [], []
            self.counts = {k: 0 for k in self.counts}
            self.recap = None
        # recap generation on offline is triggered from the bot loop (async)

    # ---------- periodic engagement check ----------
    def engagement_check(self):
        if not self.live or not self.session_start:
            return
        now = time.time()
        if now - self.session_start < 600 or now - self.last_suggestion_at < 600:
            return
        rate = self.chat_rate(180)
        if self.peak_rate >= 5 and rate < 0.25 * self.peak_rate:
            self.last_suggestion_at = now
            chat = "\n".join(f"[{t}] {u}: {m}" for t, u, m in list(self.recent_chat)[-20:])
            text = llm.suggest_topic(
                self.settings.get("streamer_name") or "the streamer",
                self.settings.get("category", ""),
                chat,
            )
            if text:
                s = {"t": self._clock(), "text": text}
                self.suggestions.append(s)
                self.log_event("suggestion", s)

    # ---------- recap ----------
    def build_recap(self) -> dict:
        mins = int((time.time() - self.session_start) // 60) if self.session_start else 0
        stats = {**self.counts, "duration_min": mins, "peak_chat_per_min": int(self.peak_rate)}
        timeline = "\n".join(
            f"[{h['t']}] HYPE ({h['reason']})" for h in self.hype_moments
        ) or "(no marked moments)"
        chat = "\n".join(f"[{t}] {u}: {m}" for t, u, m in list(self.recent_chat)[-40:])
        self.recap = llm.recap(
            self.settings.get("streamer_name") or self.settings.get("channel_slug") or "streamer",
            stats,
            timeline,
            chat,
        )
        self.recap["stats"] = stats
        self.log_event("recap", {})
        return self.recap

    # ---------- dashboard state ----------
    def state(self) -> dict:
        return {
            "connected": self.connected,
            "live": self.live,
            "viewers": self.viewers,
            "chat_rate": round(self.chat_rate(60), 1),
            "peak_rate": round(self.peak_rate, 1),
            "counts": self.counts,
            "session_start": self.session_start,
            "hype": self.hype_moments[-20:],
            "suggestions": self.suggestions[-10:],
            "answers": self.answers[-15:],
            "recent_chat": [
                {"t": t, "user": u, "text": m} for t, u, m in list(self.recent_chat)[-25:]
            ],
            "recap": self.recap,
            "settings": {k: v for k, v in self.settings.items()},
            "authorized": True,
        }


engine = Engine()
