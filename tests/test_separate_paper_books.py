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


def test_config_loads_separate_supertrend_paper_book():
    config = load_config()
    assert config.paper_trading.name == "RSI+OI"
    assert config.supertrend_paper_trading is not None
    assert config.supertrend_paper_trading.name == "Supertrend"
    assert "supertrend" in config.supertrend_paper_trading.ledger_path


def test_config_loads_scenario1_paper_book():
    config = load_config()
    assert config.rsi_s1_paper_trading is not None
    assert config.rsi_s1_paper_trading.name == "RSI+OI S1"
    assert "rsi_s1" in config.rsi_s1_paper_trading.ledger_path
    assert config.rsi_s1_paper_trading.ledger_path != config.paper_trading.ledger_path
    assert config.rsi_s1_paper_trading.lots_per_trade == 3
    assert config.rsi_s1_paper_trading.first_target_pct == 6.0
    assert config.rsi_s1_paper_trading.second_target_pct == 10.0
    assert config.rsi_s1_paper_trading.third_target_pct == 14.0
    assert config.rsi_s1_paper_trading.google_worksheet == "RSI S1 Paper trades"
    assert (
        config.rsi_s1_paper_trading.google_summary_worksheet
        == "RSI S1 Portfolio Summary"
    )
    assert config.paper_trading.google_worksheet == "RSI Paper trades"
    assert config.paper_trading.google_summary_worksheet == "RSI Portfolio Summary"
    assert config.paper_trading.lots_per_trade == 2
    assert config.paper_trading.second_target_pct == 11.0
    assert config.paper_trading.third_target_pct is None


def test_config_loads_scenario2_paper_book():
    config = load_config()
    assert config.rsi_s2_paper_trading is not None
    assert config.rsi_s2_paper_trading.name == "RSI+OI S2"
    assert "rsi_s2" in config.rsi_s2_paper_trading.ledger_path
    assert config.rsi_s2_paper_trading.ledger_path != config.rsi_s1_paper_trading.ledger_path
    assert config.rsi_s2_paper_trading.lots_per_trade == 3
    assert config.rsi_s2_paper_trading.third_target_pct == 14.0
    assert config.rsi_s2_paper_trading.google_worksheet == "RSI S2 Paper trades"
    assert (
        config.rsi_s2_paper_trading.google_summary_worksheet
        == "RSI S2 Portfolio Summary"
    )
    assert config.oi.s2_proximity_pct == 1.0
    assert config.oi.s2_pcr_strikes == 1
    assert config.oi.skip_monthly_expiry is True
    assert config.oi.extreme_proximity_pct == 2.0
    assert config.oi.extreme_cooldown_days == 2
    assert config.rsi_s2_paper_trading.stop_loss_pct == 3.0
    assert config.rsi_s2_paper_trading.block_same_day_reentry is True
    assert config.oi.proximity_pct == 1.0
    assert "DIVISLAB" in config.no_short_symbols
    assert "LAURUSLABS" in config.no_short_symbols
    assert "LUPIN" not in config.no_short_symbols
    assert "SUNPHARMA" not in config.no_short_symbols


def test_s2_call_alert_opens_short(tmp_path):
    cfg = PaperTradingConfig(
        enabled=True,
        name="RSI+OI S2",
        capital=5_000_000,
        lots_per_trade=3,
        first_target_pct=6.0,
        second_target_pct=10.0,
        third_target_pct=14.0,
        stop_loss_pct=4.0,
        margin_pct=20.0,
        ledger_path=str(tmp_path / "s2.json"),
        journal_csv=str(tmp_path / "s2.csv"),
    )
    book = PaperBook(cfg)
    book.open_from_alerts([_alert(SignalType.CALL_OI_S2)])
    assert book.positions[0].direction == "SHORT"
    assert book.positions[0].lots_open == 3


def test_rsi_and_supertrend_books_do_not_share_positions(tmp_path):
    rsi_cfg = PaperTradingConfig(
        enabled=True,
        name="RSI+OI",
        capital=5_000_000,
        lots_per_trade=2,
        first_target_pct=6.0,
        second_target_pct=11.0,
        stop_loss_pct=4.0,
        margin_pct=20.0,
        ledger_path=str(tmp_path / "rsi.json"),
        journal_csv=str(tmp_path / "rsi.csv"),
    )
    st_cfg = PaperTradingConfig(
        enabled=True,
        name="Supertrend",
        capital=5_000_000,
        lots_per_trade=2,
        first_target_pct=6.0,
        second_target_pct=11.0,
        stop_loss_pct=4.0,
        margin_pct=20.0,
        ledger_path=str(tmp_path / "st.json"),
        journal_csv=str(tmp_path / "st.csv"),
    )

    rsi_book = PaperBook(rsi_cfg)
    st_book = PaperBook(st_cfg)

    rsi_book.open_from_alerts([_alert(SignalType.CALL_OI)])
    st_book.open_from_alerts([_alert(SignalType.ST_BEARISH)])

    assert len(rsi_book.positions) == 1
    assert len(st_book.positions) == 1
    assert rsi_book.positions[0].direction == "SHORT"
    assert st_book.positions[0].direction == "SHORT"
    # Separate capital pools.
    assert rsi_book.free_capital < rsi_cfg.capital
    assert st_book.free_capital < st_cfg.capital
