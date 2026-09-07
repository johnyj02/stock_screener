import numpy as np
import pandas as pd
import pytest

from stock_screener.core.indicators import _wavetrend_series, add_common_indicators
from stock_screener.strategies.momentum import WaveTrendCross
from strategy_backtester.core.engine import BacktestEngine
from strategy_backtester.core.regime import RegimeState


def _bars(count=320):
    x = np.arange(count, dtype=float)
    # A moderate cycle reaches the configured +/-50 signal thresholds.
    close = 100 + 20 * np.sin(x / 10)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": 1_000.0,
        }
    )


def test_wavetrend_adds_lines_and_crossover_signals():
    df = add_common_indicators(_bars())
    required = {"WT1_10_21_4", "WT2_10_21_4", "WT_BUY_10_21_4", "WT_SELL_10_21_4"}
    assert required <= set(df.columns)

    buy = df["WT_BUY_10_21_4"].dropna()
    sell = df["WT_SELL_10_21_4"].dropna()
    assert not buy.empty and (buy == -70).all()
    assert not sell.empty and (sell == 70).all()

    wt1, wt2 = df["WT1_10_21_4"], df["WT2_10_21_4"]
    for index in buy.index:
        assert wt1.loc[index] > wt2.loc[index]
        assert wt1.loc[index] < -50
        assert wt1.shift(1).loc[index] <= wt2.shift(1).loc[index]
    for index in sell.index:
        assert wt1.loc[index] < wt2.loc[index]
        assert wt1.loc[index] > 50
        assert wt1.shift(1).loc[index] >= wt2.shift(1).loc[index]


def test_wavetrend_handles_flat_prices_without_infinite_values():
    df = _bars(80)
    df[["Open", "High", "Low", "Close"]] = 100.0
    wt1, wt2, buy, sell = _wavetrend_series(df)
    assert np.isfinite(wt1.dropna()).all()
    assert np.isfinite(wt2.dropna()).all()
    assert buy.dropna().empty
    assert sell.dropna().empty


def test_wavetrend_matches_fixed_values_and_signal_bars():
    x = np.arange(160, dtype=float)
    close = 100 + 20 * np.sin(x / 10)
    df = pd.DataFrame(
        {
            "Open": close - 0.25,
            "High": close + 1.0,
            "Low": close - 1.5,
            "Close": close,
            "Volume": 1_000.0 + x,
        }
    )
    wt1, wt2, buy, sell = _wavetrend_series(df)

    assert wt1.iloc[42] == pytest.approx(-64.3307390891)
    assert wt2.iloc[42] == pytest.approx(-64.4053978313)
    assert list(buy.dropna().index) == [42, 106]
    assert list(sell.dropna().index) == [75, 138]


def test_wavetrend_strategy_uses_only_indicator_signals():
    df = add_common_indicators(_bars(160))
    strategy = WaveTrendCross()
    pd.testing.assert_series_equal(strategy.signal(df), df["WT_BUY_10_21_4"].notna())
    pd.testing.assert_series_equal(strategy.exit_signal(df), df["WT_SELL_10_21_4"].notna())
    assert not strategy.use_stop_loss
    assert strategy.use_fallback_stop
    assert not strategy.use_take_profit


def test_wavetrend_strategy_can_be_sized_without_a_stop_exit():
    df = _bars(40)
    strategy = WaveTrendCross()
    engine = BacktestEngine(
        start_date="2020-01-01",
        universe=["AAA"],
        strategies=[strategy],
        min_avg_dollar_vol=0.0,
        hedge_symbol="",
    )
    amount, reason = engine._available_trade_amount(
        date=df.index[20],
        data_source={"AAA": df},
        ticker="AAA",
        entry_price=float(df["Close"].iloc[20]),
        stop_loss=None,
        regime_state=RegimeState(True, "risk_on", 0.0, 10, 0.01, 0.05),
        side=1,
        strategy=strategy,
        return_reason=True,
    )
    assert amount > 0
    assert reason is None
