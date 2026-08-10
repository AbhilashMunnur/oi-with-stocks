#!/usr/bin/env python3
"""Guided setup for Angel One credentials.

Prompts for each value one at a time and writes them to .env. Input is hidden
as you type, so nothing lands in your shell history or terminal scrollback.
"""

from __future__ import annotations

import re
import sys
from getpass import getpass
from pathlib import Path

import pyotp

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


def mask(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}{'*' * (len(value) - 6)}{value[-3:]}"


def validate_client_code(value: str) -> str | None:
    if not re.fullmatch(r"[A-Za-z0-9]{3,20}", value):
        return "Client code should be the alphanumeric ID you log in with, e.g. A123456."
    return None


def validate_pin(value: str) -> str | None:
    if not value.isdigit() or not 4 <= len(value) <= 6:
        return "The PIN should be the 4 to 6 digit MPIN you use in the Angel One app."
    return None


def validate_totp_secret(value: str) -> str | None:
    if value.isdigit():
        return (
            "That looks like the rotating 6-digit code. Enter the longer text "
            "secret shown next to the QR code during TOTP setup instead."
        )
    try:
        pyotp.TOTP(value).now()
    except Exception:
        return "That is not a valid base32 TOTP secret."
    return None


def ask(label: str, help_text: str, validator=None) -> str:
    print(f"\n{label}")
    print(f"  {help_text}")

    while True:
        value = getpass("  Paste it here (input hidden): ").strip().replace(" ", "")
        if not value:
            print("  Nothing entered, try again.")
            continue

        if validator:
            problem = validator(value)
            if problem:
                print(f"  {problem}")
                continue

        confirm = input(f"  Got {mask(value)} — is that right? [Y/n]: ").strip().lower()
        if confirm in {"", "y", "yes"}:
            return value


def write_env(values: dict[str, str]) -> None:
    """Update the named keys in .env, leaving every other line untouched."""
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    remaining = dict(values)

    for index, line in enumerate(lines):
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            lines[index] = f"{key}={remaining.pop(key)}"

    for key, value in remaining.items():
        lines.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ENV_PATH.chmod(0o600)


def main() -> None:
    if not sys.stdin.isatty():
        print("Run this directly in a terminal so the prompts can hide your input.")
        sys.exit(1)

    print("Angel One credential setup")
    print("Values are written to .env, which is gitignored and never committed.")

    values = {
        "ANGEL_API_KEY": ask(
            "1/4  API key",
            "smartapi.angelone.in -> Create an App -> choose the Trading API type.",
        ),
        "ANGEL_CLIENT_CODE": ask(
            "2/4  Client code",
            "The login ID you use in the Angel One app, e.g. A123456.",
            validate_client_code,
        ),
        "ANGEL_PIN": ask(
            "3/4  MPIN",
            "The 4 to 6 digit PIN you use to log in to the Angel One app.",
            validate_pin,
        ),
        "ANGEL_TOTP_SECRET": ask(
            "4/4  TOTP secret",
            "The text string shown beside the QR code at "
            "smartapi.angelone.in/enable-totp, not the 6-digit code.",
            validate_totp_secret,
        ),
    }

    write_env(values)
    print(f"\nSaved to {ENV_PATH} with owner-only permissions.")
    print("\nNow verify the credentials actually work:")
    print("  python scripts/check_angelone.py")


if __name__ == "__main__":
    main()
