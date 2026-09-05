import pandas as pd
import numpy as np

from strategy_backtester.core.engine import BacktestEngine
from strategy_backtester.core.regime import build_regime_series, regime_state


def _trend_df(rows: int = 260, start_price: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=rows, freq="D")
    close = pd.Series(np.linspace(start_price, start_price + 10, rows), index=idx)
    open_ = close * 0.999
    high = close + 0.5
    low = close - 0.5
    volume = pd.Series(1_000_000, index=idx, dtype="float")
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


def test_regime_state_basic():
    df = _trend_df()
    regime_df = build_regime_series(df, df.index)
    state_mid = regime_state(df.index[30], regime_df)

    # After enough history, slope positive and price > sma200 should be risk_on
    assert state_mid.severity in {"risk_on", "mild"}
    assert state_mid.hedge_pct >= 0.0


def test_hedge_added_when_needed():
    price_df = _trend_df()
    data = {"AAA": price_df, "MES=F": price_df}
    engine = BacktestEngine(
        start_date="2020-01-01",
        end_date="2020-02-10",
        universe=["AAA"],
        strategies=[],
        use_vectorized=True,
        hedge_symbol="MES=F",
        regime_symbol="AAA",  # reuse AAA to keep data_map small
    )
    engine.data_provider = type("DP", (), {"fetch_batch_data": lambda *_args, **_kwargs: data})()
    engine.screener.data_provider = engine.data_provider
    # seed a long equity holding so hedge sizing has something to offset
    engine.holdings[engine._position_key("AAA", None)] = {
        "quantity": 10,
        "asset_type": "equity",
        "entry_price": float(price_df["Close"].iloc[0]),
        "side": 1,
        "multiplier": 1.0,
    }

    # mark a hedge need by setting hedge_pct via regime_state
    regime = build_regime_series(price_df, price_df.index)
    off_state = regime_state(price_df.index[10], regime)
    off_state = off_state.__class__(
        risk_on=False,
        severity="crisis",
        hedge_pct=0.8,
        risk_on_streak=off_state.risk_on_streak,
        sma200_slope_pct=off_state.sma200_slope_pct,
        sma200_distance_pct=off_state.sma200_distance_pct,
    )
    engine._update_hedge(price_df.index[10], data, off_state)
    hedge_key = engine._position_key("MES=F", "HEDGE")
    assert hedge_key in engine.holdings
    assert engine.holdings[hedge_key]["asset_type"] == "hedge"
