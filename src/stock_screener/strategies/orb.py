"""
Opening Range Breakout (ORB) strategies for intraday data.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Tuple, Dict, Any, Optional, Iterable, List

import pandas as pd

from stock_screener.core.strategy import BaseStrategy

logger = logging.getLogger(__name__)


def _parse_time(value: Optional[object], default: dt.time) -> dt.time:
    if isinstance(value, dt.time):
        return value
    if isinstance(value, str):
        try:
            return dt.datetime.strptime(value, "%H:%M").time()
        except ValueError:
            return default
    return default


def _normalize_skip_dates(values: Optional[Iterable[object]]) -> List[dt.date]:
    if not values:
        return []
    parsed: List[dt.date] = []
    for val in values:
        try:
            parsed.append(pd.Timestamp(val).date())
        except Exception:
            continue
    return parsed


def _shift_time(value: dt.time, minutes: int) -> dt.time:
    base = dt.datetime(2000, 1, 1, value.hour, value.minute, value.second)
    shifted = base + dt.timedelta(minutes=minutes)
    if shifted.date() != base.date():
        if shifted < base:
            return dt.time(0, 0)
        return dt.time(23, 59)
    return shifted.time()


def _orb_range_settings(strategy: BaseStrategy) -> Tuple[float, float, Optional[float]]:
    min_range = float(getattr(strategy, "orb_min_range_points", 0.0) or 0.0)
    max_range = float(getattr(strategy, "orb_max_range_points", 0.0) or 0.0)
    max_range_atr_pct = getattr(strategy, "orb_max_range_atr_pct", None)
    cfg = getattr(strategy, "orb_range", None)
    if isinstance(cfg, dict):
        if cfg.get("orb_min_range_points") is not None:
            min_range = float(cfg.get("orb_min_range_points") or 0.0)
        if cfg.get("orb_max_range_points") is not None:
            max_range = float(cfg.get("orb_max_range_points") or 0.0)
        if cfg.get("orb_max_range_atr_pct") is not None:
            max_range_atr_pct = cfg.get("orb_max_range_atr_pct")
    if max_range_atr_pct is not None:
        max_range_atr_pct = float(max_range_atr_pct)
    return min_range, max_range, max_range_atr_pct


def _tp_bucket(strategy: BaseStrategy) -> Optional[Dict[str, Any]]:
    cfg = getattr(strategy, "tp_management", None)
    if not isinstance(cfg, dict) or not bool(cfg.get("commission_aware", False)):
        return None
    buckets = cfg.get("buckets", {}) or {}
    tp_r = float(getattr(strategy, "tp_r_multiple", 0.0) or 0.0)
    if tp_r < 2.0:
        return buckets.get("lt_2")
    if tp_r < 2.5:
        return buckets.get("gte_2_lt_2_5")
    return buckets.get("gte_2_5")


def _effective_exit_cutoff_time(strategy: BaseStrategy) -> Optional[dt.time]:
    exit_time_val = getattr(strategy, "exit_cutoff_time", None) or getattr(strategy, "orb_exit_time", None)
    if not exit_time_val:
        return None
    exit_time = _parse_time(exit_time_val, dt.time(15, 55))
    bucket = _tp_bucket(strategy)
    if not bucket:
        return exit_time
    shift = int(bucket.get("exit_cutoff_shift_minutes", 0) or 0)
    if shift:
        exit_time = _shift_time(exit_time, shift)
    cap_val = bucket.get("exit_cutoff_cap_time")
    if cap_val:
        cap_time = _parse_time(cap_val, exit_time)
        if exit_time > cap_time:
            exit_time = cap_time
    return exit_time


def _atr_15m_metrics(
    strategy: BaseStrategy,
    df: pd.DataFrame,
    local_idx: pd.DatetimeIndex,
    lookback_days: int,
) -> Tuple[pd.Series, pd.Series]:
    atr_col = "ORB_ATR_15M_14"
    median_col = f"ORB_ATR_15M_14_MEDIAN_{lookback_days}D"
    atr_series = df.get(atr_col)
    median_series = df.get(median_col)
    if atr_series is not None and median_series is not None:
        return atr_series, median_series

    if df.empty or not all(col in df.columns for col in ("Open", "High", "Low", "Close")):
        empty = pd.Series(index=df.index, dtype="float64")
        return empty, empty

    local_df = df[["Open", "High", "Low", "Close"]].copy()
    local_df.index = local_idx
    ohlc = local_df.resample("15min").agg(
        {
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
        }
    )
    ohlc = ohlc.dropna(subset=["Close"])
    atr_15m = strategy.atr_series(ohlc, period=14)
    if atr_15m is None or atr_15m.empty:
        empty = pd.Series(index=df.index, dtype="float64")
        return empty, empty

    atr_15m = atr_15m.astype("float64")
    atr_full = atr_15m.reindex(local_idx, method="ffill")
    atr_full = pd.Series(atr_full.to_numpy(), index=df.index, dtype="float64")
    daily_median = atr_15m.groupby(atr_15m.index.date).median()
    rolling_median = daily_median.rolling(lookback_days, min_periods=1).median()
    day_key = pd.Series(local_idx.date, index=df.index)
    median_full = day_key.map(rolling_median).astype("float64")

    df[atr_col] = atr_full
    df[median_col] = median_full
    return df[atr_col], df[median_col]


def _market_risk_gate_series(strategy: BaseStrategy, df: pd.DataFrame, side: int) -> pd.Series:
    if side == 1:
        if "MKT_RISK_ON_FOR_LONGS" in df.columns:
            return df["MKT_RISK_ON_FOR_LONGS"].astype("boolean").fillna(False)
        return pd.Series(False, index=df.index)
    if "MKT_RISK_ON_FOR_SHORTS" in df.columns:
        return df["MKT_RISK_ON_FOR_SHORTS"].astype("boolean").fillna(False)
    if "MKT_RISK_ON_FOR_LONGS" in df.columns:
        return (~df["MKT_RISK_ON_FOR_LONGS"].astype("boolean")).fillna(False)
    return pd.Series(False, index=df.index)


def _market_trend_gate_series(strategy: BaseStrategy, df: pd.DataFrame, side: int) -> pd.Series:
    if side == 1:
        if "MKT_TREND_OK" in df.columns:
            return df["MKT_TREND_OK"].astype("boolean").fillna(False)
        if "SPY_TREND_UP" in df.columns:
            return df["SPY_TREND_UP"].astype("boolean").fillna(False)
        return pd.Series(False, index=df.index)
    if "SPY_TREND_DOWN" in df.columns:
        return df["SPY_TREND_DOWN"].astype("boolean").fillna(False)
    if "MKT_TREND_OK" in df.columns:
        return (~df["MKT_TREND_OK"].astype("boolean")).fillna(False)
    return pd.Series(False, index=df.index)


def _regime_gate_series(strategy: BaseStrategy, df: pd.DataFrame, local_idx: pd.DatetimeIndex) -> pd.Series:
    cfg = getattr(strategy, "regime_gating", None)
    if not isinstance(cfg, dict) or not bool(cfg.get("enabled", False)):
        return pd.Series(True, index=df.index)

    mode = str(cfg.get("mode", "OR")).upper()
    gates_cfg = cfg.get("gates", {}) or {}
    side = 1 if getattr(strategy, "direction", "long") == "long" else -1
    gate_series: List[pd.Series] = []

    atr_cfg = gates_cfg.get("atr_expansion_15m", {}) or {}
    if bool(atr_cfg.get("enabled", False)):
        lookback_days = int(atr_cfg.get("lookback_days", 60) or 60)
        atr_15m, atr_med = _atr_15m_metrics(strategy, df, local_idx, lookback_days)
        gate_series.append((atr_15m >= atr_med).fillna(False))

    risk_cfg = gates_cfg.get("mkt_risk_on", {}) or {}
    if bool(risk_cfg.get("enabled", False)):
        gate_series.append(_market_risk_gate_series(strategy, df, side))

    trend_cfg = gates_cfg.get("mkt_trend_ok", {}) or {}
    if bool(trend_cfg.get("enabled", False)):
        gate_series.append(_market_trend_gate_series(strategy, df, side))

    if not gate_series:
        return pd.Series(True, index=df.index)

    if mode == "AND":
        gate = gate_series[0].copy()
        for series in gate_series[1:]:
            gate &= series
    else:
        gate = gate_series[0].copy()
        for series in gate_series[1:]:
            gate |= series
    return gate.fillna(False).astype(bool)


class _Orb15Breakout:
    """Shared implementation for the long and short ORB strategies."""

    sentiment = "BULLISH"
    direction = "long"
    pattern = "ORB15 Long"
    _side = 1
    take_profit_multiplier = None
    time_stop_days = None

    orb_trade_group = "ORB15"
    orb_max_trades_per_day = 2
    orb_allow_flip_on_stop = True

    orb_start_time = "09:30"
    orb_end_time = "09:45"
    entry_cutoff_time = "11:00"
    session_timezone = "America/New_York"
    index_timezone = None
    market_symbols = ["QQQ", "UVXY"]

    orb_min_range_points = 5.0
    orb_max_range_points = 15.0
    orb_max_range_atr_pct = None
    tp_r_multiple = 2.0
    regime_gating = None
    orb_range = None
    trade_count = None
    tp_management = None
    vwap_entry_filter_enabled = False
    vwap_orb_levels_filter_enabled = False

    skip_dates: List[str] = []
    skip_events_enabled = True
    skip_events_file = "data/news_days.csv"
    skip_events_types = None
    debug_orb = False
    debug_allocation = False
    exit_cutoff_time = None
    orb_exit_time = None

    def _log_debug(
        self,
        local_idx: pd.DatetimeIndex,
        df: pd.DataFrame,
        signal: pd.Series,
        orb_high: pd.Series,
        orb_low: pd.Series,
        range_ok: pd.Series,
        date_ok: pd.Series,
        entry_window_ok: pd.Series,
        gate_ok: Optional[pd.Series] = None,
    ) -> None:
        if not bool(getattr(self, "debug_orb", False)) or df.empty:
            return
        orb_ready = orb_high.notna() & orb_low.notna()
        orb_range = (orb_high - orb_low).where(orb_ready)
        date_series = pd.Series(local_idx.date, index=df.index)
        start_time = _parse_time(getattr(self, "orb_start_time", None), dt.time(9, 30))
        end_time = _parse_time(getattr(self, "orb_end_time", None), dt.time(9, 45))
        cutoff_time = _parse_time(getattr(self, "entry_cutoff_time", None), dt.time(11, 0))
        logger.info(
            "%s ORB debug: bars=%s signals=%s orb_days=%s range_ok=%s gate_ok=%s entry_window=%s date_ok=%s "
            "orb_range[min,max]=[%s,%s] tz=%s local[%s -> %s] window=%s-%s cutoff<%s",
            self.get_name(),
            len(df),
            int(signal.sum()),
            int(orb_ready.groupby(date_series).any().sum()),
            int(range_ok.sum()),
            int(gate_ok.sum()) if gate_ok is not None else None,
            int(entry_window_ok.sum()),
            int(date_ok.sum()),
            round(float(orb_range.min()), 2) if orb_range.notna().any() else None,
            round(float(orb_range.max()), 2) if orb_range.notna().any() else None,
            local_idx.tz,
            local_idx[0],
            local_idx[-1],
            start_time,
            end_time,
            cutoff_time,
        )

    def _local_index(self, df: pd.DataFrame) -> pd.DatetimeIndex:
        index = pd.DatetimeIndex(df.index)
        timezone = getattr(self, "session_timezone", "America/New_York")
        source_timezone = getattr(self, "index_timezone", None)
        if index.tz is None:
            if source_timezone:
                return index.tz_localize(source_timezone).tz_convert(timezone)
            return index.tz_localize(timezone)
        return index.tz_convert(timezone)

    def _orb_components(
        self, df: pd.DataFrame
    ) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
        local_idx = self._local_index(df)
        date_series = pd.Series(local_idx.date, index=df.index)
        time_series = pd.Series(local_idx.time, index=df.index)
        start_time = _parse_time(getattr(self, "orb_start_time", None), dt.time(9, 30))
        end_time = _parse_time(getattr(self, "orb_end_time", None), dt.time(9, 45))
        cutoff_time = _parse_time(getattr(self, "entry_cutoff_time", None), dt.time(11, 0))

        orb_mask = (time_series >= start_time) & (time_series < end_time)
        orb_high = df["High"].where(orb_mask).groupby(date_series).transform("max")
        orb_low = df["Low"].where(orb_mask).groupby(date_series).transform("min")
        orb_range = orb_high - orb_low

        min_range, max_range, max_range_atr_pct = _orb_range_settings(self)
        range_ok = orb_range >= min_range
        if max_range_atr_pct is not None:
            lookback_days = 60
            gating_cfg = getattr(self, "regime_gating", None)
            if isinstance(gating_cfg, dict):
                atr_cfg = (gating_cfg.get("gates", {}) or {}).get("atr_expansion_15m", {}) or {}
                if atr_cfg.get("lookback_days") is not None:
                    lookback_days = int(atr_cfg.get("lookback_days") or lookback_days)
            atr_15m, _ = _atr_15m_metrics(self, df, local_idx, lookback_days)
            if atr_15m.notna().any():
                max_range_series = atr_15m * max_range_atr_pct
                max_range_series = max_range_series.where(max_range_series.notna(), other=0.0)
                max_range_series = max_range_series.where(max_range_series >= min_range, min_range)
                range_ok &= orb_range <= max_range_series
            elif max_range > 0:
                range_ok &= orb_range <= max_range
        elif max_range > 0:
            range_ok &= orb_range <= max_range

        skip_dates = _normalize_skip_dates(getattr(self, "skip_dates", None))
        date_ok = ~date_series.isin(skip_dates)
        entry_window_ok = (time_series >= end_time) & (time_series < cutoff_time)
        return orb_high, orb_low, range_ok.fillna(False), date_ok, entry_window_ok

    def _vwap_filters(
        self,
        df: pd.DataFrame,
        orb_high: pd.Series,
        orb_low: pd.Series,
    ) -> Tuple[pd.Series, pd.Series]:
        close_filter = bool(getattr(self, "vwap_entry_filter_enabled", False))
        levels_filter = bool(getattr(self, "vwap_orb_levels_filter_enabled", False))
        close_ok = pd.Series(True, index=df.index)
        levels_ok = pd.Series(True, index=df.index)
        if not close_filter and not levels_filter:
            return close_ok, levels_ok

        vwap = df["VWAP"] if "VWAP" in df.columns else pd.Series(float("nan"), index=df.index)
        if self._side == 1:
            if close_filter:
                close_ok = (df["Close"] > vwap).fillna(False)
            if levels_filter:
                levels_ok = ((orb_high > vwap) & (orb_low > vwap)).fillna(False)
        else:
            if close_filter:
                close_ok = (df["Close"] < vwap).fillna(False)
            if levels_filter:
                levels_ok = ((orb_high < vwap) & (orb_low < vwap)).fillna(False)
        return close_ok, levels_ok

    def _setup(
        self, df: pd.DataFrame
    ) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
        local_idx = self._local_index(df)
        orb_high, orb_low, range_ok, date_ok, entry_window_ok = self._orb_components(df)
        gate_ok = _regime_gate_series(self, df, local_idx)
        close_vwap_ok, levels_vwap_ok = self._vwap_filters(df, orb_high, orb_low)
        setup_ok = entry_window_ok & date_ok & range_ok & gate_ok & close_vwap_ok & levels_vwap_ok
        return orb_high, orb_low, setup_ok, gate_ok, range_ok, date_ok, entry_window_ok

    def _exit_time_signal(self, df: pd.DataFrame) -> pd.Series:
        exit_time = _effective_exit_cutoff_time(self)
        if not exit_time:
            return pd.Series(False, index=df.index)
        local_idx = self._local_index(df)
        date_series = pd.Series(local_idx.date, index=df.index)
        exit_mask = pd.Series(local_idx.time, index=df.index) >= exit_time
        return (exit_mask & ~exit_mask.groupby(date_series).shift(1, fill_value=False)).astype(bool)

    def effective_breakeven_r(self, default: float) -> float:
        bucket = _tp_bucket(self)
        if bucket and bucket.get("breakeven_buffer_r") is not None:
            return float(bucket["breakeven_buffer_r"])
        return super().effective_breakeven_r(default)

    def effective_breakeven_delay(self, default: int) -> int:
        bucket = _tp_bucket(self)
        if bucket and bucket.get("breakeven_delay_bars") is not None:
            return int(bucket["breakeven_delay_bars"])
        return super().effective_breakeven_delay(default)

    def effective_breakeven_trigger_r(self, default: float) -> float:
        bucket = _tp_bucket(self)
        if bucket and bucket.get("breakeven_trigger_r") is not None:
            return float(bucket["breakeven_trigger_r"])
        return super().effective_breakeven_trigger_r(default)

    def signal(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 3:
            return pd.Series(False, index=df.index)
        orb_high, orb_low, setup_ok, gate_ok, range_ok, date_ok, entry_window_ok = self._setup(df)
        boundary = orb_high if self._side == 1 else orb_low
        signal = setup_ok & ((df["Close"] - boundary) * self._side > 0)
        local_idx = self._local_index(df)
        self._log_debug(
            local_idx,
            df,
            signal,
            orb_high,
            orb_low,
            range_ok,
            date_ok,
            entry_window_ok,
            gate_ok,
        )
        return signal.fillna(False).astype(bool)

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 3:
            return pd.Series(0.0, index=df.index)
        orb_high, orb_low, setup_ok, _, _, _, _ = self._setup(df)
        boundary = orb_high if self._side == 1 else orb_low
        orb_range = (orb_high - orb_low).replace(0, float("nan"))
        score = ((df["Close"] - boundary) * self._side / orb_range).where(setup_ok)
        return score.fillna(0.0).clip(lower=0)

    def exit_signal(self, df: pd.DataFrame) -> pd.Series:
        if df.empty:
            return pd.Series(False, index=df.index)
        return self._exit_time_signal(df)

    def stop_loss_series(self, df: pd.DataFrame) -> Optional[pd.Series]:
        if len(df) < 3:
            return None
        orb_high, orb_low, _, _, _ = self._orb_components(df)
        stop = orb_low if self._side == 1 else orb_high
        return stop.where(self.signal(df)).round(2)

    def get_take_profit(self, df: pd.DataFrame, entry_price: Optional[float] = None) -> Optional[float]:
        if entry_price is None and not df.empty:
            entry_price = float(df["Close"].iloc[-1])
        if entry_price is None:
            return None
        orb_high, orb_low, setup_ok, _, _, _, _ = self._setup(df)
        index = df.index[-1]
        if not bool(setup_ok.loc[index]):
            return None
        stop = (orb_low if self._side == 1 else orb_high).loc[index]
        if pd.isna(stop):
            return None
        risk = (entry_price - float(stop)) * self._side
        if risk <= 0:
            return None
        r_multiple = float(getattr(self, "tp_r_multiple", 2.0) or 2.0)
        return round(entry_price + (self._side * risk * r_multiple), 2)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if df.empty:
            return False, {}
        orb_high, orb_low, setup_ok, _, _, _, _ = self._setup(df)
        index = df.index[-1]
        if not bool(setup_ok.loc[index]):
            return False, {}
        if pd.isna(orb_high.loc[index]) or pd.isna(orb_low.loc[index]):
            return False, {}

        close = float(df["Close"].iloc[-1])
        boundary = float((orb_high if self._side == 1 else orb_low).loc[index])
        if (close - boundary) * self._side <= 0:
            return False, {}
        stop = float((orb_low if self._side == 1 else orb_high).loc[index])
        risk = (close - stop) * self._side
        if risk <= 0:
            return False, {}
        target = close + (self._side * risk * float(getattr(self, "tp_r_multiple", 2.0) or 2.0))
        return True, {
            "pattern": self.pattern,
            "orb_high": round(float(orb_high.loc[index]), 2),
            "orb_low": round(float(orb_low.loc[index]), 2),
            "orb_range": round(float(orb_high.loc[index] - orb_low.loc[index]), 2),
            "stop_loss": round(stop, 2),
            "take_profit": round(target, 2),
            "score": round(float(max(0.0, (close - boundary) * self._side / max(risk, 1e-6))), 2),
            "sentiment": self.sentiment,
        }


class Orb15BreakoutLong(_Orb15Breakout, BaseStrategy):
    """ORB long: close above the opening-range high with a fixed R target."""

    sentiment = "BULLISH"
    direction = "long"
    pattern = "ORB15 Long"
    skip_dates: List[str] = []


class Orb15BreakoutShort(_Orb15Breakout, BaseStrategy):
    """ORB short: close below the opening-range low with a fixed R target."""

    sentiment = "BEARISH"
    direction = "short"
    pattern = "ORB15 Short"
    _side = -1
    skip_dates: List[str] = []
