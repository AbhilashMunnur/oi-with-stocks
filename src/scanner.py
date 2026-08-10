from __future__ import annotations

from datetime import datetime, time as dt_time

from src.config import AppConfig
from src.data.nse_client import NSEClient
from src.data.price_client import PriceClient
from src.notifications.notifier import Notifier
from src.oi_analyzer import ScanAlert, evaluate_stock


class OIRsiScanner:
    def __init__(self, config: AppConfig):
        self.config = config
        self.price_client = PriceClient(rsi_period=config.rsi.period)
        self.nse_client = NSEClient()
        self.notifier = Notifier(config.notifications)
        self._owns_nse_client = True

    def close(self) -> None:
        if self._owns_nse_client:
            self.nse_client.close()

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
        current = now.time()
        return start <= current <= end

    def scan_symbol(self, symbol: str) -> ScanAlert | None:
        price = self.price_client.get_snapshot(symbol)
        if not price:
            print(f"  {symbol}: skipped (price data unavailable)")
            return None

        oi = self.nse_client.get_oi_snapshot(symbol)
        if not oi:
            print(f"  {symbol}: skipped (option chain unavailable)")
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

        rsi_text = f"{price.rsi:.1f}" if price.rsi is not None else "n/a"
        print(
            f"  {symbol}: no signal | RSI={rsi_text} | LTP=₹{oi.ltp:.2f} | "
            f"max Call OI @ ₹{oi.max_call_oi_strike:.0f} | max Put OI @ ₹{oi.max_put_oi_strike:.0f}"
        )
        return None

    def run_once(self) -> list[ScanAlert]:
        print(f"\nScan started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        alerts: list[ScanAlert] = []

        for symbol in self.config.watchlist:
            alert = self.scan_symbol(symbol)
            if alert:
                alerts.append(alert)

        if alerts:
            self.notifier.notify(alerts)
        else:
            print("No alerts this scan.")

        return alerts
