from __future__ import annotations

import os
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

from src.config import NotificationConfig, SignalType
from src.oi_analyzer import ScanAlert

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class Notifier:
    def __init__(self, config: NotificationConfig):
        load_dotenv()
        self.config = config
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self._recent: dict[str, datetime] = {}
        self._warned_missing = False

    @property
    def telegram_ready(self) -> bool:
        return bool(self.config.telegram and self.bot_token and self.chat_id)

    def _cooldown_key(self, alert: ScanAlert) -> str:
        return f"{alert.symbol}:{alert.signal.value}"

    def _is_on_cooldown(self, alert: ScanAlert) -> bool:
        last_sent = self._recent.get(self._cooldown_key(alert))
        if not last_sent:
            return False
        return datetime.now() - last_sent < timedelta(minutes=self.config.cooldown_minutes)

    def _mark_sent(self, alert: ScanAlert) -> None:
        self._recent[self._cooldown_key(alert)] = datetime.now()

    def _send_console(self, alert: ScanAlert) -> None:
        tag = "CALL OI ALERT" if alert.signal is SignalType.CALL_OI else "PUT OI ALERT"
        print(f"\n[{tag}] {alert.message}")

    def _digest(self, alerts: list[ScanAlert]) -> str:
        """One message per scan reads better on a phone than one per stock."""
        lines = [f"OI + RSI alerts — {datetime.now():%d %b %Y %H:%M}"]

        for signal, heading in (
            (SignalType.CALL_OI, "CALL OI (overbought, near max Call OI)"),
            (SignalType.PUT_OI, "PUT OI (oversold, near max Put OI)"),
        ):
            group = [a for a in alerts if a.signal is signal]
            if not group:
                continue

            lines.append(f"\n{heading}")
            for alert in sorted(group, key=lambda a: a.distance_pct):
                lines.append(
                    f"• {alert.symbol}: RSI {alert.rsi:.1f} | ₹{alert.ltp:,.2f} "
                    f"vs strike ₹{alert.oi_strike:,.0f} ({alert.distance_pct:.2f}% away)"
                )

                detail = []
                if alert.oi_change is not None:
                    lots = alert.in_contracts(alert.oi_change)
                    detail.append(
                        f"ΔOI {lots:+,} contracts"
                        if lots is not None
                        else f"ΔOI {alert.oi_change:+,} shares"
                    )
                if alert.change_pcr is not None:
                    detail.append(f"ΔPCR {alert.change_pcr:.2f}")
                if alert.buildup:
                    detail.append(alert.buildup)
                if detail:
                    lines.append(f"    {' | '.join(detail)}")

        lines.append(f"\nExpiry: {alerts[0].expiry}")
        return "\n".join(lines)

    def send_message(self, text: str) -> None:
        response = requests.post(
            TELEGRAM_API.format(token=self.bot_token),
            json={"chat_id": self.chat_id, "text": text},
            timeout=20,
        )
        response.raise_for_status()

    def notify(self, alerts: list[ScanAlert]) -> None:
        fresh = [alert for alert in alerts if not self._is_on_cooldown(alert)]
        if not fresh:
            return

        if self.config.console:
            for alert in fresh:
                self._send_console(alert)

        if self.config.telegram:
            if self.telegram_ready:
                try:
                    self.send_message(self._digest(fresh))
                    print(f"\nSent {len(fresh)} alert(s) to Telegram.")
                except requests.RequestException as exc:
                    print(f"\nTelegram notification failed: {exc}")
            elif not self._warned_missing:
                print("\nTelegram enabled but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID are missing.")
                self._warned_missing = True

        for alert in fresh:
            self._mark_sent(alert)
