from src.data.models import OISnapshot


def snapshot(call_change=None, put_change=None, lot_size=500) -> OISnapshot:
    return OISnapshot(
        symbol="RELIANCE",
        ltp=1327.0,
        max_call_oi_strike=1400,
        max_call_oi=7_508_000,
        max_put_oi_strike=1300,
        max_put_oi=5_721_500,
        expiry="2026-08-25",
        lot_size=lot_size,
        call_oi_change=call_change,
        put_oi_change=put_change,
    )


def test_open_interest_converts_to_contracts():
    assert snapshot().contracts(7_508_000) == 15_016


def test_negative_change_converts_without_rounding_away_from_zero():
    assert snapshot().contracts(-475_500) == -951


def test_change_pcr_when_both_sides_are_writing():
    assert snapshot(call_change=500_000, put_change=750_000).change_pcr == 1.5


def test_change_pcr_is_none_when_puts_unwind():
    # A negative put change would otherwise yield a negative "ratio".
    assert snapshot(call_change=560_500, put_change=-475_500).change_pcr is None


def test_change_pcr_is_none_when_calls_unwind():
    assert snapshot(call_change=-100_000, put_change=200_000).change_pcr is None


def test_change_pcr_is_none_when_calls_are_flat():
    # Guards against dividing by zero.
    assert snapshot(call_change=0, put_change=200_000).change_pcr is None


def test_change_pcr_is_none_without_data():
    assert snapshot().change_pcr is None


def test_change_pcr_uses_band_totals_when_set():
    oi = snapshot(call_change=200, put_change=50)
    oi.band_call_oi_change = 1000
    oi.band_put_oi_change = 400
    assert oi.change_pcr == 0.4



def test_alert_reports_each_leg_separately():
    """A call alert must not fold put OI into the number it shows."""
    from src.config import SignalType
    from src.data.models import PriceSnapshot
    from src.oi_analyzer import evaluate_stock

    oi = snapshot(call_change=560_500, put_change=-475_500)
    oi.max_call_oi_strike = 1330  # within 2% of the 1327 spot
    # Put unwinding → no change PCR; call writing still supports the short.
    alert = evaluate_stock(PriceSnapshot("RELIANCE", 1327.0, 74.0), oi, 70, 35, 2.0)

    assert alert is not None
    assert alert.signal is SignalType.CALL_OI
    assert alert.oi_change == 560_500
    assert alert.call_oi_change == 560_500
    assert alert.put_oi_change == -475_500
    assert "Call ΔOI +1,121 contracts" in alert.message
    assert "Put ΔOI -951 contracts" in alert.message
