from datetime import date

import pytest

from src.config import PaperTradingConfig, SignalType
from src.oi_analyzer import ScanAlert
from src.paper_trading import PaperBook


@pytest.fixture
def config(tmp_path):
    return PaperTradingConfig(
        enabled=True,
        capital=5_000_000,
        lots_per_trade=2,
        first_target_pct=6.0,
        second_target_pct=11.0,
        stop_loss_pct=3.0,
        margin_pct=20.0,
        ledger_path=str(tmp_path / "book.json"),
        journal_csv=str(tmp_path / "trades.csv"),
    )


def alert(signal=SignalType.CALL_OI, symbol="TITAN", ltp=5000.0, lot_size=175):
    return ScanAlert(
        symbol=symbol,
        signal=signal,
        ltp=ltp,
        rsi=74.6 if signal is SignalType.CALL_OI else 30.0,
        oi_strike=5100.0,
        oi_value=800_000,
        distance_pct=0.2,
        expiry="2026-08-25",
        message="",
        lot_size=lot_size,
    )


def test_call_signal_opens_a_short(config):
    book = PaperBook(config)
    book.open_from_alerts([alert()])

    position = book.positions[0]
    assert position.direction == "SHORT"
    assert position.lots_open == 2


def test_put_signal_opens_a_long(config):
    book = PaperBook(config)
    book.open_from_alerts([alert(signal=SignalType.PUT_OI)])

    assert book.positions[0].direction == "LONG"


def test_short_profits_when_price_falls(config):
    book = PaperBook(config)
    book.open_from_alerts([alert()])

    # A 6% fall is favourable for a short.
    assert book.positions[0].move_pct(4700.0) == pytest.approx(6.0)


def test_first_target_closes_one_lot_at_the_trigger_price(config):
    book = PaperBook(config)
    book.open_from_alerts([alert()])

    events = book.update({"TITAN": 4700.0})

    position = book.positions[0]
    assert position.lots_open == 1
    assert events[0].kind == "first_target"
    # Booked at exactly 6%, not at the observed price.
    assert position.closed_legs[0].exit_price == pytest.approx(4700.0)
    assert book.realised_pnl == pytest.approx(300.0 * 175)


def test_second_target_closes_the_rest(config):
    book = PaperBook(config)
    book.open_from_alerts([alert()])

    book.update({"TITAN": 4700.0})
    events = book.update({"TITAN": 4450.0})

    assert not book.positions
    assert events[-1].kind == "second_target"
    assert book.closed_count == 1


def test_a_jump_past_both_targets_still_books_the_first_lot_at_6_percent(config):
    """Price must have travelled through 6% to reach 11%."""
    book = PaperBook(config)
    book.open_from_alerts([alert()])

    events = book.update({"TITAN": 4000.0})

    assert [e.kind for e in events] == ["first_target", "second_target"]
    first, second = book.closed_count, book.realised_pnl
    assert first == 1
    # 1 lot at 6% plus 1 lot at 11%, both at their trigger levels.
    assert second == pytest.approx((300.0 + 550.0) * 175)


def test_price_landing_exactly_on_the_target_still_triggers(config):
    """5090 * 0.94 computes as 5.999...% in floating point."""
    book = PaperBook(config)
    book.open_from_alerts([alert(ltp=5090.0)])

    events = book.update({"TITAN": 5090.0 * 0.94})

    assert [e.kind for e in events] == ["first_target"]


def test_exit_rsi_is_recorded_per_leg(config):
    book = PaperBook(config)
    book.open_from_alerts([alert(ltp=5090.0)])

    book.update({"TITAN": 5090.0 * 0.94}, rsi_values={"TITAN": 41.2})
    book.update({"TITAN": 5090.0 * 0.85}, rsi_values={"TITAN": 28.4})

    rsi_by_leg = [row["Exit RSI"] for row in book._pending_rows]
    assert rsi_by_leg == [41.2, 28.4]


def test_stop_loss_closes_everything(config):
    book = PaperBook(config)
    book.open_from_alerts([alert()])

    events = book.update({"TITAN": 5150.0})

    assert not book.positions
    assert events[0].kind == "stop_loss"
    assert book.realised_pnl == pytest.approx(-150.0 * 2 * 175)


