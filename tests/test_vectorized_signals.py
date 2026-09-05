import numpy as np
import pandas as pd
import pytest

from stock_screener.strategies.momentum import (
    RsiOversold,
    MacdTurnaround,
    VptBreakout,
    BearishRsiDivergence,
)
from stock_screener.strategies.bearish import RsiOverbought, MacdBearishCross, Breakdown20
from stock_screener.strategies.trend import GoldenCross, SupertrendReversal, PullbackToEma
from stock_screener.strategies.volatility import BollingerSqueeze, Nr4Nr7Intraday, Nr4Nr7Daily
from stock_screener.strategies.pattern import BullishEngulfing, VolumeSpike
from stock_screener.strategies.gaps import (
    GapUpContinuation,
    GapDownContinuation,
    GapUpFade,
    GapDownFade,
)


def make_df(rows: int = 260) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    close = pd.Series(100 + rng.standard_normal(rows).cumsum())
    open_ = close.shift(1).fillna(close) + rng.normal(0, 0.5, rows)
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.5
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.5
    volume = pd.Series(rng.integers(1_000_000, 2_000_000, size=rows), dtype="float")
    index = pd.date_range("2020-01-01", periods=rows, freq="D")
    return pd.DataFrame(
        {
            "Open": open_.values,
            "High": high.values,
            "Low": low.values,
            "Close": close.values,
            "Volume": volume.values,
        },
        index=index,
    )


def slow_signal(strategy, df: pd.DataFrame) -> pd.Series:
    signals = []
    for i in range(len(df)):
        is_match, _ = strategy.check(df.iloc[: i + 1])
        signals.append(bool(is_match))
    return pd.Series(signals, index=df.index)


@pytest.mark.parametrize(
    "strategy_cls",
    [
        RsiOversold,
        MacdTurnaround,
        VptBreakout,
        BearishRsiDivergence,
        RsiOverbought,
        MacdBearishCross,
        Breakdown20,
        GoldenCross,
        SupertrendReversal,
        PullbackToEma,
        BollingerSqueeze,
        Nr4Nr7Intraday,
        Nr4Nr7Daily,
        BullishEngulfing,
        VolumeSpike,
        GapUpContinuation,
        GapDownContinuation,
        GapUpFade,
        GapDownFade,
    ],
)
def test_vectorized_signal_matches_check(strategy_cls):
    df = make_df()
    strategy = strategy_cls()
    vector = strategy.signal(df)
    slow = slow_signal(strategy, df)
    pd.testing.assert_series_equal(vector.astype(bool), slow.astype(bool))
