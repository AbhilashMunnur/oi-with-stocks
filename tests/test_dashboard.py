from datetime import datetime
from io import BytesIO

from PIL import Image

from src.paper_trading.dashboard import format_pnl_dashboard, render_positions_image
from src.paper_trading.models import Position


def _position(symbol="TITAN", direction="SHORT", entry=5000.0, lots=2, lot_size=175):
    return Position(
        symbol=symbol,
        direction=direction,
        entry_price=entry,
        entry_time="2026-08-10 10:00:00",
        lot_size=lot_size,
        lots_open=lots,
        lots_total=lots,
        expiry="2026-10-29",
        rsi_at_entry=74.0,
        strike=entry,
        margin_blocked=100_000.0,
    )


def test_dashboard_caption_keeps_day_pnl_up_front():
    text = format_pnl_dashboard(
        capital=5_000_000,
        free_capital=1_000_000,
        realised_pnl=12_000,
        day_realised_pnl=5_000,
        closed_count=2,
        positions=[_position()],
        prices={"TITAN": 4900.0},
        now=datetime(2026, 8, 10, 15, 30),
    )

    assert "Paper positions" in text
    assert "Day P&amp;L" in text
    assert "Book" in text


def test_positions_image_is_a_valid_png():
    png = render_positions_image(
        capital=5_000_000,
        free_capital=1_000_000,
        realised_pnl=0,
        day_realised_pnl=0,
        closed_count=0,
        positions=[
            _position("TITAN", "SHORT", 5000.0),
            _position("HAL", "LONG", 4900.0, lot_size=150),
        ],
        prices={"TITAN": 4900.0, "HAL": 5000.0},
        now=datetime(2026, 8, 10, 15, 30),
    )

    image = Image.open(BytesIO(png))
    assert image.format == "PNG"
    assert image.size[0] == 720
    assert image.size[1] > 200


def test_empty_book_still_renders():
    png = render_positions_image(
        capital=5_000_000,
        free_capital=5_000_000,
        realised_pnl=0,
        day_realised_pnl=0,
        closed_count=0,
        positions=[],
        prices={},
        now=datetime(2026, 8, 10, 15, 30),
    )

    assert Image.open(BytesIO(png)).format == "PNG"
