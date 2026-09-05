"""
Price action and pattern-based screening strategies.
"""
import numpy as np
import pandas as pd
from stock_screener.core.strategy import BaseStrategy
from typing import Tuple, Dict, Any

class BullishEngulfing(BaseStrategy):
    """
    Bullish Engulfing Pattern:
    1. Previous candle Red
    2. Current candle Green
    3. Current Body completely engulfs Previous Body
    """
    sentiment = "BULLISH"
    direction = "long"
    stop_multiplier = 2.0
    take_profit_multiplier = 2.0
    time_stop_days = 10
    confirmation_lookback_days = 2
    confirmation_weight = 1.0
    require_sma50_trend = True

    def signal(self, df: pd.DataFrame) -> pd.Series:
        min_len = 50 if self.require_sma50_trend else 2
        if len(df) < min_len:
            return pd.Series(False, index=df.index)
        open_ = df['Open']
        close = df['Close']
        prev_open = open_.shift(1)
        prev_close = close.shift(1)
        if self.require_sma50_trend:
            sma50 = self.sma_series(df, length=50)
            if sma50 is None:
                return pd.Series(False, index=df.index)
            trend_ok = (close > sma50) & (sma50 > sma50.shift(1))
        else:
            trend_ok = pd.Series(True, index=df.index)
        signal = (
            (prev_close < prev_open)
            & (close > open_)
            & (open_ <= prev_close)
            & (close > prev_open)
            & trend_ok
        )
        signal = signal.fillna(False)
        if self.require_sma50_trend:
            signal.iloc[:49] = False
        return signal.astype(bool)

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 2:
            return pd.Series(0.0, index=df.index)
        body = (df['Close'] - df['Open']).abs()
        prev_body = body.shift(1)
        ratio = body / prev_body.replace(0, np.nan)
        return ratio.fillna(0.0)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        min_len = 50 if self.require_sma50_trend else 2
        if len(df) < min_len:
            return False, {}
        
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        if self.require_sma50_trend:
            sma50 = self.sma_series(df, length=50)
            if sma50 is None:
                return False, {}
            if curr['Close'] <= sma50.iloc[-1]:
                return False, {}
            if sma50.iloc[-1] <= sma50.iloc[-2]:
                return False, {}
        
        # Previous Red
        if prev['Close'] >= prev['Open']:
            return False, {}
        
        # Current Green
        if curr['Close'] <= curr['Open']:
            return False, {}
        
        # Engulfing Body
        # Current Open < Previous Close (Gap down or equal) - Ideal
        # Current Close > Previous Open
        
        if curr['Open'] <= prev['Close'] and curr['Close'] > prev['Open']:
            # Stop: 2x ATR below Low (Academic Standard)
            stop_level = self.calculate_atr_stop(df, direction='long', multiplier=2.0)
            prev_body = abs(prev['Close'] - prev['Open'])
            curr_body = abs(curr['Close'] - curr['Open'])
            engulf_ratio = (curr_body / prev_body) if prev_body else 0.0
            return True, {
                'pattern': 'Bullish Engulfing', 
                'stop_loss': stop_level,
                'engulf_ratio': round(engulf_ratio, 2),
                'score': round(engulf_ratio, 2),
                'sentiment': 'BULLISH'
            }
            
        return False, {}

class VolumeSpike(BaseStrategy):
    """
    Unusual Volume Activity:
    Volume > 300% of 20-day Average AND Price Up.
    """
    sentiment = "BULLISH"
    direction = "long"
    stop_multiplier = 2.0
    take_profit_multiplier = 3.0
    confirmation_lookback_days = 2
    confirmation_weight = 2.0

    def signal(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 50:
            return pd.Series(False, index=df.index)
        close = df['Close']
        open_ = df['Open']
        volume = df['Volume']
        avg_vol = self.volume_sma_series(df, length=20).shift(1)
        ratio = volume / avg_vol
        high = df['High']
        low = df['Low']
        range_ = (high - low)
        close_loc = (close - low) / range_.replace(0, np.nan)
        sma50 = self.sma_series(df, length=50)
        trend_ok = (close > sma50) & (sma50 > sma50.shift(1))
        signal = (close > open_) & (avg_vol != 0) & (ratio >= 3.0) & (close_loc >= 0.7) & trend_ok
        signal = signal.fillna(False)
        signal.iloc[:49] = False
        return signal.astype(bool)

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 21:
            return pd.Series(0.0, index=df.index)
        volume = df['Volume']
        avg_vol = self.volume_sma_series(df, length=20).shift(1)
        ratio = volume / avg_vol
        return ratio.fillna(0.0)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if len(df) < 50:
            return False, {}
        
        curr_vol = df['Volume'].iloc[-1]
        avg_vol = self.volume_sma_series(df, length=20).iloc[-2]  # Rolling mean excluding today ideally, or including is fine
        sma50 = self.sma_series(df, length=50)
        
        # Price Up
        if df['Close'].iloc[-1] <= df['Open'].iloc[-1]:
            return False, {}
        if df['Close'].iloc[-1] <= sma50.iloc[-1]:
            return False, {}
        if sma50.iloc[-1] <= sma50.iloc[-2]:
            return False, {}
        
        if avg_vol == 0:
            return False, {}
        
        ratio = curr_vol / avg_vol
        curr_high = df['High'].iloc[-1]
        curr_low = df['Low'].iloc[-1]
        range_ = curr_high - curr_low
        if range_ <= 0:
            return False, {}
        close_loc = (df['Close'].iloc[-1] - curr_low) / range_
        
        if ratio >= 3.0 and close_loc >= 0.7:
            # Stop: 2x ATR below Low
            stop_level = self.calculate_atr_stop(df, direction='long', multiplier=2.0)
            return True, {
                'pattern': 'Volume Spike', 
                'ratio': round(ratio, 2),
                'close_loc': round(close_loc, 2),
                'stop_loss': stop_level,
                'score': round(ratio, 2),
                'sentiment': 'BULLISH'
            }
            
        return False, {}
