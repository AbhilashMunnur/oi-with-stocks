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
    assert "stock future" in text
    assert "OI:" in text


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


def test_s2_digest_shows_skip_reason():
    notifier = Notifier(
        NotificationConfig(console=False, telegram=False, cooldown_minutes=30)
    )
    skipped = _alert(
        symbol="IDEA",
        signal=SignalType.CALL_OI_S2,
        ltp=14.95,
        rsi=70.7,
        oi_strike=15.0,
        distance_pct=0.33,
        skip_reason="did not qualify",
    )

    text = notifier._scenario2_digest([skipped])

    assert "CALL OI S2 (RSI ≥ 70)" in text
    assert "IDEA" in text
    assert "Not taking — did not qualify" in text



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


def test_telegram_pcr_matches_the_call_put_numbers_on_the_same_line():
    """Every book: ΔPCR is Put ΔOI / Call ΔOI of the figures beside it.

    RECLTD-style: wall +86 / +126 is 1.47. S2 band 0.59 must not sit on that
    wall line (that was the Telegram bug for every S2 name, not just REC).
    """
    notifier = Notifier(
        NotificationConfig(console=False, telegram=False, cooldown_minutes=30)
    )
    lot = 1000
    wall_call, wall_put = 86 * lot, 126 * lot
    band_call, band_put = 214 * lot, 126 * lot

    rsi = _alert(
        symbol="RECLTD",
        signal=SignalType.PUT_OI,
        ltp=321.10,
        rsi=27.4,
        oi_strike=320.0,
        distance_pct=0.34,
        call_oi_change=wall_call,
        put_oi_change=wall_put,
        change_pcr=wall_put / wall_call,
        lot_size=lot,
    )
    s1 = _alert(
        symbol="RECLTD",
        signal=SignalType.PUT_OI_S1,
        ltp=321.10,
        rsi=27.4,
        oi_strike=320.0,
        distance_pct=0.34,
        call_oi_change=wall_call,
        put_oi_change=wall_put,
        change_pcr=wall_put / wall_call,
        lot_size=lot,
    )
    s2 = _alert(
        symbol="RECLTD",
        signal=SignalType.PUT_OI_S2,
        ltp=321.10,
        rsi=27.4,
        oi_strike=320.0,
        distance_pct=0.34,
        call_oi_change=wall_call,
        put_oi_change=wall_put,
        # Filter still uses the band; Telegram must not mix it with wall ΔOI.
        change_pcr=band_put / band_call,
        band_call_oi_change=band_call,
        band_put_oi_change=band_put,
        lot_size=lot,
    )
    st = _alert(
        symbol="RECLTD",
        signal=SignalType.ST_BULLISH,
        ltp=321.10,
        rsi=0.0,
        oi_strike=320.0,
        distance_pct=0.34,
        supertrend=320.0,
        call_oi_change=wall_call,
        put_oi_change=wall_put,
        change_pcr=wall_put / wall_call,
        lot_size=lot,
    )

    rsi_text = notifier._rsi_digest([rsi])
    s1_text = notifier._scenario1_digest([s1])
    s2_text = notifier._scenario2_digest([s2])
    st_text = notifier._supertrend_digest([st])

    for text in (rsi_text, s1_text, st_text):
        assert "Call ΔOI +86 | Put ΔOI +126 | ΔPCR 1.47" in text
        assert "ΔPCR 0.59" not in text

    assert "Call ΔOI +86 | Put ΔOI +126 | ΔPCR 1.47" in s2_text
    assert "Band Call ΔOI +214 | Band Put ΔOI +126 | band ΔPCR 0.59" in s2_text
    assert "Call ΔOI +86 | Put ΔOI +126 | ΔPCR 0.59" not in s2_text

    text = "\n".join(["x" * 40] * 80)
    chunks = _telegram_chunks(text, limit=500)
    assert len(chunks) > 1
    assert all(len(chunk) <= 500 for chunk in chunks)


def test_rsi_candle_digest_shows_the_reversal_pattern():
    notifier = Notifier(
        NotificationConfig(console=False, telegram=False, cooldown_minutes=30)
    )
    short = _alert(
        symbol="TITAN",
        signal=SignalType.RSI_CANDLE_SHORT,
        ltp=5090.0,
        rsi=74.3,
        oi_strike=0.0,
        distance_pct=0.0,
        candle_pattern="inverted hammer",
    )
    text = notifier._rsi_candle_digest([short])
    assert "RSI_CandlePattern alerts" in text
    assert "SHORT (after RSI ≥ 70 strong bull)" in text
    assert "inverted hammer" in text
    assert "TITAN" in text
