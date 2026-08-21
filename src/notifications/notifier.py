from __future__ import annotations

import os
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

from src.config import NotificationConfig, SignalType
from src.oi_analyzer import ScanAlert

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
TELEGRAM_TEXT_LIMIT = 3900


def _telegram_chunks(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> list[str]:
    """Telegram caps messages at 4096 characters."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.split("\n"):
        extra = len(line) + (1 if current else 0)
        if current and size + extra > limit:
            chunks.append("\n".join(current))
            current = [line]
            size = len(line)
        else:
            current.append(line)
            size += extra
    if current:
        chunks.append("\n".join(current))
    return chunks


class Notifier:
    def __init__(self, config: NotificationConfig):
        load_dotenv()
        self.config = config
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        # One or more recipients, comma separated.
        self.chat_ids = [
            chat_id.strip()
            for chat_id in os.getenv("TELEGRAM_CHAT_ID", "").split(",")
            if chat_id.strip()
        ]
        self._recent: dict[str, datetime] = {}
        self._warned_missing = False

    @property
    def telegram_ready(self) -> bool:
        return bool(self.config.telegram and self.bot_token and self.chat_ids)

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
        labels = {
            SignalType.CALL_OI: "CALL OI ALERT",
            SignalType.PUT_OI: "PUT OI ALERT",
            SignalType.CALL_OI_S1: "CALL OI S1 ALERT",
            SignalType.PUT_OI_S1: "PUT OI S1 ALERT",
            SignalType.ST_BEARISH: "ST BEARISH ALERT",
            SignalType.ST_BULLISH: "ST BULLISH ALERT",
        }
        tag = labels.get(alert.signal, "ALERT")
        print(f"\n[{tag}] {alert.message}")

    def _digest(self, alerts: list[ScanAlert], *, title: str, sections: list[tuple]) -> str:
        """One message per strategy reads better than mixing RSI and Supertrend."""
        lines = [f"{title} — {datetime.now():%d %b %Y %H:%M}"]

        for signal, heading in sections:
            group = [a for a in alerts if a.signal is signal]
            if not group:
                continue

            lines.append(f"\n{heading}")
            group = sorted(
                group,
                key=lambda a: (bool(a.skip_reason), a.distance_pct, -(a.rsi or 0)),
            )
            for alert in group:
                if alert.supertrend is not None:
                    lines.append(
                        f"• {alert.symbol}: ₹{alert.ltp:,.2f} vs ST ₹{alert.supertrend:,.2f} "
                        f"({alert.distance_pct:.2f}% away) | strike ₹{alert.oi_strike:,.0f}"
                    )
                elif alert.oi_strike > 0:
                    lines.append(
                        f"• {alert.symbol}: RSI {alert.rsi:.1f} | ₹{alert.ltp:,.2f} "
                        f"vs strike ₹{alert.oi_strike:,.0f} ({alert.distance_pct:.2f}% away)"
                    )
                else:
                    lines.append(
                        f"• {alert.symbol}: RSI {alert.rsi:.1f} | ₹{alert.ltp:,.2f}"
                    )

                detail = []
                for name, change in (
                    ("Call", alert.call_oi_change),
                    ("Put", alert.put_oi_change),
                ):
                    if change is None:
                        continue
                    lots = alert.in_contracts(change)
                    unit = f"{lots:+,}" if lots is not None else f"{change:+,} shares"
                    detail.append(f"{name} ΔOI {unit}")

                if alert.change_pcr is not None:
                    detail.append(f"ΔPCR {alert.change_pcr:.2f}")
                if detail:
                    lines.append(f"    {' | '.join(detail)}  (contracts)")
                if alert.skip_reason:
                    lines.append(f"    Not taking — {alert.skip_reason}")

        expiries = {a.expiry for a in alerts if a.expiry}
        if len(expiries) == 1:
            lines.append(f"\nExpiry: {next(iter(expiries))}")
        lines.append("\nPaper book fills the 3rd-month future, not cash.")
        return "\n".join(lines)

    def _rsi_digest(self, alerts: list[ScanAlert]) -> str:
        return self._digest(
            alerts,
            title="RSI + OI alerts",
            sections=[
                (SignalType.CALL_OI, "CALL OI (RSI ≥ 70)"),
                (SignalType.PUT_OI, "PUT OI (RSI ≤ 32)"),
            ],
        )

    def _scenario1_digest(self, alerts: list[ScanAlert]) -> str:
        return self._digest(
            alerts,
            title="RSI + OI Scenario 1 alerts",
            sections=[
                (SignalType.CALL_OI_S1, "CALL OI S1 (RSI ≥ 70)"),
                (SignalType.PUT_OI_S1, "PUT OI S1 (RSI ≤ 32)"),
            ],
        )

    def _supertrend_digest(self, alerts: list[ScanAlert]) -> str:
        return self._digest(
            alerts,
            title="Supertrend + OI alerts",
            sections=[
                (SignalType.ST_BEARISH, "BEARISH (below ST, bearish ΔOI)"),
                (SignalType.ST_BULLISH, "BULLISH (above ST, bullish ΔOI)"),
            ],
        )

    def _telegram_url(self, method: str) -> str:
        return TELEGRAM_API.format(token=self.bot_token, method=method)

    def _chat_error(self, chat_id: str, exc: requests.RequestException) -> None:
        detail = ""
        if exc.response is not None:
            try:
                detail = f": {exc.response.json().get('description', '')}"
            except ValueError:
                detail = f": {exc.response.text[:120]}"
        print(f"  could not reach chat {chat_id}{detail}")

    def send_message(self, text: str, *, parse_mode: str | None = None) -> int:
        """Send to every recipient. One bad chat ID must not silence the rest."""
        delivered = 0
        chunks = _telegram_chunks(text)

        for chat_id in self.chat_ids:
            ok = True
            for chunk in chunks:
                payload: dict = {
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": True,
                }
                if parse_mode:
                    payload["parse_mode"] = parse_mode

                try:
                    response = requests.post(
                        self._telegram_url("sendMessage"),
                        json=payload,
                        timeout=20,
                    )
                    response.raise_for_status()
                except requests.RequestException as exc:
                    self._chat_error(chat_id, exc)
                    ok = False
                    break
            if ok:
                delivered += 1

        return delivered

    def send_photo(
        self,
        image: bytes,
        caption: str = "",
        *,
        parse_mode: str | None = None,
    ) -> int:
        """Send a PNG dashboard image to every recipient."""
        delivered = 0

        for chat_id in self.chat_ids:
            data: dict = {"chat_id": chat_id}
            if caption:
                data["caption"] = caption[:1024]
            if parse_mode:
                data["parse_mode"] = parse_mode

            try:
                response = requests.post(
                    self._telegram_url("sendPhoto"),
                    data=data,
                    files={"photo": ("positions.png", image, "image/png")},
                    timeout=40,
                )
                response.raise_for_status()
                delivered += 1
            except requests.RequestException as exc:
                self._chat_error(chat_id, exc)

        return delivered

    def notify(self, alerts: list[ScanAlert]) -> None:
        taking = [alert for alert in alerts if not alert.skip_reason]
        fresh = [alert for alert in taking if not self._is_on_cooldown(alert)]

        if self.config.console:
            for alert in fresh:
                self._send_console(alert)

        rsi_alerts = [
            a for a in alerts if a.signal in (SignalType.CALL_OI, SignalType.PUT_OI)
        ]
        s1_alerts = [
            a for a in alerts if a.signal in (SignalType.CALL_OI_S1, SignalType.PUT_OI_S1)
        ]
        st_alerts = [
            a
            for a in alerts
            if a.signal in (SignalType.ST_BEARISH, SignalType.ST_BULLISH)
        ]

        if self.config.telegram:
            if self.telegram_ready:
                recipients = f"{len(self.chat_ids)} recipient(s)"
                if rsi_alerts:
                    delivered = self.send_message(self._rsi_digest(rsi_alerts))
                    print(
                        f"\nSent {len(rsi_alerts)} RSI+OI row(s) to Telegram "
                        f"({delivered}/{recipients})."
                    )
                if s1_alerts:
                    delivered = self.send_message(self._scenario1_digest(s1_alerts))
                    print(
                        f"\nSent {len(s1_alerts)} RSI+OI S1 row(s) to Telegram "
                        f"({delivered}/{recipients})."
                    )
                if st_alerts:
                    delivered = self.send_message(self._supertrend_digest(st_alerts))
                    print(
                        f"\nSent {len(st_alerts)} Supertrend row(s) to Telegram "
                        f"({delivered}/{recipients})."
                    )
            elif not self._warned_missing:
                print("\nTelegram enabled but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID are missing.")
                self._warned_missing = True

        for alert in fresh:
            self._mark_sent(alert)
