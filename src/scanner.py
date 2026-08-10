from __future__ import annotations

from datetime import datetime, time as dt_time

from src.config import AppConfig
from src.data.angelone_client import AngelOneClient
from src.data.models import PriceSnapshot
from src.notifications.notifier import Notifier
from src.oi_analyzer import ScanAlert, evaluate_stock, matched_signal
from src.paper_trading import PaperBook


class OIRsiScanner:
    def __init__(self, config: AppConfig):
        self.config = config
        self.client = AngelOneClient(
            rsi_period=config.rsi.period,
            history_days=config.data.history_days,
        )
        self.notifier = Notifier(config.notifications)
        self.book = PaperBook(config.paper_trading) if config.paper_trading.enabled else None

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
        if now.weekday() >= 5:
            return False

        start = self._parse_hhmm(self.config.schedule.market_start)
        end = self._parse_hhmm(self.config.schedule.market_end)
        return start <= now.time() <= end

    def symbols(self) -> list[str]:
        """The configured watchlist, or every F&O stock when set to 'all'."""
        watchlist = self.config.watchlist
        if isinstance(watchlist, str) and watchlist.strip().lower() == "all":
            return self.client.fno_symbols()
        return [symbol.upper() for symbol in watchlist]

    def _rsi_candidates(
        self, symbols: list[str], prices: dict[str, float]
    ) -> list[PriceSnapshot]:
        """Stocks whose RSI is stretched enough to be worth an option-chain lookup."""
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

        thresholds = dict(
            rsi_call_threshold=self.config.rsi.call_threshold,
            rsi_put_threshold=self.config.rsi.put_threshold,
            proximity_pct=self.config.oi.proximity_pct,
        )

        # OI history costs two extra requests, so only price it in once the
        # stock has actually qualified.
        if matched_signal(price, oi, **thresholds) is not None:
            self.client.add_oi_changes(oi)
            return evaluate_stock(price=price, oi=oi, **thresholds)

        print(
            f"  {price.symbol}: RSI {price.rsi:.1f} qualifies but price ₹{price.ltp:.2f} "
            f"is not near max Call OI ₹{oi.max_call_oi_strike:.0f} "
            f"or max Put OI ₹{oi.max_put_oi_strike:.0f}"
        )
        return None

    def _run_paper_trading(self, alerts: list[ScanAlert], prices: dict[str, float]) -> None:
        if not self.book:
            return

        # Exits are settled before entries so freed margin can fund new trades.
        events = self.book.update(prices)
        events += self.book.open_from_alerts(alerts)
        self.book.save()

        if events:
            print("\nPaper trading")
            for event in events:
                pnl = f"  P&L ₹{event.pnl:+,.0f}" if event.pnl else ""
                print(f"  [{event.kind}] {event.symbol}: {event.detail}{pnl}")

        print()
        print(self.book.summary(prices))

        if events and self.notifier.telegram_ready:
            lines = ["Paper trading update", ""]
            for event in events:
                pnl = f"  (₹{event.pnl:+,.0f})" if event.pnl else ""
                lines.append(f"• [{event.kind}] {event.symbol}: {event.detail}{pnl}")
            lines += ["", self.book.summary(prices)]
            self.notifier.send_message("\n".join(lines))

    def run_once(self) -> list[ScanAlert]:
        started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        symbols = self.symbols()
        print(f"\nScan started at {started} over {len(symbols)} stocks (live Angel One data)")

        prices = self.client.get_ltps(symbols)
        candidates = self._rsi_candidates(symbols, prices)
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

        self._run_paper_trading(alerts, prices)
        return alerts
