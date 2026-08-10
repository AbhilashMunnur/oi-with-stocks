from __future__ import annotations

import html
import io
from datetime import datetime
from pathlib import Path

from src.paper_trading.models import Position, TradeEvent

# Angel One–style positions palette (dark board, green/red P&L).
BG = (15, 18, 24)
CARD = (28, 33, 45)
CARD_LINE = (42, 48, 64)
HEADER = (22, 26, 36)
TEXT = (236, 239, 244)
MUTED = (148, 157, 175)
GREEN = (0, 186, 124)
RED = (255, 82, 82)
BLUE = (55, 125, 255)
BUY_BG = (0, 80, 60)
SELL_BG = (90, 35, 40)
WHITE = (255, 255, 255)

WIDTH = 720
PAD = 20
ROW_H = 58
SUMMARY_H = 92


def _inr(amount: float, signed: bool = False) -> str:
    if signed:
        return f"₹{amount:+,.0f}"
    return f"₹{amount:,.0f}"


def _pnl_color(amount: float) -> tuple[int, int, int]:
    if amount > 0:
        return GREEN
    if amount < 0:
        return RED
    return MUTED


def _font(size: int, bold: bool = False):
    from PIL import ImageFont

    names = (
        [
            "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
            "Arial Bold.ttf" if bold else "Arial.ttf",
            "Helvetica.ttc",
            "SFNS.ttf",
        ]
    )
    search = [
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/usr/share/fonts/truetype/liberation"),
        Path("/System/Library/Fonts/Supplemental"),
        Path("/System/Library/Fonts"),
        Path("/Library/Fonts"),
    ]
    for folder in search:
        for name in names:
            path = folder / name
            if path.exists():
                try:
                    return ImageFont.truetype(str(path), size=size)
                except OSError:
                    continue
    return ImageFont.load_default()


def _book_metrics(
    *,
    capital: float,
    realised_pnl: float,
    day_realised_pnl: float,
    positions: list[Position],
    prices: dict[str, float],
) -> dict:
    open_positions = [p for p in positions if p.is_open]
    open_pnl = sum(
        p.unrealised(prices[p.symbol])
        for p in open_positions
        if p.symbol in prices
    )
    day_pnl = day_realised_pnl + open_pnl
    lifetime = realised_pnl + open_pnl
    equity = capital + lifetime
    equity_pct = (lifetime / capital * 100) if capital else 0.0
    return {
        "open_positions": open_positions,
        "open_pnl": open_pnl,
        "day_pnl": day_pnl,
        "day_realised_pnl": day_realised_pnl,
        "lifetime": lifetime,
        "realised_pnl": realised_pnl,
        "equity": equity,
        "equity_pct": equity_pct,
    }


def _position_rows(
    positions: list[Position], prices: dict[str, float]
) -> list[tuple[float, str]]:
    rows: list[tuple[float, str]] = []
    for position in positions:
        price = prices.get(position.symbol)
        name = position.symbol[:10].ljust(10)
        side = ("L" if position.direction == "LONG" else "S") + str(position.lots_open)
        side = side.ljust(3)
        if price is None:
            rows.append((0.0, f"{name} {side}   n/a      n/a"))
            continue
        move = position.move_pct(price)
        upnl = position.unrealised(price)
        rows.append((upnl, f"{name} {side} {move:+6.2f}% {_inr(upnl, signed=True):>10}"))
    rows.sort(key=lambda item: item[0], reverse=True)
    return rows


