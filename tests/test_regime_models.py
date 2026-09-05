import pandas as pd

import pytest

from strategy_backtester.core.engine import BacktestEngine
from strategy_backtester.core.regime_models import MultiFactorRegimeModelV1


def _daily_df(closes):
    idx = pd.date_range("2020-01-01", periods=len(closes), freq="D")
    close = pd.Series(closes, index=idx, dtype="float64")
    open_ = close * 0.999
    high = close + 0.5
    low = close - 0.5
    volume = pd.Series(1_000_000, index=idx, dtype="float64")
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


def test_multifactor_intraday_shift():
    data_map = {
        "SPY": _daily_df([100.0, 110.0, 90.0]),
        "VIXY": _daily_df([10.0, 10.5, 11.0]),
        "HYG": _daily_df([80.0, 80.5, 80.0]),
        "TLT": _daily_df([100.0, 99.5, 100.5]),
    }
    cfg = {
        "symbols": {"market": "SPY", "vol": "VIXY", "credit": "HYG", "rates": "TLT"},
        "params": {
            "trend": {"ma_fast": 1, "ma_slow": 2, "slope_window": 1},
            "vol": {"rv_lookback": 1, "z_lookback": 1},
            "risk_on_prob": {"weights": {"trend": 1.0, "vol": 0.0, "credit": 0.0, "rates": 0.0}},
            "intraday_mode": "daily_ffill_shift1",
        },
        "missing_data_policy": {"min_components": 1},
    }
    model = MultiFactorRegimeModelV1()
    daily_index = data_map["SPY"].index
    daily_result = model.build(data_map, daily_index, interval="1d", config=cfg)
    intraday_index = pd.date_range("2020-01-01 10:00", periods=6, freq="12h")
    intraday_result = model.build(data_map, intraday_index, interval="5m", config=cfg)

    day2_prob = daily_result.frame.loc[pd.Timestamp("2020-01-02"), "risk_on_prob"]
    shifted_prob = intraday_result.frame.loc[pd.Timestamp("2020-01-03 10:00"), "risk_on_prob"]
    assert day2_prob == pytest.approx(shifted_prob)


def test_missing_market_symbol_raises():
    df = _daily_df([100.0, 101.0, 102.0])
    engine = BacktestEngine(
        start_date="2020-01-01",
        end_date="2020-01-03",
        universe=["AAA"],
        strategies=[],
        use_vectorized=True,
        regime={
            "model": "multi_factor_v1",
            "symbols": {"market": "SPY", "vol": "VIXY", "credit": "HYG", "rates": "TLT"},
        },
    )
    engine.data_provider = type(
        "DP",
        (),
        {"fetch_batch_data": lambda *_args, **_kwargs: {"AAA": df}},
    )()
    engine.screener.data_provider = engine.data_provider
    with pytest.raises(ValueError):
        engine.run()
