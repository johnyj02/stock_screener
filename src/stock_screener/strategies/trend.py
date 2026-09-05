"""
Trend-based screening strategies.
"""
import numpy as np
import pandas as pd
from stock_screener.core.strategy import BaseStrategy
from typing import Tuple, Dict, Any

class GoldenCross(BaseStrategy):
    """
    Golden Cross: SMA 50 crosses above SMA 200.
    """
    sentiment = "BULLISH"
    direction = "long"
    stop_multiplier = 2.0

    def signal(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 201:
            return pd.Series(False, index=df.index)
        sma50 = self.sma_series(df, length=50)
        sma200 = self.sma_series(df, length=200)
        if sma50 is None or sma200 is None:
            return pd.Series(False, index=df.index)
        signal = (sma50 > sma200) & (sma50.shift(1) <= sma200.shift(1))
        signal = signal.fillna(False)
        signal.iloc[:200] = False
        return signal.astype(bool)

    def stop_loss_series(self, df: pd.DataFrame) -> pd.Series:
        sma50 = self.sma_series(df, length=50)
        if sma50 is None:
            return None
        return sma50.round(2)

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        sma50 = self.sma_series(df, length=50)
        sma200 = self.sma_series(df, length=200)
        if sma50 is None or sma200 is None:
            return pd.Series(0.0, index=df.index)
        score = ((sma50 - sma200) / sma200.replace(0, np.nan) * 100).fillna(0.0)
        return score

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if len(df) < 201:
            return False, {}
            
        # Calculate SMAs
        sma50 = self.sma_series(df, length=50)
        sma200 = self.sma_series(df, length=200)
        
        if sma50 is None or sma200 is None:
             return False, {}

        # Check for crossover in the last candle
        # Current candle: SMA50 > SMA200
        # Previous candle: SMA50 <= SMA200
        curr_50 = sma50.iloc[-1]
        curr_200 = sma200.iloc[-1]
        prev_50 = sma50.iloc[-2]
        prev_200 = sma200.iloc[-2]
        
        if curr_50 > curr_200 and prev_50 <= prev_200:
            # STOP: ATR-based (2x ATR below Low)
            stop_level = self.calculate_atr_stop(df, direction='long', multiplier=2.0)
            score = ((curr_50 - curr_200) / curr_200 * 100) if curr_200 else 0.0
            return True, {
                'pattern': 'Golden Cross', 
                'sma50': round(curr_50, 2), 
                'sma200': round(curr_200, 2), 
                'stop_loss': stop_level,
                'score': round(score, 2),
                'sentiment': 'BULLISH'
            }
            
        return False, {}

class SupertrendReversal(BaseStrategy):
    """
    Price closes above Supertrend line (Reversal to Bullish).
    Uses pandas-ta supertrend (default: length=7, multiplier=3).
    """
    sentiment = "BULLISH"
    direction = "long"
    stop_multiplier = 2.0

    def signal(self, df: pd.DataFrame) -> pd.Series:
        st = self.supertrend_df(df, length=10, multiplier=3)
        if st is None:
            return pd.Series(False, index=df.index)
        st_dir_col = 'SUPERTd_10_3.0'
        if st_dir_col not in st.columns:
            return pd.Series(False, index=df.index)
        signal = (st[st_dir_col] == 1) & (st[st_dir_col].shift(1) == -1)
        signal = signal.fillna(False)
        return signal.astype(bool)

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        st = self.supertrend_df(df, length=10, multiplier=3)
        if st is None:
            return pd.Series(0.0, index=df.index)
        st_val_col = 'SUPERT_10_3.0'
        if st_val_col not in st.columns:
            return pd.Series(0.0, index=df.index)
        close = df['Close']
        score = ((close - st[st_val_col]) / close.replace(0, np.nan) * 100).fillna(0.0)
        return score

    def stop_loss_series(self, df: pd.DataFrame) -> pd.Series:
        st = self.supertrend_df(df, length=10, multiplier=3)
        if st is None:
            return None
        st_val_col = 'SUPERT_10_3.0'
        if st_val_col not in st.columns:
            return None
        return st[st_val_col].round(2)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        # Calculate Supertrend
        # pandas-ta returns a DataFrame with columns like SUPERT_7_3.0, SUPERTd_7_3.0, etc.
        st = self.supertrend_df(df, length=10, multiplier=3)
        
        if st is None:
            return False, {}
            
        # Identify the direction column (SUPERTd_10_3.0) 1=Up, -1=Down
        # And the trend line column (SUPERT_10_3.0)
        st_dir_col = 'SUPERTd_10_3.0'
        st_val_col = 'SUPERT_10_3.0'
        
        if st_dir_col not in st.columns:
             return False, {}
             
        # Check for flip from Bearish (-1) to Bullish (1)
        curr_dir = st[st_dir_col].iloc[-1]
        prev_dir = st[st_dir_col].iloc[-2]
        
        if curr_dir == 1 and prev_dir == -1:
             # For Supertrend, the Supertrend Line ITSELF is a very good academic trailing stop
             # But we can provide ATR fallback
             st_line = st[st_val_col].iloc[-1]
             stop_level = round(st_line, 2)
             score = ((df['Close'].iloc[-1] - st_line) / df['Close'].iloc[-1] * 100) if df['Close'].iloc[-1] else 0.0
             
             return True, {
                 'pattern': 'Supertrend Buy', 
                 'st_val': round(st_line, 2),
                 'stop_loss': stop_level,
                 'score': round(score, 2),
                 'sentiment': 'BULLISH'
             }
             
        return False, {}

class CloseAboveEma20(BaseStrategy):
    """
    EMA20 reclaim confirmation: close crosses above EMA20.
    """
    sentiment = "BULLISH"
    direction = "long"
    stop_multiplier = 2.0
    confirmation_lookback_days = 3
    confirmation_weight = 1.0
    consecutive_closes = 1
    require_cross = True
    require_sma200 = False
    slope_lookback = 0
    slope_min = 0.0
    ema_length = 20

    def signal(self, df: pd.DataFrame) -> pd.Series:
        ema_len = int(getattr(self, "ema_length", 20))
        min_len = (ema_len + 1) + max(0, int(self.consecutive_closes) - 1)
        if len(df) < min_len:
            return pd.Series(False, index=df.index)
        close = df["Close"]
        ema = self.ema_series(df, length=ema_len)
        if ema is None:
            return pd.Series(False, index=df.index)
        bars = int(getattr(self, "consecutive_closes", 1) or 1)
        above = close > ema
        if getattr(self, "require_sma200", False):
            sma200 = self.sma_series(df, length=200)
            if sma200 is None:
                return pd.Series(False, index=df.index)
            above &= close > sma200
        slope_lookback = int(getattr(self, "slope_lookback", 0) or 0)
        if slope_lookback > 0:
            slope = ema - ema.shift(slope_lookback)
            slope_min = float(getattr(self, "slope_min", 0.0) or 0.0)
            above &= slope > slope_min
        require_cross = bool(getattr(self, "require_cross", True))
        if bars <= 1:
            signal = above if not require_cross else (above & (close.shift(1) <= ema.shift(1)))
        else:
            signal = above
            for i in range(1, bars):
                signal &= above.shift(i)
            if require_cross:
                prev = above.shift(bars, fill_value=False)
                signal &= ~prev
        signal = signal.fillna(False)
        pad = ema_len + max(0, bars - 1)
        if pad > 0:
            signal.iloc[:pad] = False
        return signal.astype(bool)

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        ema_len = int(getattr(self, "ema_length", 20))
        if len(df) < (ema_len + 1):
            return pd.Series(0.0, index=df.index)
        ema = self.ema_series(df, length=ema_len)
        if ema is None:
            return pd.Series(0.0, index=df.index)
        close = df["Close"]
        score = ((close - ema) / ema.replace(0, np.nan) * 100).fillna(0.0)
        return score

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        ema_len = int(getattr(self, "ema_length", 20))
        bars = int(getattr(self, "consecutive_closes", 1) or 1)
        min_len = (ema_len + 1) + max(0, bars - 1)
        if len(df) < min_len:
            return False, {}
        close = df["Close"]
        ema = self.ema_series(df, length=ema_len)
        if ema is None:
            return False, {}
        if getattr(self, "require_sma200", False):
            sma200 = self.sma_series(df, length=200)
            if sma200 is None:
                return False, {}
            if close.iloc[-1] <= sma200.iloc[-1]:
                return False, {}
        slope_lookback = int(getattr(self, "slope_lookback", 0) or 0)
        if slope_lookback > 0:
            if len(ema) <= slope_lookback:
                return False, {}
            slope = ema.iloc[-1] - ema.iloc[-1 - slope_lookback]
            slope_min = float(getattr(self, "slope_min", 0.0) or 0.0)
            if pd.isna(slope) or slope <= slope_min:
                return False, {}
        require_cross = bool(getattr(self, "require_cross", True))
        if bars <= 1:
            if close.iloc[-1] <= ema.iloc[-1]:
                return False, {}
            if require_cross and close.iloc[-2] > ema.iloc[-2]:
                return False, {}
        else:
            if not (close.iloc[-bars:] > ema.iloc[-bars:]).all():
                return False, {}
            if require_cross and (len(df) <= ema_len + bars or close.iloc[-bars - 1] > ema.iloc[-bars - 1]):
                return False, {}

        dist_pct = (close.iloc[-1] - ema.iloc[-1]) / ema.iloc[-1] * 100 if ema.iloc[-1] else 0.0
        return True, {
            "pattern": f"EMA{ema_len} Reclaim",
            f"ema{ema_len}": round(float(ema.iloc[-1]), 2),
            "dist_pct": round(float(dist_pct), 2),
            "score": round(float(dist_pct), 2),
            "sentiment": "BULLISH",
        }


class CloseAboveEma50(CloseAboveEma20):
    """
    EMA50 reclaim confirmation: close crosses above EMA50.
    """
    ema_length = 50


class AboveEmaState20(CloseAboveEma20):
    """
    EMA20 state confirmation: close stays above EMA20 (no cross required).
    """
    ema_length = 20
    require_cross = False


class AboveEmaState50(CloseAboveEma20):
    """
    EMA50 state confirmation: close stays above EMA50 with positive slope.
    """
    ema_length = 50
    require_cross = False
    slope_lookback = 1
    slope_min = 0.0


class RelativeStrengthLeader(BaseStrategy):
    """
    Relative strength leader filter (benchmark-adjusted rank).
    """
    sentiment = "BULLISH"
    direction = "long"
    benchmark_symbol = "SPY"
    lookback_days = 126
    min_relative_return_pct = 0.0
    min_rank_percentile = 0.8
    confirmation_lookback_days = 0
    confirmation_weight = 1.0
    requires_benchmark = True

    def signal(self, df: pd.DataFrame) -> pd.Series:
        lookback = int(getattr(self, "lookback_days", 126) or 126)
        rank_col = f"RS_RANK_{lookback}"
        rel_col = f"RS_REL_{lookback}"
        if rank_col not in df.columns or rel_col not in df.columns:
            return pd.Series(False, index=df.index)
        rank = df[rank_col]
        rel = df[rel_col]
        min_rank = float(getattr(self, "min_rank_percentile", 0.8) or 0.0)
        if min_rank > 1:
            min_rank /= 100.0
        min_rel = float(getattr(self, "min_relative_return_pct", 0.0) or 0.0)
        signal = (rank >= min_rank) & (rel >= min_rel)
        return signal.fillna(False).astype(bool)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if df.empty:
            return False, {}
        lookback = int(getattr(self, "lookback_days", 126) or 126)
        rank_col = f"RS_RANK_{lookback}"
        rel_col = f"RS_REL_{lookback}"
        if rank_col not in df.columns or rel_col not in df.columns:
            return False, {}
        rank = df[rank_col].iloc[-1]
        rel = df[rel_col].iloc[-1]
        if pd.isna(rank) or pd.isna(rel):
            return False, {}
        min_rank = float(getattr(self, "min_rank_percentile", 0.8) or 0.0)
        if min_rank > 1:
            min_rank /= 100.0
        min_rel = float(getattr(self, "min_relative_return_pct", 0.0) or 0.0)
        if rank < min_rank or rel < min_rel:
            return False, {}
        return True, {
            "pattern": "RS Leader",
            "rs_rank": round(float(rank), 3),
            "rs_rel": round(float(rel), 3),
            "sentiment": "BULLISH",
        }


class TrendGate(BaseStrategy):
    """
    Composite trend gate: EMA50 state + positive slope + RS rank.
    """
    sentiment = "BULLISH"
    direction = "long"
    benchmark_symbol = "SPY"
    lookback_days = 126
    min_relative_return_pct = 0.0
    min_rank_percentile = 0.65
    ema_length = 50
    slope_lookback = 20
    slope_min = 0.0
    confirmation_lookback_days = 0
    confirmation_weight = 1.0
    requires_benchmark = True

    def signal(self, df: pd.DataFrame) -> pd.Series:
        ema_len = int(getattr(self, "ema_length", 50) or 50)
        lookback = int(getattr(self, "lookback_days", 126) or 126)
        min_len = max(ema_len + 1, lookback + 1)
        if len(df) < min_len:
            return pd.Series(False, index=df.index)
        close = df["Close"]
        ema = self.ema_series(df, length=ema_len)
        if ema is None:
            return pd.Series(False, index=df.index)
        signal = close > ema
        slope_lookback = int(getattr(self, "slope_lookback", 20) or 0)
        if slope_lookback > 0:
            slope = ema - ema.shift(slope_lookback)
            slope_min = float(getattr(self, "slope_min", 0.0) or 0.0)
            signal &= slope > slope_min

        rank_col = f"RS_RANK_{lookback}"
        rel_col = f"RS_REL_{lookback}"
        if rank_col not in df.columns or rel_col not in df.columns:
            return pd.Series(False, index=df.index)
        rank = df[rank_col]
        rel = df[rel_col]
        min_rank = float(getattr(self, "min_rank_percentile", 0.65) or 0.0)
        if min_rank > 1:
            min_rank /= 100.0
        min_rel = float(getattr(self, "min_relative_return_pct", 0.0) or 0.0)
        signal &= (rank >= min_rank) & (rel >= min_rel)
        return signal.fillna(False).astype(bool)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        ema_len = int(getattr(self, "ema_length", 50) or 50)
        lookback = int(getattr(self, "lookback_days", 126) or 126)
        if df.empty or len(df) < max(ema_len + 1, lookback + 1):
            return False, {}
        close = df["Close"].iloc[-1]
        ema = self.ema_series(df, length=ema_len)
        if ema is None or pd.isna(ema.iloc[-1]):
            return False, {}
        if close <= ema.iloc[-1]:
            return False, {}
        slope_lookback = int(getattr(self, "slope_lookback", 20) or 0)
        if slope_lookback > 0:
            if len(ema) <= slope_lookback or pd.isna(ema.iloc[-1 - slope_lookback]):
                return False, {}
            slope = ema.iloc[-1] - ema.iloc[-1 - slope_lookback]
            slope_min = float(getattr(self, "slope_min", 0.0) or 0.0)
            if pd.isna(slope) or slope <= slope_min:
                return False, {}

        rank_col = f"RS_RANK_{lookback}"
        rel_col = f"RS_REL_{lookback}"
        if rank_col not in df.columns or rel_col not in df.columns:
            return False, {}
        rank = df[rank_col].iloc[-1]
        rel = df[rel_col].iloc[-1]
        if pd.isna(rank) or pd.isna(rel):
            return False, {}
        min_rank = float(getattr(self, "min_rank_percentile", 0.65) or 0.0)
        if min_rank > 1:
            min_rank /= 100.0
        min_rel = float(getattr(self, "min_relative_return_pct", 0.0) or 0.0)
        if rank < min_rank or rel < min_rel:
            return False, {}

        return True, {
            "pattern": "Trend Gate",
            "ema": round(float(ema.iloc[-1]), 2),
            "rs_rank": round(float(rank), 3),
            "rs_rel": round(float(rel), 3),
            "sentiment": "BULLISH",
        }


class TrendMaturity(BaseStrategy):
    """
    Trend maturity filter: price above EMA for N bars + EMA slope positive.
    """
    sentiment = "BULLISH"
    direction = "long"
    ema_length = 50
    min_bars_above_ema = 10
    min_ema_slope_pct = 0.05
    confirmation_lookback_days = 0
    confirmation_weight = 1.0

    def signal(self, df: pd.DataFrame) -> pd.Series:
        ema_len = int(getattr(self, "ema_length", 50) or 50)
        bars = int(getattr(self, "min_bars_above_ema", 10) or 10)
        if len(df) < ema_len + bars:
            return pd.Series(False, index=df.index)
        close = df["Close"]
        ema = self.ema_series(df, length=ema_len)
        if ema is None:
            return pd.Series(False, index=df.index)
        above = close > ema
        above_ok = above.rolling(bars).sum() >= bars
        slope = (ema - ema.shift(bars)) / ema.shift(bars) * 100.0
        min_slope = float(getattr(self, "min_ema_slope_pct", 0.05) or 0.0)
        slope_ok = slope >= min_slope
        signal = above_ok & slope_ok
        return signal.fillna(False).astype(bool)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        ema_len = int(getattr(self, "ema_length", 50) or 50)
        bars = int(getattr(self, "min_bars_above_ema", 10) or 10)
        if len(df) < ema_len + bars:
            return False, {}
        close = df["Close"]
        ema = self.ema_series(df, length=ema_len)
        if ema is None:
            return False, {}
        if not (close.iloc[-bars:] > ema.iloc[-bars:]).all():
            return False, {}
        if len(ema) <= bars:
            return False, {}
        slope = (ema.iloc[-1] - ema.iloc[-1 - bars]) / ema.iloc[-1 - bars] * 100.0
        min_slope = float(getattr(self, "min_ema_slope_pct", 0.05) or 0.0)
        if pd.isna(slope) or slope < min_slope:
            return False, {}
        return True, {
            "pattern": "Trend Maturity",
            "ema_len": ema_len,
            "slope_pct": round(float(slope), 3),
            "sentiment": "BULLISH",
        }


class NoMansLandFilter(BaseStrategy):
    """
    No-man's-land filter: avoid low ATR and EMA compression zones.
    """
    sentiment = "BULLISH"
    direction = "long"
    ema_fast = 20
    ema_slow = 50
    min_atr_pct = 0.8
    block_if_between_emas = True
    confirmation_lookback_days = 0
    confirmation_weight = 1.0

    def signal(self, df: pd.DataFrame) -> pd.Series:
        fast = int(getattr(self, "ema_fast", 20) or 20)
        slow = int(getattr(self, "ema_slow", 50) or 50)
        min_len = max(fast, slow) + 1
        if len(df) < min_len:
            return pd.Series(False, index=df.index)
        close = df["Close"]
        ema_fast = self.ema_series(df, length=fast)
        ema_slow = self.ema_series(df, length=slow)
        if ema_fast is None or ema_slow is None:
            return pd.Series(False, index=df.index)
        atr = df.get("ATR_14")
        if atr is None:
            atr = self.atr_series(df)
        atr_pct = (atr / close) * 100.0
        min_atr = float(getattr(self, "min_atr_pct", 0.8) or 0.0)
        vol_ok = atr_pct >= min_atr
        between = (close > ema_fast.combine(ema_slow, min)) & (close < ema_fast.combine(ema_slow, max))
        if getattr(self, "block_if_between_emas", True):
            signal = vol_ok & (~between)
        else:
            signal = vol_ok
        return signal.fillna(False).astype(bool)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        fast = int(getattr(self, "ema_fast", 20) or 20)
        slow = int(getattr(self, "ema_slow", 50) or 50)
        min_len = max(fast, slow) + 1
        if len(df) < min_len:
            return False, {}
        close = df["Close"]
        ema_fast = self.ema_series(df, length=fast)
        ema_slow = self.ema_series(df, length=slow)
        if ema_fast is None or ema_slow is None:
            return False, {}
        atr = df.get("ATR_14")
        if atr is None:
            atr = self.atr_series(df)
        atr_pct = (atr / close) * 100.0
        min_atr = float(getattr(self, "min_atr_pct", 0.8) or 0.0)
        if pd.isna(atr_pct.iloc[-1]) or atr_pct.iloc[-1] < min_atr:
            return False, {}
        between = (close.iloc[-1] > min(ema_fast.iloc[-1], ema_slow.iloc[-1])) and (
            close.iloc[-1] < max(ema_fast.iloc[-1], ema_slow.iloc[-1])
        )
        if getattr(self, "block_if_between_emas", True) and between:
            return False, {}
        return True, {
            "pattern": "No-Man's-Land Clear",
            "atr_pct": round(float(atr_pct.iloc[-1]), 3),
            "sentiment": "BULLISH",
        }

class PullbackToEma(BaseStrategy):
    """
    Strong uptrend (EMA 200 rising + Price > EMA 200), price dips to EMA 20 range.
    """
    sentiment = "BULLISH"
    direction = "long"
    stop_multiplier = 2.0
    take_profit_multiplier = 3.0
    time_stop_days = 15
    require_sma200_trend = False
    require_ema50_trend = False
    min_atr_pct = 0.0
    min_pullback_dist_pct = 0.0
    max_pullback_dist_pct = None
    min_pullback_dist_atr = None
    max_pullback_dist_atr = None

    def signal(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 200:
            return pd.Series(False, index=df.index)
        ema20 = self.ema_series(df, length=20)
        ema200 = self.ema_series(df, length=200)
        if ema20 is None or ema200 is None:
            return pd.Series(False, index=df.index)
        close = df['Close']
        low = df['Low']
        trend_ok = (close > ema200) & (ema200 > ema200.shift(1))
        if self.require_sma200_trend:
            sma200 = self.sma_series(df, length=200)
            if sma200 is None:
                return pd.Series(False, index=df.index)
            trend_ok = trend_ok & (close > sma200) & (sma200 > sma200.shift(1))

        ema_trend_ok = True
        if self.require_ema50_trend:
            ema50 = self.ema_series(df, length=50)
            if ema50 is None:
                return pd.Series(False, index=df.index)
            ema_trend_ok = (ema20 > ema50) & (ema50 > ema50.shift(1))

        atr_ok = True
        if self.min_atr_pct and self.min_atr_pct > 0:
            atr = self.atr_series(df, period=14)
            if atr is None:
                return pd.Series(False, index=df.index)
            atr_pct = (atr / close).replace([np.inf, -np.inf], np.nan)
            atr_ok = atr_pct >= self.min_atr_pct

        dist_pct = (close - ema20) / ema20.replace(0, np.nan) * 100
        pullback_ok = dist_pct.abs() >= self.min_pullback_dist_pct
        if self.max_pullback_dist_pct is not None:
            pullback_ok = pullback_ok & (dist_pct.abs() <= self.max_pullback_dist_pct)
        if self.min_pullback_dist_atr is not None or self.max_pullback_dist_atr is not None:
            atr = self.atr_series(df, period=14)
            if atr is None:
                return pd.Series(False, index=df.index)
            dist_atr = (close - ema20).abs() / atr.replace(0, np.nan)
            if self.min_pullback_dist_atr is not None:
                pullback_ok = pullback_ok & (dist_atr >= self.min_pullback_dist_atr)
            if self.max_pullback_dist_atr is not None:
                pullback_ok = pullback_ok & (dist_atr <= self.max_pullback_dist_atr)

        signal = (
            trend_ok
            & ema_trend_ok
            & atr_ok
            & pullback_ok
            & (low <= ema20 * 1.005)
            & (close >= ema20 * 0.995)
        )
        signal = signal.fillna(False)
        signal.iloc[:199] = False
        return signal.astype(bool)

    def stop_loss_series(self, df: pd.DataFrame) -> pd.Series:
        ema20 = self.ema_series(df, length=20)
        if ema20 is None:
            return None
        atr = self.atr_series(df, period=14)
        if atr is None:
            return ema20.round(2)
        stop_level = ema20 - (atr * 0.5)
        return stop_level.round(2)

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 20:
            return pd.Series(0.0, index=df.index)
        ema20 = self.ema_series(df, length=20)
        if ema20 is None:
            return pd.Series(0.0, index=df.index)
        dist_pct = (df['Close'] - ema20) / ema20.replace(0, np.nan) * 100
        score = (2 - dist_pct.abs()).clip(lower=0)
        return score.fillna(0.0)

    def exit_signal(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 20:
            return pd.Series(False, index=df.index)
        rsi = self.rsi_series(df, length=14)
        if rsi is None:
            return pd.Series(False, index=df.index)
        signal = (rsi > 65) & (rsi.shift(1) <= 65)
        return signal.fillna(False).astype(bool)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if len(df) < 200:
            return False, {}
            
        ema20 = self.ema_series(df, length=20)
        ema200 = self.ema_series(df, length=200)
        if ema20 is None or ema200 is None:
            return False, {}
        
        curr_close = df['Close'].iloc[-1]
        curr_low = df['Low'].iloc[-1]
        curr_ema20 = ema20.iloc[-1]
        curr_ema200 = ema200.iloc[-1]
        
        # 1. Trend condition: Close above EMA 200
        if curr_close <= curr_ema200:
            return False, {}
            
        # 2. Trend condition: EMA 200 rising (current > prev)
        if curr_ema200 <= ema200.iloc[-2]:
             return False, {}

        if self.require_sma200_trend:
            sma200 = self.sma_series(df, length=200)
            if sma200 is None:
                return False, {}
            if curr_close <= sma200.iloc[-1] or sma200.iloc[-1] <= sma200.iloc[-2]:
                return False, {}

        if self.require_ema50_trend:
            ema50 = self.ema_series(df, length=50)
            if ema50 is None:
                return False, {}
            if curr_ema20 <= ema50.iloc[-1] or ema50.iloc[-1] <= ema50.iloc[-2]:
                return False, {}

        if self.min_atr_pct and self.min_atr_pct > 0:
            atr = self.atr_series(df, period=14)
            if atr is None:
                return False, {}
            atr_pct = atr.iloc[-1] / curr_close if curr_close else 0.0
            if pd.isna(atr_pct) or atr_pct < self.min_atr_pct:
                return False, {}
             
        # 3. Pullback condition: Low touches EMA 20 (within 1% tolerance) 
        # but Close is still above EMA 200 (checked above)
        
        if curr_low <= curr_ema20 * 1.005 and curr_close >= curr_ema20 * 0.995:
             dist_pct = (curr_close - curr_ema20) / curr_ema20 * 100
             if abs(dist_pct) < self.min_pullback_dist_pct:
                 return False, {}
             if self.max_pullback_dist_pct is not None and abs(dist_pct) > self.max_pullback_dist_pct:
                 return False, {}
             if self.min_pullback_dist_atr is not None or self.max_pullback_dist_atr is not None:
                 atr = self.atr_series(df, period=14)
                 if atr is None or pd.isna(atr.iloc[-1]) or atr.iloc[-1] == 0:
                     return False, {}
                 dist_atr = abs(curr_close - curr_ema20) / atr.iloc[-1]
                 if self.min_pullback_dist_atr is not None and dist_atr < self.min_pullback_dist_atr:
                     return False, {}
                 if self.max_pullback_dist_atr is not None and dist_atr > self.max_pullback_dist_atr:
                     return False, {}
             # Stop: 2x ATR below Low (Academic standard for volatility adjustment)
             stop_level = self.calculate_atr_stop(df, direction='long', multiplier=2.0)
             
             return True, {
                 'pattern': 'EMA 20 Pullback', 
                 'ema20': round(curr_ema20, 2), 
                 'dist_pct': round(dist_pct, 2), 
                 'stop_loss': stop_level,
                 'score': round(max(0.0, 2 - abs(dist_pct)), 2),
                 'sentiment': 'BULLISH'
             }
             
        return False, {}


class DonchianBreakout(BaseStrategy):
    """
    Donchian breakout: close breaks above prior N-bar high.
    """
    sentiment = "BULLISH"
    direction = "long"
    stop_multiplier = 2.0
    donchian_lookback = 55
    atr_lookback = 14
    min_atr_pct = 0.0
    stop_mode = "donchian_low"
    stop_lookback = 20
    atr_stop_mult = 2.0
    donchian_buffer_atr = 0.0

    def _donchian_high(self, df: pd.DataFrame, lookback: int) -> pd.Series:
        return df["High"].rolling(lookback).max().shift(1)

    def _donchian_low(self, df: pd.DataFrame, lookback: int) -> pd.Series:
        return df["Low"].rolling(lookback).min().shift(1)

    def signal(self, df: pd.DataFrame) -> pd.Series:
        lookback = int(getattr(self, "donchian_lookback", 55) or 55)
        if len(df) < (lookback + 1):
            return pd.Series(False, index=df.index)
        close = df["Close"]
        donchian_high = self._donchian_high(df, lookback)
        buffer_atr = float(getattr(self, "donchian_buffer_atr", 0.0) or 0.0)
        atr = None
        if buffer_atr > 0.0 or float(getattr(self, "min_atr_pct", 0.0) or 0.0) > 0.0:
            atr_len = int(getattr(self, "atr_lookback", 14) or 14)
            atr = self.atr_series(df, period=atr_len)
            if atr is None:
                return pd.Series(False, index=df.index)
        trigger = donchian_high
        if buffer_atr > 0.0 and atr is not None:
            trigger = donchian_high + (atr * buffer_atr)
        signal = close > trigger
        min_atr_pct = float(getattr(self, "min_atr_pct", 0.0) or 0.0)
        if min_atr_pct > 0.0:
            if atr is None:
                atr_len = int(getattr(self, "atr_lookback", 14) or 14)
                atr = self.atr_series(df, period=atr_len)
                if atr is None:
                    return pd.Series(False, index=df.index)
            atr_pct = (atr / close).replace([np.inf, -np.inf], np.nan) * 100.0
            signal &= atr_pct >= min_atr_pct
        signal = signal.fillna(False)
        signal.iloc[:lookback] = False
        return signal.astype(bool)

    def stop_loss_series(self, df: pd.DataFrame) -> pd.Series:
        stop_mode = getattr(self, "stop_mode", "donchian_low")
        if stop_mode == "atr":
            atr_len = int(getattr(self, "atr_lookback", 14) or 14)
            atr = self.atr_series(df, period=atr_len)
            if atr is None:
                return None
            mult = float(getattr(self, "atr_stop_mult", 2.0) or 2.0)
            stop_level = df["Close"] - (atr * mult)
            return stop_level.round(2)
        lookback = int(getattr(self, "stop_lookback", 20) or 20)
        stop_level = self._donchian_low(df, lookback)
        return stop_level.round(2)

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        lookback = int(getattr(self, "donchian_lookback", 55) or 55)
        if len(df) < (lookback + 1):
            return pd.Series(0.0, index=df.index)
        donchian_high = self._donchian_high(df, lookback)
        score = (df["Close"] - donchian_high) / donchian_high.replace(0, np.nan) * 100.0
        return score.fillna(0.0)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        lookback = int(getattr(self, "donchian_lookback", 55) or 55)
        if len(df) < (lookback + 1):
            return False, {}
        close = df["Close"].iloc[-1]
        donchian_high = self._donchian_high(df, lookback).iloc[-1]
        buffer_atr = float(getattr(self, "donchian_buffer_atr", 0.0) or 0.0)
        atr_pct_val = None
        trigger = donchian_high
        if buffer_atr > 0.0 or float(getattr(self, "min_atr_pct", 0.0) or 0.0) > 0.0:
            atr_len = int(getattr(self, "atr_lookback", 14) or 14)
            atr = self.atr_series(df, period=atr_len)
            if atr is None or pd.isna(atr.iloc[-1]):
                return False, {}
            if buffer_atr > 0.0:
                trigger = donchian_high + (atr.iloc[-1] * buffer_atr)
            atr_pct_val = (atr.iloc[-1] / close) * 100.0 if close else 0.0
        if pd.isna(donchian_high) or close <= trigger:
            return False, {}
        min_atr_pct = float(getattr(self, "min_atr_pct", 0.0) or 0.0)
        if min_atr_pct > 0.0:
            if atr_pct_val is None:
                atr_len = int(getattr(self, "atr_lookback", 14) or 14)
                atr = self.atr_series(df, period=atr_len)
                if atr is None or pd.isna(atr.iloc[-1]):
                    return False, {}
                atr_pct_val = (atr.iloc[-1] / close) * 100.0 if close else 0.0
            if pd.isna(atr_pct_val) or atr_pct_val < min_atr_pct:
                return False, {}

        stop_series = self.stop_loss_series(df)
        stop_level = stop_series.iloc[-1] if stop_series is not None else None
        score = (close - donchian_high) / donchian_high * 100.0 if donchian_high else 0.0
        metrics = {
            "pattern": "Donchian Breakout",
            "donchian_high": round(float(donchian_high), 2),
            "donchian_trigger": round(float(trigger), 2),
            "stop_loss": round(float(stop_level), 2) if stop_level is not None else None,
            "score": round(float(score), 2),
            "sentiment": "BULLISH",
        }
        if atr_pct_val is not None:
            metrics["atr_pct"] = round(float(atr_pct_val), 3)
        return True, metrics
