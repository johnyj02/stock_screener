import datetime as dt

import numpy as np
import pandas as pd
import pytest

from stock_screener.core.strategy import BaseStrategy
from stock_screener.strategies.orb import (
    Orb15BreakoutLong,
    Orb15BreakoutShort,
    _Orb15Breakout,
    _effective_exit_cutoff_time,
    _market_risk_gate_series,
    _market_trend_gate_series,
    _normalize_skip_dates,
    _orb_range_settings,
    _parse_time,
    _regime_gate_series,
    _shift_time,
)
from stock_screener.strategies.structure import (
    DoubleBottomRsiDivergence,
    DoubleTopRsiDivergence,
    FlagPennantContinuation,
    HeadAndShouldersReversal,
    LongTermSupportResistanceBreakRetest,
    MarketAlignedStructureBreak,
    PatternSeriesMixin,
    SupportResistanceBreakRetest,
    TriangleBreakout,
    _cluster_levels,
    _line_value,
    _linear_fit,
    _pivot_indices,
    _resample_weekly,
    _safe_divide,
    _window_counts,
)


def _frame(high, low, close, volume=None, rsi=None) -> pd.DataFrame:
    rows = len(close)
    index = pd.date_range("2024-01-01", periods=rows, freq="D")
    frame = pd.DataFrame(
        {
            "Open": close,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume or [100.0] * rows,
            "ATR_14": [1.0] * rows,
        },
        index=index,
    )
    if rsi is not None:
        frame["RSI_2"] = rsi
    return frame


def _orb_frame(side: int, tz=None) -> pd.DataFrame:
    index = pd.date_range("2024-06-03 09:30", periods=5, freq="5min", tz=tz)
    close = [101.0, 104.0, 103.0, 107.0, 108.0]
    high = [103.0, 106.0, 105.0, 108.0, 109.0]
    low = [99.0, 100.0, 101.0, 106.0, 107.0]
    if side == -1:
        close[-2:] = [98.0, 97.0]
        high[-2:] = [99.0, 98.0]
        low[-2:] = [97.0, 96.0]
    return pd.DataFrame(
        {
            "Open": close,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": [1_000.0] * len(index),
        },
        index=index,
    )


def test_shared_strategy_mixins_are_not_discoverable_strategies():
    assert not issubclass(_Orb15Breakout, BaseStrategy)
    assert not issubclass(PatternSeriesMixin, BaseStrategy)
    assert issubclass(Orb15BreakoutLong, BaseStrategy)
    assert issubclass(Orb15BreakoutShort, BaseStrategy)


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [
        ("10:35", dt.time(9, 30), dt.time(10, 35)),
        (dt.time(8, 15), dt.time(9, 30), dt.time(8, 15)),
        ("invalid", dt.time(9, 30), dt.time(9, 30)),
        (None, dt.time(9, 30), dt.time(9, 30)),
    ],
)
def test_orb_time_parsing(value, default, expected):
    assert _parse_time(value, default) == expected


@pytest.mark.parametrize(
    ("value", "minutes", "expected"),
    [
        (dt.time(9, 30), 15, dt.time(9, 45)),
        (dt.time(0, 5), -10, dt.time(0, 0)),
        (dt.time(23, 55), 10, dt.time(23, 59)),
    ],
)
def test_orb_time_shift_is_clamped_to_the_same_day(value, minutes, expected):
    assert _shift_time(value, minutes) == expected


def test_orb_date_and_range_configuration_normalization():
    strategy = Orb15BreakoutLong()
    strategy.orb_range = {
        "orb_min_range_points": "2.5",
        "orb_max_range_points": 8,
        "orb_max_range_atr_pct": "1.25",
    }

    assert _orb_range_settings(strategy) == (2.5, 8.0, 1.25)
    assert _normalize_skip_dates(["2024-01-02", "bad", pd.Timestamp("2024-01-03")]) == [
        dt.date(2024, 1, 2),
        dt.date(2024, 1, 3),
    ]


