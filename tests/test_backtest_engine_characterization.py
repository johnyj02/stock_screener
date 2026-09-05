import numpy as np
import pandas as pd
import pytest

from stock_screener.core.strategy import BaseStrategy
from stock_screener.strategies.orb import Orb15BreakoutLong, Orb15BreakoutShort
from strategy_backtester.core.engine import BacktestEngine
from strategy_backtester.core.regime import RegimeState


class _Strategy(BaseStrategy):
    sentiment = "BULLISH"
    use_breakeven = False
    use_exit_signal = False
    use_regime_exit = False
    use_take_profit = False
    use_time_stop = False

    def check(self, df):
        return False, {}

    def signal(self, df):
        return pd.Series(False, index=df.index)

    def stop_loss_series(self, df):
        return pd.Series(95.0, index=df.index)


class _NamedStrategy(_Strategy):
    def __init__(self, name):
        self.name = name

    def get_name(self):
        return self.name


def _engine(strategies=None, **kwargs) -> BacktestEngine:
    options = {
        "start_date": "2024-01-01",
        "universe": ["AAA"],
        "strategies": strategies or [],
        "initial_capital": 10_000.0,
        "min_avg_dollar_vol": 0.0,
        "min_avg_volume_futures": 0.0,
        "hedge_symbol": "",
    }
    options.update(kwargs)
    return BacktestEngine(**options)


def _prices(values=(100.0, 110.0, 90.0), opens=None) -> pd.DataFrame:
    close = np.asarray(values, dtype=float)
    open_px = close.copy() if opens is None else np.asarray(opens, dtype=float)
    return pd.DataFrame(
        {
            "Open": open_px,
            "High": np.maximum(open_px, close) + 2.0,
            "Low": np.minimum(open_px, close) - 2.0,
            "Close": close,
            "Volume": 1_000_000.0,
        },
        index=pd.date_range("2024-01-01", periods=len(close), freq="D"),
    )


def _regime(risk_on=True, **kwargs) -> RegimeState:
    values = {
        "risk_on": risk_on,
        "severity": "risk_on" if risk_on else "risk_off",
        "hedge_pct": 0.0,
        "risk_on_streak": 10 if risk_on else 0,
        "sma200_slope_pct": 0.01,
        "sma200_distance_pct": 0.05,
    }
    values.update(kwargs)
    return RegimeState(**values)


def test_engine_initializes_position_identity_counters_and_empty_report():
    engine = _engine()

    assert engine._position_key("AAA", None) == ("AAA", "UNKNOWN")
    assert engine._next_position_id() == 1
    assert engine._next_position_id() == 2
    assert engine._next_entry_id() == 1
    equity, trades, summary, by_strategy, by_ticker = engine.get_report()
    assert equity.empty and trades.empty and by_strategy.empty and by_ticker.empty
    assert isinstance(summary, dict)


def test_rejection_stats_normalize_dates_books_and_unknown_reasons():
    engine = _engine()
    date = pd.Timestamp("2024-01-02 15:30")

    engine._record_signal_seen(date, None)
    engine._record_accept(date, None)
    engine._record_rejection(date, None, "not-a-column")

    bucket = engine.rejection_stats[pd.Timestamp("2024-01-02")]["unassigned"]
    assert bucket["signals_seen"] == 1
    assert bucket["accepted"] == 1
    assert bucket["rejected_other"] == 1


@pytest.mark.parametrize(
    ("symbol", "sentiment", "allow_short_equity", "expected"),
    [
        ("AAA", "BULLISH", False, 1),
        ("AAA", "BEARISH", False, None),
        ("AAA", "BEARISH", True, -1),
        ("MES=F", "BEARISH", False, -1),
        ("AAA", None, True, None),
    ],
)
def test_entry_side_rules(symbol, sentiment, allow_short_equity, expected):
    assert _engine(allow_short_equity=allow_short_equity)._entry_side(symbol, sentiment) == expected


@pytest.mark.parametrize(
    ("side", "is_entry", "expected"),
    [(1, True, 101.0), (1, False, 99.0), (-1, True, 99.0), (-1, False, 101.0)],
)
def test_execution_price_applies_adverse_slippage(side, is_entry, expected):
    engine = _engine(slippage_bps=100)
    assert engine._execution_price(100.0, side, is_entry) == expected


