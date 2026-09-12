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


def test_telegram_pcr_matches_the_call_put_numbers_on_the_same_line():
    notifier = Notifier(
        NotificationConfig(console=False, telegram=False, cooldown_minutes=30)
    )
    lot = 1000
    wall_call, wall_put = 86 * lot, 126 * lot

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

    rsi_text = notifier._rsi_digest([rsi])
    assert "Call ΔOI +86 | Put ΔOI +126 | ΔPCR 1.47" in rsi_text

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


def test_telegram_http_200_with_ok_false_is_not_delivered(monkeypatch):
    import requests

    notifier = Notifier(
        NotificationConfig(console=False, telegram=True, cooldown_minutes=30)
    )
    notifier.bot_token = "token"
    notifier.chat_ids = ["111"]

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": False, "description": "Bad Request: chat not found"}

    def fake_post(*_args, **_kwargs):
        return FakeResponse()

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(
        notifier, "_chat_error", lambda chat_id, exc: None
    )
    assert notifier.send_message("hello") == 0
    assert notifier.send_photo(b"png", caption="hi") == 0
