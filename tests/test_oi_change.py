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


def test_buildup_describes_each_side():
    assert snapshot(call_change=1, put_change=-1).buildup == "call writing, put unwinding"
    assert snapshot(call_change=-1, put_change=1).buildup == "call unwinding, put writing"


def test_buildup_reports_missing_data():
    assert snapshot().buildup == "OI change unavailable"
