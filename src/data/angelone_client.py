from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta

import pandas as pd
import pyotp
from dotenv import load_dotenv
from SmartApi import SmartConnect

from src.data.base import MarketDataProvider, ProviderCredentialsError, download_cached
from src.data.models import OISnapshot, PriceSnapshot
from src.indicators import calculate_rsi

SCRIP_MASTER_URL = (
    "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
)

# Angel One quotes accept at most 50 tokens per request and roughly one request per second.
MAX_TOKENS_PER_REQUEST = 50
REQUEST_DELAY_SECONDS = 1.0

# Strikes in the instrument master are quoted in paise.
STRIKE_DIVISOR = 100.0


class AngelOneProvider(MarketDataProvider):
    """Live LTP, RSI and option-chain OI from Angel One SmartAPI."""

    name = "angelone"

    def __init__(self, rsi_period: int = 14, history_days: int = 120):
        load_dotenv()
        api_key = os.getenv("ANGEL_API_KEY", "").strip()
        client_code = os.getenv("ANGEL_CLIENT_CODE", "").strip()
        pin = os.getenv("ANGEL_PIN", "").strip()
        totp_secret = os.getenv("ANGEL_TOTP_SECRET", "").strip()

        if not all([api_key, client_code, pin, totp_secret]):
            raise ProviderCredentialsError(
                "Angel One needs ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PIN and "
                "ANGEL_TOTP_SECRET in .env. Create an app at smartapi.angelone.in."
            )

        self.rsi_period = rsi_period
        self.history_days = history_days
        self.client = SmartConnect(api_key=api_key)

        session = self.client.generateSession(client_code, pin, pyotp.TOTP(totp_secret).now())
        if not session or not session.get("status"):
            message = (session or {}).get("message", "unknown error")
            raise ProviderCredentialsError(f"Angel One login failed: {message}")

        self._equity_tokens: dict[str, str] | None = None
        self._option_rows: dict[str, list[dict]] | None = None

    def close(self) -> None:
        try:
            self.client.terminateSession(os.getenv("ANGEL_CLIENT_CODE", "").strip())
        except Exception:
            pass

    def _load_instruments(self) -> None:
        if self._equity_tokens is not None:
            return

        path = download_cached(SCRIP_MASTER_URL, "angelone_scrip_master.json")
        with path.open(encoding="utf-8") as handle:
            rows = json.load(handle)

        equities: dict[str, str] = {}
        options: dict[str, list[dict]] = {}

        for row in rows:
            segment = row.get("exch_seg")

            if segment == "NSE" and str(row.get("symbol", "")).endswith("-EQ"):
                equities[str(row["symbol"])[:-3].upper()] = str(row["token"])
            elif segment == "NFO" and row.get("instrumenttype") == "OPTSTK":
                options.setdefault(str(row.get("name", "")).upper(), []).append(row)

        self._equity_tokens = equities
        self._option_rows = options

    def _nearest_expiry_rows(self, symbol: str) -> tuple[str, list[dict]] | None:
        self._load_instruments()
        contracts = (self._option_rows or {}).get(symbol.upper())
        if not contracts:
            return None

        today = date.today()
        by_expiry: dict[date, list[dict]] = {}
        for row in contracts:
            try:
                expiry = datetime.strptime(str(row["expiry"]), "%d%b%Y").date()
            except (KeyError, ValueError):
                continue
            if expiry >= today:
                by_expiry.setdefault(expiry, []).append(row)

        if not by_expiry:
            return None

        nearest = min(by_expiry)
        return nearest.strftime("%Y-%m-%d"), by_expiry[nearest]

    def _fetch_open_interest(self, tokens: list[str]) -> dict[str, int]:
        """Fetch OI for NFO tokens in batches, keyed by symbol token."""
        open_interest: dict[str, int] = {}

        for start in range(0, len(tokens), MAX_TOKENS_PER_REQUEST):
            batch = tokens[start : start + MAX_TOKENS_PER_REQUEST]
            response = self.client.getMarketData("FULL", {"NFO": batch})
            time.sleep(REQUEST_DELAY_SECONDS)

            if not response or not response.get("status"):
                continue

            for quote in (response.get("data") or {}).get("fetched") or []:
                token = str(quote.get("symbolToken"))
                open_interest[token] = int(quote.get("opnInterest") or 0)

        return open_interest

    def get_oi_snapshot(self, symbol: str) -> OISnapshot | None:
        symbol = symbol.upper()
        nearest = self._nearest_expiry_rows(symbol)
        if not nearest:
            print(f"  {symbol}: skipped (no stock options listed on Angel One)")
            return None

        expiry, contracts = nearest
        by_token = {str(row["token"]): row for row in contracts}

        try:
            open_interest = self._fetch_open_interest(list(by_token))
        except Exception as exc:
            print(f"  {symbol}: skipped (Angel One quotes failed - {exc})")
            return None

        if not open_interest:
            print(f"  {symbol}: skipped (no OI returned)")
            return None

        max_call_oi = max_put_oi = -1
        max_call_strike = max_put_strike = 0.0

        for token, oi in open_interest.items():
            row = by_token.get(token)
            if not row:
                continue

            strike = float(row.get("strike") or 0) / STRIKE_DIVISOR
            if strike <= 0:
                continue

            if str(row.get("symbol", "")).endswith("CE"):
                if oi > max_call_oi:
                    max_call_oi, max_call_strike = oi, strike
            elif str(row.get("symbol", "")).endswith("PE"):
                if oi > max_put_oi:
                    max_put_oi, max_put_strike = oi, strike

        if max_call_oi < 0 or max_put_oi < 0:
            return None

        ltp = self._live_ltp(symbol) or 0.0

        return OISnapshot(
            symbol=symbol,
            ltp=ltp,
            max_call_oi_strike=max_call_strike,
            max_call_oi=max_call_oi,
            max_put_oi_strike=max_put_strike,
            max_put_oi=max_put_oi,
            expiry=expiry,
        )

    def _live_ltp(self, symbol: str) -> float | None:
        self._load_instruments()
        token = (self._equity_tokens or {}).get(symbol.upper())
        if not token:
            return None

        response = self.client.getMarketData("LTP", {"NSE": [token]})
        time.sleep(REQUEST_DELAY_SECONDS)

        if not response or not response.get("status"):
            return None

        fetched = (response.get("data") or {}).get("fetched") or []
        return float(fetched[0]["ltp"]) if fetched else None

    def get_price_snapshot(self, symbol: str, ltp: float | None = None) -> PriceSnapshot | None:
        symbol = symbol.upper()
        self._load_instruments()
        token = (self._equity_tokens or {}).get(symbol)
        if not token:
            print(f"  {symbol}: skipped (symbol not found on Angel One)")
            return None

        if not ltp:
            try:
                ltp = self._live_ltp(symbol)
            except Exception as exc:
                print(f"  {symbol}: skipped (Angel One LTP failed - {exc})")
                return None

        if not ltp:
            print(f"  {symbol}: skipped (no live price)")
            return None

        now = datetime.now()
        try:
            response = self.client.getCandleData(
                {
                    "exchange": "NSE",
                    "symboltoken": token,
                    "interval": "ONE_DAY",
                    "fromdate": (now - timedelta(days=self.history_days)).strftime("%Y-%m-%d %H:%M"),
                    "todate": now.strftime("%Y-%m-%d %H:%M"),
                }
            )
            time.sleep(REQUEST_DELAY_SECONDS)
        except Exception as exc:
            print(f"  {symbol}: skipped (Angel One candles failed - {exc})")
            return None

        candles = (response or {}).get("data") or []
        if not candles:
            print(f"  {symbol}: skipped (no historical candles)")
            return None

        series = pd.Series([float(candle[4]) for candle in candles], dtype=float)
        series.iloc[-1] = ltp
        rsi = calculate_rsi(series, period=self.rsi_period)

        return PriceSnapshot(symbol=symbol, ltp=float(ltp), rsi=rsi)
