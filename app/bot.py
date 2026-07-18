"""Socket.IO listener: connects to Blaze EventSub, subscribes the configured
channel, and routes events into the engine. Payload shapes are parsed
defensively — the API is young and envelope fields vary."""

import asyncio
import logging

import socketio

from app import blaze, oauth
from app.config import BLAZE_SITE
from app.engine import engine

log = logging.getLogger(__name__)

EVENT_TYPES = [
    "channel.chat.message",
    "channel.follow",
    "channel.subscribe",
    "channel.subscription.gift",
    "channel.thanks",
    "channel.raid",
    "channel.vote",
    "stream.online",
    "stream.offline",
    "channel.update",
]

sio = socketio.AsyncClient(reconnection=True, reconnection_delay=5, logger=False)
_subscribed_channel: str | None = None


def _find(obj, *keys):
    """Depth-first search for the first matching key in nested dicts."""
    if isinstance(obj, dict):
        for k in keys:
            if k in obj and obj[k] is not None:
                return obj[k]
        for v in obj.values():
            hit = _find(v, *keys)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _find(v, *keys)
            if hit is not None:
                return hit
    return None


async def _subscribe_all():
    global _subscribed_channel
    channel_id = engine.settings.get("channel_id")
    if not (engine.session_id and channel_id):
        return
    ok = 0
    for ev in EVENT_TYPES:
        try:
            await blaze.subscribe(engine.session_id, channel_id, ev)
            ok += 1
        except Exception as e:
            log.warning("subscribe %s failed: %s", ev, e)
    _subscribed_channel = channel_id if ok else None
    log.info("subscribed %d/%d events for channel %s", ok, len(EVENT_TYPES), channel_id)


async def _handle(data):
    msg_type = _find(data, "messageType", "message_type") or ""
    if msg_type == "session_welcome" or _find(data, "sessionId", "session_id"):
        if msg_type in ("session_welcome", ""):
            sid = _find(data, "sessionId", "session_id") or _find(data, "id")
            if sid and msg_type == "session_welcome":
                engine.session_id = sid
                engine.connected = True
                log.info("eventsub session %s", sid)
                await _subscribe_all()
                return
    if msg_type == "session_keepalive":
        return

    sub_type = _find(data, "subscriptionType", "subscription_type") or _find(data, "type") or ""
    event = _find(data, "event") or data

    if sub_type == "channel.chat.message":
        user = _find(event, "username", "displayName", "display_name", "name") or "viewer"
        text = _find(event, "message", "text", "content", "body") or ""
        if isinstance(text, dict):
            text = _find(text, "text", "content") or ""
        mid = _find(event, "messageId", "message_id", "id")
        uid = _find(event, "userId", "user_id")
        if text:
            await asyncio.to_thread(engine.on_chat, user, str(text), mid, uid)
    elif sub_type == "channel.follow":
        who = _find(event, "username", "displayName", "name") or "someone"
        engine.on_signal("follows", 2, f"{who} followed")
    elif sub_type in ("channel.subscribe", "channel.subscription.gift"):
        who = _find(event, "username", "displayName", "name") or "someone"
        engine.on_signal("subs", 8, f"{who} subscribed")
    elif sub_type == "channel.thanks":
        who = _find(event, "username", "displayName", "name") or "someone"
        engine.on_signal("tips", 5, f"{who} sent a tip")
    elif sub_type == "channel.raid":
        engine.on_signal("raids", 10, "incoming raid")
    elif sub_type == "stream.online":
        engine.on_stream(True)
    elif sub_type == "stream.offline":
        engine.on_stream(False)
        await asyncio.to_thread(engine.build_recap)
    elif sub_type == "channel.update":
        viewers = _find(event, "viewers", "viewerCount", "viewer_count")
        if viewers is not None:
            engine.viewers = int(viewers)
    if sub_type:
        log.debug("event %s", sub_type)


@sio.on("*")
async def catch_all(event, *args):
    try:
        await _handle(args[0] if args else event)
    except Exception:
        log.exception("event handling failed")


@sio.event
async def connect():
    log.info("socket connected, waiting for session_welcome")


@sio.event
async def disconnect():
    engine.connected = False
    engine.session_id = None


async def run():
    """Connect when authorized; re-subscribe if the channel changes; poll stats."""
    global _subscribed_channel
    tick = 0
    while True:
        try:
            if oauth.load_tokens() and not sio.connected:
                await sio.connect(BLAZE_SITE, socketio_path="/ws", transports=["websocket"])
            if sio.connected and engine.session_id:
                if engine.settings.get("channel_id") != _subscribed_channel:
                    await _subscribe_all()
            # periodic work
            tick += 1
            if tick % 2 == 0:
                await asyncio.to_thread(engine.engagement_check)
            if tick % 4 == 0 and engine.settings.get("channel_id"):
                try:
                    stats = await blaze.live_stats(engine.settings["channel_id"])
                    engine.viewers = int(
                        _find(stats, "viewers", "currentViewers", "viewerCount") or 0
                    )
                    live = _find(stats, "isLive", "live", "is_live")
                    if live is not None and bool(live) != engine.live:
                        engine.on_stream(bool(live))
                        if not live:
                            await asyncio.to_thread(engine.build_recap)
                except Exception as e:
                    log.debug("live-stats poll: %s", e)
        except Exception as e:
            log.warning("bot loop: %s", e)
        await asyncio.sleep(15)
