import csv
from datetime import datetime

from src.paper_trading.journal import COLUMNS, TradeJournal, build_row, trading_days_between


def sample_row(**overrides) -> dict:
    defaults = dict(
        symbol="TITAN",
        direction="SHORT",
        entry_time="2026-08-10 22:31:00",
        entry_price=5090.0,
        entry_rsi=74.6,
        exit_time="2026-08-24 11:15:00",
        exit_price=4784.6,
        exit_rsi=41.2,
        lots=1,
        lot_size=175,
        capital=445_375.0,
        pnl=53_445.0,
        reason="first_target",
    )
    defaults.update(overrides)
    return build_row(**defaults)


def test_short_is_recorded_as_a_sell():
    assert sample_row()["Buy/Sell"] == "Sell"


def test_long_is_recorded_as_a_buy():
    assert sample_row(direction="LONG")["Buy/Sell"] == "Buy"


def test_dates_use_the_journal_format():
    row = sample_row()
    assert row["Entry date"] == "10-Aug-26"
    assert row["Exit date"] == "24-Aug-26"


def test_holding_period_counts_trading_days_only():
    # 10 Aug 2026 is a Monday; 24 Aug is the Monday two weeks later.
    assert sample_row()["Holding trading period"] == 10


def test_holding_period_skips_the_weekend():
    friday = datetime(2026, 8, 14)
    monday = datetime(2026, 8, 17)
    assert trading_days_between(friday, monday) == 1


def test_entry_and_exit_rsi_are_both_kept():
    row = sample_row()
    assert row["Entry RSI"] == 74.6
    assert row["Exit RSI"] == 41.2


def test_missing_exit_rsi_is_left_blank():
    assert sample_row(exit_rsi=None)["Exit RSI"] == ""


def test_csv_gets_a_header_once_and_appends_after(tmp_path):
    path = tmp_path / "trades.csv"
    journal = TradeJournal(path)

    journal.append([sample_row()])
    journal.append([sample_row(symbol="HAL")])

    with path.open(encoding="utf-8") as handle:
        rows = list(csv.reader(handle))

    assert rows[0] == COLUMNS
    assert len(rows) == 3
    assert rows[1][0] == "TITAN"
    assert rows[2][0] == "HAL"


def test_google_sheet_failure_does_not_lose_the_csv_record(tmp_path):
    """Logging is best effort; the local record must still be written."""
    path = tmp_path / "trades.csv"
    journal = TradeJournal(path, sheet_id="not-a-real-sheet")

    journal.append([sample_row()])

    with path.open(encoding="utf-8") as handle:
        assert len(list(csv.reader(handle))) == 2