def format_pnl_dashboard(
    *,
    capital: float,
    free_capital: float,
    realised_pnl: float,
    day_realised_pnl: float,
    closed_count: int,
    positions: list[Position],
    prices: dict[str, float],
    events: list[TradeEvent] | None = None,
    now: datetime | None = None,
) -> str:
    """Compact HTML caption companion for the image dashboard."""
    now = now or datetime.now()
    m = _book_metrics(
        capital=capital,
        realised_pnl=realised_pnl,
        day_realised_pnl=day_realised_pnl,
        positions=positions,
        prices=prices,
    )
    stamp = html.escape(now.strftime("%d %b %Y · %H:%M IST"))
    lines = [
        f"<b>Paper positions</b> · <i>{stamp}</i>",
        f"Day P&amp;L <b>{html.escape(_inr(m['day_pnl'], signed=True))}</b>  ·  "
        f"Book {html.escape(_inr(m['equity']))} ({m['equity_pct']:+.2f}%)",
        f"Open {len(m['open_positions'])} fut  ·  Free {html.escape(_inr(free_capital))}  ·  "
        f"Closed {closed_count}",
    ]
    if events:
        lines.append("")
        for event in events[:8]:
            pnl = f" {html.escape(_inr(event.pnl, signed=True))}" if event.pnl else ""
            lines.append(
                f"• <code>{html.escape(event.kind)}</code> "
                f"<b>{html.escape(event.symbol)}</b>{pnl}"
            )
    return "\n".join(lines)


