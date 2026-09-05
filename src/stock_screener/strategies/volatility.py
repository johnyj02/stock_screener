"""
Volatility-based screening strategies.
"""
import pandas as pd
from stock_screener.core.strategy import BaseStrategy
from typing import Tuple, Dict, Any

INTRADAY_MIN_RANGE_PCT = 0.0015
DAILY_MIN_RANGE_PCT = 0.008
INTRADAY_MAX_MINUTES = 720

def _bar_minutes(df: pd.DataFrame) -> float:
    if df.index is None or len(df.index) < 2:
        return 0.0
    diffs = df.index.to_series().diff().dropna()
    if diffs.empty:
        return 0.0
    return diffs.dt.total_seconds().median() / 60.0

def _resample_daily(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.resample("1D")
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

class BollingerSqueeze(BaseStrategy):
    """
    Bollinger Band Squeeze: Bandwidth is is at a generalized low, indicating expansion soon.
    """
    sentiment = "WATCH"
    direction = "long"
    stop_multiplier = 2.0

    def signal(self, df: pd.DataFrame) -> pd.Series:
        bb = self.bbands_df(df, length=20, std=2)
        if bb is None or 'BBB_20_2.0' not in bb.columns:
            return pd.Series(False, index=df.index)
        bw = bb['BBB_20_2.0']
        lookback = 125
        min_bw = bw.rolling(lookback).min()
        signal = bw <= (min_bw * 1.05)
        signal = signal.fillna(False)
        if len(signal) >= lookback:
            signal.iloc[: lookback - 1] = False
        return signal.astype(bool)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        # BBANDS(length=20, std=2)
        bb = self.bbands_df(df, length=20, std=2)
        if bb is None:
            return False, {}
        
        # Columns: BBL_20_2.0, BBM_20_2.0, BBU_20_2.0, BBB_20_2.0 (Bandwidth), BBP_20_2.0 (%B)
        bw_col = 'BBB_20_2.0'
        
        if bw_col not in bb.columns:
            return False, {}
        
        curr_bw = bb[bw_col].iloc[-1]
        
        # Check if current bandwidth is the lowest in 6 months (approx 125 bars)
        lookback = 125
        if len(bb) < lookback:
            return False, {}
        
        min_bw = bb[bw_col].rolling(lookback).min().iloc[-1]
        
        if curr_bw <= min_bw * 1.05:
            # Standard Deviation Breakout levels are classic for Squeeze
            breakout_high = bb['BBU_20_2.0'].iloc[-1]
            breakout_low = bb['BBL_20_2.0'].iloc[-1]
            return True, {
                'pattern': 'Bollinger Squeeze', 
                'bandwidth': round(curr_bw, 4), 
                'breakout_lvl': f">{breakout_high:.2f} / <{breakout_low:.2f}",
                'sentiment': 'WATCH'
            }
            
        return False, {}

class Nr4Nr7(BaseStrategy):
    """
    Deprecated. Use Nr4Nr7Intraday or Nr4Nr7Daily.
    """
    sentiment = "WATCH"
    direction = "long"
    stop_multiplier = 2.0

    def signal(self, df: pd.DataFrame) -> pd.Series:
        return pd.Series(False, index=df.index)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        return False, {}

class Nr4Nr7Intraday(BaseStrategy):
    """
    NR4 / NR7 on intraday bars (5m/15m/1h) with a minimum range percent.
    Soft biasing based on Daily NR7 and daily EMA trend.
    """
    sentiment = "WATCH"
    direction = "long"
    stop_multiplier = 2.0

    def signal(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 8 or _bar_minutes(df) >= INTRADAY_MAX_MINUTES:
            return pd.Series(False, index=df.index)
        ranges = df['High'] - df['Low']
        range_pct = ranges / df['Close']
        min4 = ranges.rolling(4).min()
        min7 = ranges.rolling(7).min()
        signal = ((ranges == min4) | (ranges == min7)) & (range_pct >= INTRADAY_MIN_RANGE_PCT)
        signal = signal.fillna(False)
        signal.iloc[:7] = False
        return signal.astype(bool)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if len(df) < 8 or _bar_minutes(df) >= INTRADAY_MAX_MINUTES:
            return False, {}

        ranges = df['High'] - df['Low']
        range_pct = ranges / df['Close']
        curr_range = ranges.iloc[-1]
        curr_range_pct = range_pct.iloc[-1]
        if pd.isna(curr_range_pct) or curr_range_pct < INTRADAY_MIN_RANGE_PCT:
            return False, {}

        ranges_4 = ranges.iloc[-4:]
        ranges_7 = ranges.iloc[-7:]
        is_nr4 = curr_range == ranges_4.min()
        is_nr7 = curr_range == ranges_7.min()
        if not (is_nr4 or is_nr7):
            return False, {}

        daily_bias = False
        daily_trend_bullish = False
        daily_trend_bearish = False
        if _bar_minutes(df) > 0:
            daily = _resample_daily(df)
            if len(daily) >= 7:
                drange = daily['High'] - daily['Low']
                drange_pct = drange / daily['Close']
                daily_nr7 = drange.iloc[-1] == drange.iloc[-7:].min()
                daily_bias = daily_nr7 and (drange_pct.iloc[-1] >= DAILY_MIN_RANGE_PCT)

                ema20 = self.ema_series(daily, length=20)
                ema50 = self.ema_series(daily, length=50)
                if ema20 is not None and ema50 is not None and len(ema50) > 1:
                    daily_trend_bullish = (ema20.iloc[-1] > ema50.iloc[-1]) and (ema50.iloc[-1] > ema50.iloc[-2])
                    daily_trend_bearish = (ema20.iloc[-1] < ema50.iloc[-1]) and (ema50.iloc[-1] < ema50.iloc[-2])

        vol_avg = self.volume_sma_series(df, length=20).iloc[-1]
        vol_contraction = vol_avg > 0 and df['Volume'].iloc[-1] < (vol_avg * 0.8)

        score = 2
        if daily_bias:
            score += 2
        if daily_trend_bullish:
            score += 2
        if vol_contraction:
            score += 1

        if score >= 5:
            tier = "A+"
        elif score >= 3:
            tier = "Valid"
        else:
            tier = "Ignore"

        curr_high = df['High'].iloc[-1]
        curr_low = df['Low'].iloc[-1]
        pattern = "NR4/NR7 Intraday" if is_nr4 and is_nr7 else ("NR4 Intraday" if is_nr4 else "NR7 Intraday")
        if daily_trend_bullish:
            trend_alignment = "bullish"
        elif daily_trend_bearish:
            trend_alignment = "bearish"
        else:
            trend_alignment = "flat"

        return True, {
            'pattern': pattern,
            'range': round(curr_range, 2),
            'range_pct': round(curr_range_pct * 100, 2),
            'breakout_lvl': f">{curr_high:.2f} / <{curr_low:.2f}",
            'daily_nr7_bias': daily_bias,
            'daily_trend_bullish': daily_trend_bullish,
            'daily_trend_bearish': daily_trend_bearish,
            'trend_alignment': trend_alignment,
            'vol_contraction': vol_contraction,
            'setup_score': score,
            'setup_tier': tier,
            'sentiment': 'WATCH',
        }

class Nr4Nr7Daily(BaseStrategy):
    """
    NR4 / NR7 on daily bars with a minimum daily range percent.
    """
    sentiment = "WATCH"
    direction = "long"
    stop_multiplier = 2.0

    def signal(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 8 or _bar_minutes(df) < INTRADAY_MAX_MINUTES:
            return pd.Series(False, index=df.index)
        ranges = df['High'] - df['Low']
        range_pct = ranges / df['Close']
        min4 = ranges.rolling(4).min()
        min7 = ranges.rolling(7).min()
        signal = ((ranges == min4) | (ranges == min7)) & (range_pct >= DAILY_MIN_RANGE_PCT)
        signal = signal.fillna(False)
        signal.iloc[:7] = False
        return signal.astype(bool)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if len(df) < 8 or _bar_minutes(df) < INTRADAY_MAX_MINUTES:
            return False, {}

        ranges = df['High'] - df['Low']
        range_pct = ranges / df['Close']
        curr_range = ranges.iloc[-1]
        curr_range_pct = range_pct.iloc[-1]
        if pd.isna(curr_range_pct) or curr_range_pct < DAILY_MIN_RANGE_PCT:
            return False, {}

        ranges_4 = ranges.iloc[-4:]
        ranges_7 = ranges.iloc[-7:]
        is_nr4 = curr_range == ranges_4.min()
        is_nr7 = curr_range == ranges_7.min()
        if not (is_nr4 or is_nr7):
            return False, {}

        ema20 = self.ema_series(df, length=20)
        ema50 = self.ema_series(df, length=50)
        trend_bullish = False
        trend_bearish = False
        if ema20 is not None and ema50 is not None and len(ema50) > 1:
            trend_bullish = (ema20.iloc[-1] > ema50.iloc[-1]) and (ema50.iloc[-1] > ema50.iloc[-2])
            trend_bearish = (ema20.iloc[-1] < ema50.iloc[-1]) and (ema50.iloc[-1] < ema50.iloc[-2])

        if trend_bullish:
            trend_alignment = "bullish"
        elif trend_bearish:
            trend_alignment = "bearish"
        else:
            trend_alignment = "flat"

        curr_high = df['High'].iloc[-1]
        curr_low = df['Low'].iloc[-1]
        pattern = "NR4/NR7 Daily" if is_nr4 and is_nr7 else ("NR4 Daily" if is_nr4 else "NR7 Daily")
        return True, {
            'pattern': pattern,
            'range': round(curr_range, 2),
            'range_pct': round(curr_range_pct * 100, 2),
            'breakout_lvl': f">{curr_high:.2f} / <{curr_low:.2f}",
            'daily_trend_bullish': trend_bullish,
            'daily_trend_bearish': trend_bearish,
            'trend_alignment': trend_alignment,
            'sentiment': 'WATCH',
        }
