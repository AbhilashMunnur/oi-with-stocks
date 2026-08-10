#!/usr/bin/env python3
"""Guided setup for Telegram alerts.

Finds your chat ID automatically, sends a test message, and writes both values
to .env. Input is hidden as you type.
"""

from __future__ import annotations

import sys
from getpass import getpass
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.setup_angelone import mask, write_env

API = "https://api.telegram.org/bot{token}/{method}"


def call(token: str, method: str, **params):
    response = requests.get(API.format(token=token, method=method), params=params, timeout=20)
    return response.json()


def ask_token() -> str:
    print("\n1/2  Bot token")
    print("  Message @BotFather on Telegram, send /newbot, and follow the prompts.")
    print("  It replies with a token that looks like 123456789:AAE...")

    while True:
        token = getpass("  Paste the token here (input hidden): ").strip()
        if not token:
            continue

        result = call(token, "getMe")
        if not result.get("ok"):
            print(f"  Telegram rejected that token: {result.get('description', 'unknown error')}")
            continue

        username = result["result"].get("username", "your bot")
        print(f"  Connected to @{username}")
        return token


def ask_chat_id(token: str) -> str:
    username = call(token, "getMe")["result"].get("username", "your bot")

    print("\n2/2  Chat ID")
    print(f"  Open Telegram, find @{username}, and send it any message (e.g. 'hi').")
    input("  Press Enter once you have sent it: ")

    for attempt in range(3):
        updates = call(token, "getUpdates").get("result") or []
        chats = {
            str(chat["id"]): chat.get("first_name") or chat.get("title") or "chat"
            for update in updates
            if (chat := (update.get("message") or {}).get("chat"))
        }

        if chats:
            chat_id, name = next(iter(chats.items()))
            print(f"  Found chat with {name} (id {mask(chat_id)})")
            return chat_id

        if attempt < 2:
            input("  No message seen yet. Send one to the bot, then press Enter: ")

    print("\n  Could not find a message. Make sure you messaged the bot directly,")
    print("  not a group, and that you started the chat with /start.")
    sys.exit(1)


def main() -> None:
    if not sys.stdin.isatty():
        print("Run this directly in a terminal so the prompts can hide your input.")
        sys.exit(1)

    print("Telegram alert setup")
    print("Values are written to .env, which is gitignored and never committed.")

    token = ask_token()
    chat_id = ask_chat_id(token)

    result = call(
        token,
        "sendMessage",
        chat_id=chat_id,
        text="OI + RSI scanner connected. Alerts will arrive here.",
    )
    if not result.get("ok"):
        print(f"\nCould not send a test message: {result.get('description')}")
        sys.exit(1)

    write_env({"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": chat_id})

    print("\nTest message sent — check your Telegram.")
    print("Saved to .env. Alerts are enabled in config.yaml under notifications.telegram.")


if __name__ == "__main__":
    main()
