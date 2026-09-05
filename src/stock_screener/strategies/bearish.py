"""
Bearish screening strategies (short-biased signals).
"""
import numpy as np
import pandas as pd
from typing import Tuple, Dict, Any, Optional, List
from stock_screener.core.strategy import BaseStrategy


class RsiOverbought(BaseStrategy):
    """
    RSI > 70 and turning down (bearish momentum).
    """
    sentiment = "BEARISH"
    direction = "short"
    stop_multiplier = 2.0
    take_profit_multiplier = 1.5
    time_stop_days = 8

    def signal(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 200:
            return pd.Series(False, index=df.index)
        rsi = self.rsi_series(df, length=14)
        if rsi is None:
            return pd.Series(False, index=df.index)
        close = df['Close']
        sma200 = self.sma_series(df, length=200)
        sma50 = self.sma_series(df, length=50)
        if sma200 is None or sma50 is None:
            return pd.Series(False, index=df.index)
        trend_ok = (
            (close < sma200)
            & (sma200 < sma200.shift(1))
            & (close < sma50)
            & (sma50 < sma50.shift(1))
        )
        signal = (rsi > 70) & (rsi < rsi.shift(1)) & trend_ok
        signal = signal.fillna(False)
        signal.iloc[:199] = False
        return signal.astype(bool)

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 15:
            return pd.Series(0.0, index=df.index)
        rsi = self.rsi_series(df, length=14)
        if rsi is None:
            return pd.Series(0.0, index=df.index)
        score = (rsi - 70).clip(lower=0)
        return score.fillna(0.0)

    def exit_signal(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 20:
            return pd.Series(False, index=df.index)
        rsi = self.rsi_series(df, length=14)
        if rsi is None:
            return pd.Series(False, index=df.index)
        signal = (rsi < 50) & (rsi.shift(1) >= 50)
        return signal.fillna(False).astype(bool)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if len(df) < 200:
            return False, {}
        rsi = self.rsi_series(df, length=14)
        if rsi is None:
            return False, {}
        close = df['Close']
        sma200 = self.sma_series(df, length=200)
        sma50 = self.sma_series(df, length=50)
        if sma200 is None or sma50 is None:
            return False, {}
        if close.iloc[-1] >= sma200.iloc[-1] or sma200.iloc[-1] >= sma200.iloc[-2]:
            return False, {}
        if close.iloc[-1] >= sma50.iloc[-1] or sma50.iloc[-1] >= sma50.iloc[-2]:
            return False, {}
        curr = rsi.iloc[-1]
        prev = rsi.iloc[-2]
        if pd.isna(curr) or pd.isna(prev):
            return False, {}
        if curr > 70 and curr < prev:
            stop_level = self.calculate_atr_stop(df, direction='short', multiplier=2.0)
            return True, {
                'pattern': 'RSI Overbought',
                'rsi': round(curr, 2),
                'stop_loss': stop_level,
                'score': round(max(0.0, curr - 70), 2),
                'sentiment': 'BEARISH',
            }
        return False, {}


class MacdBearishCross(BaseStrategy):
    """
    MACD histogram crosses below zero (bearish momentum shift).
    """
    sentiment = "BEARISH"
    direction = "short"
    stop_multiplier = 2.0
    take_profit_multiplier = 2.5

    def signal(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 200:
            return pd.Series(False, index=df.index)
        macd = self.macd_df(df)
        if macd is None or 'MACDh_12_26_9' not in macd.columns:
            return pd.Series(False, index=df.index)
        hist = macd['MACDh_12_26_9']
        close = df['Close']
        sma200 = self.sma_series(df, length=200)
        sma50 = self.sma_series(df, length=50)
        if sma200 is None or sma50 is None:
            return pd.Series(False, index=df.index)
        trend_ok = (
            (close < sma200)
            & (sma200 < sma200.shift(1))
            & (close < sma50)
            & (sma50 < sma50.shift(1))
        )
        signal = (hist < 0) & (hist.shift(1) >= 0) & trend_ok
        signal = signal.fillna(False)
        signal.iloc[:199] = False
        return signal.astype(bool)

    def score_series(self, df: pd.DataFrame) -> pd.Series:
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
        hist_cross = (hist > 0) & (hist.shift(1) <= 0)
        ema_cross = (close > ema20) & (close.shift(1) <= ema20.shift(1))
        signal = (hist_cross | ema_cross).fillna(False)
        return signal.astype(bool)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if len(df) < 200:
            return False, {}
        macd = self.macd_df(df)
        if macd is None or 'MACDh_12_26_9' not in macd.columns:
            return False, {}
        hist = macd['MACDh_12_26_9']
        if len(hist) < 2 or pd.isna(hist.iloc[-1]) or pd.isna(hist.iloc[-2]):
            return False, {}
        close = df['Close']
        sma200 = self.sma_series(df, length=200)
        sma50 = self.sma_series(df, length=50)
        if sma200 is None or sma50 is None:
            return False, {}
        if close.iloc[-1] >= sma200.iloc[-1] or sma200.iloc[-1] >= sma200.iloc[-2]:
            return False, {}
        if close.iloc[-1] >= sma50.iloc[-1] or sma50.iloc[-1] >= sma50.iloc[-2]:
            return False, {}
        if hist.iloc[-1] < 0 and hist.iloc[-2] >= 0:
            stop_level = self.calculate_atr_stop(df, direction='short', multiplier=2.0)
            return True, {
                'pattern': 'MACD Bearish Cross',
                'hist': round(hist.iloc[-1], 3),
                'stop_loss': stop_level,
                'score': round(abs(hist.iloc[-1]), 3),
                'sentiment': 'BEARISH',
            }
        return False, {}


class Breakdown20(BaseStrategy):
    """
    Close breaks below 20-day low with a down-sloping 20-day SMA.
    """
    sentiment = "BEARISH"
    direction = "short"
    stop_multiplier = 2.0
    take_profit_multiplier = 2.5
    time_stop_days = 12

    def signal(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 50:
            return pd.Series(False, index=df.index)
        close = df['Close']
        low = df['Low']
        volume = df['Volume']
        avg_vol = self.volume_sma_series(df, length=20)
        sma20 = self.sma_series(df, length=20)
        sma50 = self.sma_series(df, length=50)
        if sma20 is None:
            return pd.Series(False, index=df.index)
        low20 = low.rolling(20).min()
        if sma50 is None:
            return pd.Series(False, index=df.index)
        trend_ok = (close < sma50) & (sma50 < sma50.shift(1))
        vol_ok = avg_vol > 0
        signal = (close < low20) & (sma20 < sma20.shift(1)) & trend_ok & vol_ok & (volume > avg_vol * 1.2)
        signal = signal.fillna(False)
        signal.iloc[:49] = False
        return signal.astype(bool)

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 21:
            return pd.Series(0.0, index=df.index)
        close = df['Close']
        low20 = df['Low'].rolling(20).min()
        score = ((low20 - close) / close.replace(0, np.nan) * 100).clip(lower=0)
        return score.fillna(0.0)

    def exit_signal(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 20:
            return pd.Series(False, index=df.index)
        close = df['Close']
        sma20 = self.sma_series(df, length=20)
        if sma20 is None:
            return pd.Series(False, index=df.index)
        signal = (close > sma20) & (close.shift(1) <= sma20.shift(1))
        return signal.fillna(False).astype(bool)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if len(df) < 50:
            return False, {}
        close = df['Close']
        low = df['Low']
        volume = df['Volume']
        avg_vol = self.volume_sma_series(df, length=20)
        sma20 = self.sma_series(df, length=20)
        sma50 = self.sma_series(df, length=50)
        if sma20 is None:
            return False, {}
        if sma50 is None:
            return False, {}
        if close.iloc[-1] >= sma50.iloc[-1] or sma50.iloc[-1] >= sma50.iloc[-2]:
            return False, {}
        if avg_vol.iloc[-1] == 0 or volume.iloc[-1] <= avg_vol.iloc[-1] * 1.2:
            return False, {}
        low20 = low.rolling(20).min().iloc[-1]
        if close.iloc[-1] < low20 and sma20.iloc[-1] < sma20.iloc[-2]:
            stop_level = self.calculate_atr_stop(df, direction='short', multiplier=2.0)
            return True, {
                'pattern': '20D Breakdown',
                'stop_loss': stop_level,
                'score': round(max(0.0, (low20 - close.iloc[-1]) / close.iloc[-1] * 100), 2),
                'sentiment': 'BEARISH',
            }
        return False, {}


class VtxBreakdown(BaseStrategy):
    """
    VTX-BRK: Volatility Trend eXhaustion – Breakdown.
    """
    sentiment = "BEARISH"
    direction = "short"
    stop_multiplier = 2.0
    take_profit_multiplier = None
    time_stop_days = None

    sma_length = 200
    atr_length = 20
    atr_expand_lookback = 5
    breakdown_lookback = 20
    sma_entry_buffer_pct = 0.02
    breakdown_buffer_pct = 0.05
    require_regime_filter = True
    regime_filter_column = "MKT_RISK_ON_MEAN_REVERSION"
    regime_filter_invert = True
    exit_on_regime_flip = True
    vol_collapse_column = "MKT_VOL_OK"
    exit_on_vol_collapse = True

    partial_take_profit_enabled = True
    partial_take_profit_r = 1.0
    partial_take_profit_pct = 0.5
    partial_take_profit_mode = "r_multiple"

    trailing_enabled = True
    trailing_start_r = 1.0
    trailing_use_mmt = False
    trailing_use_avwap = False
    trailing_use_ema20 = True
    trailing_method = "min"
    trailing_ema_length = 20
    trailing_atr_mult = 1.0

    fib_lookback = 60
    fib_levels = [0.236, 0.382, 0.5]
    fib_level_pcts = 0.5
    fib_min_range_pct = 0.0
    fib_take_profit_enabled = True
    take_profit_level_pct_mode = "remaining"

    market_symbols = ["SPY", "QQQ"]

    def get_name(self) -> str:
        return "VTX-BRK"

    def _regime_series(self, df: pd.DataFrame) -> Optional[pd.Series]:
        if not bool(getattr(self, "require_regime_filter", True)):
            return pd.Series(True, index=df.index)
        col = getattr(self, "regime_filter_column", "MKT_RISK_ON_MEAN_REVERSION")
        if col not in df.columns:
            return None
        series = df[col].astype("boolean").ffill().fillna(False)
        if bool(getattr(self, "regime_filter_invert", False)):
            series = ~series
        return series

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
        if not bool(getattr(self, "fib_take_profit_enabled", True)):
            return None
        range_vals = self._fib_range(df)
        if range_vals is None:
            return None
        swing_low, swing_high = range_vals
        levels = getattr(self, "fib_levels", [0.236, 0.382, 0.5]) or []
        targets: List[float] = []
        span = swing_high - swing_low
        for level in levels:
            try:
                fib = float(level)
            except (TypeError, ValueError):
                continue
            if fib <= 0:
                continue
            target = swing_low - (span * fib)
            if entry_price is not None and target >= entry_price:
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

    def signal(self, df: pd.DataFrame) -> pd.Series:
        sma_len = int(getattr(self, "sma_length", 200) or 200)
        atr_len = int(getattr(self, "atr_length", 20) or 20)
        atr_back = int(getattr(self, "atr_expand_lookback", 5) or 5)
        low_lookback = int(getattr(self, "breakdown_lookback", 20) or 20)
        min_len = max(sma_len, atr_len + atr_back, low_lookback + 1)
        if len(df) < min_len:
            return pd.Series(False, index=df.index)

        close = df["Close"]
        low = df["Low"]
        sma = self.sma_series(df, length=sma_len)
        atr = self.atr_series(df, period=atr_len)
        if sma is None or atr is None:
            return pd.Series(False, index=df.index)
        regime_ok = self._regime_series(df)
        if regime_ok is None:
            return pd.Series(False, index=df.index)

        sma_buffer = float(getattr(self, "sma_entry_buffer_pct", 0.0) or 0.0)
        sma_threshold = sma * (1.0 - sma_buffer)
        prior_low = low.rolling(low_lookback).min().shift(1)
        breakdown_buffer = float(getattr(self, "breakdown_buffer_pct", 0.0) or 0.0)
        breakdown_threshold = prior_low * (1.0 - breakdown_buffer)
        atr_expand = atr > atr.shift(atr_back)
        breakdown = close < breakdown_threshold
        signal = (close < sma_threshold) & atr_expand & breakdown & regime_ok
        signal = signal.fillna(False)
        signal.iloc[: min_len - 1] = False
        return signal.astype(bool)

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        low_lookback = int(getattr(self, "breakdown_lookback", 20) or 20)
        if len(df) < (low_lookback + 1):
            return pd.Series(0.0, index=df.index)
        close = df["Close"]
        prior_low = df["Low"].rolling(low_lookback).min().shift(1)
        breakdown_buffer = float(getattr(self, "breakdown_buffer_pct", 0.0) or 0.0)
        breakdown_threshold = prior_low * (1.0 - breakdown_buffer)
        score = ((breakdown_threshold - close) / close.replace(0, np.nan) * 100).clip(lower=0)
        return score.fillna(0.0)

    def stop_loss_series(self, df: pd.DataFrame) -> Optional[pd.Series]:
        atr_len = int(getattr(self, "atr_length", 20) or 20)
        if len(df) < atr_len:
            return None
        atr = self.atr_series(df, period=atr_len)
        if atr is None:
            return None
        stop = df["High"] + (atr * float(getattr(self, "stop_multiplier", 2.0)))
        signal = self.signal(df)
        stop = stop.where(signal)
        return stop.round(2)

    def exit_signal(self, df: pd.DataFrame) -> pd.Series:
        signal = pd.Series(False, index=df.index)
        if bool(getattr(self, "exit_on_regime_flip", True)):
            col = getattr(self, "regime_filter_column", "MKT_RISK_ON_MEAN_REVERSION")
            if col in df.columns:
                signal |= df[col].astype("boolean").fillna(False)
        if bool(getattr(self, "exit_on_vol_collapse", True)):
            col = getattr(self, "vol_collapse_column", "MKT_VOL_OK")
            if col in df.columns:
                signal |= df[col].astype("boolean").fillna(False)
        return signal.astype(bool)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        sma_len = int(getattr(self, "sma_length", 200) or 200)
        atr_len = int(getattr(self, "atr_length", 20) or 20)
        atr_back = int(getattr(self, "atr_expand_lookback", 5) or 5)
        low_lookback = int(getattr(self, "breakdown_lookback", 20) or 20)
        min_len = max(sma_len, atr_len + atr_back, low_lookback + 1)
        if len(df) < min_len:
            return False, {}

        close = df["Close"]
        low = df["Low"]
        sma = self.sma_series(df, length=sma_len)
        atr = self.atr_series(df, period=atr_len)
        if sma is None or atr is None:
            return False, {}
        if pd.isna(sma.iloc[-1]) or pd.isna(atr.iloc[-1]):
            return False, {}
        sma_buffer = float(getattr(self, "sma_entry_buffer_pct", 0.0) or 0.0)
        sma_threshold = float(sma.iloc[-1]) * (1.0 - sma_buffer)
        if close.iloc[-1] >= sma_threshold:
            return False, {}

        regime_ok = self._regime_series(df)
        if regime_ok is None:
            return False, {}
        if not bool(regime_ok.iloc[-1]):
            return False, {}

        prior_low = low.rolling(low_lookback).min().shift(1).iloc[-1]
        if pd.isna(prior_low):
            return False, {}
        breakdown_buffer = float(getattr(self, "breakdown_buffer_pct", 0.0) or 0.0)
        breakdown_threshold = float(prior_low) * (1.0 - breakdown_buffer)
        if close.iloc[-1] >= breakdown_threshold:
            return False, {}

        if pd.isna(atr.iloc[-1 - atr_back]):
            return False, {}
        if atr.iloc[-1] <= atr.iloc[-1 - atr_back]:
            return False, {}

        stop_level = float(df["High"].iloc[-1]) + (float(atr.iloc[-1]) * float(self.stop_multiplier))
        score = max(0.0, (breakdown_threshold - close.iloc[-1]) / close.iloc[-1] * 100)
        metrics = {
            "pattern": "VTX-BRK",
            "sma200": round(float(sma.iloc[-1]), 2),
            "atr20": round(float(atr.iloc[-1]), 3),
            "prior_low": round(float(prior_low), 2),
            "breakdown_level": round(float(breakdown_threshold), 2),
            "stop_loss": round(stop_level, 2),
            "score": round(float(score), 2),
            "sentiment": "BEARISH",
        }
        targets = self.get_take_profit_levels(df, entry_price=close.iloc[-1]) or []
        if targets:
            metrics["fib_targets"] = targets
        return True, metrics
