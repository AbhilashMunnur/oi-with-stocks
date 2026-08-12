from __future__ import annotations

from datetime import datetime

from src.us_rsi.scanner import UsRsiHit


def format_us_rsi_digest(hits: list[UsRsiHit], *, threshold: float) -> str:
    """One Telegram-friendly message for the daily US oversold scan."""
    stamp = datetime.now().strftime("%d %b %Y %H:%M")
    lines = [
        f"US Nasdaq RSI alerts — {stamp}",
        f"RSI ≤ {threshold:g} after cash close",
    ]

    if not hits:
        lines.append("\nNo names at or below the threshold today.")
        return "\n".join(lines)

    as_of = hits[0].as_of
    lines.append(f"Bars as of {as_of}")
    lines.append(f"\nOVERSOLD ({len(hits)})")
    for hit in hits:
        lines.append(f"• {hit.symbol}: RSI {hit.rsi:.1f} | ${hit.close:,.2f}")

    return "\n".join(lines)