def test_orb_commission_bucket_adjusts_exit_and_breakeven_settings():
    strategy = Orb15BreakoutLong()
    strategy.exit_cutoff_time = "15:55"
    strategy.tp_r_multiple = 1.5
    strategy.tp_management = {
        "commission_aware": True,
        "buckets": {
            "lt_2": {
                "exit_cutoff_shift_minutes": 15,
                "exit_cutoff_cap_time": "16:00",
                "breakeven_buffer_r": 0.2,
                "breakeven_delay_bars": 3,
                "breakeven_trigger_r": 1.1,
            }
        },
    }

    assert _effective_exit_cutoff_time(strategy) == dt.time(16, 0)
    assert strategy.effective_breakeven_r(-0.1) == 0.2
    assert strategy.effective_breakeven_delay(1) == 3
    assert strategy.effective_breakeven_trigger_r(1.0) == 1.1


@pytest.mark.parametrize(
    ("strategy_type", "side", "pattern", "sentiment", "stop", "target"),
    [
        (Orb15BreakoutLong, 1, "ORB15 Long", "BULLISH", 99.0, 123.0),
        (Orb15BreakoutShort, -1, "ORB15 Short", "BEARISH", 106.0, 82.0),
    ],
)
def test_orb_long_and_short_signal_metrics(strategy_type, side, pattern, sentiment, stop, target):
    strategy = strategy_type()
    data = _orb_frame(side)
    trigger = data.index[3]

    matched, metrics = strategy.check(data.iloc[:4])

    assert matched is True
    assert metrics == {
        "pattern": pattern,
        "orb_high": 106.0,
        "orb_low": 99.0,
        "orb_range": 7.0,
        "stop_loss": stop,
        "take_profit": target,
        "score": 0.12,
        "sentiment": sentiment,
    }
    assert strategy.signal(data).loc[trigger]
    assert strategy.stop_loss_series(data).loc[trigger] == stop
    assert strategy.get_take_profit(data.iloc[:4]) == target
    assert strategy.score_series(data).loc[trigger] == pytest.approx(1 / 7)


@pytest.mark.parametrize("strategy_type,side", [(Orb15BreakoutLong, 1), (Orb15BreakoutShort, -1)])
def test_orb_class_side_is_independent_of_direction_metadata(strategy_type, side):
    strategy = strategy_type()
    strategy.direction = "short" if side == 1 else "long"
    strategy.orb_min_range_points = 0
    strategy.orb_max_range_points = 20

    assert strategy.signal(_orb_frame(side)).iloc[-1]


@pytest.mark.parametrize("strategy_type,side", [(Orb15BreakoutLong, 1), (Orb15BreakoutShort, -1)])
def test_orb_respects_skip_dates_range_limits_and_cutoff(strategy_type, side):
    data = _orb_frame(side)
    strategy = strategy_type()
    strategy.skip_dates = ["2024-06-03"]
    assert not strategy.signal(data).any()

    strategy.skip_dates = []
    strategy.orb_max_range_points = 6.0
    assert not strategy.signal(data).any()

    strategy.orb_max_range_points = 15.0
    strategy.entry_cutoff_time = "09:45"
    assert not strategy.signal(data).any()


@pytest.mark.parametrize("strategy_type,side,vwap", [(Orb15BreakoutLong, 1, 105.0), (Orb15BreakoutShort, -1, 100.0)])
def test_orb_vwap_filters_are_directional(strategy_type, side, vwap):
    data = _orb_frame(side)
    data["VWAP"] = vwap
    strategy = strategy_type()
    strategy.vwap_entry_filter_enabled = True
    assert strategy.signal(data).iloc[3]

    strategy.vwap_orb_levels_filter_enabled = True
    assert not strategy.signal(data).iloc[3]


def test_orb_timezone_conversion_uses_session_clock():
    data = _orb_frame(1)
    data.index = data.index.tz_localize("America/New_York").tz_convert("UTC")
    strategy = Orb15BreakoutLong()

    assert strategy.signal(data).iloc[3]
    assert str(strategy._local_index(data).tz) == "America/New_York"


