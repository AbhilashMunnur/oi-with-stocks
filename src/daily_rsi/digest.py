from __future__ import annotations

from datetime import datetime

from src.daily_rsi.scanner import RsiHit


def format_rsi_digest(
    hits: list[RsiHit],
    *,
    threshold: float,
    market_label: str,
    currency_symbol: str = "$",
    timeframe_label: str = "daily",
) -> str:
    """One Telegram-friendly message for a post-close oversold scan."""
    stamp = datetime.now().strftime("%d %b %Y %H:%M")
    lines = [
        f"{market_label} RSI alerts — {stamp}",
        f"{timeframe_label.capitalize()} RSI ≤ {threshold:g} after cash close",
    ]

    if not hits:
        lines.append("\nNo names at or below the threshold today.")
        return "\n".join(lines)

    as_of = hits[0].as_of
    lines.append(f"Bars as of {as_of}")
    lines.append(f"\nOVERSOLD ({len(hits)})")
    for hit in hits:
        lines.append(
            f"• {hit.symbol}: RSI {hit.rsi:.1f} | {currency_symbol}{hit.close:,.2f}"
        )

    return "\n".join(lines)
