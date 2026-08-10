#!/usr/bin/env python3
"""Add another person to the Telegram alerts.

Telegram bots may only message people who have contacted them first, so the new
recipient must open the bot and press Start before running this. The script then
picks up their chat ID and appends it to TELEGRAM_CHAT_ID in .env.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.setup_angelone import write_env

API = "https://api.telegram.org/bot{token}/{method}"
WAIT_SECONDS = 180
POLL_SECONDS = 5


def call(token: str, method: str, **params):
    return requests.get(API.format(token=token, method=method), params=params, timeout=20).json()


def known_chats(token: str) -> dict[str, str]:
    updates = call(token, "getUpdates").get("result") or []
    found: dict[str, str] = {}

    for update in updates:
        chat = (update.get("message") or {}).get("chat")
        if chat:
            name = chat.get("first_name") or chat.get("title") or "chat"
            if chat.get("username"):
                name += f" (@{chat['username']})"
            found[str(chat["id"])] = name

    return found


def main() -> None:
    env = dotenv_values(ROOT / ".env")
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        print("No TELEGRAM_BOT_TOKEN in .env. Run scripts/setup_telegram.py first.")
        sys.exit(1)

    existing = [c.strip() for c in (env.get("TELEGRAM_CHAT_ID") or "").split(",") if c.strip()]
    bot = call(token, "getMe").get("result", {}).get("username", "the bot")

    print(f"Currently sending alerts to {len(existing)} recipient(s).")
    print(f"\nAsk the new person to open Telegram, search for @{bot},")
    print("open the chat and press Start (or send any message).")
    print(f"\nWaiting up to {WAIT_SECONDS // 60} minutes for them...")

    deadline = time.time() + WAIT_SECONDS
    while time.time() < deadline:
        new = {cid: name for cid, name in known_chats(token).items() if cid not in existing}
        if new:
            added = []
            for chat_id, name in new.items():
                result = call(
                    token,
                    "sendMessage",
                    chat_id=chat_id,
                    text="You have been added to the OI + RSI scanner alerts.",
                )
                if result.get("ok"):
                    print(f"  Added {name}")
                    added.append(chat_id)
                else:
                    print(f"  Could not message {name}: {result.get('description')}")

            if added:
                write_env({"TELEGRAM_CHAT_ID": ",".join(existing + added)})
                print(f"\nSaved. Alerts now go to {len(existing) + len(added)} recipient(s).")
                print("Run ./scripts/sync_github_secrets.sh to update the hosted scans.")
                return

        time.sleep(POLL_SECONDS)

    print("\nNo new chat appeared. Telegram only reveals a user after they")
    print(f"message @{bot} themselves, so make sure they pressed Start.")
    sys.exit(1)


if __name__ == "__main__":
    main()
