"""Groq LLM calls: viewer Q&A, topic suggestions, and post-stream recaps."""

import json
import logging

from groq import Groq

from app.config import GROQ_API_KEY, GROQ_MODEL

log = logging.getLogger(__name__)
_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None


def _chat(system: str, user: str, max_tokens=400) -> str:
    if _client is None:
        return ""
    resp = _client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.6,
        max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()


def answer_viewer(question: str, streamer: str, faq: str, context: str) -> str:
    system = (
        f"You are BlazeAssit, the AI co-host in {streamer}'s live stream chat on Blaze. "
        "Answer the viewer's question in ONE short, friendly chat message (max 2 sentences, "
        "no markdown). Use the streamer's FAQ notes when relevant. If you genuinely don't "
        "know, say the streamer will answer, don't invent facts.\n\n"
        f"Streamer FAQ notes:\n{faq or '(none provided)'}\n\nLive context:\n{context}"
    )
    return _chat(system, question, max_tokens=120)


def should_answer(message: str, faq: str) -> bool:
    """Cheap gate: only call the full answer path for actual questions."""
    m = message.strip().lower()
    if m.startswith("!ask") or "blazeassit" in m:
        return True
    if not m.endswith("?") or len(m) < 8:
        return False
    verdict = _chat(
        "Reply with only YES or NO. A streamer's FAQ bot should answer a chat message "
        "only if it is a genuine question a bot could help with (about the stream, "
        "schedule, setup, the streamer, or covered by the FAQ) — not banter aimed at "
        "other viewers.\n\nFAQ notes:\n" + (faq or "(none)"),
        message,
        max_tokens=4,
    )
    return verdict.upper().startswith("Y")


def suggest_topic(streamer: str, category: str, recent_chat: str) -> str:
    system = (
        "You coach live streamers in real time. Chat engagement is dropping. Based on the "
        "recent chat, suggest ONE concrete thing the streamer can do or talk about right "
        "now to re-engage viewers. One sentence, actionable, no preamble."
    )
    user = f"Streamer: {streamer}\nCategory: {category or 'unknown'}\nRecent chat:\n{recent_chat}"
    return _chat(system, user, max_tokens=80)


def recap(streamer: str, stats: dict, timeline: str, chat_sample: str) -> dict:
    system = (
        "You write post-stream recaps. Reply with ONLY a JSON object, no prose, shape: "
        '{"summary": str (3-5 sentence stream summary), '
        '"timestamps": [{"t": "HH:MM", "note": str}, ...], '
        '"tweet": str (a hype tweet under 260 chars with 2 hashtags), '
        '"discord": str (a Discord announcement with light markdown)}'
    )
    user = (
        f"Streamer: {streamer}\nSession stats: {json.dumps(stats)}\n"
        f"Event timeline (HH:MM events):\n{timeline}\n\nChat sample:\n{chat_sample}"
    )
    raw = _chat(system, user, max_tokens=700)
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        return json.loads(raw[start:end])
    except Exception:
        return {"summary": raw, "timestamps": [], "tweet": "", "discord": ""}