@pytest.mark.parametrize(
    ("row", "side", "expected"),
    [
        ({"Open": 94, "High": 101, "Low": 93, "Close": 96}, 1, (94.0, "gap_open")),
        ({"Open": 100, "High": 101, "Low": 94, "Close": 96}, 1, (95.0, "intraday_stop")),
        ({"Open": 100, "High": 101, "Low": 96, "Close": 99}, 1, (None, None)),
        ({"Open": 106, "High": 107, "Low": 100, "Close": 104}, -1, (106.0, "gap_open")),
        ({"Open": 100, "High": 106, "Low": 99, "Close": 104}, -1, (105.0, "intraday_stop")),
        ({"Open": 100, "High": 104, "Low": 99, "Close": 102}, -1, (None, None)),
    ],
)
def test_stop_fill_models_gap_and_intraday_execution(row, side, expected):
    assert _engine()._stop_fill_price(pd.Series(row), 95.0 if side == 1 else 105.0, side) == expected


def test_stop_fill_falls_back_to_close_for_incomplete_or_nan_ohlc():
    engine = _engine()
    assert engine._stop_fill_price(pd.Series({"Close": 93.0}), 95.0, 1) == (93.0, "close_based")
    row = pd.Series({"Open": np.nan, "High": 100.0, "Low": 90.0, "Close": 94.0})
    assert engine._stop_fill_price(row, 95.0, 1) == (94.0, "close_based")


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("floor_int", 3.0), ("round_int", 4.0), ("ceil_int", 4.0), ("fractional", 3.6)],
)
def test_equity_quantity_rounding_modes(mode, expected):
    assert _engine(equity_qty_rounding=mode)._round_equity_qty(3.6) == expected


def test_commission_combines_flat_and_percentage_costs():
    engine = _engine(commission_per_trade=2.0, commission_pct=0.001)
    assert engine._trade_commission(1_000.0) == 3.0
    assert engine._trade_commission(0.0) == 0.0


def test_market_value_and_equity_use_current_prices_with_entry_fallback():
    engine = _engine(initial_capital=1_000.0)
    date = pd.Timestamp("2024-01-02")
    engine.cash = 1_000.0
    engine.holdings = {
        ("AAA", "Long"): {
            "quantity": 2,
            "entry_price": 100.0,
            "multiplier": 1.0,
            "side": 1,
            "asset_type": "equity",
        },
        ("BBB", "Short"): {
            "quantity": 1,
            "entry_price": 50.0,
            "multiplier": 1.0,
            "side": -1,
            "asset_type": "equity",
        },
    }
    data = {
        "AAA": _prices((100, 110, 120)),
        "BBB": _prices((50,)).iloc[:0],
    }

    assert engine._current_market_value(date, data) == 270.0
    assert engine._current_market_value(date, data, side=1) == 220.0
    assert engine._current_ticker_value(date, data, "BBB") == 50.0
    assert engine._current_equity(date, data) == 1_170.0


@pytest.mark.parametrize(
    ("direction", "levels", "expected"),
    [
        ("long", [130, 110, 110, np.nan, 90, "bad"], [110.0, 130.0]),
        ("short", [70, 90, 90, 110, None], [90.0, 70.0]),
    ],
)
def test_take_profit_levels_are_cleaned_filtered_and_sorted(direction, levels, expected):
    strategy = _Strategy()
    strategy.direction = direction
    strategy.get_take_profit_levels = lambda df, entry_price=None: levels
    strategy.get_take_profit_level_pcts = lambda cleaned: [0.25] * len(cleaned)

    cleaned, pcts = _engine()._resolve_take_profit_levels(strategy, _prices(), 100.0)

    assert cleaned == expected
    assert pcts == [0.25] * len(expected)


def test_take_profit_levels_discard_mismatched_percentages_and_provider_errors():
    strategy = _Strategy()
    strategy.get_take_profit_levels = lambda df, entry_price=None: [110, 120]
    strategy.get_take_profit_level_pcts = lambda levels: [1.0]
    assert _engine()._resolve_take_profit_levels(strategy, _prices(), 100.0) == ([110.0, 120.0], None)

    def fail(df, entry_price=None):
        raise ValueError("bad target")

    strategy.get_take_profit_levels = fail
    assert _engine()._resolve_take_profit_levels(strategy, _prices(), 100.0) == (None, None)


