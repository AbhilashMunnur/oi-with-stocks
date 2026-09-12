from src.config import PaperTradingConfig, SignalType, load_config
from src.oi_analyzer import ScanAlert
from src.paper_trading import PaperBook


def _alert(signal, symbol="TITAN"):
    return ScanAlert(
        symbol=symbol,
        signal=signal,
        ltp=5000.0,
        rsi=70.0,
        oi_strike=5000.0,
        oi_value=1,
        distance_pct=0.2,
        expiry="2026-10-29",
        lot_size=175,
        message="test",
    )


def test_config_loads_live_and_final_candle_books():
    config = load_config()
    assert config.paper_trading is None

    two_week = config.rsi_candle_2w_paper_trading
    assert two_week is not None
    assert two_week.enabled is True
    assert two_week.name == "RSI_CandlePattern"
    assert two_week.capital == 40_000_000
    assert two_week.lots_per_trade == 3
    assert two_week.skip_new_entries is False
    assert two_week.mark_entry_contract is True
    assert two_week.cash_close_stop is True
    assert two_week.futures_month == 3
    assert two_week.first_target_pct == 5.0
    assert two_week.second_target_pct == 12.0
    assert two_week.smma_fast == 21
    assert two_week.smma_slow == 50
    assert two_week.second_lot_rsi_short == 30
    assert two_week.second_lot_rsi_long == 70
    assert two_week.candle_stop is True
    assert two_week.stop_loss_pct == 2.0
    assert two_week.smma_reversal_exit is False
    assert two_week.google_worksheet == "RSI Paper trades"
    assert two_week.google_summary_worksheet == "RSI Portfolio Summary"
    assert "rsi_candle_3m_2w" in two_week.ledger_path
    assert two_week.loss_streak_count == 3
    assert two_week.loss_streak_sessions == 10
    assert two_week.skip_qualifies_after_streak == 3

    three_lot = config.rsi_candle_3lot_paper_trading
    assert three_lot is not None
    assert three_lot.enabled is True
    assert three_lot.name == "RSI_Candle_3Lot"
    assert three_lot.capital == 40_000_000
    assert three_lot.lots_per_trade == 3
    assert three_lot.candle_stop is False
    assert three_lot.stop_loss_pct == 1.5
    assert three_lot.second_lot_stop_pct == 1.5
    # Both books take the global 70/30 screen; no per-book RSI gate.
    assert three_lot.rsi_call_threshold is None
    assert three_lot.rsi_put_threshold is None
    assert two_week.rsi_call_threshold is None
    assert two_week.rsi_put_threshold is None
    assert three_lot.smma_reversal_exit is False
    assert three_lot.first_target_pct == 5.0
    assert three_lot.second_target_pct == 12.0
    assert three_lot.google_worksheet == "RSI 3Lot Paper trades"
    assert three_lot.google_summary_worksheet == "RSI 3Lot Portfolio Summary"
    assert "rsi_candle_3lot" in three_lot.ledger_path
    assert three_lot.ledger_path != two_week.ledger_path

    assert "DIVISLAB" in config.no_short_symbols
    assert "LAURUSLABS" in config.no_short_symbols
    assert "LUPIN" not in config.no_short_symbols
    assert config.oi.skip_monthly_expiry is True
    assert config.oi.extreme_proximity_pct == 2.0
    assert config.oi.extreme_cooldown_days == 2
    assert config.oi.proximity_pct == 1.0


def test_live_and_final_books_do_not_share_positions(tmp_path):
    live_cfg = PaperTradingConfig(
        enabled=True,
        name="RSI_CandlePattern",
        capital=40_000_000,
        lots_per_trade=3,
        first_target_pct=5.0,
        second_target_pct=12.0,
        stop_loss_pct=2.0,
        candle_stop=True,
        margin_pct=20.0,
        ledger_path=str(tmp_path / "live.json"),
        journal_csv=str(tmp_path / "live.csv"),
    )
    final_cfg = PaperTradingConfig(
        enabled=True,
        name="RSI_Candle_3Lot",
        capital=40_000_000,
        lots_per_trade=3,
        first_target_pct=5.0,
        second_target_pct=12.0,
        stop_loss_pct=2.0,
        candle_stop=False,
        margin_pct=20.0,
        ledger_path=str(tmp_path / "final.json"),
        journal_csv=str(tmp_path / "final.csv"),
    )

    live_book = PaperBook(live_cfg)
    final_book = PaperBook(final_cfg)

    live_book.open_from_alerts([_alert(SignalType.RSI_CANDLE_SHORT)])
    final_book.open_from_alerts([_alert(SignalType.RSI_CANDLE_SHORT)])

    assert len(live_book.positions) == 1
    assert len(final_book.positions) == 1
    assert live_book.positions[0].direction == "SHORT"
    assert final_book.positions[0].direction == "SHORT"
    assert live_book.positions[0].lots_open == 3
    assert final_book.positions[0].lots_open == 3
    assert live_book.free_capital < live_cfg.capital
    assert final_book.free_capital < final_cfg.capital
