"""
Chart structure strategies: reversal and continuation patterns.
"""
from typing import Tuple, Dict, Any, Optional, List
import logging

import numpy as np
import pandas as pd
from stock_screener.core.strategy import BaseStrategy


def _pivot_indices(values: np.ndarray, left: int, right: int, mode: str) -> List[int]:
    pivots: List[int] = []
    if len(values) < (left + right + 1):
        return pivots
    for i in range(left, len(values) - right):
        window = values[i - left : i + right + 1]
        if mode == "high":
            if values[i] == np.max(window):
                pivots.append(i)
        else:
            if values[i] == np.min(window):
                pivots.append(i)
    return pivots


def _line_value(idx: int, idx1: int, val1: float, idx2: int, val2: float) -> float:
    if idx2 == idx1:
        return float(val2)
    slope = (val2 - val1) / (idx2 - idx1)
    return float(val1 + slope * (idx - idx1))


def _linear_fit(values: np.ndarray) -> Tuple[float, float]:
    x = np.arange(len(values))
    slope, intercept = np.polyfit(x, values, 1)
    return float(slope), float(intercept)


def _cluster_levels(points: List[Tuple[int, float]], tolerance_pct: float) -> List[Dict[str, Any]]:
    zones: List[Dict[str, Any]] = []
    for idx, price in points:
        matched = False
        for zone in zones:
            if abs(price - zone["center"]) / zone["center"] <= tolerance_pct:
                zone["prices"].append(price)
                zone["count"] += 1
                zone["center"] = float(sum(zone["prices"]) / zone["count"])
                zone["min"] = float(min(zone["min"], price))
                zone["max"] = float(max(zone["max"], price))
                zone["last_idx"] = max(zone["last_idx"], idx)
                matched = True
                break
        if not matched:
            zones.append(
                {
                    "center": float(price),
                    "min": float(price),
                    "max": float(price),
                    "count": 1,
                    "prices": [float(price)],
                    "last_idx": idx,
                }
            )
    return zones