def test_general_loss_and_stop_cooldowns_share_exclusive_end_boundary():
    strategy = _NamedStrategy("Alpha")
    strategy.loss_cooldown_days = 3
    strategy.stop_cooldown_days = 2
    engine = _engine([strategy], cooldown_days=4)
    key = ("AAA", "Alpha")
    start = pd.Timestamp("2024-01-01")
    engine.last_exit_dates[key] = start
    engine.last_loss_exit_dates[key] = start
    engine.last_stop_exit_dates[key] = start

    assert engine._in_cooldown("AAA", "Alpha", start + pd.Timedelta(days=3))
    assert not engine._in_cooldown("AAA", "Alpha", start + pd.Timedelta(days=4))
    assert engine._in_loss_cooldown("AAA", strategy, start + pd.Timedelta(days=2))
    assert not engine._in_loss_cooldown("AAA", strategy, start + pd.Timedelta(days=3))
    assert engine._in_stop_cooldown("AAA", strategy, start + pd.Timedelta(days=1))
    assert not engine._in_stop_cooldown("AAA", strategy, start + pd.Timedelta(days=2))


def test_entry_allowed_reports_each_regime_rejection_reason():
    engine = _engine()
    strategy = _Strategy()
    assert engine._entry_allowed(strategy, 1, _regime(), {}, ticker="AAA")[0]

    strategy.entry_enabled = False
    assert engine._entry_allowed(strategy, 1, _regime(), {}) == (False, "rejected_other")
    strategy.entry_enabled = True

    assert engine._entry_allowed(strategy, 1, _regime(False), {}) == (False, "rejected_regime")
    strategy.allow_risk_off_entries = True
    assert engine._entry_allowed(strategy, 1, _regime(False), {}) == (True, None)
    strategy.allow_risk_off_entries = None

    assert engine._entry_allowed(strategy, -1, _regime(), {}) == (False, "rejected_regime")
    strategy.allow_short_risk_on = True
    assert engine._entry_allowed(strategy, -1, _regime(), {}) == (True, None)

    strategy.allowed_trend_states = ["UP"]
    assert engine._entry_allowed(strategy, 1, _regime(trend_state="DOWN"), {}) == (
        False,
        "rejected_regime_trend",
    )
    strategy.allowed_trend_states = None
    strategy.max_vol_state = "NORMAL"
    assert engine._entry_allowed(strategy, 1, _regime(vol_state="HIGH"), {}) == (
        False,
        "rejected_regime_vol",
    )
    strategy.max_vol_state = None
    strategy.min_risk_on_prob = 0.8
    assert engine._entry_allowed(strategy, 1, _regime(risk_on_prob=0.7), {}) == (
        False,
        "rejected_regime_prob",
    )
    strategy.min_risk_on_prob = None
    assert engine._entry_allowed(strategy, 1, _regime(regime_usable=False), {}) == (
        False,
        "rejected_regime_usable",
    )


def test_entry_allowed_applies_stability_flip_and_large_gap_bypass():
    engine = _engine(regime_stability_days=4)
    strategy = _Strategy()
    strategy.require_regime_stability = True
    unstable = _regime(risk_on_streak=2)
    assert engine._entry_allowed(strategy, 1, unstable, {}) == (False, "rejected_regime_stability")

    strategy.allow_regime_bypass_large_gap = True
    assert engine._entry_allowed(strategy, 1, unstable, {"gap_bucket": "large"}) == (True, None)

    strategy.require_regime_stability = False
    strategy.avoid_regime_flip = True
    near_flip = _regime(sma200_distance_pct=0.001, sma200_slope_pct=0.0001)
    assert engine._entry_allowed(strategy, 1, near_flip, {}) == (False, "rejected_regime_flip_avoid")


def test_confirmation_gate_tracks_any_all_and_weighted_hits():
    entry = _NamedStrategy("Entry")
    first = _NamedStrategy("First")
    second = _NamedStrategy("Second")
    first.confirmation_lookback_days = 3
    second.confirmation_lookback_days = 3
    first.confirmation_weight = 0.75
    second.confirmation_weight = 0.5
    entry.confirmation_any = ["First"]
    entry.confirmation_all = ["First", "Second"]
    entry.confirmation_score = {"enabled": True, "threshold": 1.0, "strategies": ["First", "Second"]}
    engine = _engine([entry, first, second])
    date = pd.Timestamp("2024-01-05")
    engine.recent_signals = {"AAA": {"First": date - pd.Timedelta(days=1)}}

    ok, details = engine._confirmation_evaluation(entry, "AAA", date)
    assert not ok
    assert details["confirmation_any_hits"] == ["First"]
    assert details["confirmation_all_hits"] == ["First"]
    assert details["confirmation_score_total"] == 0.75

    engine.recent_signals["AAA"]["Second"] = date
    ok, details = engine._confirmation_evaluation(entry, "AAA", date)
    assert ok
    assert details["confirmation_hits"] == ["First", "Second"]
    assert details["confirmation_score_total"] == 1.25


