import math
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from .base import RegimeBuildResult, RegimeModel


class MultiFactorRegimeModelV1(RegimeModel):
    name = "multi_factor_v1"

    def required_symbols(self, config: Dict[str, Any]) -> list[str]:
        symbols = (config or {}).get("symbols") or {}
        candidates = [
            symbols.get("market"),
            symbols.get("vol"),
            symbols.get("credit"),
            symbols.get("rates"),
        ]
        return [s for s in candidates if s]

    def _is_intraday(self, interval: str) -> bool:
        if not interval:
            return False
        interval = str(interval).lower()
        return interval.endswith("m") or interval.endswith("h")

    def _normalize_index(self, index: pd.Index) -> pd.DatetimeIndex:
        idx = pd.DatetimeIndex(index)
        return idx.normalize()

    def _align_index(self, index: pd.DatetimeIndex, target: pd.DatetimeIndex) -> pd.DatetimeIndex:
        if index.tz is None and target.tz is not None:
            return index.tz_localize(target.tz)
        if index.tz is not None and target.tz is None:
            return index.tz_convert(None)
        return index

    def _daily_frame(self, df: Optional[pd.DataFrame], interval: str) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        if not self._is_intraday(interval):
            return df.copy()
        cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        if not cols:
            return pd.DataFrame()
        data = df[cols].copy()
        agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
        if "Volume" in data.columns:
            agg["Volume"] = "sum"
        daily = data.resample("1D").agg(agg)
        daily = daily.dropna(how="all")
        return daily

    def _close_series(self, df: Optional[pd.DataFrame], index: pd.DatetimeIndex) -> pd.Series:
        if df is None or df.empty or "Close" not in df.columns:
            return pd.Series(index=index, dtype="float64")
        close = df["Close"].copy()
        close.index = self._normalize_index(close.index)
        close = close[~close.index.duplicated(keep="last")]
        return close.reindex(index)

    def _safe_float(self, value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def build(
        self,
        data_map: Dict[str, pd.DataFrame],
        master_index: pd.Index,
        interval: str,
        config: Dict[str, Any],
        start_date: Optional[pd.Timestamp] = None,
        end_date: Optional[pd.Timestamp] = None,
    ) -> RegimeBuildResult:
        params = (config or {}).get("params") or {}
        symbols = (config or {}).get("symbols") or {}
        market_symbol = symbols.get("market")
        vol_symbol = symbols.get("vol")
        credit_symbol = symbols.get("credit")
        rates_symbol = symbols.get("rates")

        market_df = self._daily_frame(data_map.get(market_symbol), interval)
        vol_df = self._daily_frame(data_map.get(vol_symbol), interval)
        credit_df = self._daily_frame(data_map.get(credit_symbol), interval)
        rates_df = self._daily_frame(data_map.get(rates_symbol), interval)

        daily_index = self._normalize_index(market_df.index)
        if daily_index.empty:
            regime = pd.DataFrame(index=master_index)
            regime["risk_on"] = True
            regime["severity"] = "risk_on"
            regime["hedge_pct"] = 0.0
            regime["risk_on_streak"] = 0
            regime["sma200_slope_pct"] = 0.0
            regime["sma200_distance_pct"] = 0.0
            regime["risk_on_prob"] = 1.0
            regime["trend_state"] = "UP"
            regime["vol_state"] = "NORMAL"
            regime["stress"] = 0.0
            regime["regime_usable"] = False
            diagnostics = {
                "market_symbol": market_symbol,
                "missing_counts": {market_symbol: len(master_index)},
                "no_overlap": [market_symbol],
                "bad_days": len(master_index),
                "bad_days_pct": 1.0,
                "total_days": len(master_index),
            }
            return RegimeBuildResult(regime, diagnostics=diagnostics)

        market_close = self._close_series(market_df, daily_index)
        vol_close = self._close_series(vol_df, daily_index)
        credit_close = self._close_series(credit_df, daily_index)
        rates_close = self._close_series(rates_df, daily_index)

        trend_cfg = params.get("trend") or {}
        ma_fast = int(self._safe_float(trend_cfg.get("ma_fast", 50), 50))
        ma_slow = int(self._safe_float(trend_cfg.get("ma_slow", 200), 200))
        slope_window = int(self._safe_float(trend_cfg.get("slope_window", 20), 20))

        fast_ma = market_close.rolling(ma_fast).mean()
        slow_ma = market_close.rolling(ma_slow).mean()
        trend_up = (market_close > slow_ma) & (fast_ma > slow_ma)
        trend_down = (market_close < slow_ma) & (fast_ma < slow_ma)
        market_available = market_close.notna()
        trend_state = pd.Series("RANGE", index=daily_index)
        trend_state[trend_up] = "UP"
        trend_state[trend_down] = "DOWN"
        trend_score = pd.Series(0.5, index=daily_index)
        trend_score[trend_up] = 1.0
        trend_score[trend_down] = 0.0
        trend_score = trend_score.where(market_available)
        trend_state = trend_state.where(market_available, "unknown")

        sma200 = market_close.rolling(200).mean()
        sma200_slope = (sma200 - sma200.shift(slope_window)) / sma200.shift(slope_window)
        sma200_dist = (market_close - sma200).abs() / sma200

        returns = market_close.pct_change()
        vol_cfg = params.get("vol") or {}
        rv_lookback = int(self._safe_float(vol_cfg.get("rv_lookback", 20), 20))
        z_lookback = int(self._safe_float(vol_cfg.get("z_lookback", 252), 252))
        rv = returns.rolling(rv_lookback).std() * math.sqrt(252)
        rv_mean = rv.rolling(z_lookback).mean()
        rv_std = rv.rolling(z_lookback).std()
        rv_z = (rv - rv_mean) / rv_std

        vol_signal = rv_z.copy()
        if vol_close.notna().any():
            vol_mean = vol_close.rolling(z_lookback).mean()
            vol_std = vol_close.rolling(z_lookback).std()
            vol_z = (vol_close - vol_mean) / vol_std
            vol_signal = (rv_z * 0.5) + (vol_z * 0.5)

        vol_state = pd.Series("NORMAL", index=daily_index)
        vol_state[vol_signal <= -0.5] = "LOW"
        vol_state[(vol_signal > 0.5) & (vol_signal <= 1.5)] = "HIGH"
        vol_state[vol_signal > 1.5] = "CRISIS"

        vol_score = pd.Series(0.8, index=daily_index)
        vol_score[vol_state == "LOW"] = 1.0
        vol_score[vol_state == "HIGH"] = 0.4
        vol_score[vol_state == "CRISIS"] = 0.0

        vol_state = vol_state.where(market_available, "unknown")
        vol_score = vol_score.where(market_available)
        stress = ((vol_signal - 0.5) / 2.0).clip(lower=0.0, upper=1.0)
        stress = stress.where(market_available)

        credit_score = pd.Series(index=daily_index, dtype="float64")
        credit_cfg = params.get("credit") or {}
        credit_ma = int(self._safe_float(credit_cfg.get("ma_length", 50), 50))
        if credit_close.notna().any():
            credit_series = credit_close.copy()
            if rates_close.notna().any():
                ratio = credit_close / rates_close
                ratio_ma = ratio.rolling(credit_ma).mean()
                credit_raw = (ratio - ratio_ma) / ratio_ma
                credit_score = 0.5 + 0.5 * np.tanh(credit_raw * 5.0)
            else:
                credit_ma_series = credit_series.rolling(credit_ma).mean()
                credit_raw = (credit_series - credit_ma_series) / credit_ma_series
                credit_score = 0.5 + 0.5 * np.tanh(credit_raw * 5.0)

        rates_score = pd.Series(index=daily_index, dtype="float64")
        rates_cfg = params.get("rates") or {}
        rates_ma = int(self._safe_float(rates_cfg.get("ma_length", 50), 50))
        if rates_close.notna().any():
            rates_ma_series = rates_close.rolling(rates_ma).mean()
            rates_raw = (rates_close - rates_ma_series) / rates_ma_series
            rates_score = 0.5 - 0.5 * np.tanh(rates_raw * 5.0)

        weights_cfg = (params.get("risk_on_prob") or {}).get("weights") or {}
        weights = {
            "trend": self._safe_float(weights_cfg.get("trend", 0.5), 0.5),
            "vol": self._safe_float(weights_cfg.get("vol", 0.3), 0.3),
            "credit": self._safe_float(weights_cfg.get("credit", 0.2), 0.2),
            "rates": self._safe_float(weights_cfg.get("rates", 0.0), 0.0),
        }

        score_df = pd.DataFrame({
            "trend": trend_score,
            "vol": vol_score,
            "credit": credit_score,
            "rates": rates_score,
        })
        weight_series = pd.Series(weights)
        available_mask = score_df.notna()
        component_counts = available_mask.sum(axis=1)
        weighted_scores = score_df.mul(weight_series, axis=1)
        weight_sums = (available_mask * weight_series).sum(axis=1)
        risk_on_prob = weighted_scores.sum(axis=1).div(weight_sums).replace([np.inf, -np.inf], np.nan)
        risk_on_prob = risk_on_prob.clip(lower=0.0, upper=1.0)

        min_components = int(self._safe_float((config or {}).get("missing_data_policy", {}).get("min_components", 2), 2))
        regime_usable = market_available & (component_counts >= min_components) & (weight_sums > 0)
        risk_on_prob = risk_on_prob.where(regime_usable, np.nan)

        hysteresis_on = self._safe_float(params.get("hysteresis_on", 0.65), 0.65)
        hysteresis_off = self._safe_float(params.get("hysteresis_off", 0.55), 0.55)
        risk_on_flags = []
        current = False
        for prob in risk_on_prob.fillna(np.nan).tolist():
            if prob is None or not math.isfinite(prob):
                current = False
            else:
                if current:
                    if prob <= hysteresis_off:
                        current = False
                else:
                    if prob >= hysteresis_on:
                        current = True
            risk_on_flags.append(current)
        risk_on = pd.Series(risk_on_flags, index=daily_index)

        risk_on_threshold = self._safe_float(params.get("risk_on_threshold", 0.60), 0.60)
        severity = pd.Series("risk_on", index=daily_index)
        off_mask = (~risk_on) & regime_usable
        severity[off_mask & (risk_on_prob <= risk_on_threshold)] = "confirmed"
        severity[off_mask & (risk_on_prob <= 0.4)] = "crisis"
        severity[off_mask & (risk_on_prob > 0.4)] = "mild"
        severity[~regime_usable] = "unknown"

        hedge_max_pct = self._safe_float(params.get("hedge_max_pct", 0.50), 0.50)
        hedge_pct = (1.0 - risk_on_prob.fillna(0.0)) * hedge_max_pct
        hedge_pct = hedge_pct.where(regime_usable, 0.0)

        streak = []
        streak_val = 0
        for is_on in risk_on.fillna(False).tolist():
            if is_on:
                streak_val += 1
            else:
                streak_val = 0
            streak.append(streak_val)
        risk_on_streak = pd.Series(streak, index=daily_index)

        regime_daily = pd.DataFrame({
            "risk_on": risk_on,
            "severity": severity,
            "hedge_pct": hedge_pct,
            "risk_on_streak": risk_on_streak,
            "sma200_slope_pct": sma200_slope,
            "sma200_distance_pct": sma200_dist,
            "risk_on_prob": risk_on_prob,
            "trend_state": trend_state,
            "vol_state": vol_state,
            "stress": stress,
            "regime_usable": regime_usable,
        })

        intraday_mode = params.get("intraday_mode", "daily_ffill_shift1")
        if self._is_intraday(interval) and intraday_mode == "daily_ffill_shift1":
            regime_daily = regime_daily.shift(1)

        master_index = pd.DatetimeIndex(master_index)
        regime_daily.index = self._align_index(regime_daily.index, master_index)
        regime = regime_daily.reindex(master_index, method="ffill")
        regime["risk_on"] = regime["risk_on"].astype("boolean").fillna(False).astype(bool)
        regime["risk_on_prob"] = pd.to_numeric(regime["risk_on_prob"], errors="coerce")
        regime["regime_usable"] = (
            regime["regime_usable"].astype("boolean").fillna(False).astype(bool)
        )

        diagnostics = self._build_diagnostics(
            market_symbol,
            vol_symbol,
            credit_symbol,
            rates_symbol,
            market_close,
            vol_close,
            credit_close,
            rates_close,
            regime_usable,
            start_date,
            end_date,
        )

        return RegimeBuildResult(regime, diagnostics=diagnostics)

    def _build_diagnostics(
        self,
        market_symbol: Optional[str],
        vol_symbol: Optional[str],
        credit_symbol: Optional[str],
        rates_symbol: Optional[str],
        market_close: pd.Series,
        vol_close: pd.Series,
        credit_close: pd.Series,
        rates_close: pd.Series,
        regime_usable: pd.Series,
        start_date: Optional[pd.Timestamp],
        end_date: Optional[pd.Timestamp],
    ) -> Dict[str, Any]:
        symbols = {
            market_symbol: market_close,
            vol_symbol: vol_close,
            credit_symbol: credit_close,
            rates_symbol: rates_close,
        }
        idx = market_close.index
        if start_date is not None:
            start_date = pd.Timestamp(start_date).normalize()
            if idx.tz is not None and start_date.tzinfo is None:
                start_date = start_date.tz_localize(idx.tz)
        if end_date is not None:
            end_date = pd.Timestamp(end_date).normalize()
            if idx.tz is not None and end_date.tzinfo is None:
                end_date = end_date.tz_localize(idx.tz)
        window = idx
        if start_date is not None:
            window = window[window >= start_date]
        if end_date is not None:
            window = window[window <= end_date]
        window = pd.DatetimeIndex(window)

        missing_counts: Dict[str, int] = {}
        no_overlap: list[str] = []
        for symbol, series in symbols.items():
            if not symbol:
                continue
            series = series.reindex(window)
            missing = series.isna().sum()
            missing_counts[symbol] = int(missing)
            if series.dropna().empty:
                no_overlap.append(symbol)

        usable_window = regime_usable.reindex(window).fillna(False)
        bad_days = int((~usable_window).sum())
        total_days = int(len(window))
        bad_days_pct = (bad_days / total_days) if total_days else 0.0

        return {
            "market_symbol": market_symbol,
            "missing_counts": missing_counts,
            "no_overlap": no_overlap,
            "bad_days": bad_days,
            "bad_days_pct": bad_days_pct,
            "total_days": total_days,
        }