def test_orb_market_gate_fallbacks_are_symmetric():
    index = pd.date_range("2024-01-01", periods=3, freq="D")
    data = pd.DataFrame(
        {
            "MKT_RISK_ON_FOR_LONGS": [True, False, pd.NA],
            "MKT_TREND_OK": [True, False, True],
        },
        index=index,
    )
    strategy = Orb15BreakoutLong()

    assert _market_risk_gate_series(strategy, data, 1).tolist() == [True, False, False]
    assert _market_risk_gate_series(strategy, data, -1).tolist() == [False, True, False]
    assert _market_trend_gate_series(strategy, data, 1).tolist() == [True, False, True]
    assert _market_trend_gate_series(strategy, data, -1).tolist() == [False, True, False]


def test_orb_regime_gates_support_and_or_modes():
    data = _orb_frame(1)
    data["MKT_RISK_ON_FOR_LONGS"] = [True, False, True, False, True]
    data["MKT_TREND_OK"] = [False, False, True, True, False]
    strategy = Orb15BreakoutLong()
    strategy.regime_gating = {
        "enabled": True,
        "mode": "AND",
        "gates": {"mkt_risk_on": {"enabled": True}, "mkt_trend_ok": {"enabled": True}},
    }
    local_index = strategy._local_index(data)

    assert _regime_gate_series(strategy, data, local_index).tolist() == [False, False, True, False, False]
    strategy.regime_gating["mode"] = "OR"
    assert _regime_gate_series(strategy, data, local_index).tolist() == [True, False, True, True, True]


def test_orb_exit_signal_fires_once_per_session():
    index = pd.to_datetime(
        [
            "2024-01-02 15:50",
            "2024-01-02 15:55",
            "2024-01-02 16:00",
            "2024-01-03 15:55",
        ]
    )
    data = pd.DataFrame({"Close": 100.0}, index=index)
    strategy = Orb15BreakoutLong()
    strategy.exit_cutoff_time = "15:55"

    assert strategy.exit_signal(data).tolist() == [False, True, False, True]


def test_structure_math_helpers_cover_boundaries_and_invalid_division():
    values = np.array([1.0, 3.0, 2.0, 4.0, 1.0])
    assert _pivot_indices(values, 1, 1, "high") == [1, 3]
    assert _pivot_indices(values, 1, 1, "low") == [2]
    assert _line_value(3, 1, 10.0, 5, 14.0) == 12.0
    assert _line_value(3, 1, 10.0, 1, 14.0) == 14.0
    assert _linear_fit(np.array([2.0, 4.0, 6.0])) == pytest.approx((2.0, 2.0))
    np.testing.assert_allclose(
        _safe_divide(np.array([4.0, 2.0, 1.0]), np.array([2.0, 0.0, -1.0])),
        np.array([2.0, np.nan, np.nan]),
        equal_nan=True,
    )
    assert _window_counts(np.array([0, 1, 1, 2, 3]), 3).tolist() == [0, 1, 1, 2, 2]


def test_structure_level_clustering_updates_zone_statistics():
    zones = _cluster_levels([(1, 100.0), (4, 100.4), (7, 110.0)], tolerance_pct=0.01)

    assert len(zones) == 2
    assert zones[0] == {
        "center": 100.2,
        "min": 100.0,
        "max": 100.4,
        "count": 2,
        "prices": [100.0, 100.4],
        "last_idx": 4,
    }


def test_structure_weekly_resampling_uses_ohlcv_aggregations():
    index = pd.date_range("2024-01-01", periods=10, freq="D")
    data = pd.DataFrame(
        {
            "Open": np.arange(10.0),
            "High": np.arange(10.0) + 2,
            "Low": np.arange(10.0) - 1,
            "Close": np.arange(10.0) + 1,
            "Volume": np.ones(10),
        },
        index=index,
    )

    weekly = _resample_weekly(data, "weekly")

    assert weekly.iloc[0].to_dict() == {"Open": 0.0, "High": 6.0, "Low": -1.0, "Close": 5.0, "Volume": 5.0}


