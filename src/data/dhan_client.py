from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta

import pandas as pd
from dhanhq import DhanContext, dhanhq
from dotenv import load_dotenv

from src.data.base import MarketDataProvider, ProviderCredentialsError, download_cached
from src.data.models import OISnapshot, PriceSnapshot
from src.indicators import calculate_rsi

SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
EQUITY_SEGMENT = "NSE_EQ"


def _payload(response: dict | None):
    """Unwrap the SDK envelope ({status, remarks, data}) down to the API payload."""
    if not isinstance(response, dict):
        return None
    if str(response.get("status", "success")).lower() in {"failure", "error"}:
        return None

    data = response.get("data")
    while isinstance(data, dict) and "data" in data and set(data) <= {"data", "status", "remarks"}:
        data = data["data"]
    return data


class DhanProvider(MarketDataProvider):
    """Live LTP, RSI and option-chain OI from DhanHQ v2."""

    name = "dhan"

    def __init__(
        self,
        rsi_period: int = 14,
        history_days: int = 120,
        option_chain_delay_seconds: float = 3.0,
    ):
        load_dotenv()
        client_id = os.getenv("DHAN_CLIENT_ID", "").strip()
        access_token = os.getenv("DHAN_ACCESS_TOKEN", "").strip()

        if not client_id or not access_token:
            raise ProviderCredentialsError(
                "Dhan needs DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN in .env. "
                "Generate an access token from web.dhan.co under DhanHQ Trading APIs."
            )

        self.rsi_period = rsi_period
        self.history_days = history_days
        self.option_chain_delay_seconds = option_chain_delay_seconds
        self.client = dhanhq(DhanContext(client_id, access_token))
        self._security_ids: dict[str, int] | None = None
        self._last_chain_call = 0.0

    def _load_security_ids(self) -> dict[str, int]:
        """Map each F&O stock symbol to the security ID of its underlying equity."""
        if self._security_ids is not None:
            return self._security_ids

        path = download_cached(SCRIP_MASTER_URL, "dhan_scrip_master.csv")
        frame = pd.read_csv(
            path,
            usecols=["EXCH_ID", "INSTRUMENT", "UNDERLYING_SYMBOL", "UNDERLYING_SECURITY_ID"],
            dtype={"EXCH_ID": "string", "INSTRUMENT": "string", "UNDERLYING_SYMBOL": "string"},
        )

        options = frame[(frame["EXCH_ID"] == "NSE") & (frame["INSTRUMENT"] == "OPTSTK")]
        mapping = (
            options.groupby("UNDERLYING_SYMBOL")["UNDERLYING_SECURITY_ID"].first().dropna().to_dict()
        )

        self._security_ids = {str(k).upper(): int(v) for k, v in mapping.items()}
        return self._security_ids

    def _security_id(self, symbol: str) -> int | None:
        return self._load_security_ids().get(symbol.upper())

    def _throttle_option_chain(self) -> None:
        """Dhan allows one option-chain request every three seconds."""
        elapsed = time.monotonic() - self._last_chain_call
        if elapsed < self.option_chain_delay_seconds:
            time.sleep(self.option_chain_delay_seconds - elapsed)
        self._last_chain_call = time.monotonic()

    def _nearest_expiry(self, security_id: int) -> str | None:
        expiries = _payload(self.client.expiry_list(security_id, EQUITY_SEGMENT))
        if not expiries:
            return None

        today = date.today()
        upcoming = [e for e in expiries if datetime.strptime(e, "%Y-%m-%d").date() >= today]
        return upcoming[0] if upcoming else expiries[0]

    def get_oi_snapshot(self, symbol: str) -> OISnapshot | None:
        symbol = symbol.upper()
        security_id = self._security_id(symbol)
        if not security_id:
            print(f"  {symbol}: skipped (no stock options listed on Dhan)")
            return None

        try:
            expiry = self._nearest_expiry(security_id)
            if not expiry:
                return None

            self._throttle_option_chain()
            chain = _payload(self.client.option_chain(security_id, EQUITY_SEGMENT, expiry))
        except Exception as exc:
            print(f"  {symbol}: skipped (Dhan option chain failed - {exc})")
            return None

        if not chain or not chain.get("oc"):
            print(f"  {symbol}: skipped (empty option chain)")
            return None

        ltp = float(chain.get("last_price") or 0)
        max_call_oi = max_put_oi = -1
        max_call_strike = max_put_strike = 0.0

        for raw_strike, legs in chain["oc"].items():
            strike = float(raw_strike)
            call_oi = int((legs.get("ce") or {}).get("oi") or 0)
            put_oi = int((legs.get("pe") or {}).get("oi") or 0)

            if call_oi > max_call_oi:
                max_call_oi, max_call_strike = call_oi, strike
            if put_oi > max_put_oi:
                max_put_oi, max_put_strike = put_oi, strike

        if max_call_oi < 0 or max_put_oi < 0:
            return None

        return OISnapshot(
            symbol=symbol,
            ltp=ltp,
            max_call_oi_strike=max_call_strike,
            max_call_oi=max_call_oi,
            max_put_oi_strike=max_put_strike,
            max_put_oi=max_put_oi,
            expiry=expiry,
        )

    def _live_ltp(self, security_id: int) -> float | None:
        response = _payload(self.client.ticker_data({EQUITY_SEGMENT: [security_id]}))
        if not isinstance(response, dict):
            return None

        quote = (response.get(EQUITY_SEGMENT) or {}).get(str(security_id)) or {}
        price = quote.get("last_price")
        return float(price) if price else None

    def get_price_snapshot(self, symbol: str, ltp: float | None = None) -> PriceSnapshot | None:
        symbol = symbol.upper()
        security_id = self._security_id(symbol)
        if not security_id:
            return None

        if not ltp:
            try:
                ltp = self._live_ltp(security_id)
            except Exception as exc:
                print(f"  {symbol}: skipped (Dhan LTP failed - {exc})")
                return None

        if not ltp:
            print(f"  {symbol}: skipped (no live price)")
            return None

        today = date.today()
        try:
            candles = _payload(
                self.client.historical_daily_data(
                    security_id=str(security_id),
                    exchange_segment=EQUITY_SEGMENT,
                    instrument_type="EQUITY",
                    from_date=(today - timedelta(days=self.history_days)).strftime("%Y-%m-%d"),
                    to_date=today.strftime("%Y-%m-%d"),
                )
            )
        except Exception as exc:
            print(f"  {symbol}: skipped (Dhan candles failed - {exc})")
            return None

        closes = (candles or {}).get("close") or []
        if not closes:
            print(f"  {symbol}: skipped (no historical candles)")
            return None

        series = pd.Series([float(c) for c in closes], dtype=float)
        series.iloc[-1] = ltp
        rsi = calculate_rsi(series, period=self.rsi_period)

        return PriceSnapshot(symbol=symbol, ltp=float(ltp), rsi=rsi)