def test_orb_trade_state_blocks_active_trade_then_allows_stop_flip():
    long = Orb15BreakoutLong()
    short = Orb15BreakoutShort()
    engine = _engine([long, short])
    date = pd.Timestamp("2024-06-03 10:00", tz="America/New_York")
    holding = {"symbol": "MES=F", "strategy": long.get_name(), "peak_r": 0.25, "last_exit_r": -1.0}

    engine._register_orb_entry(long, "MES=F", date, 1, holding)
    assert engine._orb_entry_allowed(short, "MES=F", -1, date, {}) == (False, "rejected_other")

    engine._register_orb_exit(holding, "Stop Loss")
    assert engine._orb_entry_allowed(short, "MES=F", -1, date, {}) == (True, None)
    assert engine._orb_entry_allowed(long, "MES=F", 1, date, {}) == (False, "rejected_other")


@pytest.mark.parametrize(
    ("side", "exit_price", "entry_action", "exit_action"),
    [(1, 110.0, "BUY", "SELL"), (-1, 90.0, "SELL_SHORT", "BUY_TO_COVER")],
)
def test_long_and_short_round_trips_have_symmetric_cash_and_pnl(side, exit_price, entry_action, exit_action):
    strategy = _NamedStrategy("Trade")
    engine = _engine([strategy])
    data = {"AAA": _prices()}
    entry_date, exit_date = data["AAA"].index[:2]

    assert engine._execute_buy(
        "AAA",
        entry_date,
        100.0,
        1_000.0,
        95.0 if side == 1 else 105.0,
        "Trade",
        side,
        "equity",
        None,
        {},
        data,
    )
    assert engine._execute_sell(("AAA", "Trade"), exit_date, exit_price, "Test Exit", data)

    assert engine.cash == pytest.approx(10_100.0)
    assert engine.trades[0]["action"] == entry_action
    assert engine.trades[1]["action"] == exit_action
    assert engine.trades[1]["pnl"] == pytest.approx(100.0)
    assert not engine.holdings


def test_round_trip_allocates_commissions_and_slippage_to_trade_records():
    strategy = _NamedStrategy("Costs")
    engine = _engine([strategy], commission_per_trade=2.0, commission_pct=0.001, slippage_bps=10)
    data = {"AAA": _prices()}
    entry_date, exit_date = data["AAA"].index[:2]

    engine._execute_buy("AAA", entry_date, 100.0, 1_001.0, 95.0, "Costs", 1, "equity", None, {}, data)
    engine._execute_sell(("AAA", "Costs"), exit_date, 110.0, "Test Exit", data)

    entry, exit_trade = engine.trades
    assert entry["price"] == pytest.approx(100.1)
    assert exit_trade["price"] == pytest.approx(109.89)
    assert entry["commission"] > 0
    assert exit_trade["commission"] > 0
    assert entry["slippage_cost"] > 0
    assert exit_trade["slippage_cost"] > 0
    assert engine.cash < 10_100.0


def test_partial_exit_keeps_position_then_full_exit_closes_it():
    strategy = _NamedStrategy("Partial")
    engine = _engine([strategy])
    data = {"AAA": _prices()}
    entry_date, exit_date = data["AAA"].index[:2]
    engine._execute_buy("AAA", entry_date, 100.0, 1_000.0, 95.0, "Partial", 1, "equity", None, {}, data)

    assert engine._execute_sell(("AAA", "Partial"), exit_date, 110.0, "Trim", data, qty=4, partial=True)
    assert engine.holdings[("AAA", "Partial")]["quantity"] == 6
    assert engine.trades[-1]["partial"] is True

    assert engine._execute_sell(("AAA", "Partial"), exit_date, 110.0, "Close", data)
    assert engine.cash == 10_100.0
    assert ("AAA", "Partial") not in engine.holdings