def _positive_structure_cases():
    head = HeadAndShouldersReversal()
    head.lookback = 10
    head.pivot_left = head.pivot_right = 1
    head.volume_lookback = 2
    head.volume_break_ratio = 1.5
    head.retest_window = 3
    head_data = _frame(
        [8, 10, 8.5, 9, 13, 9, 8.5, 10.2, 8, 7.5],
        [7, 8, 7, 8, 10, 7.2, 8, 8, 6.8, 7],
        [7.5, 9, 8, 8.5, 12, 8, 8.2, 9, 6.9, 7.1],
        [100] * 8 + [300, 100],
    )

    top = DoubleTopRsiDivergence()
    top.lookback = 7
    top.pivot_left = top.pivot_right = 1
    top.rsi_length = 2
    top.volume_lookback = 2
    top.volume_break_ratio = 1.5
    top_data = _frame(
        [9, 12, 10, 9, 11.8, 10, 7],
        [8, 9, 8, 7, 9, 8, 6],
        [8.5, 11, 9, 8, 11, 9, 6.5],
        [100, 100, 100, 100, 100, 100, 300],
        [50, 70, 55, 50, 60, 50, 40],
    )

    bottom = DoubleBottomRsiDivergence()
    bottom.lookback = 7
    bottom.pivot_left = bottom.pivot_right = 1
    bottom.rsi_length = 2
    bottom.volume_lookback = 2
    bottom.volume_break_ratio = 1.5
    bottom_data = _frame(
        [11, 9, 11, 12, 9, 10, 14],
        [10, 7, 9, 10, 7.1, 9, 12],
        [10.5, 8, 10, 11, 8, 9.5, 13],
        [100, 100, 100, 100, 100, 100, 300],
        [50, 25, 40, 45, 35, 45, 60],
    )

    triangle = TriangleBreakout()
    triangle.lookback = 10
    triangle.min_bars = 5
    triangle.volume_lookback = 2
    triangle.volume_break_ratio = 1.5
    triangle.volume_decay_ratio = 10
    triangle.contraction_ratio = 1
    triangle.break_buffer_pct = 0
    triangle_data = _frame(
        [12, 11.8, 11.6, 11.4, 11.2, 11, 10.8, 10.6, 10.4, 11],
        [8, 8.2, 8.4, 8.6, 8.8, 9, 9.2, 9.4, 9.6, 9.7],
        [10, 10, 10, 10, 10, 10, 10, 10, 10, 10.9],
        [200, 200, 180, 180, 160, 140, 120, 100, 100, 300],
    )
    return [
        (head, head_data, "Head & Shoulders", "BEARISH"),
        (top, top_data, "Double Top", "BEARISH"),
        (bottom, bottom_data, "Double Bottom", "BULLISH"),
        (triangle, triangle_data, "Triangle Breakout", "BULLISH"),
    ]


@pytest.mark.parametrize("strategy,data,pattern,sentiment", _positive_structure_cases())
def test_structure_positive_patterns_keep_scalar_and_vector_metrics(strategy, data, pattern, sentiment):
    matched, metrics = strategy.check(data)
    context_match, context_metrics = strategy._scan_index(strategy._build_context(data), len(data) - 1)

    assert matched is context_match is True
    assert metrics == context_metrics
    assert metrics["pattern"] == pattern
    assert metrics["sentiment"] == sentiment
    assert metrics["stop_loss"] == strategy.stop_loss_series(data).iloc[-1]
    assert metrics["take_profit"] == strategy.get_take_profit(data)
    assert metrics["score"] == strategy.score_series(data).iloc[-1]
    assert strategy.signal(data).iloc[-1]


def test_head_and_shoulders_scalar_scan_rejects_an_expired_first_break():
    data = _frame(
        [8, 10, 8.5, 9, 13, 9, 8.5, 10.2, 7.2, 7.4, 7.6, 7.8, 7.9, 8.0],
        [7, 8, 7, 8, 10, 7.2, 8, 8, 6.7, 7.2, 7.3, 7.4, 6.8, 7.5],
        [7.5, 9, 8, 8.5, 12, 8, 8.2, 9, 6.9, 7.5, 7.6, 7.7, 7.0, 7.7],
        [100] * 8 + [300, 100, 100, 100, 300, 100],
    )
    strategy = HeadAndShouldersReversal()
    strategy.lookback = 14
    strategy.pivot_left = strategy.pivot_right = 1
    strategy.volume_lookback = 2
    strategy.volume_break_ratio = 1.5
    strategy.retest_window = 3

    assert strategy.check(data) == (False, {})
    assert strategy.signal(data).iloc[-1]


