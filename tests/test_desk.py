import json
from pathlib import Path

from src.desk import build_desk


def test_desk_reads_booked_profit_and_open_lots(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "book.json").write_text(
        json.dumps(
            {
                "capital": 40000000,
                "realised_pnl": 125000.5,
                "closed_count": 2,
                "updated_at": "2026-10-06 16:46:47",
                "positions": [
                    {
                        "symbol": "TITAN",
                        "direction": "SHORT",
                        "lots_open": 1,
                        "lots_total": 3,
                        "entry_price": 3500,
                        "entry_time": "2026-10-01 15:15:00",
                        "expiry": "2026-12-29",
                        "lot_size": 10,
                        "margin_blocked": 200000,
                        "closed_legs": [{"pnl": 40000}],
                    },
                    {
                        "symbol": "ITC",
                        "direction": "LONG",
                        "lots_open": 0,
                        "lots_total": 3,
                        "margin_blocked": 999,
                    },
                ],
                "last_marks": {"TITAN": 3400},
            }
        ),
        encoding="utf-8",
    )
    (data / "trades.csv").write_text(
        "Symbol,Buy/Sell,Entry date,Entry price,Entry RSI,Exit date,Exit price,Exit RSI,"
        "Holding trading period,Capital needed,Profit/loss,Exit reason,Exit trigger\n"
        "TITAN,Sell,01-Oct-26,3500,70,06-Oct-26,3400,60,3,100*1,40000,first_target,5%\n",
        encoding="utf-8",
    )
    desk = build_desk(
        tmp_path,
        books=(("RSI Stretch", "data/book.json", "data/trades.csv"),),
    )
    book = desk["books"][0]
    assert book["realised"] == 125000.5
    assert book["open_count"] == 1
    assert book["open_lots"] == 1
    assert book["margin"] == 200000
    assert book["open"][0]["symbol"] == "TITAN"
    assert book["open"][0]["booked"] == 40000
    assert book["unrealised"] == 1000
    assert book["recent_exits"][0]["pnl"] == 40000
    assert "google" not in json.dumps(desk).lower()