@pytest.mark.parametrize(
    ("side", "high", "low", "expected_mfe", "expected_mae"),
    [(1, 104.0, 97.0, 4.0, 3.0), (-1, 104.0, 97.0, 3.0, 4.0)],
)
def test_excursion_tracking_is_symmetric(side, high, low, expected_mfe, expected_mae):
    holding = {
        "entry_price": 100.0,
        "entry_date": pd.Timestamp("2024-01-01"),
        "initial_risk": 2.0,
        "side": side,
        "mfe": 0.0,
        "mae": 0.0,
        "peak_r": 0.0,
        "time_to_1r_days": None,
    }
    _engine()._update_excursions(holding, pd.Series({"High": high, "Low": low}), pd.Timestamp("2024-01-03"))
    assert holding["mfe"] == expected_mfe
    assert holding["mae"] == expected_mae
    assert holding["mfe_r"] == expected_mfe / 2
    assert holding["mae_r"] == expected_mae / 2
    assert holding["time_to_1r_days"] == 2


@pytest.mark.parametrize(("side", "trigger_price"), [(1, 102.0), (-1, 98.0)])
def test_breakeven_stop_is_symmetric_for_long_and_short(side, trigger_price):
    strategy = _Strategy()
    strategy.breakeven_r = 0.0
    strategy.breakeven_delay_bars = 0
    strategy.breakeven_trigger_r = 1.0
    data = {"AAA": _prices((100, trigger_price, trigger_price))}
    holding = {
        "entry_price": 100.0,
        "entry_date": data["AAA"].index[0],
        "initial_risk": 2.0,
        "stop_loss": 95.0 if side == 1 else 105.0,
        "side": side,
        "be_trigger_index": None,
    }

    stop = _engine()._apply_breakeven_stop(
        holding,
        trigger_price,
        data["AAA"].index[1],
        data,
        "AAA",
        strategy,
    )

    assert stop == 100.0
    assert holding["stop_type"] == "breakeven"


def test_exit_predicates_cover_decay_time_and_follow_through():
    strategy = _NamedStrategy("ExitRules")
    strategy.decay_exit_enabled = True
    strategy.decay_exit_days = 3
    strategy.decay_exit_mfe_r = 0.5
    strategy.use_time_stop = True
    strategy.time_stop_days = 4
    strategy.follow_through_exit_enabled = True
    strategy.follow_through_bars = 2
    engine = _engine([strategy])
    data = _prices((100, 99, 98, 97, 96))
    entry_date = data.index[0]
    holding = {"entry_date": entry_date, "mfe_r": 0.25, "side": 1, "strategy": "ExitRules"}
    engine.holdings[("AAA", "ExitRules")] = holding

    assert not engine._should_decay_exit(holding, data.index[2], strategy)
    assert engine._should_decay_exit(holding, data.index[3], strategy)
    assert engine._should_follow_through_exit(holding, data.loc[data.index[2]], data, strategy, data.index[2])
    assert not engine._time_stop_reached(("AAA", "ExitRules"), data.index[3])
    assert engine._time_stop_reached(("AAA", "ExitRules"), data.index[4])


def test_liquidity_filter_handles_equities_futures_and_short_histories():
    data = _prices((10, 10, 10))
    date = data.index[-1]
    engine = _engine(liquidity_lookback=3, min_avg_dollar_vol=9_000_000, min_avg_volume_futures=900_000)

    assert engine._passes_liquidity_filter(data, "AAA", date)
    assert engine._passes_liquidity_filter(data, "MES=F", date)
    assert not engine._passes_liquidity_filter(data.iloc[:2], "AAA", data.index[1])


def test_scalar_exit_pipeline_uses_gap_open_stop_before_other_exits():
    strategy = _NamedStrategy("Stops")
    strategy.use_stop_loss = True
    engine = _engine([strategy], exit_on_risk_off=False)
    data = {"AAA": _prices((100, 94), opens=(100, 93))}
    entry_date, exit_date = data["AAA"].index
    engine._execute_buy("AAA", entry_date, 100.0, 1_000.0, 95.0, "Stops", 1, "equity", None, {}, data)

    engine._process_signals(exit_date, [], data, _regime())

    exit_trade = engine.trades[-1]
    assert exit_trade["reason"] == "Stop Loss"
    assert exit_trade["raw_price"] == 93.0
    assert exit_trade["stop_fill_mode"] == "gap_open"
    assert not engine.holdings


