from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import logzero
import pandas as pd
import pyotp
from dotenv import load_dotenv
from SmartApi import SmartConnect

# The SDK logs connection pool chatter at info level on every call.
logzero.loglevel(logging.WARNING)

from src.data.base import CACHE_DIR, CredentialsError, download_cached
from src.data.models import OISnapshot
from src.data.option_expiry import select_scan_oi_expiry
from src.indicators import calculate_rsi
from src.oi_analyzer import select_active_oi_walls
from src.paper_trading.futures_expiry import target_futures_year_month

SCRIP_MASTER_URL = (
    "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
)

# Quotes accept 50 tokens per request at 1 request/second.
MAX_TOKENS_PER_REQUEST = 50
QUOTE_INTERVAL_SECONDS = 1.05
# Drop deep OTM strikes before quoting. Max OI walls we trade sit near spot;
# skipping the far tails cuts several quote batches per name.
OI_SNAPSHOT_BAND_PCT = 25.0
OI_STRIKE_BAND_PCT = 8.0

# Angel's published candle limit is higher, but hosted runners hit AB1021
# quickly if we push it — stay near 1 request/second and back off hard.
CANDLE_INTERVAL_SECONDS = 1.1

RETRY_BACKOFF_SECONDS = (2, 5, 10)
RATE_LIMIT_BACKOFF_SECONDS = (20, 45, 90)


@dataclass(frozen=True)
class StockFuture:
    """One monthly stock-futures contract from Angel's instrument master."""

    expiry: str
    lot_size: int
    token: str
    nfo_symbol: str = ""
    exchange: str = "NFO"

# Angel One error codes that mean the session must be re-established.
AUTH_ERROR_CODES = {"AG8001", "AG8002", "AG8003", "AB1010", "AB1011", "AB8050", "AB8051"}

# Strikes in the instrument master are quoted in paise.
STRIKE_DIVISOR = 100.0


class Throttle:
    """Spaces out calls to one endpoint to stay inside its rate limit."""

    def __init__(self, min_interval_seconds: float):
        self.min_interval_seconds = min_interval_seconds
        self._last_call = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)
        self._last_call = time.monotonic()


