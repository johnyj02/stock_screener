import importlib
import pkgutil
from datetime import datetime

import pandas as pd

from stock_screener.core.loader import StrategyLoader
from strategy_backtester.core.metrics import equity_metrics, trade_summary


def test_strategy_loader_skips_broken_modules(monkeypatch):
    real_import = importlib.import_module
    real_iter = pkgutil.iter_modules

    def fake_iter_modules(paths):
        for mod in real_iter(paths):
            yield mod
        yield (None, "fake_broken_mod", False)

    def fake_import_module(name, package=None):
        if name.endswith("fake_broken_mod"):
            raise ImportError("boom")
        return real_import(name, package)

    monkeypatch.setattr(pkgutil, "iter_modules", fake_iter_modules)
    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    loader = StrategyLoader()
    strategies = loader.load_strategies()
    assert isinstance(strategies, list)
    assert strategies, "Expected at least one real strategy to load"


def test_metrics_handles_basic_cases():
    equity_df = pd.DataFrame(
        [
            {"date": datetime(2020, 1, 1), "equity": 100_000, "positions": 0, "vol_scale": 1.0},
            {"date": datetime(2020, 1, 2), "equity": 101_000, "positions": 1, "vol_scale": 1.0},
            {"date": datetime(2020, 1, 3), "equity": 102_000, "positions": 1, "vol_scale": 1.0},
        ]
    )
    trades_df = pd.DataFrame(
        [
            {"action": "BUY", "pnl": 0.0, "commission": 1.0, "slippage_cost": 0.5, "trade_notional": 10_000},
            {"action": "SELL", "pnl": 500.0, "commission": 1.0, "slippage_cost": 0.5, "trade_notional": 10_000, "holding_days": 2, "return_pct": 5.0},
        ]
    )

    summary = trade_summary(trades_df)
    assert summary["total_trades"] == 1
    assert summary["total_pnl"] == 500.0
    assert summary["avg_commission"] > 0

    eq_summary = equity_metrics(equity_df, trades_df)
    assert eq_summary["final_equity"] == 102_000
    assert eq_summary["total_return_pct"] > 0
    assert eq_summary["time_in_market_pct"] >= 0


def test_equity_metrics_accepts_index_only():
    dates = pd.date_range("2020-01-01", periods=3, freq="D")
    equity_df = pd.DataFrame({"equity": [100_000, 101_000, 102_000]}, index=dates)
    summary = equity_metrics(equity_df, pd.DataFrame())

    assert summary["final_equity"] == 102_000
    assert summary["total_return_pct"] > 0


def test_equity_metrics_handles_date_index_and_column():
    dates = pd.date_range("2020-01-01", periods=3, freq="D")
    equity_df = pd.DataFrame({"date": dates, "equity": [100_000, 101_000, 102_000]}).set_index(
        "date"
    )
    equity_df["date"] = dates

    summary = equity_metrics(equity_df, pd.DataFrame())

    assert summary["final_equity"] == 102_000
    assert summary["total_return_pct"] > 0
