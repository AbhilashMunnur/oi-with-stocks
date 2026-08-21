from datetime import date

from src.paper_trading.futures_expiry import (
    futures_symbol_year_month,
    is_standard_stock_future,
    target_futures_year_month,
)


def test_third_month_from_august_is_october():
    assert target_futures_year_month(date(2026, 8, 10), month_index=3) == (2026, 10)


def test_third_month_wraps_the_year():
    assert target_futures_year_month(date(2026, 11, 15), month_index=3) == (2027, 1)
    assert target_futures_year_month(date(2026, 12, 1), month_index=3) == (2027, 2)


def test_current_month_index_is_unchanged():
    assert target_futures_year_month(date(2026, 8, 10), month_index=1) == (2026, 8)


def test_standard_futures_symbol_is_recognised():
    assert is_standard_stock_future("TITAN26OCTFUT")
    assert not is_standard_stock_future("TITAN27OCT26FUT")
    assert futures_symbol_year_month("TITAN26OCTFUT") == (2026, 10)


def test_futures_contract_picks_october_in_august():
    from src.data.angelone_client import AngelOneClient

    client = AngelOneClient.__new__(AngelOneClient)
    client._equity_tokens = {"TITAN": "1"}
    client._option_rows = {}
    client._futures_rows = {
        "TITAN": [
            {"symbol": "TITAN25AUG26FUT", "expiry": "25AUG2026", "lotsize": "175", "token": "1"},
            {"symbol": "TITAN26AUGFUT", "expiry": "27AUG2026", "lotsize": "175", "token": "2"},
            {"symbol": "TITAN26SEPFUT", "expiry": "24SEP2026", "lotsize": "175", "token": "3"},
            {"symbol": "TITAN29SEP26FUT", "expiry": "29SEP2026", "lotsize": "175", "token": "4"},
            {"symbol": "TITAN27OCT26FUT", "expiry": "27OCT2026", "lotsize": "175", "token": "5"},
            {"symbol": "TITAN26OCTFUT", "expiry": "29OCT2026", "lotsize": "175", "token": "oct"},
        ]
    }
    client._cache_date = date(2026, 8, 10)
    client._refresh_for_new_day = lambda: None

    contract = client.futures_contract("TITAN", month_index=3, as_of=date(2026, 8, 10))

    assert contract is not None
    assert contract.expiry == "2026-10-29"
    assert contract.lot_size == 175
    assert contract.token == "oct"
    assert contract.nfo_symbol == "TITAN26OCTFUT"


def test_futures_contract_falls_back_when_only_day_prefixed_symbols_exist():
    from src.data.angelone_client import AngelOneClient

    client = AngelOneClient.__new__(AngelOneClient)
    client._equity_tokens = {"HAL": "1"}
    client._option_rows = {}
    client._futures_rows = {
        "HAL": [
            {"symbol": "HAL27OCT26FUT", "expiry": "27OCT2026", "lotsize": "150", "token": "a"},
            {"symbol": "HAL29OCT26FUT", "expiry": "29OCT2026", "lotsize": "150", "token": "b"},
        ]
    }
    client._refresh_for_new_day = lambda: None

    contract = client.futures_contract("HAL", month_index=3, as_of=date(2026, 8, 10))

    assert contract is not None
    assert contract.expiry == "2026-10-29"
    assert contract.lot_size == 150
    assert contract.token == "b"


def test_get_futures_ltps_quotes_the_nfo_token():
    from src.data.angelone_client import AngelOneClient

    client = AngelOneClient.__new__(AngelOneClient)
    client._equity_tokens = {"TITAN": "1"}
    client._option_rows = {}
    client._futures_rows = {
        "TITAN": [
            {
                "symbol": "TITAN26OCTFUT",
                "expiry": "29OCT2026",
                "lotsize": "175",
                "token": "oct",
                "exch_seg": "NFO",
            }
        ]
    }
    client._refresh_for_new_day = lambda: None
    client._quote_throttle = object()

    def fake_call(_throttle, method, mode, payload):
        assert method == "getMarketData"
        assert mode == "LTP"
        assert payload == {"NFO": ["oct"]}
        return {
            "status": True,
            "data": {"fetched": [{"symbolToken": "oct", "ltp": 5120.0}]},
        }

    client._call = fake_call
    prices = client.get_futures_ltps(
        ["TITAN"], month_index=3, as_of=date(2026, 8, 10)
    )
    assert prices == {"TITAN": 5120.0}
