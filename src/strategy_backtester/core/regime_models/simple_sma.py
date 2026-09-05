from typing import Any, Dict, Optional

import pandas as pd

from .base import RegimeBuildResult, RegimeModel


class SimpleSmaRegimeModel(RegimeModel):
    name = "simple_sma"

    def required_symbols(self, config: Dict[str, Any]) -> list[str]:
        symbols = (config or {}).get("symbols") or {}
        market = symbols.get("market")
        if market:
            return [market]
        market = (config or {}).get("market_symbol") or (config or {}).get("regime_symbol")
        return [market] if market else []

    def _resolve_market_df(
        self,
        data_map: Any,
        config: Dict[str, Any],
    ) -> Optional[pd.DataFrame]:
        if isinstance(data_map, pd.DataFrame):
            return data_map
        if not isinstance(data_map, dict) or not data_map:
            return None
        symbols = (config or {}).get("symbols") or {}
        market = symbols.get("market") or (config or {}).get("market_symbol") or (config or {}).get("regime_symbol")
        if market and market in data_map:
            return data_map.get(market)
        return next(iter(data_map.values()))

    def build(
        self,
        data_map: Dict[str, pd.DataFrame],
        master_index: pd.Index,
        interval: str,
        config: Dict[str, Any],
        start_date: Optional[pd.Timestamp] = None,
        end_date: Optional[pd.Timestamp] = None,
    ) -> RegimeBuildResult:
        data = self._resolve_market_df(data_map, config)
        if data is None or data.empty:
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
            regime["regime_usable"] = True
            return RegimeBuildResult(regime)

        close = data["Close"]
        sma200 = close.rolling(200).mean()
        slope_window = 20
        sma200_slope = (sma200 - sma200.shift(slope_window)) / sma200.shift(slope_window)
        sma200_dist = (close - sma200).abs() / sma200
        sma20 = close.rolling(20).mean()
        slope_up = sma20 > sma20.shift(1)
        risk_on = (close > sma200) & slope_up
        confirmed_off = (close <= sma200) & (~slope_up)
        mild_off = (~risk_on) & (~confirmed_off)

        high = data["High"]
        low = data["Low"]
        prev_close = close.shift(1)
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        low20 = low.rolling(20).min()
        day_range = high - low
        crisis = (close < low20) & (day_range > (atr * 1.5))

        severity = pd.Series("risk_on", index=data.index)
        severity[mild_off] = "mild"
        severity[confirmed_off] = "confirmed"
        severity[crisis] = "crisis"

        hedge_pct = pd.Series(0.0, index=data.index)
        hedge_pct[severity == "mild"] = 0.30
        hedge_pct[severity == "confirmed"] = 0.50
        hedge_pct[severity == "crisis"] = 0.80

        trend_state = pd.Series("RANGE", index=data.index)
        trend_state[risk_on] = "UP"
        trend_state[confirmed_off] = "DOWN"

        vol_state = pd.Series("NORMAL", index=data.index)
        vol_state[severity.isin(["mild", "confirmed"])] = "HIGH"
        vol_state[severity == "crisis"] = "CRISIS"

        stress = pd.Series(0.0, index=data.index)
        stress[severity.isin(["mild", "confirmed"])] = 0.5
        stress[severity == "crisis"] = 1.0

        regime = pd.DataFrame({
            "risk_on": risk_on,
            "severity": severity,
            "hedge_pct": hedge_pct,
            "sma200_slope_pct": sma200_slope,
            "sma200_distance_pct": sma200_dist,
            "risk_on_prob": risk_on.astype(float).fillna(0.0),
            "trend_state": trend_state,
            "vol_state": vol_state,
            "stress": stress,
            "regime_usable": True,
        })

        streak = []
        current = 0
        for is_on in risk_on.fillna(False).tolist():
            if is_on:
                current += 1
            else:
                current = 0
            streak.append(current)
        regime["risk_on_streak"] = pd.Series(streak, index=data.index)

        regime = regime.reindex(master_index).ffill()
        regime["risk_on"] = regime["risk_on"].fillna(False)
        regime["risk_on_prob"] = pd.to_numeric(regime["risk_on_prob"], errors="coerce").fillna(0.0)
        regime["regime_usable"] = regime["regime_usable"].fillna(True)

        return RegimeBuildResult(regime)
