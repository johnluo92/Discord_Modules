#!/usr/bin/env python3
"""
S&P 500 Reconstitution Tracker
Monitors index additions/removals and posts formatted reports to Discord.
Run weekly via cron: 0 18 * * 5 /usr/bin/python3 /path/to/sp500_tracker.py
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
STATE_FILE = os.path.join(os.path.dirname(__file__), "sp500_state.json")
WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
HEARTBEAT_EVERY_N_RUNS = 4  # Post "no changes" digest every 4 runs (~monthly at weekly cadence)
MAX_CHANGES_TO_DISPLAY = 20  # guard against scraping huge history on first run


# ─── State ────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"seen_keys": [], "run_count": 0, "last_run": None}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ─── Scraping ─────────────────────────────────────────────────────────────────

def fetch_changes() -> list[dict]:
    """
    Scrapes the S&P 500 Wikipedia changes table.
    Returns list of change dicts: {date, added_ticker, added_name, removed_ticker, removed_name, reason, key}
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    resp = requests.get(WIKIPEDIA_URL, headers=headers, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # The changes table is the second wikitable on the page
    tables = soup.find_all("table", {"class": "wikitable"})
    if len(tables) < 2:
        raise RuntimeError("Could not locate the changes table on Wikipedia.")

    changes_table = tables[1]
    rows = changes_table.find_all("tr")

    # Skip the two header rows (merged header + sub-header)
    data_rows = rows[2:]

    changes = []
    for row in data_rows:
        cols = row.find_all(["td", "th"])
        if len(cols) < 4:
            continue

        def cell(i: int) -> str:
            return cols[i].get_text(separator=" ", strip=True) if i < len(cols) else ""

        date_str      = cell(0)
        added_ticker  = cell(1).split("[")[0].strip()   # strip footnote refs like [1]
        added_name    = cell(2).split("[")[0].strip()
        removed_ticker = cell(3).split("[")[0].strip()
        removed_name  = cell(4).split("[")[0].strip()
        reason        = cell(5).split("[")[0].strip()

        # Skip rows that are clearly empty or sub-headers
        if not date_str or date_str.lower() == "date":
            continue

        key = f"{date_str}|{added_ticker}|{removed_ticker}"

        changes.append({
            "date":            date_str,
            "added_ticker":    added_ticker,
            "added_name":      added_name,
            "removed_ticker":  removed_ticker,
            "removed_name":    removed_name,
            "reason":          reason,
            "key":             key,
        })

    return changes


# ─── Discord ──────────────────────────────────────────────────────────────────

COLOR_ALERT    = 0xE8C547   # gold  — new changes
COLOR_HEARTBEAT = 0x2B2D31  # dark  — no changes / status
COLOR_ERROR    = 0xD9534F   # red   — errors

def _post_embed(embeds: list[dict]):
    if not DISCORD_WEBHOOK_URL:
        print("[WARN] DISCORD_WEBHOOK_URL not set — printing embed instead.")
        print(json.dumps(embeds, indent=2))
        return

    # Discord allows max 10 embeds per request
    for i in range(0, len(embeds), 10):
        resp = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"embeds": embeds[i:i + 10]},
            timeout=15
        )
        resp.raise_for_status()


def post_changes(new_changes: list[dict]):
    """
    Groups changes by date and posts one embed per date bucket.
    """
    by_date: dict[str, list[dict]] = {}
    for c in new_changes:
        by_date.setdefault(c["date"], []).append(c)

    embeds = []

    for change_date, changes in by_date.items():
        additions = [
            (c["added_ticker"], c["added_name"])
            for c in changes
            if c["added_ticker"] and c["added_ticker"] != "—"
        ]
        removals = [
            (c["removed_ticker"], c["removed_name"])
            for c in changes
            if c["removed_ticker"] and c["removed_ticker"] != "—"
        ]
        reasons = list({c["reason"] for c in changes if c["reason"]})

        fields = []

        if additions:
            body = "\n".join(
                f"`{ticker:<6}` **{name}**" for ticker, name in additions
            )
            fields.append({
                "name":   "✅  Joining the Index",
                "value":  body or "—",
                "inline": False,
            })

        if removals:
            body = "\n".join(
                f"`{ticker:<6}` ~~{name}~~" for ticker, name in removals
            )
            fields.append({
                "name":   "❌  Leaving the Index",
                "value":  body or "—",
                "inline": False,
            })

        if reasons:
            fields.append({
                "name":   "📋  Rationale",
                "value":  "\n".join(f"• {r}" for r in reasons),
                "inline": False,
            })

        fields.append({
            "name":   "📅  Effective Date",
            "value":  change_date,
            "inline": True,
        })
        fields.append({
            "name":   "🔢  Changes",
            "value":  f"{len(additions)} in · {len(removals)} out",
            "inline": True,
        })

        embeds.append({
            "title":       "📊  S&P 500 — Index Reconstitution Alert",
            "description": (
                "The S&P Index Committee has confirmed the following constituent changes.\n"
                "Passive funds will execute corresponding trades at the effective date."
            ),
            "color":       COLOR_ALERT,
            "fields":      fields,
            "footer":      {
                "text": "Source: S&P Global via Wikipedia  •  Byzantium Technologies"
            },
            "timestamp":   datetime.now(timezone.utc).isoformat(),
        })

    if embeds:
        _post_embed(embeds)
        print(f"[OK] Posted {len(embeds)} embed(s) covering {len(new_changes)} change(s).")


