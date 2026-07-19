# 🔥 BlazeAssit — AI co-host for Blaze streamers

**Live: https://blazeassit.duckdns.org** · Built for the [Blaze Builder Challenge](https://backstage.blaze.stream/announcing-the-blaze-builder-challenge/)

Streamers can't read every chat message, notice every hype spike, or write the
social posts after a 4-hour stream. BlazeAssit is an AI co-host that sits in a
Blaze channel's real-time event feed and does it for them.

## What it does

- **Answers viewer questions in chat** — repetitive questions ("what's your
  schedule?", "do you have a discord?", `!ask ...`) get answered instantly by
  the bot using the streamer's FAQ notes, via Groq LLM + the Blaze chat API.
  An LLM gate decides *whether* a message deserves a bot answer, so it never
  butts into banter.
- **Marks hype moments** — chat-velocity surges, tips (`channel.thanks`), subs
  and raids feed a live hype score; peaks are timestamped on a **Hype
  Timeline** (perfect clip markers) and optionally called out in chat.
- **Coaches engagement** — when chat rate collapses vs. session peak, the
  co-host generates one concrete, actionable topic suggestion for the streamer.
- **Writes the post-stream recap** — when the stream ends it produces a
  summary, timestamped highlights, a ready-to-post tweet and a Discord
  announcement, all copyable from the dashboard.

## Blaze API usage

| Capability | Blaze API |
|---|---|
| OAuth (PKCE) + refresh | `bapi/oauth2/generate-auth-url`, `/token`, `/refresh` |
| Real-time events | Socket.IO `wss://blaze.stream/ws` + `POST /v1/events/subscriptions` (10 event types: chat, follows, subs, gifts, tips, raids, votes, stream online/offline, channel update) |
| Bot chat replies | `POST /v1/chats/messages` (scopes `users.bot`, `channel.moderate`, respects the 20 msg/30s limit) |
| Channel resolution & stats | `GET /v1/channels`, `GET /v1/channels/live-stats` |

## Architecture

```
Blaze EventSub (Socket.IO) ──► bot.py ──► engine.py ──► Groq LLM
                                             │              │
        chat replies ◄── blaze.py ◄──────────┴──► dashboard (FastAPI + static)
```

Single FastAPI process: the Socket.IO listener runs in the app's lifespan, all
LLM work happens off the event loop, and the dashboard polls `/api/state`.
A built-in **demo mode** pushes synthetic viewers through the exact same
pipeline, so the co-host can be demonstrated without a live audience.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install fastapi uvicorn httpx groq python-dotenv "python-socketio[asyncio_client]"
cp .env.example .env   # fill in Blaze client id/secret + Groq key
.venv/bin/uvicorn main:app --port 8020
```

Then open the dashboard, hit **Connect your Blaze account**, set your channel
slug and FAQ notes, and go live.

## Stack

FastAPI · python-socketio · Groq (`llama-3.1-8b-instant`) · vanilla-JS dashboard · Caddy · pm2
