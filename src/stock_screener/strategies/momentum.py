"""
Momentum-based screening strategies.
"""
import pandas as pd
import numpy as np
from stock_screener.core.strategy import BaseStrategy
from typing import Tuple, Dict, Any, Optional, List

class RsiOversold(BaseStrategy):
    """
    RSI < 30 indicates oversold conditions.
    """
    sentiment = "BULLISH"
    direction = "long"
    stop_multiplier = 2.0
    take_profit_multiplier = 1.5
    time_stop_days = 10
    confirmation_lookback_days = 3
    confirmation_weight = 1.0
    require_sma200_trend = True

    def signal(self, df: pd.DataFrame) -> pd.Series:
        min_len = 200 if self.require_sma200_trend else 20
        if len(df) < min_len:
            return pd.Series(False, index=df.index)
        rsi = self.rsi_series(df, length=14)
        if rsi is None:
            return pd.Series(False, index=df.index)
        close = df['Close']
        if self.require_sma200_trend:
            sma200 = self.sma_series(df, length=200)
            if sma200 is None:
                return pd.Series(False, index=df.index)
            trend_ok = (close > sma200) & (sma200 > sma200.shift(1))
        else:
            trend_ok = pd.Series(True, index=df.index)
        signal = (rsi < 30) & trend_ok
        signal = signal.fillna(False)
        if self.require_sma200_trend:
            signal.iloc[:199] = False
        return signal.astype(bool)

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        min_len = 200 if self.require_sma200_trend else 15
        if len(df) < min_len:
            return pd.Series(0.0, index=df.index)
        rsi = self.rsi_series(df, length=14)
        if rsi is None:
            return pd.Series(0.0, index=df.index)
        score = (30 - rsi).clip(lower=0)
        return score.fillna(0.0)

    def exit_signal(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 20:
            return pd.Series(False, index=df.index)
        close = df['Close']
        rsi = self.rsi_series(df, length=14)
        ema20 = self.ema_series(df, length=20)
        if rsi is None or ema20 is None:
            return pd.Series(False, index=df.index)
        rsi_cross = (rsi > 50) & (rsi.shift(1) <= 50)
        ema_cross = (close > ema20) & (close.shift(1) <= ema20.shift(1))
        signal = (rsi_cross | ema_cross).fillna(False)
        return signal.astype(bool)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        min_len = 200 if self.require_sma200_trend else 20
        if len(df) < min_len:
            return False, {}
        rsi = self.rsi_series(df, length=14)
        if rsi is None:
            return False, {}

        close = df['Close']
        if self.require_sma200_trend:
            sma200 = self.sma_series(df, length=200)
            if sma200 is None:
                return False, {}
        
        curr_rsi = rsi.iloc[-1]
        if pd.isna(curr_rsi):
            return False, {}

        if self.require_sma200_trend:
            if close.iloc[-1] <= sma200.iloc[-1]:
                return False, {}
            if sma200.iloc[-1] <= sma200.iloc[-2]:
                return False, {}
        
        if curr_rsi < 30:
            # STOP: 2x ATR below Low (Volatility adjusted safety net)
            stop_level = self.calculate_atr_stop(df, direction='long', multiplier=2.0)
            return True, {
                'pattern': 'RSI Oversold', 
                'rsi': round(curr_rsi, 2), 
                'stop_loss': stop_level,
                'score': round(max(0.0, 30 - curr_rsi), 2),
                'sentiment': 'BULLISH'
            }
            
        return False, {}

class Sma200RsiOversoldFib(BaseStrategy):
    """
    Mean-reversion: close below SMA200 with RSI oversold, targeting fib retracements.
    """
    sentiment = "BULLISH"
    direction = "long"
    stop_multiplier = 2.0
    take_profit_multiplier = None
    early_failure_exit_enabled = True
    early_failure_bars = 5
    early_failure_requires_close_below_entry = True
    atr_contraction_tighten_enabled = True
    atr_contraction_tighten_threshold = 0.8
    atr_contraction_lookback = 20
    momentum_fail_exit_enabled = True
    momentum_fail_entry_rsi = 30.0
    momentum_fail_confirm_rsi = 35.0
    momentum_fail_confirm_bars = 3
    momentum_fail_rsi_length = 14
    debug_counters = True

    sma_length = 200
    rsi_length = 14
    rsi_oversold = 25.0
    require_rsi_cross = True
    rsi_overbought = 70.0
    sma_distance_min_pct = 0.07
    sma_exit_buffer_pct = 0.02
    volume_sma_length = 20
    require_regime_filter = False
    regime_filter_column = "MKT_RISK_ON_MEAN_REVERSION"
    market_symbols = ["SPY", "QQQ"]

    fib_lookback = 60
    fib_levels = [0.618, 0.786, 1.0]
    fib_level_pcts = 0.5
    fib_min_range_pct = 0.0
    take_profit_level_pct_mode = "remaining"
    structure_stop_atr_mult = 0.25
    percent_stop_pct = 0.04
    stop_cooldown_days = 15

    def _fib_range(self, df: pd.DataFrame) -> Optional[Tuple[float, float]]:
        lookback = int(getattr(self, "fib_lookback", 60) or 60)
        if len(df) < lookback:
            return None
        window = df.iloc[-lookback:]
        swing_low = window["Low"].min()
        swing_high = window["High"].max()
        if pd.isna(swing_low) or pd.isna(swing_high):
            return None
        if swing_high <= swing_low:
            return None
        min_range = float(getattr(self, "fib_min_range_pct", 0.0) or 0.0)
        if min_range > 0 and swing_low > 0:
            if (swing_high - swing_low) / swing_low < min_range:
                return None
        return float(swing_low), float(swing_high)

    def _fib_targets(self, df: pd.DataFrame, entry_price: Optional[float] = None) -> Optional[List[float]]:
        range_vals = self._fib_range(df)
        if range_vals is None:
            return None
        swing_low, swing_high = range_vals
        levels = getattr(self, "fib_levels", [0.618, 0.786, 1.0]) or []
        targets: List[float] = []
        span = swing_high - swing_low
        for level in levels:
            try:
                fib = float(level)
            except (TypeError, ValueError):
                continue
            if fib <= 0:
                continue
            target = swing_low + (span * fib)
            if entry_price is not None and target <= entry_price:
                continue
            targets.append(round(float(target), 2))
        if not targets:
            return None
        deduped: List[float] = []
        seen = set()
        for target in targets:
            if target in seen:
                continue
            seen.add(target)
            deduped.append(target)
        return deduped

    def get_take_profit_levels(self, df: pd.DataFrame, entry_price: Optional[float] = None) -> Optional[List[float]]:
        targets = self._fib_targets(df, entry_price=entry_price)
        if not targets:
            return None
        return sorted(targets)

    def get_take_profit_level_pcts(self, levels: List[float]) -> Optional[List[float]]:
        pcts = getattr(self, "fib_level_pcts", None)
        if pcts is None:
            return None
        if isinstance(pcts, (int, float)):
            pcts = [float(pcts)] * len(levels)
        else:
            pcts = list(pcts)
        if len(pcts) != len(levels):
            return None
        cleaned: List[float] = []
        total = 0.0
        for pct in pcts:
            try:
                value = float(pct)
            except (TypeError, ValueError):
                return None
            if value <= 0:
                value = 0.0
            cleaned.append(value)
            total += value
        if total <= 0:
            return None
        mode = (getattr(self, "take_profit_level_pct_mode", "initial") or "initial").lower()
        if mode != "remaining" and total > 1.0:
            cleaned = [value / total for value in cleaned]
        return cleaned

    def _rsi_entry_signal(self, rsi: pd.Series, threshold: float) -> pd.Series:
        prev = rsi.shift(1)
        cross_up = (rsi > threshold) & (prev <= threshold)
        cross_down = (rsi <= threshold) & (prev > threshold)
        return (cross_up | cross_down).fillna(False)

    def signal(self, df: pd.DataFrame) -> pd.Series:
        sma_len = int(getattr(self, "sma_length", 200) or 200)
        rsi_len = int(getattr(self, "rsi_length", 14) or 14)
        lookback = int(getattr(self, "fib_lookback", 60) or 60)
        vol_len = int(getattr(self, "volume_sma_length", 20) or 20)
        min_len = max(sma_len, rsi_len + 1, lookback, vol_len)
        if len(df) < min_len:
            return pd.Series(False, index=df.index)
        close = df["Close"]
        sma = self.sma_series(df, length=sma_len)
        rsi = self.rsi_series(df, length=rsi_len)
        vol_sma = self.volume_sma_series(df, length=vol_len)
        if sma is None or rsi is None or vol_sma is None:
            return pd.Series(False, index=df.index)
        require_regime = bool(getattr(self, "require_regime_filter", False))
        if require_regime:
            regime_col = getattr(self, "regime_filter_column", "MKT_RISK_ON_MEAN_REVERSION")
            if regime_col not in df.columns:
                return pd.Series(False, index=df.index)
            regime_ok = df[regime_col].astype("boolean").fillna(False)
        else:
            regime_ok = pd.Series(True, index=df.index)
        threshold = float(getattr(self, "rsi_oversold", 25.0))
        distance_min = float(getattr(self, "sma_distance_min_pct", 0.0) or 0.0)
        distance = (sma - close) / sma.replace(0, np.nan)
        distance_ok = distance >= distance_min
        volume_ok = df["Volume"] > vol_sma
        require_cross = bool(getattr(self, "require_rsi_cross", True))
        if require_cross:
            rsi_trigger = self._rsi_entry_signal(rsi, threshold)
        else:
            rsi_trigger = rsi <= threshold
        signal = (close < sma) & distance_ok & volume_ok & rsi_trigger & regime_ok
        signal = signal.fillna(False)
        signal.iloc[: min_len - 1] = False
        return signal.astype(bool)

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        rsi_len = int(getattr(self, "rsi_length", 14) or 14)
        if len(df) < (rsi_len + 1):
            return pd.Series(0.0, index=df.index)
        rsi = self.rsi_series(df, length=rsi_len)
        if rsi is None:
            return pd.Series(0.0, index=df.index)
        threshold = float(getattr(self, "rsi_oversold", 25.0))
        return (threshold - rsi).clip(lower=0).fillna(0.0)

    def stop_loss_series(self, df: pd.DataFrame) -> Optional[pd.Series]:
        lookback = int(getattr(self, "fib_lookback", 60) or 60)
        if len(df) < lookback:
            return None
        atr = self.atr_series(df, period=14)
        anchor_low = df["Low"].rolling(lookback).min()
        buffer_mult = float(getattr(self, "structure_stop_atr_mult", 0.25) or 0.25)
        pct = float(getattr(self, "percent_stop_pct", 0.04) or 0.04)
        percent_stop = df["Close"] * (1.0 - pct)
        struct_stop = pd.Series(np.nan, index=df.index)
        if atr is None:
            stop = percent_stop
        else:
            struct_stop = anchor_low - (atr * buffer_mult)
            stop = pd.concat([struct_stop, percent_stop], axis=1).max(axis=1)
        signal = self.signal(df)
        stop = stop.where(signal)
        return stop.round(2)

    def exit_signal(self, df: pd.DataFrame) -> pd.Series:
        sma_len = int(getattr(self, "sma_length", 200) or 200)
        rsi_len = int(getattr(self, "rsi_length", 14) or 14)
        min_len = max(sma_len, rsi_len)
        if len(df) < min_len:
            return pd.Series(False, index=df.index)
        close = df["Close"]
        sma = self.sma_series(df, length=sma_len)
        rsi = self.rsi_series(df, length=rsi_len)
        if sma is None or rsi is None:
            return pd.Series(False, index=df.index)
        overbought = float(getattr(self, "rsi_overbought", 70.0))
        buffer_pct = float(getattr(self, "sma_exit_buffer_pct", 0.0) or 0.0)
        sma_exit = sma * (1.0 - buffer_pct)
        signal = (rsi >= overbought) | (close >= sma_exit)
        return signal.fillna(False).astype(bool)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        sma_len = int(getattr(self, "sma_length", 200) or 200)
        rsi_len = int(getattr(self, "rsi_length", 14) or 14)
        lookback = int(getattr(self, "fib_lookback", 60) or 60)
        vol_len = int(getattr(self, "volume_sma_length", 20) or 20)
        min_len = max(sma_len, rsi_len + 1, lookback, vol_len)
        if len(df) < min_len:
            return False, {}
        close = df["Close"]
        sma = self.sma_series(df, length=sma_len)
        rsi = self.rsi_series(df, length=rsi_len)
        vol_sma = self.volume_sma_series(df, length=vol_len)
        if sma is None or rsi is None or vol_sma is None:
            return False, {}
        curr_rsi = rsi.iloc[-1]
        if pd.isna(curr_rsi):
            return False, {}
        if pd.isna(sma.iloc[-1]):
            return False, {}
        require_regime = bool(getattr(self, "require_regime_filter", False))
        regime_col = getattr(self, "regime_filter_column", "MKT_RISK_ON_MEAN_REVERSION")
        if require_regime:
            if regime_col not in df.columns:
                return False, {}
            regime_val = df[regime_col].iloc[-1]
            if pd.isna(regime_val) or not bool(regime_val):
                return False, {}
        if close.iloc[-1] >= sma.iloc[-1]:
            return False, {}
        distance_min = float(getattr(self, "sma_distance_min_pct", 0.0) or 0.0)
        if sma.iloc[-1] > 0:
            distance_pct = (sma.iloc[-1] - close.iloc[-1]) / sma.iloc[-1]
        else:
            distance_pct = 0.0
        if distance_pct < distance_min:
            return False, {}
        if pd.isna(vol_sma.iloc[-1]) or df["Volume"].iloc[-1] <= vol_sma.iloc[-1]:
            return False, {}
        threshold = float(getattr(self, "rsi_oversold", 25.0))
        if len(rsi) < 2 or pd.isna(rsi.iloc[-2]):
            return False, {}
        prev_rsi = rsi.iloc[-2]
        cross_up = (curr_rsi > threshold) and (prev_rsi <= threshold)
        cross_down = (curr_rsi <= threshold) and (prev_rsi > threshold)
        require_cross = bool(getattr(self, "require_rsi_cross", True))
        if require_cross:
            if not (cross_up or cross_down):
                return False, {}
        elif curr_rsi >= threshold:
            return False, {}

        targets = self.get_take_profit_levels(df, entry_price=close.iloc[-1]) or []
        take_profit = targets[-1] if targets else None
        pct = float(getattr(self, "percent_stop_pct", 0.04) or 0.04)
        percent_stop = close.iloc[-1] * (1.0 - pct)
        stop_level = percent_stop
        range_vals = self._fib_range(df)
        atr = self.atr_series(df, period=14)
        if range_vals is not None and atr is not None and not pd.isna(atr.iloc[-1]):
            anchor_low = range_vals[0]
            buffer_mult = float(getattr(self, "structure_stop_atr_mult", 0.25) or 0.25)
            struct_stop = anchor_low - (float(atr.iloc[-1]) * buffer_mult)
            stop_level = max(struct_stop, percent_stop)
        score = max(0.0, threshold - float(curr_rsi))
        metrics = {
            "pattern": "SMA200 RSI Oversold Fib",
            "sma200": round(float(sma.iloc[-1]), 2),
            "rsi": round(float(curr_rsi), 2),
            "sma_distance_pct": round(float(distance_pct * 100.0), 2),
            "stop_loss": round(float(stop_level), 2) if stop_level is not None else None,
            "score": round(float(score), 2),
            "sentiment": "BULLISH",
        }
        if require_regime:
            metrics["regime_ok"] = True
            metrics["regime_col"] = regime_col
        if take_profit is not None:
            metrics["take_profit"] = round(float(take_profit), 2)
        if targets:
            metrics["fib_targets"] = targets
        if require_cross:
            if cross_up:
                metrics["rsi_cross"] = "up"
            elif cross_down:
                metrics["rsi_cross"] = "down"
        return True, metrics

class MacdTurnaround(BaseStrategy):
    """
    MACD Histogram ticks up while below zero (Momentum shifting bullish) 
    OR standard Bullish Crossover.
    """
    sentiment = "BULLISH"
    direction = "long"
    stop_multiplier = 2.0
    take_profit_multiplier = 3.0

    def signal(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 50:
            return pd.Series(False, index=df.index)
        macd = self.macd_df(df)
        if macd is None or 'MACDh_12_26_9' not in macd.columns:
            return pd.Series(False, index=df.index)

        close = df['Close']
        sma50 = self.sma_series(df, length=50)
        if sma50 is None:
            return pd.Series(False, index=df.index)
        trend_ok = (close > sma50) & (sma50 > sma50.shift(1))

        hist = macd['MACDh_12_26_9']
        cond1 = (hist < 0) & (hist > hist.shift(1)) & (hist.shift(1) < hist.shift(2))
        cond2 = (hist > 0) & (hist.shift(1) <= 0)
        signal = (cond1 | cond2) & trend_ok
        signal = signal.fillna(False)
        signal.iloc[:49] = False
        return signal.astype(bool)

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 35:
            return pd.Series(0.0, index=df.index)
        macd = self.macd_df(df)
        if macd is None or 'MACDh_12_26_9' not in macd.columns:
            return pd.Series(0.0, index=df.index)
        hist = macd['MACDh_12_26_9']
        return hist.abs().fillna(0.0)

    def exit_signal(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 35:
            return pd.Series(False, index=df.index)
        close = df['Close']
        ema20 = self.ema_series(df, length=20)
        macd = self.macd_df(df)
        if ema20 is None or macd is None or 'MACDh_12_26_9' not in macd.columns:
            return pd.Series(False, index=df.index)
        hist = macd['MACDh_12_26_9']
        hist_cross = (hist < 0) & (hist.shift(1) >= 0)
        ema_cross = (close < ema20) & (close.shift(1) >= ema20.shift(1))
        signal = (hist_cross | ema_cross).fillna(False)
        return signal.astype(bool)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if len(df) < 50:
            return False, {}
        # MACD(12, 26, 9)
        macd = self.macd_df(df)
        if macd is None:
            return False, {}
        
        # macd columns: MACD_12_26_9, MACDh_12_26_9 (hist), MACDs_12_26_9 (signal)
        hist_col = 'MACDh_12_26_9'
        if hist_col not in macd.columns:
            return False, {}

        hist = macd[hist_col]
        if len(hist) < 3 or hist.iloc[-3:].isna().any():
            return False, {}
        
        close = df['Close']
        sma50 = self.sma_series(df, length=50)
        if sma50 is None:
            return False, {}
        if close.iloc[-1] <= sma50.iloc[-1]:
            return False, {}
        if sma50.iloc[-1] <= sma50.iloc[-2]:
            return False, {}

        # Stop loss: 2x ATR
        stop_level = self.calculate_atr_stop(df, direction='long', multiplier=2.0)
        score = round(abs(hist.iloc[-1]), 3)

        # Condition 1: Histogram ticking up (getting less negative) while below zero
        if hist.iloc[-1] < 0 and hist.iloc[-1] > hist.iloc[-2] and hist.iloc[-2] < hist.iloc[-3]:
             return True, {
                 'pattern': 'MACD Histogram Turnaround', 
                 'hist': round(hist.iloc[-1], 3), 
                 'stop_loss': stop_level,
                 'score': score,
                 'sentiment': 'BULLISH'
             }
             
        # Condition 2: Crossover (Hist crosses 0 upwards)
        if hist.iloc[-1] > 0 and hist.iloc[-2] <= 0:
             return True, {
                 'pattern': 'MACD Crossover', 
                 'hist': round(hist.iloc[-1], 3), 
                 'stop_loss': stop_level,
                 'score': score,
                 'sentiment': 'BULLISH'
             }
             
        return False, {}

class VptBreakout(BaseStrategy):
    """
    Volume Price Trend (VPT) Breakout.
    Checks if VPT is crossing its moving average or making a new high with price.
    Simplified: Price Up + Volume > 2x Avg + VPT Rising
    """
    sentiment = "BULLISH"
    direction = "long"
    stop_multiplier = 2.0
    take_profit_multiplier = 4.0

    def signal(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 50:
            return pd.Series(False, index=df.index)
        close = df['Close']
        volume = df['Volume']
        prev_close = close.shift(1)
        change_pct = (close - prev_close) / prev_close
        avg_vol = self.volume_sma_series(df, length=20)
        vol_ratio = volume / avg_vol
        high = df['High']
        low = df['Low']
        range_ = (high - low)
        close_loc = (close - low) / range_.replace(0, np.nan)

        ema50 = self.ema_series(df, length=50)
        trend_ok = ema50 is not None and (close > ema50) & (ema50 > ema50.shift(1))

        signal = (change_pct > 0.02) & (vol_ratio > 2.5) & (avg_vol != 0) & (close_loc >= 0.7) & trend_ok
        signal = signal.fillna(False)
        signal.iloc[:49] = False
        return signal.astype(bool)

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 21:
            return pd.Series(0.0, index=df.index)
        close = df['Close']
        volume = df['Volume']
        prev_close = close.shift(1)
        change_pct = (close - prev_close) / prev_close
        avg_vol = self.volume_sma_series(df, length=20)
        vol_ratio = (volume / avg_vol).fillna(0.0)
        score = (change_pct.fillna(0.0) * 100) + (vol_ratio * 2.0)
        return score.fillna(0.0)

    def exit_signal(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 20:
            return pd.Series(False, index=df.index)
        close = df['Close']
        ema20 = self.ema_series(df, length=20)
        if ema20 is None:
            return pd.Series(False, index=df.index)
        signal = (close < ema20) & (close.shift(1) >= ema20.shift(1))
        return signal.fillna(False).astype(bool)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if len(df) < 50:
            return False, {}
        # VPT calculation not standard in all libs, approximate logic:
        # Price Up > 2% AND Volume > 2.5x 20-day Average Volume
        
        curr_close = df['Close'].iloc[-1]
        prev_close = df['Close'].iloc[-2]
        change_pct = (curr_close - prev_close) / prev_close
        
        curr_vol = df['Volume'].iloc[-1]
        avg_vol = self.volume_sma_series(df, length=20).iloc[-1]
        if pd.isna(avg_vol) or avg_vol == 0:
            return False, {}

        ema50 = self.ema_series(df, length=50)
        if ema50 is None:
            return False, {}
        if curr_close <= ema50.iloc[-1] or ema50.iloc[-1] <= ema50.iloc[-2]:
            return False, {}
        
        vol_ratio = curr_vol / avg_vol
        curr_high = df['High'].iloc[-1]
        curr_low = df['Low'].iloc[-1]
        range_ = curr_high - curr_low
        if range_ <= 0:
            return False, {}
        close_loc = (curr_close - curr_low) / range_
        
        # Breakout criteria
        if change_pct > 0.02 and vol_ratio > 2.5 and close_loc >= 0.7:
             # Stop: 2x ATR below Low
             stop_level = self.calculate_atr_stop(df, direction='long', multiplier=2.0)
             return True, {
                 'pattern': 'Volume Breakout', 
                 'change_pct': round(change_pct * 100, 2),
                 'vol_ratio': round(vol_ratio, 2),
                 'close_loc': round(close_loc, 2),
                 'stop_loss': stop_level,
                 'score': round((change_pct * 100) + (vol_ratio * 2.0), 2),
                 'sentiment': 'BULLISH'
             }
             
        return False, {}

class BearishRsiDivergence(BaseStrategy):
    """
    Bearish RSI Divergence:
    Price makes Higher High, RSI makes Lower High (Last 14 candles).
    """
    sentiment = "BEARISH"
    direction = "short"
    stop_multiplier = 2.0
    take_profit_multiplier = 2.0
    time_stop_days = float("inf")

    def signal(self, df: pd.DataFrame) -> pd.Series:
        window = 14
        if len(df) < 200:
            return pd.Series(False, index=df.index)
        rsi = self.rsi_series(df, length=14)
        if rsi is None:
            return pd.Series(False, index=df.index)

        high = df['High']
        low = df['Low']
        close = df['Close']
        sma200 = self.sma_series(df, length=200)
        sma20 = self.sma_series(df, length=20)
        if sma200 is None or sma20 is None:
            return pd.Series(False, index=df.index)

        trend_ok = (close < sma200) & (sma20 < sma20.shift(1))
        curr_high = high.rolling(window).max()
        curr_rsi_high = rsi.rolling(window).max()
        idx_price_max = high.rolling(window).apply(lambda x: float(np.argmax(x)), raw=True)

        prev_high_max = curr_high.shift(window)
        prev_rsi_max = curr_rsi_high.shift(window)

        cond_recent_peak = idx_price_max >= (window - 3)
        confirmation = (close < close.shift(1)) & (low < low.shift(1))
        signal = (
            cond_recent_peak
            & trend_ok
            & confirmation
            & (curr_high > prev_high_max)
            & (curr_rsi_high < prev_rsi_max)
            & (prev_rsi_max > 65)
        )
        signal = signal.fillna(False)
        signal.iloc[:199] = False
        return signal.astype(bool)

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        window = 14
        if len(df) < 200:
            return pd.Series(0.0, index=df.index)
        rsi = self.rsi_series(df, length=14)
        if rsi is None:
            return pd.Series(0.0, index=df.index)
        curr_rsi_high = rsi.rolling(window).max()
        prev_rsi_max = curr_rsi_high.shift(window)
        score = (prev_rsi_max - curr_rsi_high).clip(lower=0)
        return score.fillna(0.0)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if len(df) < 200:
            return False, {}
        
        rsi = self.rsi_series(df, length=14)
        if rsi is None:
            return False, {}

        sma200 = self.sma_series(df, length=200)
        sma20 = self.sma_series(df, length=20)
        if sma200 is None or sma20 is None:
            return False, {}

        curr_close = df['Close'].iloc[-1]
        if curr_close >= sma200.iloc[-1]:
            return False, {}
        if sma20.iloc[-1] >= sma20.iloc[-2]:
            return False, {}
        
        # Look at last 2 peaks. Simplified scan:
        # 1. Identify local max in Price
        # 2. Identify local max in RSI
        # This is hard to do perfectly on just last candle, so we look back slightly.
        # We check if Current High is Highest in 10 days, 
        # BUT RSI is NOT Highest in 10 days (and significantly lower).
        
        window = 14
        recent = df.iloc[-window:]
        recent_rsi = rsi.iloc[-window:]
        
        curr_high = recent['High'].max()
        curr_rsi_high = recent_rsi.max()
        if pd.isna(curr_rsi_high):
            return False, {}
        
        # Get indices of max
        idx_price_max = recent['High'].argmax() # 0 to window-1
        # If Price Peak is very recent (last 3 bars)
        if idx_price_max >= window - 3:
            # Check previous window (window to 2*window ago) for a comparable peak
            prev_window = df.iloc[-2*window:-window]
            prev_rsi_window = rsi.iloc[-2*window:-window]
            
            if prev_window.empty:
                return False, {}
            
            prev_high_max = prev_window['High'].max()
            prev_rsi_max = prev_rsi_window.max()
            if pd.isna(prev_rsi_max):
                return False, {}
            
            # Divergence Logic:
            # Current Price High > Previous Price High
            # Current RSI High < Previous RSI High
            
            if curr_high > prev_high_max and curr_rsi_high < prev_rsi_max:
                # Ensure RSI is overbought (e.g. > 70) to be significant
                if prev_rsi_max > 65:
                    # Confirmation: lower close + lower low
                    if df['Close'].iloc[-1] >= df['Close'].iloc[-2]:
                        return False, {}
                    if df['Low'].iloc[-1] >= df['Low'].iloc[-2]:
                        return False, {}
                    # Stop: 2x ATR ABOVE High (Since Shorting)
                    stop_level = self.calculate_atr_stop(df, direction='short', multiplier=2.0)
                    return True, {
                        'pattern': 'Bearish RSI Div', 
                        'price_highs': f"{prev_high_max:.2f} -> {curr_high:.2f}",
                        'rsi_highs': f"{prev_rsi_max:.1f} -> {curr_rsi_high:.1f}",
                        'stop_loss': stop_level,
                        'score': round(max(0.0, prev_rsi_max - curr_rsi_high), 2),
                        'sentiment': 'BEARISH'
                    }
        
        return False, {}
