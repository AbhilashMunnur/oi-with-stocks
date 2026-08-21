from src.data.angelone_client import AngelOneClient


def test_contracts_near_price_drops_far_otm_strikes():
    rows = [
        {"strike": 100_000, "token": "atm"},   # ₹1,000
        {"strike": 200_000, "token": "far"},   # ₹2,000
    ]
    kept = AngelOneClient._contracts_near_price(None, rows, 1000.0, 25.0)
    assert [row["token"] for row in kept] == ["atm"]


def test_contracts_near_price_keeps_full_chain_when_band_empty():
    rows = [{"strike": 500_000, "token": "only"}]
    kept = AngelOneClient._contracts_near_price(None, rows, 1000.0, 25.0)
    assert kept == rows
