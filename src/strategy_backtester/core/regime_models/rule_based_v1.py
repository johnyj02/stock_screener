from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
import pandas_ta as ta

from stock_screener.core.regime_features import compute_daily_regimes, _daily_frame
from .base import RegimeBuildResult, RegimeModel


class RuleBasedRegimeModelV1(RegimeModel):
    name = "rule_based_v1"

    def required_symbols(self, config: Dict[str, Any]) -> list[str]:
        symbols = (config or {}).get("symbols") or {}
        candidates = [
            symbols.get("market"),
            symbols.get("vol"),
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

    def _safe_float(self, value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _safe_int(self, value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _pick_vol_symbol(self, data_map: Dict[str, pd.DataFrame], config: Dict[str, Any]) -> Optional[str]:
        symbols = (config or {}).get("symbols") or {}
        vol_symbol = symbols.get("vol")
        if vol_symbol:
            return vol_symbol
        for candidate in ("^VIX", "VIX", "VXX", "VIXY", "UVXY"):
            if candidate in data_map:
                return candidate
        return None

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
        market_symbol = symbols.get("market") or (config or {}).get("market_symbol") or (config or {}).get("regime_symbol")
        if not market_symbol:
            market_symbol = next(iter(data_map.keys()), None)
        vol_symbol = self._pick_vol_symbol(data_map, config)

        daily_market = _daily_frame(data_map.get(market_symbol), interval)
        if daily_market.empty:
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
                "no_overlap": [market_symbol] if market_symbol else [],
                "bad_days": len(master_index),
                "bad_days_pct": 1.0 if master_index is not None else 0.0,
                "total_days": len(master_index),
            }
            return RegimeBuildResult(regime, diagnostics=diagnostics)

        daily_features = compute_daily_regimes(
            data_map=data_map,
            market_symbol=market_symbol,
            vol_symbol=vol_symbol,
            interval=interval,
            config=params,
        )

        daily_index = self._normalize_index(daily_market.index)
        daily_features = daily_features.reindex(daily_index)
        close = daily_market["Close"].reindex(daily_index)

        trend_cfg = (params.get("features") or params).get("trend", {}) if isinstance(params, dict) else {}
        sma_slow = self._safe_int(trend_cfg.get("sma_slow", 200), 200)
        slope_window = self._safe_int(trend_cfg.get("slope_window", 20), 20)

        sma200 = ta.sma(close, length=sma_slow)
        if sma200 is None:
            sma200 = close.rolling(sma_slow).mean()
        sma200_slope = (sma200 - sma200.shift(slope_window)) / sma200.shift(slope_window)
        sma200_dist = (close - sma200).abs() / sma200

        risk_on_mr = daily_features.get("MKT_RISK_ON_MEAN_REVERSION")
        if risk_on_mr is None:
            risk_on_mr = pd.Series(False, index=daily_index)
        risk_on = risk_on_mr.fillna(False)

        trend_label = daily_features.get("MKT_TREND_LABEL")
        vol_label = daily_features.get("MKT_VOL_LABEL")
        risk_label = daily_features.get("MKT_RISK_LABEL")

        trend_state = pd.Series("RANGE", index=daily_index)
        if trend_label is not None:
            trend_state[trend_label.isin(["strong_up", "weak_up"])] = "UP"
            trend_state[trend_label.isin(["strong_down", "weak_down"])] = "DOWN"
            trend_state[trend_label == "transition"] = "TRANSITION"

        vol_state = pd.Series("NORMAL", index=daily_index)
        if vol_label is not None:
            vol_state[vol_label.isin(["low", "contracting"])] = "LOW"
            vol_state[vol_label.isin(["high", "expanding"])] = "HIGH"
            vol_state[vol_label == "crisis"] = "CRISIS"

        stress = pd.Series(0.0, index=daily_index)
        if vol_label is not None:
            stress[vol_label == "high"] = 0.6
            stress[vol_label == "expanding"] = 0.7
            stress[vol_label == "crisis"] = 1.0

        if risk_label is not None:
            stress[risk_label == "risk_off"] = np.maximum(stress[risk_label == "risk_off"], 0.5)
            stress[risk_label == "crisis"] = 1.0

        trend_score = daily_features.get("MKT_TREND_SCORE")
        vol_score = daily_features.get("MKT_VOL_SCORE")
        liq_score = daily_features.get("MKT_LIQ_SCORE")
        risk_score = daily_features.get("MKT_RISK_SCORE")
        score_df = pd.DataFrame({
            "trend": trend_score,
            "vol": vol_score,
            "liq": liq_score,
            "risk": risk_score,
        })
        risk_on_prob = score_df.mean(axis=1)
        risk_on_prob = risk_on_prob.where(risk_on_prob.notna(), risk_on.astype(float))
        risk_on_prob = risk_on_prob.clip(lower=0.0, upper=1.0)

        regime_usable = close.notna()
        severity = pd.Series("risk_on", index=daily_index)
        off_mask = (~risk_on) & regime_usable
        severity[off_mask] = "risk_off"
        if risk_label is not None:
            severity[(risk_label == "crisis") & regime_usable] = "crisis"

        hedge_max_pct = self._safe_float(params.get("hedge_max_pct", 0.5), 0.5)
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

        extra_cols = {}
        if not daily_features.empty:
            extra_cols = {
                "REGIME_TREND_D": daily_features.get("MKT_TREND_LABEL"),
                "REGIME_TREND_SCORE_D": daily_features.get("MKT_TREND_SCORE"),
                "REGIME_VOL_D": daily_features.get("MKT_VOL_LABEL"),
                "REGIME_VOL_SCORE_D": daily_features.get("MKT_VOL_SCORE"),
                "REGIME_LIQ_D": daily_features.get("MKT_LIQ_LABEL"),
                "REGIME_LIQ_SCORE_D": daily_features.get("MKT_LIQ_SCORE"),
                "REGIME_RISK_D": daily_features.get("MKT_RISK_LABEL"),
                "REGIME_RISK_SCORE_D": daily_features.get("MKT_RISK_SCORE"),
                "REGIME_RISK_ON_MEAN_REVERSION": daily_features.get("MKT_RISK_ON_MEAN_REVERSION"),
            }
        for col, series in extra_cols.items():
            if series is None:
                continue
            regime_daily[col] = series

        intraday_mode = params.get("intraday_mode", "daily_ffill_shift1")
        if self._is_intraday(interval) and intraday_mode == "daily_ffill_shift1":
            regime_daily = regime_daily.shift(1)

        master_index = pd.DatetimeIndex(master_index)
        regime_daily.index = self._align_index(regime_daily.index, master_index)
        regime = regime_daily.reindex(master_index, method="ffill")
        regime["risk_on"] = regime["risk_on"].fillna(False)
        regime["risk_on_prob"] = pd.to_numeric(regime["risk_on_prob"], errors="coerce")
        regime["regime_usable"] = regime["regime_usable"].fillna(False)

        diagnostics = self._build_diagnostics(
            market_symbol=market_symbol,
            vol_symbol=vol_symbol,
            market_close=close,
            vol_close=_daily_frame(data_map.get(vol_symbol), interval).get("Close", pd.Series(index=daily_index, dtype="float64")),
            regime_usable=regime_usable,
            start_date=start_date,
            end_date=end_date,
        )
        return RegimeBuildResult(regime, diagnostics=diagnostics)

    def _build_diagnostics(
        self,
        market_symbol: Optional[str],
        vol_symbol: Optional[str],
        market_close: pd.Series,
        vol_close: pd.Series,
        regime_usable: pd.Series,
        start_date: Optional[pd.Timestamp],
        end_date: Optional[pd.Timestamp],
    ) -> Dict[str, Any]:
        symbols = {
            market_symbol: market_close,
            vol_symbol: vol_close,
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
