#!/usr/bin/env python3
"""
S&P Global Press Room monitor.
Checks daily (Mon–Fri) for new S&P 500 constituent change announcements.
Posts to Discord only when a new announcement is found — silent on quiet days.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
STATE_FILE = os.path.join(os.path.dirname(__file__), "spglobal_state.json")
PRESS_ROOM_URL = "https://press.spglobal.com/index.php?s=2429&l=25"

COLOR_ALERT = 0x4A90D9   # blue — official announcement
COLOR_ERROR = 0xD9534F   # red  — errors


# ─── State ────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"seen_urls": [], "last_run": None}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ─── Scraping ─────────────────────────────────────────────────────────────────

def _make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def fetch_announcements() -> list[dict]:
    """
    Scrapes S&P Global press room for index constituent change announcements.
    Returns list of dicts: {date, title, url} — filtered to S&P 500 only.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    session = _make_session()
    resp = session.get(PRESS_ROOM_URL, headers=headers, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    items = soup.find_all("li", class_="wd_item")

    if not items:
        raise RuntimeError("No press release items found — page structure may have changed.")

    announcements = []
    for item in items:
        date_tag = item.find("div", class_="wd_date")
        title_tag = item.find("div", class_="wd_title")
        if not date_tag or not title_tag:
            continue

        link = title_tag.find("a")
        if not link:
            continue

        title = link.get_text(strip=True)
        url = link.get("href", "")
        date_str = date_tag.get_text(strip=True)

        if "S&P 500" not in title:
            continue

        announcements.append({"date": date_str, "title": title, "url": url})

    return announcements


# ─── Discord ──────────────────────────────────────────────────────────────────

def _post_embed(embeds: list[dict]):
    if not DISCORD_WEBHOOK_URL:
        print("[WARN] DISCORD_WEBHOOK_URL not set — printing embed instead.")
        print(json.dumps(embeds, indent=2))
        return

    for i in range(0, len(embeds), 10):
        resp = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"embeds": embeds[i:i + 10]},
            timeout=15,
        )
        resp.raise_for_status()


def post_announcement(announcement: dict):
    embed = {
        "title":       "📢  S&P 500 — Official Announcement",
        "description": f"**{announcement['title']}**",
        "url":         announcement["url"],
        "color":       COLOR_ALERT,
        "fields": [
            {
                "name":   "📅  Announced",
                "value":  announcement["date"],
                "inline": True,
            },
            {
                "name":   "🔗  Full Press Release",
                "value":  f"[Read on S&P Global]({announcement['url']})",
                "inline": True,
            },
        ],
        "footer":    {"text": "Source: S&P Global Press Room  •  Byzantium Technologies"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _post_embed([embed])
    print(f"[OK] Posted: {announcement['title']}")


def post_error(error_msg: str):
    embed = {
        "title":       "🔴  S&P Global Monitor — Scrape Error",
        "description": f"```{error_msg[:1800]}```",
        "color":       COLOR_ERROR,
        "footer":      {"text": "Byzantium Technologies"},
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    }
    _post_embed([embed])


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="S&P Global Press Room Monitor")
    parser.add_argument(
        "--test",
        action="store_true",
        help="Force-post the most recent announcement regardless of seen state (for testing).",
    )
    args = parser.parse_args()

    state = load_state()
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    seen_urls = set(state.get("seen_urls", []))

    print("[INFO] Fetching S&P Global press room...")

    try:
        announcements = fetch_announcements()
    except Exception as exc:
        msg = f"Failed to fetch/parse S&P Global press room: {exc}"
        print(f"[ERROR] {msg}", file=sys.stderr)
        try:
            post_error(msg)
        except Exception:
            pass
        save_state(state)
        sys.exit(1)

    print(f"[INFO] Found {len(announcements)} S&P 500 announcement(s) on page.")

    if args.test:
        if announcements:
            print(f"[TEST] Forcing post of: {announcements[0]['title']}")
            post_announcement(announcements[0])
        else:
            print("[TEST] No announcements found to test with.")
        save_state(state)
        return

    is_first_run = not seen_urls
    new_announcements = [a for a in announcements if a["url"] not in seen_urls]

    if new_announcements:
        seen_urls.update(a["url"] for a in new_announcements)
        if is_first_run:
            print(f"[INFO] First run: seeding {len(new_announcements)} historical announcement(s) — no Discord post.")
        else:
            for announcement in reversed(new_announcements):  # oldest first
                post_announcement(announcement)
            print(f"[INFO] Posted {len(new_announcements)} new announcement(s).")
    else:
        print("[INFO] No new S&P 500 announcements.")

    state["seen_urls"] = list(seen_urls)
    save_state(state)
    print("[DONE]")


if __name__ == "__main__":
    main()
