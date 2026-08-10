from __future__ import annotations

from src.config import AppConfig
from src.data.base import MarketDataProvider, ProviderCredentialsError, ProviderError

DHAN_ALIASES = {"dhan", "dhanhq"}
ANGEL_ALIASES = {"angelone", "angel_one", "angel", "smartapi"}


def create_provider(config: AppConfig) -> MarketDataProvider:
    """Build the live market data provider named in config.yaml."""
    provider = config.data.provider.strip().lower()

    if provider in DHAN_ALIASES:
        try:
            from src.data.dhan_client import DhanProvider
        except ImportError as exc:
            raise ProviderError(
                "The Dhan SDK is not installed. Run 'pip install dhanhq'. "
                "Note that Dhan's market data needs the paid DhanHQ Data API plan; "
                "Angel One serves the same data for free."
            ) from exc

        return DhanProvider(
            rsi_period=config.rsi.period,
            history_days=config.data.history_days,
            option_chain_delay_seconds=config.data.option_chain_delay_seconds,
        )

    if provider in ANGEL_ALIASES:
        from src.data.angelone_client import AngelOneProvider

        return AngelOneProvider(
            rsi_period=config.rsi.period,
            history_days=config.data.history_days,
        )

    raise ProviderError(
        f"Unknown data provider '{config.data.provider}'. Use 'dhan' or 'angelone'."
    )


__all__ = ["create_provider", "MarketDataProvider", "ProviderCredentialsError", "ProviderError"]