class AngelOneClient:
    """Live LTP, RSI and option-chain OI from Angel One SmartAPI."""

    name = "angelone"

    def __init__(self, rsi_period: int = 14, history_days: int = 120):
        load_dotenv()
        api_key = os.getenv("ANGEL_API_KEY", "").strip()
        client_code = os.getenv("ANGEL_CLIENT_CODE", "").strip()
        pin = os.getenv("ANGEL_PIN", "").strip()
        totp_secret = os.getenv("ANGEL_TOTP_SECRET", "").strip()

        if not all([api_key, client_code, pin, totp_secret]):
            raise CredentialsError(
                "Angel One needs ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PIN and "
                "ANGEL_TOTP_SECRET in .env. Create an app at smartapi.angelone.in."
            )

        self.rsi_period = rsi_period
        self.history_days = history_days
        self.client_code = client_code
        self._api_key = api_key
        self._pin = pin
        self._totp_secret = totp_secret

        self._quote_throttle = Throttle(QUOTE_INTERVAL_SECONDS)
        self._candle_throttle = Throttle(CANDLE_INTERVAL_SECONDS)

        self._equity_tokens: dict[str, str] | None = None
        self._option_rows: dict[str, list[dict]] | None = None
        self._futures_rows: dict[str, list[dict]] | None = None
        self._closes_cache: dict[str, list[tuple[str, float]]] | None = None
        # date, high, low, close — shared with Supertrend (same Angel candles as RSI).
        self._ohlc_cache: dict[str, list[tuple[str, float, float, float]]] | None = None
        self._fut_daily_cache: dict[str, list[tuple[str, float]]] = {}
        self._fut_intraday_cache: dict[str, list[tuple[datetime, float]]] = {}
        self._cache_date: date | None = None

        self.login()

    def login(self) -> None:
        """Open a fresh session. Tokens expire daily, so long runs re-login."""
        self.client = SmartConnect(api_key=self._api_key)
        session = self.client.generateSession(
            self.client_code, self._pin, pyotp.TOTP(self._totp_secret).now()
        )

        if not session or not session.get("status"):
            message = (session or {}).get("message", "unknown error")
            raise CredentialsError(f"Angel One login failed: {message}")

        self._session_date = date.today()

    def _refresh_for_new_day(self) -> None:
        """Drop day-scoped caches and re-login once the date rolls over."""
        today = date.today()
        if self._cache_date == today:
            return

        if self._cache_date is not None:
            print(f"New trading day ({today}); refreshing instruments and session.")
            self._save_closes_cache()
            self._save_ohlc_cache()
            self._equity_tokens = None
            self._option_rows = None
            self._futures_rows = None
            self._closes_cache = None
            self._ohlc_cache = None
            if getattr(self, "_session_date", None) != today:
                self.login()

        self._cache_date = today

    def __enter__(self) -> AngelOneClient:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def close(self) -> None:
        self._save_closes_cache()
        self._save_ohlc_cache()
        try:
            self.client.terminateSession(self.client_code)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Instruments
    # ------------------------------------------------------------------ #

    def _load_instruments(self) -> None:
        self._refresh_for_new_day()
        if self._equity_tokens is not None:
            return

        path = download_cached(SCRIP_MASTER_URL, "angelone_scrip_master.json")
        with path.open(encoding="utf-8") as handle:
            rows = json.load(handle)

        equities: dict[str, str] = {}
        options: dict[str, list[dict]] = {}
        futures: dict[str, list[dict]] = {}

        for row in rows:
            segment = row.get("exch_seg")

            if segment == "NSE" and str(row.get("symbol", "")).endswith("-EQ"):
                equities[str(row["symbol"])[:-3].upper()] = str(row["token"])
            elif segment == "NFO" and row.get("instrumenttype") == "OPTSTK":
                options.setdefault(str(row.get("name", "")).upper(), []).append(row)
            # Paper fills/P&L: NSE stock futures only. Ignore BSE (BFO) copies.
            elif segment == "NFO" and row.get("instrumenttype") == "FUTSTK":
                futures.setdefault(str(row.get("name", "")).upper(), []).append(row)

        self._equity_tokens = equities
        self._option_rows = options
        self._futures_rows = futures

    def fno_symbols(self) -> list[str]:
        """Every NSE stock with listed options, that also has a tradable equity token."""
        self._load_instruments()
        return sorted(
            symbol
            for symbol in (self._option_rows or {})
            if symbol in (self._equity_tokens or {})
        )

    def _oi_expiry_rows(self, symbol: str) -> tuple[str, list[dict]] | None:
        """Current-month monthly option chain (cash OI for the scan)."""
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

        chosen = select_scan_oi_expiry(list(by_expiry), today)
        if chosen is None:
            return None
        return chosen.strftime("%Y-%m-%d"), by_expiry[chosen]

    def lot_size(self, symbol: str) -> int:
        nearest = self._oi_expiry_rows(symbol)
        if not nearest:
            return 0
        return int(float(nearest[1][0].get("lotsize") or 0))

    def futures_contract(
        self, symbol: str, month_index: int = 3, as_of: date | None = None
    ) -> StockFuture | None:
        """Monthly stock future for the chosen month index.

        Picks the NSE (NFO) contract in that month (e.g. month_index=3 in
        August → October). BSE futures are never used.
        """
        self._load_instruments()
        rows = (self._futures_rows or {}).get(symbol.upper())
        if not rows:
            return None

        as_of = as_of or date.today()
        target_year, target_month = target_futures_year_month(as_of, month_index)
        nfo: list[dict] = []

        for row in rows:
            try:
                expiry = datetime.strptime(str(row["expiry"]), "%d%b%Y").date()
            except (KeyError, ValueError):
                continue
            if (expiry.year, expiry.month) != (target_year, target_month):
                continue
            if str(row.get("exch_seg") or "NFO").upper() != "NFO":
                continue
            nfo.append(row)

        if not nfo:
            return None

        def expiry_of(row: dict) -> date:
            return datetime.strptime(str(row["expiry"]), "%d%b%Y").date()

        row = max(nfo, key=expiry_of)
        return StockFuture(
            expiry=expiry_of(row).strftime("%Y-%m-%d"),
            lot_size=int(float(row.get("lotsize") or 0)),
            token=str(row.get("token") or ""),
            nfo_symbol=str(row.get("symbol") or ""),
            exchange="NFO",
        )

    def get_futures_ltps(
        self,
        symbols: list[str],
        month_index: int = 3,
        as_of: date | None = None,
    ) -> dict[str, float]:
        """Last traded price of the configured monthly future, keyed by cash symbol."""
        self._load_instruments()
        by_exchange: dict[str, dict[str, str]] = {}
        for symbol in symbols:
            contract = self.futures_contract(
                symbol, month_index=month_index, as_of=as_of
            )
            if not contract or not contract.token:
                continue
            if contract.exchange != "NFO":
                continue
            by_exchange.setdefault(contract.exchange, {})[contract.token] = symbol.upper()

        prices: dict[str, float] = {}
        for exchange, tokens in by_exchange.items():
            token_list = list(tokens)
            for start in range(0, len(token_list), MAX_TOKENS_PER_REQUEST):
                batch = token_list[start : start + MAX_TOKENS_PER_REQUEST]
                try:
                    response = self._call(
                        self._quote_throttle,
                        "getMarketData",
                        "LTP",
                        {exchange: batch},
                    )
                except Exception as exc:
                    print(f"  {exchange} futures quote batch failed: {exc}")
                    continue
                if not response or not response.get("status"):
                    continue
                for quote in (response.get("data") or {}).get("fetched") or []:
                    symbol = tokens.get(str(quote.get("symbolToken")))
                    price = quote.get("ltp")
                    if symbol and price:
                        prices[symbol] = float(price)
        return prices

    def futures_daily_close(
        self, symbol: str, on: date, month_index: int = 3
    ) -> float | None:
        """3rd-month futures daily close on or before `on` (entry-date restatement)."""
        contract = self.futures_contract(symbol, month_index=month_index, as_of=on)
        if not contract or not contract.token:
            return None
        key = f"{contract.exchange}:{contract.token}"
        series = self._fut_daily_cache.get(key)
        if series is None:
            series = self._fetch_futures_daily(contract)
            self._fut_daily_cache[key] = series
        target = on.isoformat()
        prior = [close for day, close in series if day <= target]
        return prior[-1] if prior else None

    def _fetch_futures_daily(self, contract: StockFuture) -> list[tuple[str, float]]:
        now = datetime.now()
        try:
            response = self._call(
                self._candle_throttle,
                "getCandleData",
                {
                    "exchange": contract.exchange,
                    "symboltoken": contract.token,
                    "interval": "ONE_DAY",
                    "fromdate": (now - timedelta(days=60)).strftime("%Y-%m-%d %H:%M"),
                    "todate": now.strftime("%Y-%m-%d %H:%M"),
                },
            )
        except Exception as exc:
            print(f"  {contract.nfo_symbol}: fut candles unavailable ({exc})")
            return []

        rows: list[tuple[str, float]] = []
        for row in (response or {}).get("data") or []:
            if len(row) < 5:
                continue
            rows.append((str(row[0])[:10], float(row[4])))
        return rows

    def futures_price_at(
        self, symbol: str, when: datetime, month_index: int = 3
    ) -> float | None:
        """3rd-month futures print at `when` (5-min bar in session, else daily close)."""
        minutes = when.hour * 60 + when.minute
        in_session = (9 * 60 + 15) <= minutes <= (15 * 60 + 30)
        if in_session:
            print_px = self._futures_intraday_at(symbol, when, month_index)
            if print_px is not None:
                return print_px
        return self.futures_daily_close(symbol, when.date(), month_index)

    def _futures_intraday_at(
        self, symbol: str, when: datetime, month_index: int
    ) -> float | None:
        contract = self.futures_contract(
            symbol, month_index=month_index, as_of=when.date()
        )
        if not contract or not contract.token:
            return None
        key = f"{contract.exchange}:{contract.token}:{when.date().isoformat()}"
        series = self._fut_intraday_cache.get(key)
        if series is None:
            series = self._fetch_futures_intraday(contract, when.date())
            self._fut_intraday_cache[key] = series
        prior = [close for ts, close in series if ts <= when]
        if prior:
            return prior[-1]
        return series[0][1] if series else None

    def _fetch_futures_intraday(
        self, contract: StockFuture, on: date
    ) -> list[tuple[datetime, float]]:
        try:
            response = self._call(
                self._candle_throttle,
                "getCandleData",
                {
                    "exchange": contract.exchange,
                    "symboltoken": contract.token,
                    "interval": "FIVE_MINUTE",
                    "fromdate": f"{on.isoformat()} 09:15",
                    "todate": f"{on.isoformat()} 15:30",
                },
            )
        except Exception as exc:
            print(f"  {contract.nfo_symbol}: fut 5-min unavailable ({exc})")
            return []

        rows: list[tuple[datetime, float]] = []
        for row in (response or {}).get("data") or []:
            if len(row) < 5:
                continue
            try:
                ts = datetime.strptime(str(row[0]).replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            rows.append((ts, float(row[4])))
        return rows

    def _contracts_near_price(
        self, contracts: list[dict], price: float, band_pct: float
    ) -> list[dict]:
        """Keep option rows whose strike is within `band_pct` of `price`."""
        if price <= 0 or band_pct <= 0:
            return contracts
        kept: list[dict] = []
        for row in contracts:
            strike = float(row.get("strike") or 0) / STRIKE_DIVISOR
            if strike <= 0:
                continue
            if abs(strike - price) / price * 100 <= band_pct:
                kept.append(row)
        return kept or contracts

    # ------------------------------------------------------------------ #
    # Requests
    # ------------------------------------------------------------------ #

    def _call(self, throttle: Throttle, method_name: str, *args):
        """Call an endpoint, retrying past rate limits and expired sessions."""
        last_error: Exception | None = None
        rate_hits = 0
        max_attempts = 1 + len(RETRY_BACKOFF_SECONDS) + len(RATE_LIMIT_BACKOFF_SECONDS)

        for attempt in range(max_attempts):
            throttle.wait()
            try:
                response = getattr(self.client, method_name)(*args)
            except Exception as exc:
                last_error = exc
                text = str(exc).lower()
                if "access rate" in text or "too many" in text:
                    delay = RATE_LIMIT_BACKOFF_SECONDS[
                        min(rate_hits, len(RATE_LIMIT_BACKOFF_SECONDS) - 1)
                    ]
                    rate_hits += 1
                    print(f"  rate limited on {method_name}; sleeping {delay}s")
                    time.sleep(delay)
                    continue
                if attempt >= 2:
                    raise
                time.sleep(RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)])
                continue

            if isinstance(response, dict) and not response.get("status"):
                code = str(response.get("errorcode", ""))
                message = str(response.get("message", ""))

                if code in AUTH_ERROR_CODES:
                    print(f"  session expired ({code}); logging in again")
                    self.login()
                    last_error = RuntimeError(message or code)
                    continue

                if "rate" in message.lower() or code == "AB1021":
                    delay = RATE_LIMIT_BACKOFF_SECONDS[
                        min(rate_hits, len(RATE_LIMIT_BACKOFF_SECONDS) - 1)
                    ]
                    rate_hits += 1
                    print(f"  rate limited on {method_name}; sleeping {delay}s")
                    time.sleep(delay)
                    last_error = RuntimeError(message or code)
                    continue

                # Non-rate API error — return it for the caller to handle.
                return response

            return response

        if last_error:
            raise last_error
        return None

    def get_ltps(self, symbols: list[str]) -> dict[str, float]:
        """Fetch last traded prices for many symbols, 50 per request."""
        self._load_instruments()
        tokens = {
            token: symbol
            for symbol in symbols
            if (token := (self._equity_tokens or {}).get(symbol.upper()))
        }

        prices: dict[str, float] = {}
        token_list = list(tokens)

        for start in range(0, len(token_list), MAX_TOKENS_PER_REQUEST):
            batch = token_list[start : start + MAX_TOKENS_PER_REQUEST]
            try:
                response = self._call(
                    self._quote_throttle, "getMarketData", "LTP", {"NSE": batch}
                )
            except Exception as exc:
                print(f"  quote batch failed: {exc}")
                continue

            if not response or not response.get("status"):
                continue

            for quote in (response.get("data") or {}).get("fetched") or []:
                symbol = tokens.get(str(quote.get("symbolToken")))
                price = quote.get("ltp")
                if symbol and price:
                    prices[symbol] = float(price)

        return prices

    # ------------------------------------------------------------------ #
    # Candles and RSI
    # ------------------------------------------------------------------ #

    def _closes_cache_path(self) -> Path:
        return CACHE_DIR / f"daily_closes_{date.today():%Y-%m-%d}.json"

    def _ohlc_cache_path(self) -> Path:
        return CACHE_DIR / f"daily_ohlc_{date.today():%Y-%m-%d}.json"

    def _load_closes_cache(self) -> dict[str, list[tuple[str, float]]]:
        self._refresh_for_new_day()
        if self._closes_cache is not None:
            return self._closes_cache

        path = self._closes_cache_path()
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            self._closes_cache = {k: [(d, float(c)) for d, c in v] for k, v in raw.items()}
        else:
            self._closes_cache = {}
        return self._closes_cache

    def _load_ohlc_cache(self) -> dict[str, list[tuple[str, float, float, float]]]:
        self._refresh_for_new_day()
        if self._ohlc_cache is not None:
            return self._ohlc_cache

        path = self._ohlc_cache_path()
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            self._ohlc_cache = {
                k: [(d, float(h), float(l), float(c)) for d, h, l, c in v]
                for k, v in raw.items()
            }
        else:
            self._ohlc_cache = {}
        return self._ohlc_cache

    def _save_closes_cache(self) -> None:
        if not self._closes_cache:
            return
        CACHE_DIR.mkdir(exist_ok=True)
        self._closes_cache_path().write_text(
            json.dumps(self._closes_cache), encoding="utf-8"
        )
        # Keep a committed seed so GitHub runners start warm and skip candle API.
        seed = Path("data") / "daily_closes_seed.json"
        seed.parent.mkdir(parents=True, exist_ok=True)
        seed.write_text(json.dumps(self._closes_cache), encoding="utf-8")

    def _save_ohlc_cache(self) -> None:
        if not self._ohlc_cache:
            return
        CACHE_DIR.mkdir(exist_ok=True)
        self._ohlc_cache_path().write_text(
            json.dumps(self._ohlc_cache), encoding="utf-8"
        )

    def seed_closes_cache_from_repo(self) -> int:
        """Load data/daily_closes_seed.json into today's cache when cold."""
        seed = Path("data") / "daily_closes_seed.json"
        today_path = self._closes_cache_path()
        if today_path.exists():
            return len(self._load_closes_cache())
        if not seed.exists():
            return 0

        CACHE_DIR.mkdir(exist_ok=True)
        today_path.write_text(seed.read_text(encoding="utf-8"), encoding="utf-8")
        self._closes_cache = None
        loaded = self._load_closes_cache()
        print(f"  Seeded daily-close cache with {len(loaded)} symbols from repo.")
        return len(loaded)

    def _ingest_candles(
        self, symbol: str, candles: list
    ) -> list[tuple[str, float, float, float]]:
        """Parse Angel candle rows into OHLC and mirror closes for RSI."""
        ohlc: list[tuple[str, float, float, float]] = []
        for row in candles:
            if len(row) < 5:
                continue
            # Angel format: [timestamp, open, high, low, close, volume]
            ohlc.append(
                (str(row[0])[:10], float(row[2]), float(row[3]), float(row[4]))
            )

        ohlc_cache = self._load_ohlc_cache()
        closes_cache = self._load_closes_cache()
        ohlc_cache[symbol] = ohlc
        closes_cache[symbol] = [(d, c) for d, _h, _l, c in ohlc]
        return ohlc

    def _request_candles(self, symbol: str) -> list:
        self._load_instruments()
        token = (self._equity_tokens or {}).get(symbol)
        if not token:
            return []

        now = datetime.now()
        try:
            response = self._call(
                self._candle_throttle,
                "getCandleData",
                {
                    "exchange": "NSE",
                    "symboltoken": token,
                    "interval": "ONE_DAY",
                    "fromdate": (now - timedelta(days=self.history_days)).strftime(
                        "%Y-%m-%d %H:%M"
                    ),
                    "todate": now.strftime("%Y-%m-%d %H:%M"),
                },
            )
        except Exception as exc:
            print(f"  {symbol}: candles unavailable ({exc})")
            return []

        return (response or {}).get("data") or []

    def daily_ohlc(self, symbol: str) -> list[tuple[str, float, float, float]]:
        """Daily (date, high, low, close) from Angel One, cached once per day."""
        symbol = symbol.upper()
        cache = self._load_ohlc_cache()
        if symbol in cache:
            return cache[symbol]

        candles = self._request_candles(symbol)
        if not candles:
            cache[symbol] = []
            return []

        series = self._ingest_candles(symbol, candles)
        if len(cache) % 10 == 0:
            self._save_ohlc_cache()
            self._save_closes_cache()
        return series

    def daily_closes(self, symbol: str) -> list[tuple[str, float]]:
        """Daily (date, close) pairs, fetched once per day and cached on disk."""
        symbol = symbol.upper()
        ohlc_cache = self._load_ohlc_cache()
        if symbol in ohlc_cache:
            return [(d, c) for d, _h, _l, c in ohlc_cache[symbol]]

        cache = self._load_closes_cache()
        if symbol in cache:
            return cache[symbol]

        candles = self._request_candles(symbol)
        if not candles:
            return []

        ohlc = self._ingest_candles(symbol, candles)
        # Flush often so a timed-out GitHub job still leaves a usable cache.
        if len(cache) % 10 == 0:
            self._save_closes_cache()
            self._save_ohlc_cache()
        return [(d, c) for d, _h, _l, c in ohlc]

    def get_rsi(self, symbol: str, ltp: float) -> float | None:
        """RSI from cached daily closes, with today's close set to the live price."""
        series = self.daily_closes(symbol)
        if not series:
            return None

        closes = [close for _, close in series]
        today = f"{date.today():%Y-%m-%d}"

        # Only overwrite the final close when it is today's still-forming candle,
        # otherwise the live price would replace the previous session's close.
        if series[-1][0] == today:
            closes[-1] = ltp
        else:
            closes.append(ltp)

        return calculate_rsi(pd.Series(closes, dtype=float), period=self.rsi_period)

    # ------------------------------------------------------------------ #
    # Open interest
    # ------------------------------------------------------------------ #

    def _fetch_open_interest(self, tokens: list[str]) -> dict[str, int]:
        open_interest: dict[str, int] = {}

        for start in range(0, len(tokens), MAX_TOKENS_PER_REQUEST):
            batch = tokens[start : start + MAX_TOKENS_PER_REQUEST]
            response = self._call(
                self._quote_throttle, "getMarketData", "FULL", {"NFO": batch}
            )

            if not response or not response.get("status"):
                continue

            for quote in (response.get("data") or {}).get("fetched") or []:
                open_interest[str(quote.get("symbolToken"))] = int(
                    quote.get("opnInterest") or 0
                )

        return open_interest

    def get_oi_snapshot(self, symbol: str, ltp: float = 0.0) -> OISnapshot | None:
        symbol = symbol.upper()
        nearest = self._oi_expiry_rows(symbol)
        if not nearest:
            print(f"  {symbol}: no stock options listed")
            return None

        expiry, contracts = nearest
        contracts = self._contracts_near_price(contracts, ltp, OI_SNAPSHOT_BAND_PCT)
        by_token = {str(row["token"]): row for row in contracts}

        try:
            open_interest = self._fetch_open_interest(list(by_token))
        except Exception as exc:
            print(f"  {symbol}: option quotes failed ({exc})")
            return None

        if not open_interest:
            print(f"  {symbol}: no open interest returned")
            return None

        legs_by_strike: dict[float, dict[str, tuple[int, str]]] = {}

        for token, oi in open_interest.items():
            row = by_token.get(token)
            if not row:
                continue

            strike = float(row.get("strike") or 0) / STRIKE_DIVISOR
            if strike <= 0:
                continue

            symbol_name = str(row.get("symbol", ""))
            if symbol_name.endswith("CE"):
                legs_by_strike.setdefault(strike, {})["CE"] = (oi, token)
            elif symbol_name.endswith("PE"):
                legs_by_strike.setdefault(strike, {})["PE"] = (oi, token)

        if not legs_by_strike:
            return None

        call_wall, put_wall = select_active_oi_walls(legs_by_strike, ltp)
        if call_wall is None and put_wall is None:
            return None

        call_strike, call_oi, call_token = call_wall or (0.0, 0, "")
        put_strike, put_oi, put_token = put_wall or (0.0, 0, "")

        return OISnapshot(
            symbol=symbol,
            ltp=ltp,
            max_call_oi_strike=call_strike,
            max_call_oi=call_oi,
            max_put_oi_strike=put_strike,
            max_put_oi=put_oi,
            expiry=expiry,
            lot_size=int(float(contracts[0].get("lotsize") or 0)),
            max_call_token=call_token,
            max_put_token=put_token,
            legs_by_strike=legs_by_strike,
        )

    def get_oi_at_price(self, symbol: str, target_price: float, ltp: float = 0.0) -> OISnapshot | None:
        """OI for the call and put whose strike is nearest to `target_price`.

        Used by the Supertrend strategy: target_price is the Supertrend level.
        The returned snapshot stores that strike on both max_call/max_put fields
        so add_oi_changes() and the existing alert plumbing still work.
        """
        symbol = symbol.upper()
        if target_price <= 0:
            return None

        nearest = self._oi_expiry_rows(symbol)
        if not nearest:
            return None

        expiry, contracts = nearest
        contracts = self._contracts_near_price(contracts, target_price, OI_STRIKE_BAND_PCT)
        by_token = {str(row["token"]): row for row in contracts}

        try:
            open_interest = self._fetch_open_interest(list(by_token))
        except Exception as exc:
            print(f"  {symbol}: option quotes failed ({exc})")
            return None

        best_call = best_put = None  # (distance, strike, oi, token)

        for token, oi in open_interest.items():
            row = by_token.get(token)
            if not row:
                continue
            strike = float(row.get("strike") or 0) / STRIKE_DIVISOR
            if strike <= 0:
                continue
            distance = abs(strike - target_price)
            symbol_name = str(row.get("symbol", ""))
            if symbol_name.endswith("CE"):
                if best_call is None or distance < best_call[0]:
                    best_call = (distance, strike, oi, token)
            elif symbol_name.endswith("PE"):
                if best_put is None or distance < best_put[0]:
                    best_put = (distance, strike, oi, token)

        if best_call is None or best_put is None:
            return None

        # Prefer a shared strike when CE/PE both exist at the same level near ST.
        call_strike, put_strike = best_call[1], best_put[1]
        if call_strike != put_strike:
            # Re-pick the put/call at the strike closest to ST that has both legs
            # when possible; otherwise keep each leg's nearest strike.
            strikes: dict[float, dict[str, tuple[int, str]]] = {}
            for token, oi in open_interest.items():
                row = by_token.get(token)
                if not row:
                    continue
                strike = float(row.get("strike") or 0) / STRIKE_DIVISOR
                if strike <= 0:
                    continue
                leg = "CE" if str(row.get("symbol", "")).endswith("CE") else (
                    "PE" if str(row.get("symbol", "")).endswith("PE") else ""
                )
                if not leg:
                    continue
                strikes.setdefault(strike, {})[leg] = (oi, token)

            both = [
                (abs(strike - target_price), strike, legs)
                for strike, legs in strikes.items()
                if "CE" in legs and "PE" in legs
            ]
            if both:
                both.sort(key=lambda item: item[0])
                _, strike, legs = both[0]
                best_call = (0.0, strike, legs["CE"][0], legs["CE"][1])
                best_put = (0.0, strike, legs["PE"][0], legs["PE"][1])

        return OISnapshot(
            symbol=symbol,
            ltp=ltp,
            max_call_oi_strike=best_call[1],
            max_call_oi=best_call[2],
            max_put_oi_strike=best_put[1],
            max_put_oi=best_put[2],
            expiry=expiry,
            lot_size=int(float(contracts[0].get("lotsize") or 0)),
            max_call_token=best_call[3],
            max_put_token=best_put[3],
        )

    def _previous_session_oi(self, token: str) -> int | None:
        """Open interest at the previous session's close for one contract."""
        now = datetime.now()
        try:
            response = self._call(
                self._candle_throttle,
                "getOIData",
                {
                    "exchange": "NFO",
                    "symboltoken": token,
                    "interval": "ONE_DAY",
                    "fromdate": (now - timedelta(days=10)).strftime("%Y-%m-%d %H:%M"),
                    "todate": now.strftime("%Y-%m-%d %H:%M"),
                },
            )
        except Exception as exc:
            print(f"  OI history unavailable for token {token} ({exc})")
            return None

        today = f"{date.today():%Y-%m-%d}"
        prior = [
            int(float(row["oi"]))
            for row in (response or {}).get("data") or []
            if str(row.get("time", ""))[:10] < today
        ]
        return prior[-1] if prior else None

    def add_oi_changes(self, snapshot: OISnapshot) -> OISnapshot:
        """Fill in CE/PE OI change at the tokens already on the snapshot.

        For RSI signals, call align_snapshot_to_reference_strike() first so both
        tokens point at the same reference strike (max Call or max Put OI).
        Supertrend snapshots already share one strike from get_oi_at_price().
        """
        if snapshot.max_call_token:
            previous = self._previous_session_oi(snapshot.max_call_token)
            if previous is not None:
                snapshot.call_oi_change = snapshot.max_call_oi - previous

        if snapshot.max_put_token:
            previous = self._previous_session_oi(snapshot.max_put_token)
            if previous is not None:
                snapshot.put_oi_change = snapshot.max_put_oi - previous

        return snapshot
