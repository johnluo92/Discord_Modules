#!/usr/bin/env python3
"""
S&P Global Press Room monitor.
Checks daily (Mon–Fri) for new S&P 500 constituent change announcements.
Posts to Discord only when a new announcement is found — silent on quiet days.
"""

import argparse
import os
import sys
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from sp500_common import USER_AGENT, get_session, load_state, post_embeds, post_error, save_state

STATE_FILE = os.path.join(os.path.dirname(__file__), "spglobal_state.json")
PRESS_ROOM_URL = "https://press.spglobal.com/index.php?s=2429&l=25"

_STATE_DEFAULT = {"seen_urls": [], "last_run": None}

COLOR_ALERT = 0x4A90D9   # blue — official announcement


# ─── Scraping ─────────────────────────────────────────────────────────────────

def fetch_announcements() -> list[dict]:
    resp = get_session().get(PRESS_ROOM_URL, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    items = soup.find_all("li", class_="wd_item")

    if not items:
        raise RuntimeError("No press release items found — page structure may have changed.")

    announcements = []
    for item in items:
        date_tag  = item.find("div", class_="wd_date")
        title_tag = item.find("div", class_="wd_title")
        if not date_tag or not title_tag:
            continue

        link = title_tag.find("a")
        if not link:
            continue

        title    = link.get_text(strip=True)
        url      = link.get("href", "")
        date_str = date_tag.get_text(strip=True)

        if "S&P 500" not in title:
            continue

        announcements.append({"date": date_str, "title": title, "url": url})

    return announcements


# ─── Discord ──────────────────────────────────────────────────────────────────

def post_announcement(announcement: dict):
    embed = {
        "title":       "📢  S&P 500 — Official Announcement",
        "description": f"**{announcement['title']}**",
        "url":         announcement["url"],
        "color":       COLOR_ALERT,
        "fields": [
            {"name": "📅  Announced",        "value": announcement["date"],                              "inline": True},
            {"name": "🔗  Full Press Release","value": f"[Read on S&P Global]({announcement['url']})",  "inline": True},
        ],
        "footer":    {"text": "Source: S&P Global Press Room  •  Byzantium Technologies"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    post_embeds([embed])
    print(f"[OK] Posted: {announcement['title']}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="S&P Global Press Room Monitor")
    parser.add_argument("--test", action="store_true",
                        help="Force-post the most recent announcement regardless of seen state.")
    args = parser.parse_args()

    state = load_state(STATE_FILE, _STATE_DEFAULT)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    seen_urls = set(state.get("seen_urls", []))

    print("[INFO] Fetching S&P Global press room...")

    try:
        announcements = fetch_announcements()
    except Exception as exc:
        msg = f"Failed to fetch/parse S&P Global press room: {exc}"
        print(f"[ERROR] {msg}", file=sys.stderr)
        try:
            post_error("S&P Global Monitor", msg)
        except Exception:
            pass
        save_state(STATE_FILE, state)
        sys.exit(1)

    print(f"[INFO] Found {len(announcements)} S&P 500 announcement(s) on page.")

    if args.test:
        if announcements:
            print(f"[TEST] Forcing post of: {announcements[0]['title']}")
            post_announcement(announcements[0])
        else:
            print("[TEST] No announcements found to test with.")
        save_state(STATE_FILE, state)
        return

    is_first_run = not seen_urls
    new_announcements = [a for a in announcements if a["url"] not in seen_urls]

    if is_first_run and new_announcements:
        seen_urls.update(a["url"] for a in new_announcements)
        print(f"[INFO] First run: seeding {len(new_announcements)} historical announcement(s) — no Discord post.")
    elif new_announcements:
        seen_urls.update(a["url"] for a in new_announcements)
        for announcement in reversed(new_announcements):
            post_announcement(announcement)
        print(f"[INFO] Posted {len(new_announcements)} new announcement(s).")
    else:
        print("[INFO] No new S&P 500 announcements.")

    state["seen_urls"] = list(seen_urls)
    save_state(STATE_FILE, state)
    print("[DONE]")


if __name__ == "__main__":
    main()