def test_flag_scalar_scan_uses_the_first_flag_close_as_the_pole_end():
    data = _frame(
        [101, 111, 121, 116, 122],
        [99, 109, 119, 112, 114],
        [100, 110, 120, 115, 121],
        [100, 100, 100, 50, 200],
    )
    strategy = FlagPennantContinuation()
    strategy.flagpole_lookback = 3
    strategy.flag_lookback = 2
    strategy.retrace_min = 0
    strategy.retrace_max = 1
    strategy.break_buffer_pct = -0.02
    strategy.volume_decay_ratio = 2
    strategy.volume_lookback = 2

    matched, metrics = strategy.check(data)

    assert matched
    assert metrics["pole_return_pct"] == 15.0
    assert strategy.score_series(data).iloc[-1] == pytest.approx(25.33)


@pytest.mark.parametrize(
    ("case_index", "expected_stop"),
    [(1, 12.49), (2, 6.41), (3, 9.2)],
)
def test_structure_scalar_patterns_keep_full_history_atr(case_index, expected_stop):
    strategy, data, _, _ = _positive_structure_cases()[case_index]
    prefix = data.iloc[[0] * 30].copy()
    prefix.index = pd.date_range("2023-01-01", periods=len(prefix), freq="D")
    prefix["High"] += 5
    prefix["Low"] -= 5
    data = data.copy()
    data.index = pd.date_range("2023-03-01", periods=len(data), freq="D")
    data = pd.concat([prefix, data]).drop(columns="ATR_14")

    matched, metrics = strategy.check(data)

    assert matched
    assert metrics["stop_loss"] == expected_stop


def test_structure_series_are_computed_once_and_shared(monkeypatch):
    strategy = TriangleBreakout()
    data = _frame([2, 3, 4, 5], [1, 2, 3, 4], [1.5, 2.5, 3.5, 4.5])
    calls = []
    monkeypatch.setattr(strategy, "_build_context", lambda frame: {"df": frame})
    monkeypatch.setattr(
        strategy,
        "_candidate_mask",
        lambda frame, context: pd.Series([False, True, False, True], index=frame.index),
    )

    def scan(context, index):
        calls.append(index)
        return True, {"score": index, "stop_loss": index + 10, "take_profit": index + 20}

    monkeypatch.setattr(strategy, "_scan_index", scan)

    assert strategy.signal(data).tolist() == [False, True, False, True]
    assert strategy.score_series(data).tolist() == [0.0, 1.0, 0.0, 3.0]
    assert strategy.stop_loss_series(data).dropna().tolist() == [11.0, 13.0]
    assert calls == [1, 3]


@pytest.mark.parametrize(
    "strategy_type",
    [
        HeadAndShouldersReversal,
        DoubleTopRsiDivergence,
        DoubleBottomRsiDivergence,
        TriangleBreakout,
        FlagPennantContinuation,
        SupportResistanceBreakRetest,
        LongTermSupportResistanceBreakRetest,
        MarketAlignedStructureBreak,
    ],
)
def test_structure_strategies_return_aligned_series_on_realistic_data(strategy_type):
    rng = np.random.default_rng(42)
    rows = 260
    close = 100 + rng.normal(0, 1, rows).cumsum()
    open_px = close + rng.normal(0, 0.3, rows)
    data = pd.DataFrame(
        {
            "Open": open_px,
            "High": np.maximum(open_px, close) + 0.5,
            "Low": np.minimum(open_px, close) - 0.5,
            "Close": close,
            "Volume": rng.integers(100_000, 200_000, rows),
        },
        index=pd.date_range("2020-01-01", periods=rows, freq="D"),
    )
    strategy = strategy_type()

    for series in (strategy.signal(data), strategy.score_series(data), strategy.stop_loss_series(data)):
        assert series.index.equals(data.index)
        assert len(series) == rows