def test_one_percent_adverse_does_not_stop_before_first_target(config):
    book = PaperBook(config)
    book.open_from_alerts([alert()])

    # Short @ 5000; 1% adverse is 5050, but initial stop is 3% in the fixture.
    events = book.update({"TITAN": 5050.0})

    assert not events
    assert book.positions[0].lots_open == 2


def test_after_first_target_stop_tightens_to_one_percent_from_entry(config):
    book = PaperBook(config)
    book.open_from_alerts([alert()])

    book.update({"TITAN": 4700.0})  # +6% for the short → book 1 lot
    assert book.positions[0].lots_open == 1

    # 1% adverse from entry on a short @ 5000 is 5050.
    events = book.update({"TITAN": 5050.0})

    assert [e.kind for e in events] == ["stop_loss"]
    assert not book.positions
    assert events[0].pnl == pytest.approx(-50.0 * 175)


def test_stop_wins_when_a_snapshot_shows_both_levels(config):
    """Intrabar order is unknown, so the adverse case is assumed."""
    book = PaperBook(config)
    book.open_from_alerts([alert()])
    book.positions[0].entry_price = 5000.0

    events = book.update({"TITAN": 5200.0})

    assert [e.kind for e in events] == ["stop_loss"]


def test_expiry_closes_at_the_observed_price(config):
    book = PaperBook(config)
    book.open_from_alerts([alert()])

    events = book.update({"TITAN": 4990.0}, today=date(2026, 8, 25))

    assert events[0].kind == "expiry"
    assert not book.positions


def test_no_duplicate_position_in_the_same_stock(config):
    book = PaperBook(config)
    book.open_from_alerts([alert()])
    events = book.open_from_alerts([alert()])

    assert len(book.positions) == 1
    assert not events


def test_trade_is_skipped_when_margin_is_short(config):
    config.capital = 100_000
    book = PaperBook(config)

    events = book.open_from_alerts([alert()])

    assert not book.positions
    assert events[0].kind == "skipped"


def test_margin_is_released_when_a_position_closes(config):
    book = PaperBook(config)
    book.open_from_alerts([alert()])
    assert book.margin_blocked > 0

    book.update({"TITAN": 5150.0})

    assert book.margin_blocked == 0
    assert book.free_capital == pytest.approx(config.capital + book.realised_pnl)


def test_ledger_survives_a_restart(config):
    book = PaperBook(config)
    book.open_from_alerts([alert()])
    book.update({"TITAN": 4700.0})
    book.save()

    reloaded = PaperBook(config)

    assert len(reloaded.positions) == 1
    assert reloaded.positions[0].lots_open == 1
    assert reloaded.realised_pnl == pytest.approx(book.realised_pnl)
    assert reloaded.day_realised_pnl == pytest.approx(book.day_realised_pnl)


def test_day_realised_tracks_todays_exits(config):
    book = PaperBook(config)
    book.open_from_alerts([alert()])

    book.update({"TITAN": 4700.0})

    assert book.day_realised_pnl == pytest.approx(300.0 * 175)
    assert book.day_pnl({"TITAN": 4700.0}) == pytest.approx(
        book.day_realised_pnl + book.unrealised({"TITAN": 4700.0})
    )


def test_day_realised_resets_on_a_new_calendar_day(config):
    book = PaperBook(config)
    book.open_from_alerts([alert()])
    book.update({"TITAN": 4700.0})
    book.day_date = "2020-01-01"
    book.save()

    reloaded = PaperBook(config)

    assert reloaded.day_date == str(date.today())
    assert reloaded.day_realised_pnl == 0.0
    assert reloaded.realised_pnl == pytest.approx(300.0 * 175)


def test_telegram_report_lists_each_open_position_and_day_pnl(config):
    book = PaperBook(config)
    book.open_from_alerts(
        [
            alert(symbol="TITAN", ltp=5000.0),
            alert(signal=SignalType.PUT_OI, symbol="HAL", ltp=4900.0, lot_size=150),
        ]
    )

    report = book.telegram_report(
        {"TITAN": 4900.0, "HAL": 5000.0},
        events=[],
    )

    assert "Paper positions" in report
    assert "Day P&amp;L" in report
    assert "Book" in report
