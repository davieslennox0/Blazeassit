"""Thin async client for the Blaze REST API (user-token flows)."""

import logging

import httpx

from app.config import BLAZE_API, BLAZE_CLIENT_ID, BLAZE_CLIENT_SECRET
from app import oauth

log = logging.getLogger(__name__)


async def _headers() -> dict:
    tokens = await oauth.refresh_if_needed()
    if not tokens or not tokens.get("access"):
        raise RuntimeError("not authorized — complete OAuth first")
    return {
        "Authorization": f"Bearer {tokens['access']}",
        "client-id": BLAZE_CLIENT_ID,
        "secret": BLAZE_CLIENT_SECRET,
        "content-type": "application/json",
    }


async def get(path: str, params=None):
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{BLAZE_API}{path}", params=params, headers=await _headers())
    body = r.json() if r.content else {}
    if r.status_code != 200:
        raise RuntimeError(f"GET {path} {r.status_code}: {body}")
    return body


async def post(path: str, json_body: dict):
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{BLAZE_API}{path}", json=json_body, headers=await _headers())
    body = r.json() if r.content else {}
    if r.status_code not in (200, 201):
        raise RuntimeError(f"POST {path} {r.status_code}: {body}")
    return body


async def resolve_channel(slug: str) -> dict | None:
    body = await get("/channels", params={"slug[]": slug, "type": "all", "limit": 5})
    rows = body.get("data") or body.get("channels") or body
    if isinstance(rows, dict):
        rows = rows.get("rows") or rows.get("channels") or rows.get("items") or []
    for ch in rows or []:
        if (ch.get("slug") or "").lower() == slug.lower():
            return ch
    return (rows or [None])[0]


async def live_stats(channel_id: str) -> dict:
    body = await get("/channels/live-stats", params={"channelId": channel_id})
    return body.get("data") if isinstance(body.get("data"), dict) else body


async def send_chat(channel_id: str, message: str, reply_to: str | None = None):
    payload = {"channelId": channel_id, "message": message[:400]}
    if reply_to:
        payload["replyToMessageId"] = reply_to
    return await post("/chats/messages", payload)


async def subscribe(session_id: str, channel_id: str, event_type: str):
    return await post(
        "/events/subscriptions",
        {
            "type": event_type,
            "version": "1",
            "sessionId": session_id,
            "condition": {"channelId": channel_id},
        },
    )
