from __future__ import annotations

import os
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

from src.config import NotificationConfig
from src.oi_analyzer import ScanAlert


class Notifier:
    def __init__(self, config: NotificationConfig):
        load_dotenv()
        self.config = config
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self._recent: dict[str, datetime] = {}

    def _cooldown_key(self, alert: ScanAlert) -> str:
        return f"{alert.symbol}:{alert.signal.value}"

    def _is_on_cooldown(self, alert: ScanAlert) -> bool:
        key = self._cooldown_key(alert)
        last_sent = self._recent.get(key)
        if not last_sent:
            return False
        cooldown = timedelta(minutes=self.config.cooldown_minutes)
        return datetime.now() - last_sent < cooldown

    def _mark_sent(self, alert: ScanAlert) -> None:
        self._recent[self._cooldown_key(alert)] = datetime.now()

    def _send_console(self, alert: ScanAlert) -> None:
        tag = "CALL OI ALERT" if alert.signal.value == "CALL_OI" else "PUT OI ALERT"
        print(f"\n[{tag}] {alert.message}")

    def _send_telegram(self, alert: ScanAlert) -> None:
        if not self.bot_token or not self.chat_id:
            print("Telegram enabled but TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing.")
            return

        tag = "CALL OI" if alert.signal.value == "CALL_OI" else "PUT OI"
        text = f"*{tag} ALERT*\n{alert.message}\nExpiry: {alert.expiry}"

        response = requests.post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=15,
        )
        response.raise_for_status()

    def notify(self, alerts: list[ScanAlert]) -> None:
        for alert in alerts:
            if self._is_on_cooldown(alert):
                continue

            if self.config.console:
                self._send_console(alert)

            if self.config.telegram:
                try:
                    self._send_telegram(alert)
                except requests.RequestException as exc:
                    print(f"Telegram notification failed: {exc}")

            self._mark_sent(alert)
