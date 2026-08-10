from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import yfinance as yf


@dataclass
class PriceSnapshot:
    symbol: str
    ltp: float
    rsi: float | None


class PriceClient:
    def __init__(self, rsi_period: int = 14, history_days: int = 60):
        self.rsi_period = rsi_period
        self.history_days = history_days

    def _yahoo_symbol(self, nse_symbol: str) -> str:
        return f"{nse_symbol.upper()}.NS"

    def get_snapshot(self, symbol: str) -> PriceSnapshot | None:
        from src.indicators import calculate_rsi

        ticker = yf.Ticker(self._yahoo_symbol(symbol))
        history = ticker.history(period=f"{self.history_days}d", interval="1d", auto_adjust=True)

        if history.empty:
            return None

        closes = history["Close"]
        ltp = float(closes.iloc[-1])
        rsi = calculate_rsi(closes, period=self.rsi_period)

        return PriceSnapshot(symbol=symbol.upper(), ltp=ltp, rsi=rsi)