def test_scalar_and_vector_exit_pipelines_match_stop_execution():
    data = {"AAA": _prices((100, 94), opens=(100, 93))}
    entry_date, exit_date = data["AAA"].index
    outcomes = []

    for vectorized in (False, True):
        strategy = _NamedStrategy("Stops")
        strategy.use_stop_loss = True
        engine = _engine([strategy], exit_on_risk_off=False)
        engine._execute_buy("AAA", entry_date, 100.0, 1_000.0, 95.0, "Stops", 1, "equity", None, {}, data)

        if vectorized:
            regime = pd.DataFrame(
                {"risk_on": [True], "severity": ["risk_on"], "hedge_pct": [0.0]},
                index=[exit_date],
            )
            stop_series = pd.Series(95.0, index=data["AAA"].index)
            engine._run_vectorized(
                [exit_date],
                data,
                {"AAA": {}},
                {"AAA": {"Stops": stop_series}},
                {"AAA": {}},
                {"AAA": {}},
                regime,
            )
        else:
            engine._process_signals(exit_date, [], data, _regime())

        trade = engine.trades[-1]
        outcomes.append(
            {
                key: trade.get(key)
                for key in ("reason", "raw_price", "price", "stop_level", "stop_fill_mode", "pnl")
            }
        )

    assert outcomes[0] == outcomes[1]


def test_scalar_candidate_selection_uses_highest_strategy_score():
    low = _NamedStrategy("Low")
    high = _NamedStrategy("High")
    engine = _engine(
        [low, high],
        risk_per_trade_pct=0.02,
        max_alloc_pct=1.0,
        liquidity_lookback=0,
    )
    data = {"AAA": _prices((100,))}
    date = data["AAA"].index[0]
    results = [
        {
            "symbol": "AAA",
            "price": 100.0,
            "matches": ["Low", "High"],
            "metrics": {
                "Low": {"sentiment": "BULLISH", "score": 1.0, "stop_loss": 95.0},
                "High": {"sentiment": "BULLISH", "score": 3.0, "stop_loss": 95.0},
            },
        }
    ]

    engine._process_signals(date, results, data, _regime())

    assert ("AAA", "High") in engine.holdings
    assert ("AAA", "Low") not in engine.holdings
    assert engine.trades[0]["strategy"] == "High"


def test_staged_take_profit_can_cross_and_close_multiple_levels_on_one_bar():
    strategy = _NamedStrategy("Targets")
    engine = _engine([strategy])
    data = {"AAA": _prices((100, 111))}
    entry_date, exit_date = data["AAA"].index
    engine._execute_buy(
        "AAA",
        entry_date,
        100.0,
        1_000.0,
        95.0,
        "Targets",
        1,
        "equity",
        110.0,
        {},
        data,
        take_profit_levels=[105.0, 110.0],
        take_profit_level_pcts=[0.5, 0.5],
        take_profit_level_pct_mode="initial",
    )
    holding = engine.holdings[("AAA", "Targets")]

    assert engine._apply_staged_take_profit("AAA", holding, 111.0, exit_date, strategy, data)
    assert ("AAA", "Targets") not in engine.holdings
    exits = [trade for trade in engine.trades if trade.get("reason") == "Take Profit"]
    assert [trade["qty"] for trade in exits] == [5.0, 5.0]


def test_book_allocator_initializes_budget_and_state_labels():
    allocator = {
        "total_risk_budget_pct": 0.1,
        "books": {
            "core": {
                "risk_budget_pct": 0.6,
                "max_concurrent_risk_pct": 0.5,
                "drawdown_reduce_to_pct": 0.25,
            }
        },
    }
    engine = _engine(book_allocator=allocator)

    assert engine._allocator_state_label("core") == "normal"
    assert engine._book_active_risk_pct("core") == 0.5
    assert engine._book_budget_dollars("core", 10_000) == 500.0
    engine.book_state["core"]["throttled"] = True
    assert engine._allocator_state_label("core") == "throttled"
    assert engine._book_active_risk_pct("core") == 0.25
    engine.book_state["core"]["disabled"] = True
    assert engine._allocator_state_label("core") == "disabled"
    assert engine._book_active_risk_pct("core") == 0.0
