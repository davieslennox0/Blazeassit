"""Blaze OAuth2: auth-url generation, code exchange, refresh, token storage."""

import json
import logging
import time

import httpx

from app.config import (
    BLAZE_CLIENT_ID,
    BLAZE_CLIENT_SECRET,
    BLAZE_SITE,
    REDIRECT_URI,
    SCOPES,
    TOKENS_FILE,
)

log = logging.getLogger(__name__)

# state -> codeVerifier for in-flight authorizations
_pending: dict[str, str] = {}


def _pick(d: dict, *names):
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return None


def load_tokens() -> dict | None:
    if TOKENS_FILE.exists():
        return json.loads(TOKENS_FILE.read_text())
    return None


def save_tokens(raw: dict):
    access = _pick(raw, "accessToken", "access_token", "token")
    refresh = _pick(raw, "refreshToken", "refresh_token")
    expires = _pick(raw, "expiresIn", "expires_in") or 3600
    data = {
        "access": access,
        "refresh": refresh,
        "expires_at": time.time() + float(expires) - 60,
        "raw": raw,
    }
    TOKENS_FILE.write_text(json.dumps(data, indent=2))
    return data


async def generate_auth_url() -> str:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{BLAZE_SITE}/bapi/oauth2/generate-auth-url",
            json={
                "clientId": BLAZE_CLIENT_ID,
                "clientSecret": BLAZE_CLIENT_SECRET,
                "redirectUri": REDIRECT_URI,
                "scopes": SCOPES,
            },
        )
    body = r.json()
    if r.status_code != 200:
        raise RuntimeError(f"generate-auth-url {r.status_code}: {body}")
    inner = body.get("data") if isinstance(body.get("data"), dict) else body
    url = _pick(inner, "url", "authUrl", "authorizationUrl", "authorization_url")
    state = _pick(inner, "state", "stateNonce")
    verifier = _pick(inner, "codeVerifier", "code_verifier", "verifier")
    if not url:
        raise RuntimeError(f"no auth url in response: {body}")
    if state and verifier:
        _pending[state] = verifier
    return url


async def exchange_code(code: str, state: str | None) -> dict:
    verifier = _pending.pop(state, None) if state else None
    payload = {
        "clientId": BLAZE_CLIENT_ID,
        "clientSecret": BLAZE_CLIENT_SECRET,
        "code": code,
        "redirectUri": REDIRECT_URI,
        "grantType": "authorization_code",
    }
    if verifier:
        payload["codeVerifier"] = verifier
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{BLAZE_SITE}/bapi/oauth2/token", json=payload)
    body = r.json()
    if r.status_code != 200:
        raise RuntimeError(f"token exchange {r.status_code}: {body}")
    inner = body.get("data") if isinstance(body.get("data"), dict) else body
    return save_tokens(inner)


async def refresh_if_needed() -> dict | None:
    tokens = load_tokens()
    if not tokens:
        return None
    if time.time() < tokens["expires_at"]:
        return tokens
    if not tokens.get("refresh"):
        return tokens
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{BLAZE_SITE}/bapi/oauth2/refresh",
            json={
                "clientId": BLAZE_CLIENT_ID,
                "clientSecret": BLAZE_CLIENT_SECRET,
                "refreshToken": tokens["refresh"],
            },
        )
    body = r.json()
    if r.status_code != 200:
        log.error("token refresh failed %s: %s", r.status_code, body)
        return tokens
    inner = body.get("data") if isinstance(body.get("data"), dict) else body
    # Some providers omit the refresh token on refresh — keep the old one.
    if not _pick(inner, "refreshToken", "refresh_token"):
        inner["refreshToken"] = tokens["refresh"]
    return save_tokens(inner)