def post_heartbeat(run_count: int, last_change_date: str | None):
    embed = {
        "title":       "🟢  S&P 500 Tracker — Weekly Status",
        "description": "Routine scan complete. No new reconstitution events detected this cycle.",
        "color":       COLOR_HEARTBEAT,
        "fields":      [
            {
                "name":   "Last Known Change",
                "value":  last_change_date or "None on record",
                "inline": True,
            },
            {
                "name":   "Total Runs",
                "value":  str(run_count),
                "inline": True,
            },
            {
                "name":   "Next Scheduled Scan",
                "value":  "Friday ~18:00 ET",
                "inline": True,
            },
        ],
        "footer":    {"text": "Byzantium Technologies  •  S&P 500 Reconstitution Monitor"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _post_embed([embed])
    print("[OK] Posted heartbeat (no changes).")


def post_error(error_msg: str):
    embed = {
        "title":       "🔴  S&P 500 Tracker — Scrape Error",
        "description": f"```{error_msg[:1800]}```",
        "color":       COLOR_ERROR,
        "footer":      {"text": "Byzantium Technologies"},
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    }
    _post_embed([embed])


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="S&P 500 Reconstitution Tracker")
    parser.add_argument(
        "--test",
        action="store_true",
        help="Force-post the most recent change regardless of seen state (for testing)."
    )
    parser.add_argument(
        "--heartbeat",
        action="store_true",
        help="Force-post a heartbeat status message."
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Wipe local state (will re-post all recent changes on next run — use with care)."
    )
    args = parser.parse_args()

    if args.reset:
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
            print("[OK] State reset.")
        else:
            print("[OK] No state file found.")
        return

    state = load_state()
    state["run_count"] = state.get("run_count", 0) + 1
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    seen_keys = set(state.get("seen_keys", []))

    if args.heartbeat:
        last = state.get("last_change_date")
        post_heartbeat(state["run_count"], last)
        save_state(state)
        return

    print(f"[INFO] Run #{state['run_count']} — fetching changes from Wikipedia...")

    try:
        all_changes = fetch_changes()
    except Exception as exc:
        msg = f"Failed to fetch/parse Wikipedia: {exc}"
        print(f"[ERROR] {msg}", file=sys.stderr)
        try:
            post_error(msg)
        except Exception:
            pass
        save_state(state)
        sys.exit(1)

    print(f"[INFO] Found {len(all_changes)} total historical rows.")

    if args.test:
        # In test mode: force-post the most recent entry
        if all_changes:
            test_change = all_changes[0]
            print(f"[TEST] Forcing post of: {test_change['key']}")
            post_changes([test_change])
        else:
            print("[TEST] No changes found to test with.")
        save_state(state)
        return

    is_first_run = not state.get("seen_keys")
    new_changes = [
        c for c in all_changes
        if c["key"] not in seen_keys
    ][:MAX_CHANGES_TO_DISPLAY]

    if new_changes:
        seen_keys.update(c["key"] for c in new_changes)
        state["last_change_date"] = new_changes[0]["date"]
        if is_first_run:
            # Silently seed state on first run — don't flood channel with history
            print(f"[INFO] First run: seeding state with {len(new_changes)} historical change(s), no Discord post.")
            post_heartbeat(state["run_count"], state.get("last_change_date"))
        else:
            print(f"[INFO] {len(new_changes)} new change(s) detected.")
            post_changes(new_changes)
    else:
        print("[INFO] No new changes.")
        # Post heartbeat on every Nth run when no changes
        if state["run_count"] % HEARTBEAT_EVERY_N_RUNS == 0:
            post_heartbeat(state["run_count"], state.get("last_change_date"))

    state["seen_keys"] = list(seen_keys)
    save_state(state)
    print("[DONE]")


if __name__ == "__main__":
    main()
