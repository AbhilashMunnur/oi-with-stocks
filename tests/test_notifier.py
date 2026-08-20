from src.config import NotificationConfig, SignalType
from src.notifications.notifier import Notifier, _telegram_chunks
from src.oi_analyzer import ScanAlert


def _alert(**kwargs) -> ScanAlert:
    base = dict(
        symbol="TITAN",
        signal=SignalType.CALL_OI,
        ltp=5090.0,
        rsi=74.3,
        oi_strike=5100.0,
        oi_value=800_000,
        distance_pct=0.20,
        expiry="2026-08-25",
        message="",
        lot_size=175,
    )
    base.update(kwargs)
    return ScanAlert(**base)


def test_rsi_digest_shows_why_a_name_is_not_taken():
    notifier = Notifier(
        NotificationConfig(console=False, telegram=False, cooldown_minutes=30)
    )
    taking = _alert(
        call_oi_change=175 * 111,
        put_oi_change=175 * -1,
    )
    skipped = _alert(
        symbol="HDFCBANK",
        ltp=1650.0,
        rsi=71.4,
        oi_strike=1720.0,
        distance_pct=4.07,
        skip_reason="4.07% from max Call OI (need ≤ 1%)",
    )

    text = notifier._rsi_digest([skipped, taking])

    assert "CALL OI (RSI ≥ 70)" in text
    assert text.index("TITAN") < text.index("HDFCBANK")
    assert "Not taking — 4.07% from max Call OI (need ≤ 1%)" in text
    assert "Call ΔOI +111" in text


def test_s1_digest_shows_skip_reason():
    notifier = Notifier(
        NotificationConfig(console=False, telegram=False, cooldown_minutes=30)
    )
    skipped = _alert(
        symbol="COFORGE",
        signal=SignalType.CALL_OI_S1,
        ltp=1810.0,
        rsi=73.9,
        oi_strike=1900.0,
        distance_pct=4.74,
        skip_reason="4.74% from max Call OI (need ≤ 1%)",
    )

    text = notifier._scenario1_digest([skipped])

    assert "CALL OI S1 (RSI ≥ 70)" in text
    assert "COFORGE" in text
    assert "Not taking — 4.74% from max Call OI (need ≤ 1%)" in text


def test_supertrend_digest_shows_skip_reason():
    notifier = Notifier(
        NotificationConfig(console=False, telegram=False, cooldown_minutes=30)
    )
    skipped = _alert(
        symbol="RELIANCE",
        signal=SignalType.ST_BEARISH,
        ltp=1380.0,
        rsi=0.0,
        oi_strike=1380.0,
        distance_pct=0.32,
        supertrend=1384.5,
        skip_reason="Call ΔOI +0 shares (call unwinding/flat — skip short)",
        call_oi_change=0,
        put_oi_change=100,
        lot_size=250,
    )

    text = notifier._supertrend_digest([skipped])

    assert "BEARISH" in text
    assert "RELIANCE" in text
    assert "Not taking — Call ΔOI +0 shares" in text

    text = "\n".join(["x" * 40] * 80)
    chunks = _telegram_chunks(text, limit=500)
    assert len(chunks) > 1
    assert all(len(chunk) <= 500 for chunk in chunks)
