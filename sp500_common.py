import json
import os
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()

COLOR_ERROR = 0xD9534F

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
_session: requests.Session | None = None


def get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        _session.mount("https://", HTTPAdapter(max_retries=retry))
    return _session


def load_state(path: str, default: dict) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default.copy()


def save_state(path: str, state: dict):
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def post_embeds(embeds: list[dict]):
    if not _WEBHOOK_URL:
        print("[WARN] DISCORD_WEBHOOK_URL not set — printing embed instead.")
        print(json.dumps(embeds, indent=2))
        return
    session = get_session()
    for i in range(0, len(embeds), 10):  # Discord allows max 10 embeds per request
        resp = session.post(
            _WEBHOOK_URL,
            json={"embeds": embeds[i:i + 10]},
            timeout=15,
        )
        resp.raise_for_status()


def post_error(source_title: str, error_msg: str):
    embed = {
        "title":       f"🔴  {source_title} — Scrape Error",
        "description": f"```{error_msg[:1800]}```",
        "color":       COLOR_ERROR,
        "footer":      {"text": "Byzantium Technologies"},
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    }
    post_embeds([embed])
