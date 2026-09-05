import pandas as pd
import numpy as np

from strategy_backtester.core.engine import BacktestEngine
from strategy_backtester.core.regime import RegimeState
from stock_screener.core.strategy import BaseStrategy


def _df(rows: int = 30, start_price: float = 50.0) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=rows, freq="D")
    close = pd.Series(np.linspace(start_price, start_price + 5, rows), index=idx)
    open_ = close
    high = close + 0.5
    low = close - 0.5
    volume = pd.Series(1_000_000, index=idx, dtype="float")
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


class _DummyStrategy(BaseStrategy):
    use_take_profit = False
    use_exit_signal = False
    use_time_stop = False
    use_regime_exit = False
    use_stop_loss = True
    use_breakeven = True
    breakeven_r = 0.5
    breakeven_delay_bars = 0

    def check(self, df):
        return True, {}

    def signal(self, df):
        return pd.Series(True, index=df.index)

    def stop_loss_series(self, df):
        return (df["Close"] * 0.98).round(2)


def test_scalar_atr_stop_matches_series():
    data = _df()
    strategy = _DummyStrategy()

    for direction in ("long", "short"):
        expected = strategy.atr_stop_series(data, direction=direction).iloc[-1]
        assert strategy.calculate_atr_stop(data, direction=direction) == expected


def test_available_trade_amount_respects_min_notional_and_risk():
    price_df = _df()
    data = {"AAA": price_df}
    engine = BacktestEngine(
        start_date="2020-01-01",
        end_date="2020-02-10",
        universe=["AAA"],
        strategies=[],
        use_vectorized=True,
        trade_size=10_000,
        risk_per_trade_pct=0.01,
        min_trade_notional=5_000,
    )
    # no holdings, ample cash
    amt, reason = engine._available_trade_amount(
        date=price_df.index[5],
        data_source=data,
        ticker="AAA",
        entry_price=float(price_df["Close"].iloc[5]),
        stop_loss=float(price_df["Close"].iloc[5] * 0.97),
        regime_state=RegimeState(True, "risk_on", 0.0, 5, 0.0, 0.05),
        side=1,
        return_reason=True,
    )
    assert amt > 0
    assert reason is None

    engine.min_trade_notional = 50_000
    amt2, reason2 = engine._available_trade_amount(
        date=price_df.index[5],
        data_source=data,
        ticker="AAA",
        entry_price=float(price_df["Close"].iloc[5]),
        stop_loss=float(price_df["Close"].iloc[5] * 0.97),
        regime_state=RegimeState(True, "risk_on", 0.0, 5, 0.0, 0.05),
        side=1,
        return_reason=True,
    )
    assert amt2 == 0.0
    assert reason2 in {"min_notional", "risk_budget"}


def test_breakeven_stop_moves_stop_loss(monkeypatch):
    price_df = _df(rows=10)
    data = {"AAA": price_df}
    strat = _DummyStrategy()
    engine = BacktestEngine(
        start_date="2020-01-01",
        end_date="2020-01-15",
        universe=["AAA"],
        strategies=[strat],
        use_vectorized=True,
        breakeven_r=0.5,
        breakeven_delay_bars=0,
    )
    holding = {
        "entry_price": float(price_df["Close"].iloc[0]),
        "initial_risk": 1.0,
        "stop_loss": float(price_df["Close"].iloc[0] * 0.98),
        "side": 1,
        "entry_date": price_df.index[0],
        "be_trigger_index": None,
    }
    updated = engine._apply_breakeven_stop(
        holding,
        price=float(price_df["Close"].iloc[5]),
        date=price_df.index[5],
        data_source=data,
        ticker="AAA",
        strategy=strat,
    )
    assert updated is not None
    assert updated >= holding["entry_price"], "breakeven stop should not be below entry when breakeven_r>0"