def _draw_round_rect(draw, box, fill, radius=14):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def _ellipsis(draw, text: str, font, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text + "…"


def render_positions_image(
    *,
    capital: float,
    free_capital: float,
    realised_pnl: float,
    day_realised_pnl: float,
    closed_count: int,
    positions: list[Position],
    prices: dict[str, float],
    events: list[TradeEvent] | None = None,
    now: datetime | None = None,
) -> bytes:
    """Render an Angel One–style positions board as a PNG."""
    from PIL import Image, ImageDraw

    now = now or datetime.now()
    m = _book_metrics(
        capital=capital,
        realised_pnl=realised_pnl,
        day_realised_pnl=day_realised_pnl,
        positions=positions,
        prices=prices,
    )
    open_positions = m["open_positions"]
    ranked = sorted(
        open_positions,
        key=lambda p: p.unrealised(prices[p.symbol]) if p.symbol in prices else 0.0,
        reverse=True,
    )

    event_rows = min(len(events or []), 4)
    height = (
        PAD
        + 56  # title
        + SUMMARY_H
        + 18
        + 34  # column header
        + max(len(ranked), 1) * ROW_H
        + (48 + event_rows * 28 if events else 0)
        + 50  # footer
        + PAD
    )

    image = Image.new("RGB", (WIDTH, height), BG)
    draw = ImageDraw.Draw(image)

    title_font = _font(26, bold=True)
    label_font = _font(13)
    value_font = _font(22, bold=True)
    small_font = _font(12)
    row_font = _font(16, bold=True)
    row_small = _font(13)
    badge_font = _font(11, bold=True)

    y = PAD
    draw.text((PAD, y), "Positions", fill=TEXT, font=title_font)
    stamp = now.strftime("%d %b %Y · %H:%M IST")
    stamp_w = draw.textlength(stamp, font=small_font)
    draw.text((WIDTH - PAD - stamp_w, y + 10), stamp, fill=MUTED, font=small_font)
    y += 44

    # Summary strip — Total / Unrealised / Realised / Open
    cards = [
        ("Total P&L", _inr(m["day_pnl"], signed=True), _pnl_color(m["day_pnl"])),
        ("Unrealised", _inr(m["open_pnl"], signed=True), _pnl_color(m["open_pnl"])),
        ("Realised today", _inr(m["day_realised_pnl"], signed=True), _pnl_color(m["day_realised_pnl"])),
        ("Open", str(len(open_positions)), BLUE),
    ]
    gap = 10
    card_w = (WIDTH - 2 * PAD - 3 * gap) // 4
    for i, (label, value, color) in enumerate(cards):
        x0 = PAD + i * (card_w + gap)
        _draw_round_rect(draw, (x0, y, x0 + card_w, y + SUMMARY_H - 8), CARD, radius=12)
        draw.text((x0 + 12, y + 12), label, fill=MUTED, font=label_font)
        draw.text((x0 + 12, y + 40), value, fill=color, font=value_font)
    y += SUMMARY_H + 8

    # Book value = starting capital + lifetime P&L (futures paper book, not cash equity).
    book_line = (
        f"Book {_inr(m['equity'])} ({m['equity_pct']:+.2f}%)   ·   "
        f"Free {_inr(free_capital)}   ·   Lifetime {_inr(m['realised_pnl'], signed=True)} "
        f"({closed_count} closed)"
    )
    draw.text((PAD, y), book_line, fill=MUTED, font=small_font)
    y += 28

    # Column headers
    _draw_round_rect(draw, (PAD, y, WIDTH - PAD, y + 30), HEADER, radius=8)
    headers = [
        (PAD + 14, "Instrument"),
        (PAD + 250, "Side"),
        (PAD + 330, "Avg"),
        (PAD + 430, "LTP"),
        (PAD + 530, "P&L"),
    ]
    for x, label in headers:
        draw.text((x, y + 7), label, fill=MUTED, font=small_font)
    y += 38

    if not ranked:
        _draw_round_rect(draw, (PAD, y, WIDTH - PAD, y + ROW_H), CARD, radius=10)
        draw.text((PAD + 20, y + 18), "No open positions", fill=MUTED, font=row_font)
        y += ROW_H + 12
    else:
        for position in ranked:
            _draw_round_rect(draw, (PAD, y, WIDTH - PAD, y + ROW_H - 6), CARD, radius=10)
            price = prices.get(position.symbol)
            upnl = position.unrealised(price) if price is not None else 0.0
            move = position.move_pct(price) if price is not None else 0.0
            color = _pnl_color(upnl) if price is not None else MUTED

            # BUY / SELL pill
            is_buy = position.direction == "LONG"
            badge = "BUY" if is_buy else "SELL"
            badge_bg = BUY_BG if is_buy else SELL_BG
            badge_fg = GREEN if is_buy else RED
            bx0, by0 = PAD + 250, y + 16
            _draw_round_rect(draw, (bx0, by0, bx0 + 52, by0 + 22), badge_bg, radius=6)
            tw = draw.textlength(badge, font=badge_font)
            draw.text((bx0 + (52 - tw) / 2, by0 + 4), badge, fill=badge_fg, font=badge_font)

            symbol = _ellipsis(draw, position.symbol, row_font, 200)
            draw.text((PAD + 14, y + 10), symbol, fill=TEXT, font=row_font)
            qty = f"{position.lots_open} lot · x{position.lot_size}"
            draw.text((PAD + 14, y + 32), qty, fill=MUTED, font=row_small)

            draw.text((PAD + 330, y + 18), f"{position.entry_price:,.1f}", fill=TEXT, font=row_small)
            ltp_text = f"{price:,.1f}" if price is not None else "—"
            draw.text((PAD + 430, y + 18), ltp_text, fill=TEXT, font=row_small)

            pnl_text = _inr(upnl, signed=True) if price is not None else "—"
            move_text = f"{move:+.2f}%" if price is not None else ""
            pnl_w = draw.textlength(pnl_text, font=row_font)
            draw.text((WIDTH - PAD - 14 - pnl_w, y + 8), pnl_text, fill=color, font=row_font)
            if move_text:
                mw = draw.textlength(move_text, font=row_small)
                draw.text((WIDTH - PAD - 14 - mw, y + 32), move_text, fill=color, font=row_small)

            # Left accent bar
            draw.rectangle((PAD, y + 8, PAD + 4, y + ROW_H - 14), fill=color)
            y += ROW_H

    if events:
        y += 8
        draw.text((PAD, y), "This scan", fill=MUTED, font=label_font)
        y += 22
        for event in events[:4]:
            pnl = f"  {_inr(event.pnl, signed=True)}" if event.pnl else ""
            line = f"{event.kind}  {event.symbol}{pnl}"
            draw.text((PAD + 8, y), _ellipsis(draw, line, row_small, WIDTH - 2 * PAD), fill=TEXT, font=row_small)
            y += 24

    # Footer stripe
    y = height - 40
    draw.rectangle((0, y, WIDTH, height), fill=HEADER)
    footer = "OI + RSI paper book  ·  3rd-month futures  ·  not live orders"
    fw = draw.textlength(footer, font=small_font)
    draw.text(((WIDTH - fw) / 2, y + 12), footer, fill=MUTED, font=small_font)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
