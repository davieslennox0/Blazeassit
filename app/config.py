"""Environment-driven configuration for BlazeAssit."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

BLAZE_CLIENT_ID = os.getenv("BLAZE_CLIENT_ID", "")
BLAZE_CLIENT_SECRET = os.getenv("BLAZE_CLIENT_SECRET", "")
BASE_URL = os.getenv("BASE_URL", "https://blazeassit.duckdns.org").rstrip("/")
REDIRECT_URI = f"{BASE_URL}/callback"

BLAZE_SITE = "https://blaze.stream"
BLAZE_API = "https://api.blaze.stream/v1"

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Simple shared secret guarding mutating dashboard endpoints.
DASH_KEY = os.getenv("DASH_KEY", "")

SCOPES = ["users.read", "users.bot", "channel.moderate", "offline.access"]

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
TOKENS_FILE = DATA_DIR / "tokens.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
EVENTS_FILE = DATA_DIR / "events.ndjson"