def _safe_divide(numer: np.ndarray, denom: np.ndarray) -> np.ndarray:
    numer_arr = np.asarray(numer, dtype=float)
    denom_arr = np.asarray(denom, dtype=float)
    out = np.full_like(numer_arr, np.nan, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        np.divide(numer_arr, denom_arr, out=out, where=denom_arr > 0)
    return out


def _resample_weekly(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if rule.lower() in ("1w", "w", "weekly"):
        rule = "W-FRI"
    return (
        df.resample(rule)
        .agg(
            {
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum",
            }
        )
        .dropna()
    )


def _pivot_context(values: np.ndarray, left: int, right: int, mode: str) -> Tuple[np.ndarray, np.ndarray]:
    pivots = np.array(_pivot_indices(values, left, right, mode), dtype=int)
    mask = np.zeros(len(values), dtype=bool)
    if pivots.size:
        mask[pivots] = True
    cum = np.cumsum(mask.astype(int))
    return pivots, cum


def _window_counts(cum: np.ndarray, lookback: int) -> np.ndarray:
    counts = cum.copy()
    if lookback < len(cum):
        counts[lookback:] = cum[lookback:] - cum[:-lookback]
    return counts


class PatternSeriesMixin:
    _series_cache_key: Optional[Tuple[int, int]] = None
    _series_cache: Optional[Dict[str, pd.Series]] = None

    def _series_cache_key_for(self, df: pd.DataFrame) -> Tuple[int, int]:
        return (id(df), len(df))

    def _build_context(self, df: pd.DataFrame) -> Dict[str, Any]:
        return {"df": df}

    def _candidate_mask(self, df: pd.DataFrame, context: Dict[str, Any]) -> pd.Series:
        return pd.Series(True, index=df.index)

    def _scan_index(self, context: Dict[str, Any], idx: int) -> Tuple[bool, Dict[str, Any]]:
        df = context["df"]
        return self._scan_pattern(df.iloc[: idx + 1])

    def _compute_series(self, df: pd.DataFrame) -> Dict[str, pd.Series]:
        cache_key = self._series_cache_key_for(df)
        if self._series_cache_key == cache_key and self._series_cache is not None:
            return self._series_cache

        signals = pd.Series(False, index=df.index)
        scores = pd.Series(0.0, index=df.index)
        stops = pd.Series(index=df.index, dtype="float64")
        targets = pd.Series(index=df.index, dtype="float64")
        context = self._build_context(df)
        mask = self._candidate_mask(df, context)
        if mask is None:
            mask = pd.Series(True, index=df.index)
        mask = mask.reindex(df.index).fillna(False).astype(bool)
        candidate_idx = np.flatnonzero(mask.values)

        for i in candidate_idx:
            ok, metrics = self._scan_index(context, int(i))
            if not ok:
                continue
            signals.iat[i] = True
            try:
                scores.iat[i] = float(metrics.get("score", 0.0) or 0.0)
            except Exception:
                scores.iat[i] = 0.0
            stop_loss = metrics.get("stop_loss")
            if stop_loss is not None:
                try:
                    stops.iat[i] = float(stop_loss)
                except Exception:
                    pass
            take_profit = metrics.get("take_profit")
            if take_profit is not None:
                try:
                    targets.iat[i] = float(take_profit)
                except Exception:
                    pass

        self._series_cache_key = cache_key
        self._series_cache = {
            "signals": signals,
            "scores": scores,
            "stops": stops,
            "targets": targets,
        }
        return self._series_cache


class HeadAndShouldersReversal(BaseStrategy, PatternSeriesMixin):
    """
    Head & Shoulders Reversal:
    - Neckline break with volume confirmation
    - Retest entry
    - Stop above right shoulder
    - Target is measured move (head to neckline)
    """
    sentiment = "BEARISH"
    direction = "short"
    stop_multiplier = 2.0
    take_profit_multiplier = None

    lookback = 120
    pivot_left = 3
    pivot_right = 3
    shoulder_tolerance_pct = 0.08
    head_to_shoulder_min_pct = 0.03
    break_buffer_pct = 0.002
    retest_window = 8
    retest_tolerance_pct = 0.003
    volume_lookback = 20
    volume_break_ratio = 1.5
    shoulder_buffer_atr = 0.1

    def _build_context(self, df: pd.DataFrame) -> Dict[str, Any]:
        high = df["High"].values
        low = df["Low"].values
        close = df["Close"].values
        volume = df["Volume"].values
        avg_vol = pd.Series(volume).rolling(self.volume_lookback).mean().shift(1).values
        atr = self.atr_series(df).values
        pivots, pivot_cum = _pivot_context(high, self.pivot_left, self.pivot_right, "high")
        return {
            "df": df,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "avg_vol": avg_vol,
            "atr": atr,
            "pivots": pivots,
            "pivot_cum": pivot_cum,
        }

    def _candidate_mask(self, df: pd.DataFrame, context: Dict[str, Any]) -> pd.Series:
        n = len(df)
        mask = np.zeros(n, dtype=bool)
        if n >= self.lookback:
            counts = _window_counts(context["pivot_cum"], self.lookback)
            mask = counts >= 3
            mask[: self.lookback - 1] = False
        return pd.Series(mask, index=df.index)

    def _scan_index(self, context: Dict[str, Any], idx: int) -> Tuple[bool, Dict[str, Any]]:
        if idx < self.lookback - 1:
            return False, {}
        high = context["high"]
        low = context["low"]
        close = context["close"]
        volume = context["volume"]
        avg_vol = context["avg_vol"]
        atr = context["atr"]
        pivots = context["pivots"]
        if pivots.size < 3:
            return False, {}

        window_start = idx - self.lookback + 1
        start_pos = np.searchsorted(pivots, window_start, side="left")
        end_pos = np.searchsorted(pivots, idx, side="right")
        window_pivots = pivots[start_pos:end_pos]
        if window_pivots.size < 3:
            return False, {}
        p1, p2, p3 = window_pivots[-3:]
        if p3 >= idx:
            return False, {}

        left_shoulder = high[p1]
        head = high[p2]
        right_shoulder = high[p3]
        if head <= left_shoulder * (1 + self.head_to_shoulder_min_pct):
            return False, {}
        if head <= right_shoulder * (1 + self.head_to_shoulder_min_pct):
            return False, {}
        if abs(left_shoulder - right_shoulder) / head > self.shoulder_tolerance_pct:
            return False, {}

        t1 = p1 + int(np.argmin(low[p1 : p2 + 1]))
        t2 = p2 + int(np.argmin(low[p2 : p3 + 1]))
        if not (p1 < t1 < p2 and p2 < t2 < p3):
            return False, {}

        neck1 = low[t1]
        neck2 = low[t2]

        def neckline_at(i: int) -> float:
            return _line_value(i, t1, neck1, t2, neck2)

        break_idx = None
        vol_ratio = None
        start_break = max(p3 + 1, idx - self.retest_window)
        if start_break >= idx:
            return False, {}
        for i in range(start_break, idx):
            neck = neckline_at(i)
            if close[i] < neck * (1 - self.break_buffer_pct):
                avg = avg_vol[i]
                if avg and not np.isnan(avg):
                    vol_ratio = volume[i] / avg
                    if vol_ratio >= self.volume_break_ratio:
                        break_idx = i
                        break
        if break_idx is None:
            return False, {}

        neck_now = neckline_at(idx)
        if high[idx] < neck_now * (1 - self.retest_tolerance_pct):
            return False, {}
        if close[idx] > neck_now:
            return False, {}

        atr_val = atr[idx] if idx < len(atr) else np.nan
        buffer = atr_val * self.shoulder_buffer_atr if not np.isnan(atr_val) else 0.0
        stop_loss = right_shoulder + buffer

        neck_at_head = neckline_at(p2)
        head_height = head - neck_at_head
        target = neck_now - head_height
        score = (head_height / head) * 100 if head else 0.0

        return True, {
            "pattern": "Head & Shoulders",
            "neckline": round(neck_now, 2),
            "head_height": round(head_height, 2),
            "volume_ratio": round(vol_ratio or 0.0, 2),
            "stop_loss": round(stop_loss, 2),
            "take_profit": round(target, 2),
            "score": round(score, 2),
            "sentiment": "BEARISH",
        }

    def _scan_pattern(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if len(df) < self.lookback:
            return False, {}
        hist = df.iloc[-self.lookback:]
        high = hist["High"].values
        low = hist["Low"].values
        close = hist["Close"].values
        volume = hist["Volume"].values

        pivots_high = _pivot_indices(high, self.pivot_left, self.pivot_right, mode="high")
        if len(pivots_high) < 3:
            return False, {}

        p1, p2, p3 = pivots_high[-3:]
        if not (p1 < p2 < p3):
            return False, {}

        left_shoulder = high[p1]
        head = high[p2]
        right_shoulder = high[p3]
        if head <= left_shoulder * (1 + self.head_to_shoulder_min_pct):
            return False, {}
        if head <= right_shoulder * (1 + self.head_to_shoulder_min_pct):
            return False, {}
        if abs(left_shoulder - right_shoulder) / head > self.shoulder_tolerance_pct:
            return False, {}

        t1 = p1 + int(np.argmin(low[p1 : p2 + 1]))
        t2 = p2 + int(np.argmin(low[p2 : p3 + 1]))
        if not (p1 < t1 < p2 and p2 < t2 < p3):
            return False, {}

        neck1 = low[t1]
        neck2 = low[t2]

        def neckline_at(idx: int) -> float:
            return _line_value(idx, t1, neck1, t2, neck2)

        avg_vol = pd.Series(volume).rolling(self.volume_lookback).mean().shift(1).values
        break_idx = None
        vol_ratio = None
        for i in range(p3 + 1, len(hist)):
            neck = neckline_at(i)
            if close[i] < neck * (1 - self.break_buffer_pct):
                avg = avg_vol[i]
                if avg and not np.isnan(avg):
                    vol_ratio = volume[i] / avg if avg else None
                if vol_ratio is not None and vol_ratio >= self.volume_break_ratio:
                    break_idx = i
                    break
        if break_idx is None:
            return False, {}

        current_idx = len(hist) - 1
        if current_idx <= break_idx:
            return False, {}
        if current_idx - break_idx > self.retest_window:
            return False, {}

        neck_now = neckline_at(current_idx)
        if high[current_idx] < neck_now * (1 - self.retest_tolerance_pct):
            return False, {}
        if close[current_idx] > neck_now:
            return False, {}

        atr = self.atr_series(df).iloc[-1]
        buffer = atr * self.shoulder_buffer_atr if pd.notna(atr) else 0.0
        stop_loss = right_shoulder + buffer

        neck_at_head = neckline_at(p2)
        head_height = head - neck_at_head
        target = neck_now - head_height
        score = (head_height / head) * 100 if head else 0.0

        return True, {
            "pattern": "Head & Shoulders",
            "neckline": round(neck_now, 2),
            "head_height": round(head_height, 2),
            "volume_ratio": round(vol_ratio or 0.0, 2),
            "stop_loss": round(stop_loss, 2),
            "take_profit": round(target, 2),
            "score": round(score, 2),
            "sentiment": "BEARISH",
        }

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        return self._scan_pattern(df)

    def signal(self, df: pd.DataFrame) -> pd.Series:
        return self._compute_series(df)["signals"]

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        return self._compute_series(df)["scores"]

    def stop_loss_series(self, df: pd.DataFrame) -> Optional[pd.Series]:
        return self._compute_series(df)["stops"]

    def get_take_profit(self, df: pd.DataFrame, entry_price: Optional[float] = None) -> Optional[float]:
        ok, metrics = self._scan_pattern(df)
        if ok and metrics.get("take_profit") is not None:
            return metrics["take_profit"]
        return super().get_take_profit(df, entry_price=entry_price)


class DoubleTopRsiDivergence(BaseStrategy, PatternSeriesMixin):
    """
    Double Top with RSI divergence (bearish).
    """
    sentiment = "BEARISH"
    direction = "short"
    stop_multiplier = 2.0
    take_profit_multiplier = None

    lookback = 90
    pivot_left = 3
    pivot_right = 3
    peak_tolerance_pct = 0.03
    rsi_length = 14
    rsi_divergence_min = 3.0
    break_buffer_pct = 0.002
    volume_lookback = 20
    volume_break_ratio = 1.5
    peak_buffer_atr = 0.1

    def _build_context(self, df: pd.DataFrame) -> Dict[str, Any]:
        high = df["High"].values
        low = df["Low"].values
        close = df["Close"].values
        volume = df["Volume"].values
        avg_vol = pd.Series(volume).rolling(self.volume_lookback).mean().shift(1).values
        vol_ratio = _safe_divide(volume, avg_vol)
        rsi = self.rsi_series(df, length=self.rsi_length)
        if rsi is None:
            rsi_values = np.full(len(df), np.nan)
        else:
            rsi_values = rsi.values
        atr = self.atr_series(df).values
        pivots, pivot_cum = _pivot_context(high, self.pivot_left, self.pivot_right, "high")
        return {
            "df": df,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "avg_vol": avg_vol,
            "vol_ratio": vol_ratio,
            "rsi": rsi_values,
            "atr": atr,
            "pivots": pivots,
            "pivot_cum": pivot_cum,
        }

    def _candidate_mask(self, df: pd.DataFrame, context: Dict[str, Any]) -> pd.Series:
        n = len(df)
        mask = np.zeros(n, dtype=bool)
        if n >= self.lookback:
            counts = _window_counts(context["pivot_cum"], self.lookback)
            mask = counts >= 2
            mask[: self.lookback - 1] = False
            vol_ratio = context["vol_ratio"]
            mask &= np.where(np.isnan(vol_ratio), False, vol_ratio >= self.volume_break_ratio)
        return pd.Series(mask, index=df.index)

    def _scan_index(self, context: Dict[str, Any], idx: int) -> Tuple[bool, Dict[str, Any]]:
        if idx < self.lookback - 1:
            return False, {}
        high = context["high"]
        low = context["low"]
        close = context["close"]
        vol_ratio = context["vol_ratio"]
        rsi = context["rsi"]
        atr = context["atr"]
        pivots = context["pivots"]
        if pivots.size < 2:
            return False, {}

        window_start = idx - self.lookback + 1
        start_pos = np.searchsorted(pivots, window_start, side="left")
        end_pos = np.searchsorted(pivots, idx, side="right")
        window_pivots = pivots[start_pos:end_pos]
        if window_pivots.size < 2:
            return False, {}
        p1, p2 = window_pivots[-2:]
        if p2 >= idx:
            return False, {}

        h1 = high[p1]
        h2 = high[p2]
        if abs(h1 - h2) / h1 > self.peak_tolerance_pct:
            return False, {}

        neckline = float(low[p1 : p2 + 1].min())
        if close[idx] >= neckline * (1 - self.break_buffer_pct):
            return False, {}

        rsi1 = rsi[p1] if p1 < len(rsi) else np.nan
        rsi2 = rsi[p2] if p2 < len(rsi) else np.nan
        if np.isnan(rsi1) or np.isnan(rsi2):
            return False, {}
        if rsi2 > (rsi1 - self.rsi_divergence_min):
            return False, {}

        if np.isnan(vol_ratio[idx]) or vol_ratio[idx] < self.volume_break_ratio:
            return False, {}

        atr_val = atr[idx] if idx < len(atr) else np.nan
        buffer = atr_val * self.peak_buffer_atr if not np.isnan(atr_val) else 0.0
        stop_loss = h2 + buffer

        height = max(h1, h2) - neckline
        target = neckline - height
        score = (rsi1 - rsi2) + (height / h2 * 100 if h2 else 0.0)

        return True, {
            "pattern": "Double Top",
            "neckline": round(neckline, 2),
            "rsi_divergence": round(rsi1 - rsi2, 2),
            "volume_ratio": round(vol_ratio[idx], 2),
            "stop_loss": round(stop_loss, 2),
            "take_profit": round(target, 2),
            "score": round(score, 2),
            "sentiment": "BEARISH",
        }

    def _scan_pattern(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if len(df) < self.lookback:
            return False, {}
        hist = df.iloc[-self.lookback:]
        high = hist["High"].values
        low = hist["Low"].values
        close = hist["Close"].values
        volume = hist["Volume"].values

        pivots = _pivot_indices(high, self.pivot_left, self.pivot_right, mode="high")
        if len(pivots) < 2:
            return False, {}
        p1, p2 = pivots[-2:]
        if not (p1 < p2):
            return False, {}

        h1 = high[p1]
        h2 = high[p2]
        if abs(h1 - h2) / h1 > self.peak_tolerance_pct:
            return False, {}

        neckline = float(low[p1 : p2 + 1].min())
        if close[-1] >= neckline * (1 - self.break_buffer_pct):
            return False, {}

        rsi = self.rsi_series(hist, length=self.rsi_length)
        if rsi is None or rsi.isna().all():
            return False, {}
        rsi1 = rsi.iloc[p1]
        rsi2 = rsi.iloc[p2]
        if pd.isna(rsi1) or pd.isna(rsi2):
            return False, {}
        if rsi2 > (rsi1 - self.rsi_divergence_min):
            return False, {}

        avg_vol = hist["Volume"].rolling(self.volume_lookback).mean().shift(1).iloc[-1]
        if pd.isna(avg_vol) or avg_vol == 0:
            return False, {}
        vol_ratio = volume[-1] / avg_vol
        if vol_ratio < self.volume_break_ratio:
            return False, {}

        atr = self.atr_series(df).iloc[-1]
        buffer = atr * self.peak_buffer_atr if pd.notna(atr) else 0.0
        stop_loss = h2 + buffer

        height = max(h1, h2) - neckline
        target = neckline - height
        score = (rsi1 - rsi2) + (height / h2 * 100 if h2 else 0.0)

        return True, {
            "pattern": "Double Top",
            "neckline": round(neckline, 2),
            "rsi_divergence": round(rsi1 - rsi2, 2),
            "volume_ratio": round(vol_ratio, 2),
            "stop_loss": round(stop_loss, 2),
            "take_profit": round(target, 2),
            "score": round(score, 2),
            "sentiment": "BEARISH",
        }

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        return self._scan_pattern(df)

    def signal(self, df: pd.DataFrame) -> pd.Series:
        return self._compute_series(df)["signals"]

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        return self._compute_series(df)["scores"]

    def stop_loss_series(self, df: pd.DataFrame) -> Optional[pd.Series]:
        return self._compute_series(df)["stops"]

    def get_take_profit(self, df: pd.DataFrame, entry_price: Optional[float] = None) -> Optional[float]:
        ok, metrics = self._scan_pattern(df)
        if ok and metrics.get("take_profit") is not None:
            return metrics["take_profit"]
        return super().get_take_profit(df, entry_price=entry_price)


class DoubleBottomRsiDivergence(BaseStrategy, PatternSeriesMixin):
    """
    Double Bottom with RSI divergence (bullish).
    """
    sentiment = "BULLISH"
    direction = "long"
    stop_multiplier = 1.5
    take_profit_multiplier = None
    use_take_profit = False
    partial_take_profit_mode = "take_profit"
    partial_take_profit_pct = 0.30
    trailing_enabled = True
    trailing_start_r = 1.25
    trailing_use_mmt = True
    trailing_use_avwap = True
    trailing_use_ema20 = True
    trailing_method = "max"
    trailing_ema_length = 20
    trend_confirm_enabled = True
    trend_confirm_mode = "either"
    trend_confirm_consecutive_closes = 2
    trend_confirm_lookback_days = 5
    trend_trailing_method = "ema"
    trend_trailing_ema_length = 50
    exhaustion_enabled = True
    exhaustion_min_signals = 2
    exhaustion_rsi_level = 70.0
    exhaustion_rsi_rollover_bars = 2
    exhaustion_candle_close_loc = 0.4
    exhaustion_resistance_lookback = 40
    exhaustion_resistance_atr_mult = 0.4
    exhaustion_volume_ratio = 0.9
    exhaustion_stall_atr_mult = 0.25
    exhaustion_require_near_or_mmt = True
    exhaustion_disable_after_trend_confirm = True
    addons_enabled = True
    max_adds = 1
    add_size_pct_initial = 0.5
    add_min_peak_r = 1.5
    add_min_days_since_entry = 3
    add_only_if_above_trail = True
    add_requires_trend_confirm = True
    add_requires_trend_confirm_bars = 2

    lookback = 90
    pivot_left = 3
    pivot_right = 3
    trough_tolerance_pct = 0.03
    rsi_length = 14
    rsi_divergence_min = 3.0
    rsi_exit_threshold = None
    break_buffer_pct = 0.002
    volume_lookback = 20
    volume_break_ratio = 1.5
    trough_buffer_atr = 0.1

    def _build_context(self, df: pd.DataFrame) -> Dict[str, Any]:
        high = df["High"].values
        low = df["Low"].values
        close = df["Close"].values
        volume = df["Volume"].values
        avg_vol = pd.Series(volume).rolling(self.volume_lookback).mean().shift(1).values
        vol_ratio = _safe_divide(volume, avg_vol)
        rsi = self.rsi_series(df, length=self.rsi_length)
        if rsi is None:
            rsi_values = np.full(len(df), np.nan)
        else:
            rsi_values = rsi.values
        atr = self.atr_series(df).values
        pivots, pivot_cum = _pivot_context(low, self.pivot_left, self.pivot_right, "low")
        return {
            "df": df,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "avg_vol": avg_vol,
            "vol_ratio": vol_ratio,
            "rsi": rsi_values,
            "atr": atr,
            "pivots": pivots,
            "pivot_cum": pivot_cum,
        }

    def _candidate_mask(self, df: pd.DataFrame, context: Dict[str, Any]) -> pd.Series:
        n = len(df)
        mask = np.zeros(n, dtype=bool)
        if n >= self.lookback:
            counts = _window_counts(context["pivot_cum"], self.lookback)
            mask = counts >= 2
            mask[: self.lookback - 1] = False
            vol_ratio = context["vol_ratio"]
            mask &= np.where(np.isnan(vol_ratio), False, vol_ratio >= self.volume_break_ratio)
        return pd.Series(mask, index=df.index)

    def _scan_index(self, context: Dict[str, Any], idx: int) -> Tuple[bool, Dict[str, Any]]:
        if idx < self.lookback - 1:
            return False, {}
        high = context["high"]
        low = context["low"]
        close = context["close"]
        vol_ratio = context["vol_ratio"]
        rsi = context["rsi"]
        atr = context["atr"]
        pivots = context["pivots"]
        if pivots.size < 2:
            return False, {}

        window_start = idx - self.lookback + 1
        start_pos = np.searchsorted(pivots, window_start, side="left")
        end_pos = np.searchsorted(pivots, idx, side="right")
        window_pivots = pivots[start_pos:end_pos]
        if window_pivots.size < 2:
            return False, {}
        p1, p2 = window_pivots[-2:]
        if p2 >= idx:
            return False, {}

        l1 = low[p1]
        l2 = low[p2]
        if abs(l1 - l2) / l1 > self.trough_tolerance_pct:
            return False, {}

        neckline = float(high[p1 : p2 + 1].max())
        if close[idx] <= neckline * (1 + self.break_buffer_pct):
            return False, {}

        rsi1 = rsi[p1] if p1 < len(rsi) else np.nan
        rsi2 = rsi[p2] if p2 < len(rsi) else np.nan
        if np.isnan(rsi1) or np.isnan(rsi2):
            return False, {}
        if rsi2 < (rsi1 + self.rsi_divergence_min):
            return False, {}

        if np.isnan(vol_ratio[idx]) or vol_ratio[idx] < self.volume_break_ratio:
            return False, {}

        atr_val = atr[idx] if idx < len(atr) else np.nan
        buffer = atr_val * self.trough_buffer_atr if not np.isnan(atr_val) else 0.0
        stop_loss = l2 - buffer

        height = neckline - min(l1, l2)
        target = neckline + height
        score = (rsi2 - rsi1) + (height / neckline * 100 if neckline else 0.0)

        return True, {
            "pattern": "Double Bottom",
            "neckline": round(neckline, 2),
            "rsi_divergence": round(rsi2 - rsi1, 2),
            "volume_ratio": round(vol_ratio[idx], 2),
            "stop_loss": round(stop_loss, 2),
            "take_profit": round(target, 2),
            "score": round(score, 2),
            "sentiment": "BULLISH",
        }

    def _scan_pattern(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if len(df) < self.lookback:
            return False, {}
        hist = df.iloc[-self.lookback:]
        high = hist["High"].values
        low = hist["Low"].values
        close = hist["Close"].values
        volume = hist["Volume"].values

        pivots = _pivot_indices(low, self.pivot_left, self.pivot_right, mode="low")
        if len(pivots) < 2:
            return False, {}
        p1, p2 = pivots[-2:]
        if not (p1 < p2):
            return False, {}

        l1 = low[p1]
        l2 = low[p2]
        if abs(l1 - l2) / l1 > self.trough_tolerance_pct:
            return False, {}

        neckline = float(high[p1 : p2 + 1].max())
        if close[-1] <= neckline * (1 + self.break_buffer_pct):
            return False, {}

        rsi = self.rsi_series(hist, length=self.rsi_length)
        if rsi is None or rsi.isna().all():
            return False, {}
        rsi1 = rsi.iloc[p1]
        rsi2 = rsi.iloc[p2]
        if pd.isna(rsi1) or pd.isna(rsi2):
            return False, {}
        if rsi2 < (rsi1 + self.rsi_divergence_min):
            return False, {}

        avg_vol = hist["Volume"].rolling(self.volume_lookback).mean().shift(1).iloc[-1]
        if pd.isna(avg_vol) or avg_vol == 0:
            return False, {}
        vol_ratio = volume[-1] / avg_vol
        if vol_ratio < self.volume_break_ratio:
            return False, {}

        atr = self.atr_series(df).iloc[-1]
        buffer = atr * self.trough_buffer_atr if pd.notna(atr) else 0.0
        stop_loss = l2 - buffer

        height = neckline - min(l1, l2)
        target = neckline + height
        score = (rsi2 - rsi1) + (height / neckline * 100 if neckline else 0.0)

        return True, {
            "pattern": "Double Bottom",
            "neckline": round(neckline, 2),
            "rsi_divergence": round(rsi2 - rsi1, 2),
            "volume_ratio": round(vol_ratio, 2),
            "stop_loss": round(stop_loss, 2),
            "take_profit": round(target, 2),
            "score": round(score, 2),
            "sentiment": "BULLISH",
        }

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        return self._scan_pattern(df)

    def signal(self, df: pd.DataFrame) -> pd.Series:
        return self._compute_series(df)["signals"]

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        return self._compute_series(df)["scores"]

    def stop_loss_series(self, df: pd.DataFrame) -> Optional[pd.Series]:
        return self._compute_series(df)["stops"]

    def get_take_profit(self, df: pd.DataFrame, entry_price: Optional[float] = None) -> Optional[float]:
        ok, metrics = self._scan_pattern(df)
        if ok and metrics.get("take_profit") is not None:
            return metrics["take_profit"]
        return super().get_take_profit(df, entry_price=entry_price)

    def exit_signal(self, df: pd.DataFrame) -> pd.Series:
        if self.rsi_exit_threshold is None:
            return pd.Series(False, index=df.index)
        if len(df) < (self.rsi_length + 1):
            return pd.Series(False, index=df.index)
        rsi = self.rsi_series(df, length=self.rsi_length)
        if rsi is None:
            return pd.Series(False, index=df.index)
        signal = rsi > self.rsi_exit_threshold
        return signal.fillna(False).astype(bool)


class TriangleBreakout(BaseStrategy, PatternSeriesMixin):
    """
    Triangle breakout with volume contraction into apex and spike on break.
    """
    sentiment = "BULLISH"
    direction = "long"
    stop_multiplier = 2.0
    take_profit_multiplier = None

    lookback = 60
    min_bars = 20
    contraction_ratio = 0.7
    break_buffer_pct = 0.002
    volume_lookback = 20
    volume_decay_ratio = 0.8
    volume_break_ratio = 1.5
    stop_buffer_atr = 0.1

    def _build_context(self, df: pd.DataFrame) -> Dict[str, Any]:
        high = df["High"].values
        low = df["Low"].values
        close = df["Close"].values
        volume = df["Volume"].values
        avg_vol = pd.Series(volume).rolling(self.volume_lookback).mean().shift(1).values
        vol_ratio = _safe_divide(volume, avg_vol)
        atr = self.atr_series(df).values
        return {
            "df": df,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "avg_vol": avg_vol,
            "vol_ratio": vol_ratio,
            "atr": atr,
        }

    def _candidate_mask(self, df: pd.DataFrame, context: Dict[str, Any]) -> pd.Series:
        n = len(df)
        mask = np.zeros(n, dtype=bool)
        if n >= self.lookback:
            vol_ratio = context["vol_ratio"]
            mask = np.where(np.isnan(vol_ratio), False, vol_ratio >= self.volume_break_ratio)
            mask[: self.lookback - 1] = False
        return pd.Series(mask, index=df.index)

    def _scan_index(self, context: Dict[str, Any], idx: int) -> Tuple[bool, Dict[str, Any]]:
        if idx < self.lookback - 1:
            return False, {}
        high = context["high"]
        low = context["low"]
        close = context["close"]
        volume = context["volume"]
        vol_ratio = context["vol_ratio"]
        atr = context["atr"]

        window_start = idx - self.lookback + 1
        high_w = high[window_start : idx + 1]
        low_w = low[window_start : idx + 1]
        volume_w = volume[window_start : idx + 1]
        if len(high_w) < self.min_bars:
            return False, {}

        slope_high, intercept_high = _linear_fit(high_w)
        slope_low, intercept_low = _linear_fit(low_w)
        if slope_high >= 0 or slope_low <= 0:
            return False, {}

        start_range = (intercept_high - intercept_low)
        end_range = (slope_high * (len(high_w) - 1) + intercept_high) - (slope_low * (len(high_w) - 1) + intercept_low)
        if start_range <= 0 or end_range / start_range > self.contraction_ratio:
            return False, {}

        half = len(high_w) // 2
        avg_vol_early = np.mean(volume_w[:half]) if half > 0 else 0.0
        avg_vol_late = np.mean(volume_w[half:]) if half > 0 else 0.0
        if avg_vol_early <= 0 or avg_vol_late > avg_vol_early * self.volume_decay_ratio:
            return False, {}

        upper_now = slope_high * (len(high_w) - 1) + intercept_high
        lower_now = slope_low * (len(high_w) - 1) + intercept_low
        if close[idx] <= upper_now * (1 + self.break_buffer_pct):
            return False, {}

        if np.isnan(vol_ratio[idx]) or vol_ratio[idx] < self.volume_break_ratio:
            return False, {}

        atr_val = atr[idx] if idx < len(atr) else np.nan
        buffer = atr_val * self.stop_buffer_atr if not np.isnan(atr_val) else 0.0
        stop_loss = lower_now - buffer

        height = float(np.max(high_w) - np.min(low_w))
        target = close[idx] + height
        score = (height / close[idx] * 100 if close[idx] else 0.0) + (vol_ratio[idx] * 2.0)

        return True, {
            "pattern": "Triangle Breakout",
            "height": round(height, 2),
            "volume_ratio": round(vol_ratio[idx], 2),
            "stop_loss": round(stop_loss, 2),
            "take_profit": round(target, 2),
            "score": round(score, 2),
            "sentiment": "BULLISH",
        }

    def _scan_pattern(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if len(df) < self.lookback:
            return False, {}
        hist = df.iloc[-self.lookback:]
        if len(hist) < self.min_bars:
            return False, {}
        high = hist["High"].values
        low = hist["Low"].values
        close = hist["Close"].values
        volume = hist["Volume"].values

        slope_high, intercept_high = _linear_fit(high)
        slope_low, intercept_low = _linear_fit(low)
        if slope_high >= 0 or slope_low <= 0:
            return False, {}

        start_range = (intercept_high - intercept_low)
        end_range = (slope_high * (len(hist) - 1) + intercept_high) - (slope_low * (len(hist) - 1) + intercept_low)
        if start_range <= 0 or end_range / start_range > self.contraction_ratio:
            return False, {}

        half = len(hist) // 2
        avg_vol_early = np.mean(volume[:half]) if half > 0 else 0.0
        avg_vol_late = np.mean(volume[half:]) if half > 0 else 0.0
        if avg_vol_early <= 0 or avg_vol_late > avg_vol_early * self.volume_decay_ratio:
            return False, {}

        upper_now = slope_high * (len(hist) - 1) + intercept_high
        lower_now = slope_low * (len(hist) - 1) + intercept_low
        if close[-1] <= upper_now * (1 + self.break_buffer_pct):
            return False, {}

        avg_vol = hist["Volume"].rolling(self.volume_lookback).mean().shift(1).iloc[-1]
        if pd.isna(avg_vol) or avg_vol == 0:
            return False, {}
        vol_ratio = volume[-1] / avg_vol
        if vol_ratio < self.volume_break_ratio:
            return False, {}

        atr = self.atr_series(df).iloc[-1]
        buffer = atr * self.stop_buffer_atr if pd.notna(atr) else 0.0
        stop_loss = lower_now - buffer

        height = float(np.max(high) - np.min(low))
        target = close[-1] + height
        score = (height / close[-1] * 100 if close[-1] else 0.0) + (vol_ratio * 2.0)

        return True, {
            "pattern": "Triangle Breakout",
            "height": round(height, 2),
            "volume_ratio": round(vol_ratio, 2),
            "stop_loss": round(stop_loss, 2),
            "take_profit": round(target, 2),
            "score": round(score, 2),
            "sentiment": "BULLISH",
        }

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        return self._scan_pattern(df)

    def signal(self, df: pd.DataFrame) -> pd.Series:
        return self._compute_series(df)["signals"]

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        return self._compute_series(df)["scores"]

    def stop_loss_series(self, df: pd.DataFrame) -> Optional[pd.Series]:
        return self._compute_series(df)["stops"]

    def get_take_profit(self, df: pd.DataFrame, entry_price: Optional[float] = None) -> Optional[float]:
        ok, metrics = self._scan_pattern(df)
        if ok and metrics.get("take_profit") is not None:
            return metrics["take_profit"]
        return super().get_take_profit(df, entry_price=entry_price)


class FlagPennantContinuation(BaseStrategy, PatternSeriesMixin):
    """
    Flag/Pennant continuation after a strong flagpole move.
    """
    sentiment = "BULLISH"
    direction = "long"
    stop_multiplier = 2.0
    take_profit_multiplier = None

    flagpole_lookback = 12
    flag_lookback = 10
    min_flagpole_return = 0.08
    retrace_min = 0.38
    retrace_max = 0.5
    break_buffer_pct = 0.002
    volume_decay_ratio = 0.7
    volume_break_ratio = 1.5
    volume_lookback = 20
    stop_buffer_atr = 0.1

    def _build_context(self, df: pd.DataFrame) -> Dict[str, Any]:
        high = df["High"].values
        low = df["Low"].values
        close = df["Close"].values
        volume = df["Volume"].values
        avg_vol = pd.Series(volume).rolling(self.volume_lookback).mean().shift(1).values
        vol_ratio = _safe_divide(volume, avg_vol)
        atr = self.atr_series(df).values
        return {
            "df": df,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "avg_vol": avg_vol,
            "vol_ratio": vol_ratio,
            "atr": atr,
        }

    def _candidate_mask(self, df: pd.DataFrame, context: Dict[str, Any]) -> pd.Series:
        n = len(df)
        needed = self.flagpole_lookback + self.flag_lookback
        mask = np.zeros(n, dtype=bool)
        if n >= needed:
            vol_ratio = context["vol_ratio"]
            mask = np.where(np.isnan(vol_ratio), False, vol_ratio >= self.volume_break_ratio)
            mask[: needed - 1] = False
        return pd.Series(mask, index=df.index)

    def _scan_index(self, context: Dict[str, Any], idx: int) -> Tuple[bool, Dict[str, Any]]:
        needed = self.flagpole_lookback + self.flag_lookback
        if idx < needed - 1:
            return False, {}
        high = context["high"]
        low = context["low"]
        close = context["close"]
        volume = context["volume"]
        vol_ratio = context["vol_ratio"]
        atr = context["atr"]

        pole_start = idx - needed + 1
        pole_end = idx - self.flag_lookback + 1
        if pole_start < 0 or pole_end <= pole_start:
            return False, {}

        pole_start_close = close[pole_start]
        pole_end_close = close[pole_end - 1]
        if pole_start_close <= 0:
            return False, {}
        pole_return = (pole_end_close - pole_start_close) / pole_start_close
        if pole_return < self.min_flagpole_return:
            return False, {}

        flag_high = np.max(high[pole_end: idx + 1])
        flag_low = np.min(low[pole_end: idx + 1])

        pole_height = pole_end_close - pole_start_close
        retrace = (pole_end_close - flag_low) / pole_height if pole_height > 0 else 0.0
        if retrace < self.retrace_min or retrace > self.retrace_max:
            return False, {}

        pole_vol = np.mean(volume[pole_start:pole_end])
        flag_vol = np.mean(volume[pole_end: idx + 1])
        if pole_vol <= 0 or flag_vol > pole_vol * self.volume_decay_ratio:
            return False, {}

        if close[idx] <= flag_high * (1 + self.break_buffer_pct):
            return False, {}
        if np.isnan(vol_ratio[idx]) or vol_ratio[idx] < self.volume_break_ratio:
            return False, {}

        atr_val = atr[idx] if idx < len(atr) else np.nan
        buffer = atr_val * self.stop_buffer_atr if not np.isnan(atr_val) else 0.0
        stop_loss = flag_low - buffer
        target = close[idx] + pole_height
        score = (pole_return * 100) + (vol_ratio[idx] * 2.0)

        return True, {
            "pattern": "Flag/Pennant",
            "pole_return_pct": round(pole_return * 100, 2),
            "retrace_pct": round(retrace * 100, 2),
            "volume_ratio": round(vol_ratio[idx], 2),
            "stop_loss": round(stop_loss, 2),
            "take_profit": round(target, 2),
            "score": round(score, 2),
            "sentiment": "BULLISH",
        }

    def _scan_pattern(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        needed = self.flagpole_lookback + self.flag_lookback
        if len(df) < needed:
            return False, {}

        pole_start = -needed
        pole_end = -self.flag_lookback
        pole_start_close = df["Close"].iloc[pole_start]
        pole_end_close = df["Close"].iloc[pole_end]
        if pole_start_close <= 0:
            return False, {}
        pole_return = (pole_end_close - pole_start_close) / pole_start_close
        if pole_return < self.min_flagpole_return:
            return False, {}

        flag = df.iloc[-self.flag_lookback:]
        flag_high = flag["High"].max()
        flag_low = flag["Low"].min()

        pole_height = pole_end_close - pole_start_close
        retrace = (pole_end_close - flag_low) / pole_height if pole_height > 0 else 0.0
        if retrace < self.retrace_min or retrace > self.retrace_max:
            return False, {}

        pole_vol = df["Volume"].iloc[pole_start:pole_end].mean()
        flag_vol = flag["Volume"].mean()
        if pole_vol <= 0 or flag_vol > pole_vol * self.volume_decay_ratio:
            return False, {}

        current_close = df["Close"].iloc[-1]
        if current_close <= flag_high * (1 + self.break_buffer_pct):
            return False, {}

        avg_vol = df["Volume"].rolling(self.volume_lookback).mean().shift(1).iloc[-1]
        if pd.isna(avg_vol) or avg_vol == 0:
            return False, {}
        vol_ratio = df["Volume"].iloc[-1] / avg_vol
        if vol_ratio < self.volume_break_ratio:
            return False, {}

        atr = self.atr_series(df).iloc[-1]
        buffer = atr * self.stop_buffer_atr if pd.notna(atr) else 0.0
        stop_loss = flag_low - buffer
        target = current_close + pole_height
        score = (pole_return * 100) + (vol_ratio * 2.0)

        return True, {
            "pattern": "Flag/Pennant",
            "pole_return_pct": round(pole_return * 100, 2),
            "retrace_pct": round(retrace * 100, 2),
            "volume_ratio": round(vol_ratio, 2),
            "stop_loss": round(stop_loss, 2),
            "take_profit": round(target, 2),
            "score": round(score, 2),
            "sentiment": "BULLISH",
        }

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        return self._scan_pattern(df)

    def signal(self, df: pd.DataFrame) -> pd.Series:
        return self._compute_series(df)["signals"]

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        return self._compute_series(df)["scores"]

    def stop_loss_series(self, df: pd.DataFrame) -> Optional[pd.Series]:
        return self._compute_series(df)["stops"]

    def get_take_profit(self, df: pd.DataFrame, entry_price: Optional[float] = None) -> Optional[float]:
        ok, metrics = self._scan_pattern(df)
        if ok and metrics.get("take_profit") is not None:
            return metrics["take_profit"]
        return super().get_take_profit(df, entry_price=entry_price)


class SupportResistanceBreakRetest(BaseStrategy, PatternSeriesMixin):
    """
    Support/Resistance zone break with retest entry.
    """
    sentiment = "BULLISH"
    direction = "long"
    stop_multiplier = 2.0
    take_profit_multiplier = None

    lookback = 120
    pivot_left = 3
    pivot_right = 3
    zone_tolerance_pct = 0.005
    min_touches = 3
    break_buffer_pct = 0.002
    retest_window = 8
    volume_lookback = 20
    volume_break_ratio = 1.5

    def _build_context(self, df: pd.DataFrame) -> Dict[str, Any]:
        high = df["High"].values
        low = df["Low"].values
        close = df["Close"].values
        volume = df["Volume"].values
        avg_vol = pd.Series(volume).rolling(self.volume_lookback).mean().shift(1).values
        pivots, pivot_cum = _pivot_context(high, self.pivot_left, self.pivot_right, "high")
        return {
            "df": df,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "avg_vol": avg_vol,
            "pivots": pivots,
            "pivot_cum": pivot_cum,
        }

    def _candidate_mask(self, df: pd.DataFrame, context: Dict[str, Any]) -> pd.Series:
        n = len(df)
        mask = np.zeros(n, dtype=bool)
        if n >= self.lookback:
            counts = _window_counts(context["pivot_cum"], self.lookback)
            mask = counts >= self.min_touches
            mask[: self.lookback - 1] = False
        return pd.Series(mask, index=df.index)

    def _scan_index(self, context: Dict[str, Any], idx: int) -> Tuple[bool, Dict[str, Any]]:
        if idx < self.lookback - 1:
            return False, {}
        high = context["high"]
        low = context["low"]
        close = context["close"]
        volume = context["volume"]
        avg_vol = context["avg_vol"]
        pivots = context["pivots"]
        if pivots.size < self.min_touches:
            return False, {}

        window_start = idx - self.lookback + 1
        start_pos = np.searchsorted(pivots, window_start, side="left")
        end_pos = np.searchsorted(pivots, idx, side="right")
        window_pivots = pivots[start_pos:end_pos]
        if window_pivots.size < self.min_touches:
            return False, {}

        points = [(int(p), float(high[p])) for p in window_pivots]
        zones = _cluster_levels(points, self.zone_tolerance_pct)
        current_close = close[idx]
        zone = self._select_zone(zones, current_close)
        if zone is None:
            return False, {}

        zone_low = zone["center"] * (1 - self.zone_tolerance_pct)
        zone_high = zone["center"] * (1 + self.zone_tolerance_pct)

        break_idx = None
        vol_ratio = None
        start_break = max(window_start, idx - self.retest_window)
        for i in range(start_break, idx + 1):
            if close[i] > zone_high * (1 + self.break_buffer_pct):
                avg = avg_vol[i]
                if avg and not np.isnan(avg):
                    vol_ratio = volume[i] / avg if avg else None
                if vol_ratio is not None and vol_ratio >= self.volume_break_ratio:
                    break_idx = i
                    break
        if break_idx is None or break_idx >= idx:
            return False, {}
        if idx - break_idx > self.retest_window:
            return False, {}
        if low[idx] > zone_high or close[idx] < zone_high:
            return False, {}

        next_zone = self._next_zone(zones, zone["center"])
        target = next_zone["center"] if next_zone else None
        score = (zone["count"] * 2.0) + (vol_ratio or 0.0)

        return True, {
            "pattern": "S/R Break Retest",
            "zone": round(zone["center"], 2),
            "touches": zone["count"],
            "volume_ratio": round(vol_ratio or 0.0, 2),
            "stop_loss": round(zone_low, 2),
            "take_profit": round(float(target), 2) if target else None,
            "score": round(score, 2),
            "sentiment": "BULLISH",
        }

    def _select_zone(self, zones: List[Dict[str, Any]], current_price: float) -> Optional[Dict[str, Any]]:
        candidates = [z for z in zones if z["count"] >= self.min_touches and z["center"] <= current_price]
        if not candidates:
            return None
        candidates.sort(key=lambda z: (z["center"], z["count"]))
        return candidates[-1]

    def _next_zone(self, zones: List[Dict[str, Any]], above_price: float) -> Optional[Dict[str, Any]]:
        higher = [z for z in zones if z["center"] > above_price]
        if not higher:
            return None
        higher.sort(key=lambda z: z["center"])
        return higher[0]

    def _scan_pattern(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if len(df) < self.lookback:
            return False, {}
        hist = df.iloc[-self.lookback:]
        high = hist["High"].values
        low = hist["Low"].values
        close = hist["Close"].values
        volume = hist["Volume"].values

        pivots = _pivot_indices(high, self.pivot_left, self.pivot_right, mode="high")
        points = [(idx, float(high[idx])) for idx in pivots]
        zones = _cluster_levels(points, self.zone_tolerance_pct)
        current_close = close[-1]
        zone = self._select_zone(zones, current_close)
        if zone is None:
            return False, {}

        zone_low = zone["center"] * (1 - self.zone_tolerance_pct)
        zone_high = zone["center"] * (1 + self.zone_tolerance_pct)

        avg_vol = pd.Series(volume).rolling(self.volume_lookback).mean().shift(1).values
        break_idx = None
        vol_ratio = None
        start_idx = max(0, len(hist) - self.retest_window - 1)
        for i in range(start_idx, len(hist)):
            if close[i] > zone_high * (1 + self.break_buffer_pct):
                avg = avg_vol[i]
                if avg and not np.isnan(avg):
                    vol_ratio = volume[i] / avg if avg else None
                if vol_ratio is not None and vol_ratio >= self.volume_break_ratio:
                    break_idx = i
                    break
        if break_idx is None:
            return False, {}

        current_idx = len(hist) - 1
        if current_idx <= break_idx:
            return False, {}
        if current_idx - break_idx > self.retest_window:
            return False, {}
        if low[current_idx] > zone_high or close[current_idx] < zone_high:
            return False, {}

        next_zone = self._next_zone(zones, zone["center"])
        target = next_zone["center"] if next_zone else None
        score = (zone["count"] * 2.0) + (vol_ratio or 0.0)

        return True, {
            "pattern": "S/R Break Retest",
            "zone": round(zone["center"], 2),
            "touches": zone["count"],
            "volume_ratio": round(vol_ratio or 0.0, 2),
            "stop_loss": round(zone_low, 2),
            "take_profit": round(float(target), 2) if target else None,
            "score": round(score, 2),
            "sentiment": "BULLISH",
        }

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        return self._scan_pattern(df)

    def signal(self, df: pd.DataFrame) -> pd.Series:
        return self._compute_series(df)["signals"]

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        return self._compute_series(df)["scores"]

    def stop_loss_series(self, df: pd.DataFrame) -> Optional[pd.Series]:
        return self._compute_series(df)["stops"]

    def get_take_profit(self, df: pd.DataFrame, entry_price: Optional[float] = None) -> Optional[float]:
        ok, metrics = self._scan_pattern(df)
        if ok and metrics.get("take_profit") is not None:
            return metrics["take_profit"]
        return super().get_take_profit(df, entry_price=entry_price)


class LongTermSupportResistanceBreakRetest(BaseStrategy, PatternSeriesMixin):
    """
    Weekly S/R level construction with daily break/retest execution.
    """
    sentiment = "BULLISH"
    direction = "long"
    stop_multiplier = 2.0
    take_profit_multiplier = None

    levels_timeframe = "1w"
    levels_shift = 1
    levels_lookback_weeks = 130
    pivot_left = 3
    pivot_right = 3
    min_touches = 2
    cluster_atr_mult = 0.8
    zone_tolerance_pct = 0.005
    max_levels = 12

    break_buffer_pct = 0.002
    break_confirm_closes = 1
    retest_max_days = 10
    retest_confirm_closes = 1

    arm_across_regime = False
    arm_expiry_days = 30
    arm_requires_risk_on_entry = True
    arm_invalidate_on_close_below_level = True
    arm_invalidate_buffer_pct = 0.001
    arm_record_break_metrics = True

    min_level_distance_atr = 1.5
    stop_atr_mult = 1.0
    max_stop_atr = 3.0
    time_stop_days = 25

    volume_lookback = 20
    volume_break_ratio = 0.0

    def _build_context(self, df: pd.DataFrame) -> Dict[str, Any]:
        high = df["High"].values
        low = df["Low"].values
        close = df["Close"].values
        volume = df["Volume"].values
        avg_vol = pd.Series(volume).rolling(self.volume_lookback).mean().shift(1).values
        atr_daily = self.atr_series(df).values

        weekly = _resample_weekly(df, self.levels_timeframe)
        if weekly.empty:
            weekly_high = np.array([])
            weekly_atr = np.array([])
            weekly_pivots = np.array([], dtype=int)
            weekly_pivot_cum = np.array([], dtype=int)
            week_pos = np.full(len(df), -1, dtype=int)
        else:
            weekly_high = weekly["High"].values
            weekly_atr_series = self.atr_series(weekly)
            weekly_atr = weekly_atr_series.values if weekly_atr_series is not None else np.full(len(weekly), np.nan)
            weekly_pivots, weekly_pivot_cum = _pivot_context(weekly_high, self.pivot_left, self.pivot_right, "high")
            week_pos = weekly.index.searchsorted(df.index, side="right") - 1

        risk_on = None
        if "REGIME_RISK_ON" in df.columns:
            risk_on = df["REGIME_RISK_ON"].values
        return {
            "df": df,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "avg_vol": avg_vol,
            "atr_daily": atr_daily,
            "weekly_high": weekly_high,
            "weekly_atr": weekly_atr,
            "weekly_pivots": weekly_pivots,
            "weekly_pivot_cum": weekly_pivot_cum,
            "week_pos": week_pos,
            "risk_on": risk_on,
            "pending": None,
        }

    def _candidate_mask(self, df: pd.DataFrame, context: Dict[str, Any]) -> pd.Series:
        n = len(df)
        mask = np.zeros(n, dtype=bool)
        pivots = context["weekly_pivots"]
        pivot_cum = context["weekly_pivot_cum"]
        week_pos = context["week_pos"]
        if pivots.size == 0 or pivot_cum.size == 0:
            return pd.Series(mask, index=df.index)
        for i in range(n):
            current_week = week_pos[i]
            if current_week < self.levels_shift:
                continue
            valid_week = current_week - self.levels_shift
            start_week = max(0, valid_week - self.levels_lookback_weeks + 1)
            count = pivot_cum[valid_week]
            if start_week > 0:
                count -= pivot_cum[start_week - 1]
            if count >= self.min_touches:
                mask[i] = True
        return pd.Series(mask, index=df.index)

    def _scan_index(self, context: Dict[str, Any], idx: int) -> Tuple[bool, Dict[str, Any]]:
        low = context["low"]
        close = context["close"]
        volume = context["volume"]
        avg_vol = context["avg_vol"]
        atr_daily = context["atr_daily"]
        weekly_high = context["weekly_high"]
        weekly_atr = context["weekly_atr"]
        weekly_pivots = context["weekly_pivots"]
        week_pos = context["week_pos"]
        if idx < 1 or weekly_pivots.size < self.min_touches:
            return False, {}

        current_week = week_pos[idx]
        if current_week < self.levels_shift:
            return False, {}
        valid_week = current_week - self.levels_shift
        if valid_week < 0 or valid_week >= len(weekly_high):
            return False, {}

        lookback_start = max(0, valid_week - self.levels_lookback_weeks + 1)
        start_pos = np.searchsorted(weekly_pivots, lookback_start, side="left")
        end_pos = np.searchsorted(weekly_pivots, valid_week + 1, side="right")
        window_pivots = weekly_pivots[start_pos:end_pos]
        if window_pivots.size < self.min_touches:
            return False, {}

        points = [(int(p), float(weekly_high[p])) for p in window_pivots]
        tolerance_pct = self.zone_tolerance_pct
        atr_week = weekly_atr[valid_week] if valid_week < len(weekly_atr) else np.nan
        current_close = close[idx]
        if not np.isnan(atr_week) and current_close:
            tolerance_pct = max(tolerance_pct, (self.cluster_atr_mult * atr_week) / current_close)
        zones = _cluster_levels(points, tolerance_pct)
        if self.max_levels and len(zones) > self.max_levels:
            zones.sort(key=lambda z: (z["count"], z["last_idx"]), reverse=True)
            zones = zones[: self.max_levels]

        zone = self._select_zone(zones, current_close)
        if zone is None:
            return False, {}

        next_zone = self._next_zone(zones, zone["center"])
        atr_val = atr_daily[idx] if idx < len(atr_daily) else np.nan
        if next_zone and self.min_level_distance_atr and not np.isnan(atr_val):
            if (next_zone["center"] - zone["center"]) < (self.min_level_distance_atr * atr_val):
                return False, {}

        pending = context.get("pending")
        risk_on_series = context.get("risk_on")
        risk_on = True
        if risk_on_series is not None and idx < len(risk_on_series):
            risk_on = bool(risk_on_series[idx])

        if pending is not None:
            armed_date = pending["armed_date"]
            days_armed = (context["df"].index[idx] - armed_date).days
            expiry_days = int(getattr(self, "arm_expiry_days", self.retest_max_days) or self.retest_max_days)
            if expiry_days and days_armed > expiry_days:
                pending = None
            elif self.arm_invalidate_on_close_below_level:
                if close[idx] < pending["armed_level"] * (1 - self.arm_invalidate_buffer_pct):
                    pending = None

        break_now = False
        vol_ratio = None
        break_level = zone["center"] * (1 + tolerance_pct) * (1 + self.break_buffer_pct)
        if close[idx] > break_level:
            if self.break_confirm_closes > 1:
                start_confirm = idx - self.break_confirm_closes + 1
                if start_confirm >= 0 and np.all(close[start_confirm : idx + 1] > break_level):
                    break_now = True
            else:
                break_now = True
            if break_now and self.volume_break_ratio > 0:
                avg = avg_vol[idx]
                if avg and not np.isnan(avg):
                    vol_ratio = volume[idx] / avg if avg else None
                if vol_ratio is None or vol_ratio < self.volume_break_ratio:
                    break_now = False

        if break_now:
            pending = {
                "armed_idx": idx,
                "armed_date": context["df"].index[idx],
                "armed_level": float(zone["center"]),
                "armed_break_price": float(close[idx]),
                "armed_regime_on": bool(risk_on),
                "zone_count": int(zone["count"]),
                "tolerance_pct": float(tolerance_pct),
                "volume_ratio": float(vol_ratio or 0.0),
            }

        if pending is None or idx <= pending["armed_idx"]:
            context["pending"] = pending
            return False, {}

        days_armed = (context["df"].index[idx] - pending["armed_date"]).days
        expiry_days = int(getattr(self, "arm_expiry_days", self.retest_max_days) or self.retest_max_days)
        if expiry_days and days_armed > expiry_days:
            context["pending"] = None
            return False, {}

        zone_low = pending["armed_level"] * (1 - pending["tolerance_pct"])
        zone_high = pending["armed_level"] * (1 + pending["tolerance_pct"])
        if low[idx] > zone_high or close[idx] < zone_high:
            context["pending"] = pending
            return False, {}
        if self.retest_confirm_closes > 1:
            start_retest = idx - self.retest_confirm_closes + 1
            if start_retest < 0 or not np.all(close[start_retest : idx + 1] >= zone_high):
                context["pending"] = pending
                return False, {}

        if self.arm_requires_risk_on_entry and not risk_on:
            context["pending"] = pending
            return False, {}

        stop_loss = zone_low
        if not np.isnan(atr_val):
            stop_loss = zone_low - (self.stop_atr_mult * atr_val)
            if self.max_stop_atr and ((current_close - stop_loss) / atr_val) > self.max_stop_atr:
                context["pending"] = None
                return False, {}

        target = next_zone["center"] if next_zone else None
        score = (pending["zone_count"] * 2.0) + (pending.get("volume_ratio") or 0.0)

        metrics = {
            "pattern": "Long-Term S/R Break Retest",
            "zone": round(pending["armed_level"], 2),
            "touches": pending["zone_count"],
            "volume_ratio": round(pending.get("volume_ratio", 0.0), 2),
            "stop_loss": round(stop_loss, 2),
            "take_profit": round(float(target), 2) if target else None,
            "score": round(score, 2),
            "sentiment": "BULLISH",
        }
        if self.arm_record_break_metrics:
            metrics.update(
                {
                    "armed_from_date": pending["armed_date"].isoformat(),
                    "armed_days": int(days_armed),
                    "armed_level": round(pending["armed_level"], 2),
                    "armed_break_price": round(pending["armed_break_price"], 2),
                    "armed_regime_at_break": "risk_on" if pending["armed_regime_on"] else "risk_off",
                }
            )

        context["pending"] = None
        return True, metrics

    def _select_zone(self, zones: List[Dict[str, Any]], current_price: float) -> Optional[Dict[str, Any]]:
        candidates = [z for z in zones if z["count"] >= self.min_touches and z["center"] <= current_price]
        if not candidates:
            return None
        candidates.sort(key=lambda z: (z["center"], z["count"]))
        return candidates[-1]

    def _next_zone(self, zones: List[Dict[str, Any]], above_price: float) -> Optional[Dict[str, Any]]:
        higher = [z for z in zones if z["center"] > above_price]
        if not higher:
            return None
        higher.sort(key=lambda z: z["center"])
        return higher[0]

    def _scan_pattern(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if df.empty:
            return False, {}
        context = self._build_context(df)
        return self._scan_index(context, len(df) - 1)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        return self._scan_pattern(df)

    def signal(self, df: pd.DataFrame) -> pd.Series:
        return self._compute_series(df)["signals"]

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        return self._compute_series(df)["scores"]

    def stop_loss_series(self, df: pd.DataFrame) -> Optional[pd.Series]:
        return self._compute_series(df)["stops"]

    def get_take_profit(self, df: pd.DataFrame, entry_price: Optional[float] = None) -> Optional[float]:
        ok, metrics = self._scan_pattern(df)
        if ok and metrics.get("take_profit") is not None:
            return metrics["take_profit"]
        return super().get_take_profit(df, entry_price=entry_price)


class MarketAlignedStructureBreak(BaseStrategy, PatternSeriesMixin):
    """
    Market-aligned daily structure break with retest + candle confirmation.
    """
    sentiment = "BULLISH"
    direction = "long"
    stop_multiplier = 2.0
    take_profit_multiplier = None

    market_symbols = ["SPY", "QQQ", "UVXY"]

    lookback = 180
    pivot_left = 3
    pivot_right = 3
    min_touches = 2
    zone_tolerance_pct = 0.005
    max_levels = 12

    break_buffer_pct = 0.001
    break_confirm_closes = 1
    retest_max_days = 10
    retest_confirm_closes = 1
    retest_band_atr = 0.0

    trend_pivot_left = 2
    trend_pivot_right = 2
    ema_fast = 10
    ema_mid = 20
    ema_slow = 50

    min_room_pct = 0.02
    min_rr = 2.0
    fib_extensions: List[float] = [1.272, 1.618]
    fib_lookback = 120
    fib_min_swing_pct = 0.02
    target_buffer_pct = 0.0
    prefer_fib_if_rr_better = False

    stop_min_pct = 0.015
    stop_max_pct = 0.025

    volume_lookback = 20
    volume_break_ratio = 1.0
    volume_retest_max_ratio = 1.0
    debug_counters = False
    debug_rr_samples_max = 50

    def __init__(self) -> None:
        super().__init__()
        self._debug_counts: Dict[str, int] = {}
        self._debug_seen_keys: set = set()
        self._debug_rr_samples: List[Dict[str, Any]] = []

    @staticmethod
    def _debug_keys() -> Tuple[str, ...]:
        return (
            "seen",
            "market_ok",
            "has_ils_level",
            "break_detected",
            "armed_setups",
            "retest_in_window",
            "rr_room_ok",
            "entry_signal_emitted",
        )

    def reset_debug_counts(self) -> None:
        self._debug_counts = {key: 0 for key in self._debug_keys()}
        self._debug_seen_keys = set()
        self._debug_rr_samples = []

    def _debug_count(self, key: str, inc: int = 1) -> None:
        if not getattr(self, "debug_counters", False):
            return
        if not self._debug_counts:
            self.reset_debug_counts()
        self._debug_counts[key] = self._debug_counts.get(key, 0) + inc

    def _debug_track_market(self, df: pd.DataFrame, market_ok: Optional[np.ndarray]) -> None:
        if not getattr(self, "debug_counters", False):
            return
        cache_key = (id(df), len(df))
        if cache_key in self._debug_seen_keys:
            return
        self._debug_seen_keys.add(cache_key)
        self._debug_count("seen", len(df))
        if market_ok is None:
            self._debug_count("market_ok", len(df))
        else:
            self._debug_count("market_ok", int(np.count_nonzero(market_ok)))

    def _debug_add_rr_sample(self, sample: Dict[str, Any]) -> None:
        if not getattr(self, "debug_counters", False):
            return
        if len(self._debug_rr_samples) >= self.debug_rr_samples_max:
            return
        self._debug_rr_samples.append(sample)

    def log_debug_summary(self) -> None:
        if not getattr(self, "debug_counters", False):
            return
        if not self._debug_counts:
            return
        counts = self._debug_counts
        seen = counts.get("seen", 0)
        market_ok = counts.get("market_ok", 0)
        market_pct = (market_ok / seen * 100.0) if seen else 0.0
        logger = logging.getLogger(__name__)
        logger.info(
            "MarketAlignedStructureBreak debug: seen=%s market_ok=%s (%.1f%%) "
            "has_ils_level=%s break_detected=%s armed_setups=%s retest_in_window=%s "
            "rr_room_ok=%s entry_signal_emitted=%s",
            seen,
            market_ok,
            market_pct,
            counts.get("has_ils_level", 0),
            counts.get("break_detected", 0),
            counts.get("armed_setups", 0),
            counts.get("retest_in_window", 0),
            counts.get("rr_room_ok", 0),
            counts.get("entry_signal_emitted", 0),
        )
        if self._debug_rr_samples:
            logger.info(
                "MarketAlignedStructureBreak rr/room failures (sample %s/%s):",
                len(self._debug_rr_samples),
                self.debug_rr_samples_max,
            )
            for sample in self._debug_rr_samples:
                logger.info(
                    "MASB rr_fail: date=%s entry=%.4f stop=%.4f target=%.4f rr=%.2f "
                    "room_pct=%.2f risk=%.4f reward=%.4f zone=%.4f next_zone=%s mode=%s",
                    sample["date"],
                    sample["entry"],
                    sample["stop"],
                    sample["target"],
                    sample["rr"],
                    sample["room_pct"],
                    sample["risk"],
                    sample["reward"],
                    sample["zone"],
                    sample["next_zone"],
                    sample["target_mode"],
                )

    def _build_context(self, df: pd.DataFrame) -> Dict[str, Any]:
        open_px = df["Open"].values
        high = df["High"].values
        low = df["Low"].values
        close = df["Close"].values
        volume = df["Volume"].values
        avg_vol = pd.Series(volume).rolling(self.volume_lookback).mean().shift(1).values
        atr = self.atr_series(df).values

        pivots_high, pivot_cum = _pivot_context(high, self.pivot_left, self.pivot_right, "high")
        pivots_low = np.array(_pivot_indices(low, self.pivot_left, self.pivot_right, "low"), dtype=int)

        ema_fast = self.ema_series(df, length=self.ema_fast).values
        ema_mid = self.ema_series(df, length=self.ema_mid).values
        ema_slow = self.ema_series(df, length=self.ema_slow).values

        trend_state = self._trend_state(high, low)
        trend_up = (
            (trend_state == 1)
            & (close > ema_slow)
            & (ema_fast > ema_mid)
            & (ema_mid > ema_slow)
        )

        market_ok = None
        if "MKT_RISK_ON_FOR_LONGS" in df.columns:
            market_ok = df["MKT_RISK_ON_FOR_LONGS"].values
        self._debug_track_market(df, market_ok)

        return {
            "df": df,
            "open": open_px,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "avg_vol": avg_vol,
            "atr": atr,
            "pivots_high": pivots_high,
            "pivot_cum": pivot_cum,
            "pivots_low": pivots_low,
            "ema_fast": ema_fast,
            "ema_mid": ema_mid,
            "ema_slow": ema_slow,
            "trend_up": trend_up,
            "market_ok": market_ok,
            "pending": None,
        }

    def _candidate_mask(self, df: pd.DataFrame, context: Dict[str, Any]) -> pd.Series:
        n = len(df)
        mask = np.zeros(n, dtype=bool)
        if n >= self.lookback:
            counts = _window_counts(context["pivot_cum"], self.lookback)
            mask = counts >= self.min_touches
            mask[: self.lookback - 1] = False
        return pd.Series(mask, index=df.index)

    def _scan_index(self, context: Dict[str, Any], idx: int) -> Tuple[bool, Dict[str, Any]]:
        if idx < self.lookback - 1:
            return False, {}

        df = context["df"]
        high = context["high"]
        low = context["low"]
        close = context["close"]
        volume = context["volume"]
        avg_vol = context["avg_vol"]
        pivots_high = context["pivots_high"]
        pivots_low = context["pivots_low"]

        pending = context.get("pending")
        if pending is None:
            window_start = idx - self.lookback + 1
            start_pos = np.searchsorted(pivots_high, window_start, side="left")
            end_pos = np.searchsorted(pivots_high, idx + 1, side="right")
            window_pivots = pivots_high[start_pos:end_pos]
            if window_pivots.size < self.min_touches:
                return False, {}

            points = [(int(p), float(high[p])) for p in window_pivots]
            zones = _cluster_levels(points, self.zone_tolerance_pct)
            if self.max_levels and len(zones) > self.max_levels:
                zones.sort(key=lambda z: (z["count"], z["last_idx"]), reverse=True)
                zones = zones[: self.max_levels]
            zone = self._select_zone(zones, close[idx])
            if zone is None:
                return False, {}
            self._debug_count("has_ils_level")

            zone_low = zone["center"] * (1 - self.zone_tolerance_pct)
            zone_high = zone["center"] * (1 + self.zone_tolerance_pct)
            break_level = zone_high * (1 + self.break_buffer_pct)
            break_now = close[idx] > break_level
            if break_now and self.break_confirm_closes > 1:
                start_confirm = idx - self.break_confirm_closes + 1
                if start_confirm < 0 or not np.all(close[start_confirm : idx + 1] > break_level):
                    break_now = False
            vol_ratio = None
            if break_now and self.volume_break_ratio > 0:
                avg = avg_vol[idx]
                if avg and not np.isnan(avg):
                    vol_ratio = volume[idx] / avg if avg else None
                if vol_ratio is None or vol_ratio < self.volume_break_ratio:
                    break_now = False

            if not break_now:
                return False, {}

            self._debug_count("break_detected")
            self._debug_count("armed_setups")
            zones_above = sorted([float(z["center"]) for z in zones if z["center"] > zone["center"]])
            swing_low = self._fib_swing_low(low, pivots_low, idx, zone["center"])

            pending = {
                "break_idx": idx,
                "zone_center": float(zone["center"]),
                "zone_low": float(zone_low),
                "zone_high": float(zone_high),
                "touches": int(zone["count"]),
                "zones_above": zones_above,
                "next_zone": float(zones_above[0]) if zones_above else None,
                "swing_low": swing_low,
                "volume_ratio": float(vol_ratio or 0.0),
            }
            context["pending"] = pending
            return False, {}

        days_since_break = idx - pending["break_idx"]
        if days_since_break > self.retest_max_days:
            context["pending"] = None
            return False, {}

        if "retest_idx" in pending:
            if idx > pending["retest_idx"] + 1:
                context["pending"] = None
                return False, {}
            if idx != pending["retest_idx"] + 1:
                return False, {}

            if close[idx] <= pending["retest_close"]:
                context["pending"] = None
                return False, {}

            market_ok = True
            if context["market_ok"] is not None:
                market_ok = bool(context["market_ok"][idx])
            if not market_ok:
                context["pending"] = None
                return False, {}

            if not context["trend_up"][idx]:
                context["pending"] = None
                return False, {}

            entry_price = float(close[idx])
            stop_loss = min(pending["retest_low"], entry_price * (1 - self.stop_min_pct))
            stop_dist_pct = (entry_price - stop_loss) / entry_price if entry_price else 0.0
            if stop_dist_pct < self.stop_min_pct:
                stop_loss = entry_price * (1 - self.stop_min_pct)
                stop_dist_pct = (entry_price - stop_loss) / entry_price
            if stop_dist_pct <= 0 or stop_dist_pct > self.stop_max_pct:
                context["pending"] = None
                return False, {}

            risk = entry_price - stop_loss
            if risk <= 0:
                context["pending"] = None
                return False, {}

            target_ils = None
            zones_above = pending.get("zones_above") or []
            for level in zones_above:
                if level > entry_price * (1 + self.target_buffer_pct):
                    target_ils = float(level)
                    break

            target_fib = None
            swing_low = pending.get("swing_low")
            if swing_low is None:
                swing_low = self._fib_swing_low(low, pivots_low, idx, pending["zone_center"])
            if swing_low is not None:
                height = pending["zone_center"] - swing_low
                if height > 0:
                    for ext in self.fib_extensions:
                        candidate = pending["zone_center"] + (ext * height)
                        if candidate > entry_price:
                            target_fib = candidate
                            break

            target = None
            target_mode = "next_ils"
            if target_ils is None and target_fib is None:
                context["pending"] = None
                return False, {}
            if target_ils is None:
                target = target_fib
                target_mode = "fib_ext"
            elif target_fib is None:
                target = target_ils
            else:
                rr_ils = (target_ils - entry_price) / risk if risk else 0.0
                rr_fib = (target_fib - entry_price) / risk if risk else 0.0
                if self.prefer_fib_if_rr_better and rr_fib > rr_ils:
                    target = target_fib
                    target_mode = "fib_ext"
                else:
                    target = target_ils

            room_pct = (target - entry_price) / entry_price if entry_price else 0.0
            rr = (target - entry_price) / risk if entry_price else 0.0
            risk = entry_price - stop_loss
            required_room_pct = self.min_room_pct
            if entry_price:
                required_room_pct = max(self.min_room_pct, (self.min_rr * risk / entry_price))
            if room_pct < required_room_pct or rr < self.min_rr:
                risk = entry_price - stop_loss
                reward = target - entry_price
                self._debug_add_rr_sample(
                    {
                        "date": df.index[idx],
                        "entry": entry_price,
                        "stop": stop_loss,
                        "target": float(target),
                        "rr": rr,
                        "room_pct": room_pct * 100,
                        "risk": risk,
                        "reward": reward,
                        "zone": pending["zone_center"],
                        "next_zone": pending["next_zone"],
                        "target_mode": target_mode,
                    }
                )
                context["pending"] = None
                return False, {}

            score = (rr * 10.0) + pending["touches"]
            self._debug_count("rr_room_ok")
            self._debug_count("entry_signal_emitted")
            context["pending"] = None
            return True, {
                "pattern": "Market Aligned Structure Break",
                "zone": round(pending["zone_center"], 2),
                "touches": pending["touches"],
                "stop_loss": round(stop_loss, 2),
                "take_profit": round(float(target), 2),
                "rr": round(rr, 2),
                "room_pct": round(room_pct * 100, 2),
                "target_mode": target_mode,
                "score": round(score, 2),
                "sentiment": "BULLISH",
            }

        zone_high = pending["zone_high"]
        atr_val = context["atr"][idx] if idx < len(context["atr"]) else np.nan
        band = 0.0
        if self.retest_band_atr and not np.isnan(atr_val):
            band = float(self.retest_band_atr) * float(atr_val)
        retest_low = zone_high - band
        retest_high = zone_high + band
        if low[idx] <= retest_high and close[idx] >= retest_low:
            self._debug_count("retest_in_window")
            vol_ratio = None
            if self.volume_retest_max_ratio > 0:
                avg = avg_vol[idx]
                if avg and not np.isnan(avg):
                    vol_ratio = volume[idx] / avg if avg else None
                if vol_ratio is not None and vol_ratio > self.volume_retest_max_ratio:
                    return False, {}

            if not self._bullish_reversal(context, idx):
                return False, {}

            pending["retest_idx"] = idx
            pending["retest_low"] = float(low[idx])
            pending["retest_close"] = float(close[idx])
            context["pending"] = pending
            return False, {}

        return False, {}

    def _select_zone(self, zones: List[Dict[str, Any]], current_price: float) -> Optional[Dict[str, Any]]:
        candidates = [z for z in zones if z["count"] >= self.min_touches and z["center"] <= current_price]
        if not candidates:
            return None
        candidates.sort(key=lambda z: (z["center"], z["count"]))
        return candidates[-1]

    def _next_zone(self, zones: List[Dict[str, Any]], above_price: float) -> Optional[Dict[str, Any]]:
        higher = [z for z in zones if z["center"] > above_price]
        if not higher:
            return None
        higher.sort(key=lambda z: z["center"])
        return higher[0]

    def _fib_swing_low(
        self,
        low: np.ndarray,
        pivots_low: np.ndarray,
        idx: int,
        zone_center: float,
    ) -> Optional[float]:
        if zone_center <= 0:
            return None
        start = max(0, idx - self.fib_lookback + 1)
        candidates = pivots_low[pivots_low >= start] if pivots_low.size else np.array([], dtype=int)
        if candidates.size:
            for p in candidates[::-1]:
                lval = float(low[int(p)])
                if (zone_center - lval) / zone_center >= self.fib_min_swing_pct:
                    return lval
        if idx >= start:
            return float(np.min(low[start : idx + 1]))
        return None

    def _trend_state(self, high: np.ndarray, low: np.ndarray) -> np.ndarray:
        pivots_high = np.array(_pivot_indices(high, self.trend_pivot_left, self.trend_pivot_right, "high"), dtype=int)
        pivots_low = np.array(_pivot_indices(low, self.trend_pivot_left, self.trend_pivot_right, "low"), dtype=int)
        mask_high = np.zeros(len(high), dtype=bool)
        mask_low = np.zeros(len(low), dtype=bool)
        if pivots_high.size:
            mask_high[pivots_high] = True
        if pivots_low.size:
            mask_low[pivots_low] = True

        state = np.zeros(len(high), dtype=int)
        last_highs: list = []
        last_lows: list = []
        for i in range(len(high)):
            if mask_high[i]:
                last_highs.append(float(high[i]))
                if len(last_highs) > 2:
                    last_highs.pop(0)
            if mask_low[i]:
                last_lows.append(float(low[i]))
                if len(last_lows) > 2:
                    last_lows.pop(0)
            if len(last_highs) == 2 and len(last_lows) == 2:
                if last_highs[1] > last_highs[0] and last_lows[1] > last_lows[0]:
                    state[i] = 1
                elif last_highs[1] < last_highs[0] and last_lows[1] < last_lows[0]:
                    state[i] = -1
        state_series = pd.Series(state, index=pd.RangeIndex(len(state))).replace(0, np.nan).ffill().fillna(0)
        return state_series.to_numpy(dtype=int)

    def _bullish_reversal(self, context: Dict[str, Any], idx: int) -> bool:
        if idx < 1:
            return False
        open_px = context["open"]
        high = context["high"]
        low = context["low"]
        close = context["close"]

        body = abs(close[idx] - open_px[idx])
        if body == 0:
            body = 1e-6
        upper_wick = high[idx] - max(open_px[idx], close[idx])
        lower_wick = min(open_px[idx], close[idx]) - low[idx]
        hammer = (close[idx] >= open_px[idx]) and (lower_wick >= 2 * body) and (upper_wick <= body)

        prev_bear = close[idx - 1] < open_px[idx - 1]
        bullish_engulf = (
            close[idx] > open_px[idx]
            and prev_bear
            and close[idx] >= open_px[idx - 1]
            and open_px[idx] <= close[idx - 1]
        )
        return bool(hammer or bullish_engulf)

    def _scan_pattern(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if df.empty:
            return False, {}
        context = self._build_context(df)
        return self._scan_index(context, len(df) - 1)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        return self._scan_pattern(df)

    def signal(self, df: pd.DataFrame) -> pd.Series:
        return self._compute_series(df)["signals"]

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        return self._compute_series(df)["scores"]

    def stop_loss_series(self, df: pd.DataFrame) -> Optional[pd.Series]:
        return self._compute_series(df)["stops"]

    def get_take_profit(self, df: pd.DataFrame, entry_price: Optional[float] = None) -> Optional[float]:
        ok, metrics = self._scan_pattern(df)
        if ok and metrics.get("take_profit") is not None:
            return metrics["take_profit"]
        return super().get_take_profit(df, entry_price=entry_price)
