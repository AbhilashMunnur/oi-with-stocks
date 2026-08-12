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
