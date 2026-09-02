import pandas as pd
import pytest

from src.indicators import calculate_smma, calculate_smma_series


def test_smma_seeds_with_sma_then_smooths():
    closes = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    series = calculate_smma_series(closes, period=3)

    assert series.iloc[2] == pytest.approx(2.0)
    assert series.iloc[3] == pytest.approx((2.0 * 2 + 4.0) / 3)
    assert series.iloc[4] == pytest.approx((series.iloc[3] * 2 + 5.0) / 3)
    assert calculate_smma(closes, 3) == pytest.approx(series.iloc[4])


def test_flat_closes_keep_smma_flat():
    closes = pd.Series([10.0] * 30)
    assert calculate_smma(closes, 21) == pytest.approx(10.0)


def test_smma_needs_a_full_window():
    assert calculate_smma(pd.Series([1.0, 2.0]), period=21) is None
