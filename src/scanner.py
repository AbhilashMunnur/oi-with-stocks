from __future__ import annotations

from datetime import datetime, time as dt_time

from src.config import AppConfig
from src.data.angelone_client import AngelOneClient
from src.data.models import PriceSnapshot
from src.notifications.notifier import Notifier
from src.oi_analyzer import ScanAlert, evaluate_stock


class OIRsiScanner:
    def __init__(self, config: AppConfig):
        self.config = config
        self.client = AngelOneClient(
            rsi_period=config.rsi.period,
            history_days=config.data.history_days,
        )
        self.notifier = Notifier(config.notifications)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> OIRsiScanner:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def _parse_hhmm(self, value: str) -> dt_time:
        hour, minute = map(int, value.split(":"))
        return dt_time(hour=hour, minute=minute)

    def is_market_hours(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        start = self._parse_hhmm(self.config.schedule.market_start)
        end = self._parse_hhmm(self.config.schedule.market_end)
        return start <= now.time() <= end

    def symbols(self) -> list[str]:
        """The configured watchlist, or every F&O stock when set to 'all'."""
        watchlist = self.config.watchlist
        if isinstance(watchlist, str) and watchlist.strip().lower() == "all":
            return self.client.fno_symbols()
        return [symbol.upper() for symbol in watchlist]

    def _rsi_candidates(self, symbols: list[str]) -> list[PriceSnapshot]:
        """Stocks whose RSI is stretched enough to be worth an option-chain lookup."""
        prices = self.client.get_ltps(symbols)
        missing = [s for s in symbols if s not in prices]
        if missing:
            print(f"  no live price for {len(missing)} symbol(s): {', '.join(missing[:5])}")

        candidates: list[PriceSnapshot] = []
        for index, symbol in enumerate(symbols, 1):
            ltp = prices.get(symbol)
            if not ltp:
                continue

            rsi = self.client.get_rsi(symbol, ltp)
            if rsi is None:
                continue

            if rsi >= self.config.rsi.call_threshold or rsi <= self.config.rsi.put_threshold:
                candidates.append(PriceSnapshot(symbol=symbol, ltp=ltp, rsi=rsi))

            if index % 25 == 0:
                print(f"  screened {index}/{len(symbols)} symbols...")

        return candidates

    def _check_candidate(self, price: PriceSnapshot) -> ScanAlert | None:
        oi = self.client.get_oi_snapshot(price.symbol, ltp=price.ltp)
        if not oi:
            return None

        alert = evaluate_stock(
            price=price,
            oi=oi,
            rsi_call_threshold=self.config.rsi.call_threshold,
            rsi_put_threshold=self.config.rsi.put_threshold,
            proximity_pct=self.config.oi.proximity_pct,
        )

        if alert:
            return alert

        print(
            f"  {price.symbol}: RSI {price.rsi:.1f} qualifies but price ₹{price.ltp:.2f} "
            f"is not near max Call OI ₹{oi.max_call_oi_strike:.0f} "
            f"or max Put OI ₹{oi.max_put_oi_strike:.0f}"
        )
        return None

    def run_once(self) -> list[ScanAlert]:
        started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        symbols = self.symbols()
        print(f"\nScan started at {started} over {len(symbols)} stocks (live Angel One data)")

        candidates = self._rsi_candidates(symbols)
        print(
            f"\n{len(candidates)} stock(s) passed the RSI filter "
            f"(>= {self.config.rsi.call_threshold} or <= {self.config.rsi.put_threshold})"
        )

        alerts: list[ScanAlert] = []
        for price in candidates:
            alert = self._check_candidate(price)
            if alert:
                alerts.append(alert)

        if alerts:
            self.notifier.notify(alerts)
        else:
            print("\nNo alerts this scan.")

        return alerts
