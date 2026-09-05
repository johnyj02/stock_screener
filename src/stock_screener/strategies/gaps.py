"""
Gap-based strategies for daily bars.
"""
import numpy as np
import pandas as pd
from typing import Tuple, Dict, Any
from stock_screener.core.strategy import BaseStrategy

GAP_CONT_MIN = 0.025
GAP_CONT_MAX = 0.04
GAP_CONT_LARGE_MAX = 0.09
GAP_CONT_VOL_MIN = 1.8
GAP_CONT_VOL_LARGE = 2.2
GAP_CONT_CLOSE_MIN = 0.85
GAP_CONT_CLOSE_LARGE = 0.90

GAP_FADE_PCT = 0.04
VOL_MULT = 1.5
MAX_OPEN_EMA20_ATR = 2.0
MAX_OPEN_EMA20_PCT = 1.04
MIN_OPEN_EMA20_PCT = 0.96
MAX_OPEN_EXTENSION_ATR = 1.0
CHASE_VOL_RATIO = 2.5
CHASE_CLOSE_LOC = 0.9


def _is_daily(df: pd.DataFrame) -> bool:
    if df.index is None or len(df.index) < 2:
        return True
    diffs = df.index.to_series().diff().dropna()
    if diffs.empty:
        return True
    return diffs.dt.total_seconds().median() >= 60 * 60 * 12


def _close_location(df: pd.DataFrame) -> pd.Series:
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    range_ = (high - low).replace(0, np.nan)
    return (close - low) / range_


class GapUpContinuation(BaseStrategy):
    """
    Gap up with strong close and volume expansion (trend continuation).
    """
    sentiment = "BULLISH"
    direction = "long"
    stop_multiplier = 2.0
    take_profit_multiplier = 3.0
    time_stop_days = 5
    stop_mode = "structure_first"
    require_regime_stability = True
    allow_regime_bypass_large_gap = True
    avoid_regime_flip = True
    tight_stop_bars = 2
    max_initial_r_pct = 0.04
    exit_on_d1_close_below_gap_low = True
    exit_on_d2_no_higher_high = True
    large_gap_follow_through = True
    giveback_enabled = True
    giveback_trigger_r = 2.0
    giveback_floor_r = 1.0
    giveback_min_days = 2

    def signal(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 50 or not _is_daily(df):
            return pd.Series(False, index=df.index)
        close = df["Close"]
        open_ = df["Open"]
        volume = df["Volume"]
        prev_close = close.shift(1)
        gap_pct = (open_ - prev_close) / prev_close
        avg_vol = self.volume_sma_series(df, length=20)
        vol_ratio = volume / avg_vol
        close_loc = _close_location(df)
        day_range = df["High"] - df["Low"]

        ema20 = self.ema_series(df, length=20)
        ema50 = self.ema_series(df, length=50)
        if ema20 is None or ema50 is None:
            return pd.Series(False, index=df.index)
        trend_ok = (ema20 > ema50) & (ema50 > ema50.shift(1))
        atr = self.atr_series(df, period=14)
        range_exp = atr is not None and (day_range > (atr * 1.2))
        open_ext = (open_ > (ema20 * MAX_OPEN_EMA20_PCT))
        chase_ext = pd.Series(False, index=df.index)
        if atr is not None:
            open_ext = open_ext | (open_ > (ema20 + (atr * MAX_OPEN_EMA20_ATR)))
            atr_safe = atr.replace(0, np.nan)
            body_atr = (close - open_) / atr_safe
            chase_ext = body_atr > MAX_OPEN_EXTENSION_ATR
            chase_override = (vol_ratio >= CHASE_VOL_RATIO) & (close_loc >= CHASE_CLOSE_LOC)
            chase_ext = chase_ext & (~chase_override)

        mid_gap = (
            (gap_pct >= GAP_CONT_MIN)
            & (gap_pct <= GAP_CONT_MAX)
            & (vol_ratio >= GAP_CONT_VOL_MIN)
            & (close_loc >= GAP_CONT_CLOSE_MIN)
        )
        large_gap = (
            (gap_pct > GAP_CONT_MAX)
            & (gap_pct <= GAP_CONT_LARGE_MAX)
            & (vol_ratio >= GAP_CONT_VOL_LARGE)
            & (close_loc >= GAP_CONT_CLOSE_LARGE)
            & range_exp
        )
        signal = (mid_gap | large_gap) & trend_ok & (~open_ext) & (~chase_ext)
        signal = signal.fillna(False)
        signal.iloc[:49] = False
        return signal.astype(bool)

    def stop_loss_series(self, df: pd.DataFrame) -> pd.Series:
        entry = self.signal(df).astype(int)
        idx = pd.Series(range(len(df)), index=df.index)
        entry_id = entry.cumsum()
        last_entry = idx.where(entry == 1).ffill()
        bars_since = (idx - last_entry).fillna(999)
        entry_low = df["Low"].where(entry == 1).ffill()
        entry_price = df["Close"].where(entry == 1).ffill()

        atr = self.atr_series(df, period=14)
        ema20 = self.ema_series(df, length=20)
        if atr is None or ema20 is None:
            return None
        close = df["Close"]
        low = df["Low"]
        stop_atr = close - (atr * 1.5)
        trail = ema20 - (atr * 0.5)
        stop_mode = getattr(self, "stop_mode", "tightest")
        if stop_mode == "tightest":
            stop_level = pd.concat([low, stop_atr, trail], axis=1).max(axis=1)
            tight_mask = bars_since <= (self.tight_stop_bars - 1)
            stop_level = stop_level.where(~tight_mask, entry_low)
            if self.max_initial_r_pct:
                cap_stop = entry_price - (entry_price * self.max_initial_r_pct)
                stop_level = pd.concat([stop_level, cap_stop], axis=1).max(axis=1)
        else:
            initial_stop = stop_atr if stop_mode == "atr_first" else entry_low
            if self.max_initial_r_pct:
                cap_stop = entry_price - (entry_price * self.max_initial_r_pct)
                initial_stop = pd.concat([initial_stop, cap_stop], axis=1).max(axis=1)
            initial_risk = entry_price - initial_stop
            day1_confirm = (bars_since == 1) & (close > entry_price)
            r_trigger = close >= (entry_price + (initial_risk * 0.75))
            validation = (day1_confirm | r_trigger)
            validated = validation.groupby(entry_id).cummax()
            validated = validated.where(entry_id > 0, False)
            trail_stop = pd.concat([initial_stop, trail], axis=1).max(axis=1)
            stop_level = initial_stop.where(~validated, trail_stop)
        return stop_level.round(2)

    def exit_signal(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 3:
            return pd.Series(False, index=df.index)
        entry = self.signal(df)
        open_ = df["Open"]
        close = df["Close"]
        low = df["Low"]
        high = df["High"]
        prev_close = close.shift(1)
        gap_pct = (open_ - prev_close) / prev_close
        large_gap = (gap_pct > GAP_CONT_MAX) & (gap_pct <= GAP_CONT_LARGE_MAX)
        day1_fail = entry.shift(1) & (close < low.shift(1))
        high_roll3 = high.rolling(3).max()
        day2_no_follow = entry.shift(2) & (high_roll3 <= high.shift(2))
        signal = pd.Series(False, index=df.index)
        if self.exit_on_d1_close_below_gap_low:
            signal = signal | day1_fail
        if self.exit_on_d2_no_higher_high:
            signal = signal | day2_no_follow
        if self.large_gap_follow_through:
            entry_large = entry & large_gap
            day1_large = entry_large.shift(1)
            day1_follow_fail = day1_large & (
                (high <= high.shift(1)) | (close <= close.shift(1))
            )
            signal = signal | day1_follow_fail
        signal = signal.fillna(False)
        return signal.astype(bool)

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 2:
            return pd.Series(0.0, index=df.index)
        close = df["Close"]
        open_ = df["Open"]
        volume = df["Volume"]
        prev_close = close.shift(1)
        gap_pct = (open_ - prev_close) / prev_close
        avg_vol = self.volume_sma_series(df, length=20)
        vol_ratio = volume / avg_vol
        close_loc = _close_location(df)
        score = (gap_pct.fillna(0.0) * 100) + (vol_ratio.fillna(0.0) * 2.0) + (close_loc.fillna(0.0) * 10.0)
        return score.fillna(0.0)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if len(df) < 50 or not _is_daily(df):
            return False, {}
        close = df["Close"]
        open_ = df["Open"]
        volume = df["Volume"]
        prev_close = close.iloc[-2]
        gap_pct = (open_.iloc[-1] - prev_close) / prev_close
        avg_vol = self.volume_sma_series(df, length=20).iloc[-1]
        if pd.isna(avg_vol) or avg_vol == 0:
            return False, {}
        vol_ratio = volume.iloc[-1] / avg_vol
        close_loc = _close_location(df).iloc[-1]
        day_range = df["High"].iloc[-1] - df["Low"].iloc[-1]

        ema20 = self.ema_series(df, length=20)
        ema50 = self.ema_series(df, length=50)
        if ema20 is None or ema50 is None:
            return False, {}
        if ema20.iloc[-1] <= ema50.iloc[-1] or ema50.iloc[-1] <= ema50.iloc[-2]:
            return False, {}

        atr = self.atr_series(df, period=14)
        range_exp = atr is not None and (day_range > (atr.iloc[-1] * 1.2))
        open_ext = open_.iloc[-1] > (ema20.iloc[-1] * MAX_OPEN_EMA20_PCT)
        chase_ext = False
        if atr is not None:
            open_ext = open_ext or (open_.iloc[-1] > (ema20.iloc[-1] + (atr.iloc[-1] * MAX_OPEN_EMA20_ATR)))
            if atr.iloc[-1] > 0:
                body_atr = (close.iloc[-1] - open_.iloc[-1]) / atr.iloc[-1]
                chase_ext = body_atr > MAX_OPEN_EXTENSION_ATR
                chase_override = (vol_ratio >= CHASE_VOL_RATIO) and (close_loc >= CHASE_CLOSE_LOC)
                chase_ext = chase_ext and (not chase_override)
        mid_gap = (
            (gap_pct >= GAP_CONT_MIN)
            and (gap_pct <= GAP_CONT_MAX)
            and (vol_ratio >= GAP_CONT_VOL_MIN)
            and (close_loc >= GAP_CONT_CLOSE_MIN)
        )
        large_gap = (
            (gap_pct > GAP_CONT_MAX)
            and (gap_pct <= GAP_CONT_LARGE_MAX)
            and (vol_ratio >= GAP_CONT_VOL_LARGE)
            and (close_loc >= GAP_CONT_CLOSE_LARGE)
            and range_exp
        )
        if (mid_gap or large_gap) and not open_ext and not chase_ext:
            stop_level = self.calculate_atr_stop(df, direction='long', multiplier=2.0)
            score = (gap_pct * 100) + (vol_ratio * 2.0) + (close_loc * 10.0)
            gap_bucket = "large" if large_gap else "mid"
            return True, {
                "pattern": "Gap Up Continuation",
                "gap_pct": round(gap_pct * 100, 2),
                "vol_ratio": round(vol_ratio, 2),
                "close_loc": round(close_loc, 2),
                "stop_loss": stop_level,
                "score": round(score, 2),
                "gap_bucket": gap_bucket,
                "sentiment": "BULLISH",
            }
        return False, {}


class GapDownContinuation(BaseStrategy):
    """
    Gap down with weak close and volume expansion (trend continuation).
    """
    sentiment = "BEARISH"
    direction = "short"
    stop_multiplier = 2.0
    take_profit_multiplier = 3.0
    time_stop_days = 5
    stop_mode = "structure_first"
    allow_regime_bypass_large_gap = True
    avoid_regime_flip = True
    tight_stop_bars = 2
    max_initial_r_pct = 0.04
    exit_on_d1_close_above_gap_high = True
    exit_on_d2_no_lower_low = True
    large_gap_follow_through = True
    giveback_enabled = True
    giveback_trigger_r = 2.0
    giveback_floor_r = 1.0
    giveback_min_days = 2

    def signal(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 50 or not _is_daily(df):
            return pd.Series(False, index=df.index)
        close = df["Close"]
        open_ = df["Open"]
        volume = df["Volume"]
        prev_close = close.shift(1)
        gap_pct = (open_ - prev_close) / prev_close
        avg_vol = self.volume_sma_series(df, length=20)
        vol_ratio = volume / avg_vol
        close_loc = _close_location(df)
        day_range = df["High"] - df["Low"]

        ema20 = self.ema_series(df, length=20)
        ema50 = self.ema_series(df, length=50)
        if ema20 is None or ema50 is None:
            return pd.Series(False, index=df.index)
        trend_ok = (ema20 < ema50) & (ema50 < ema50.shift(1))
        atr = self.atr_series(df, period=14)
        range_exp = atr is not None and (day_range > (atr * 1.2))
        open_ext = (open_ < (ema20 * MIN_OPEN_EMA20_PCT))
        chase_ext = pd.Series(False, index=df.index)
        if atr is not None:
            open_ext = open_ext | (open_ < (ema20 - (atr * MAX_OPEN_EMA20_ATR)))
            atr_safe = atr.replace(0, np.nan)
            body_atr = (open_ - close) / atr_safe
            chase_ext = body_atr > MAX_OPEN_EXTENSION_ATR
            chase_override = (vol_ratio >= CHASE_VOL_RATIO) & (close_loc <= (1 - CHASE_CLOSE_LOC))
            chase_ext = chase_ext & (~chase_override)

        mid_gap = (
            (gap_pct <= -GAP_CONT_MIN)
            & (gap_pct >= -GAP_CONT_MAX)
            & (vol_ratio >= GAP_CONT_VOL_MIN)
            & (close_loc <= (1 - GAP_CONT_CLOSE_MIN))
        )
        large_gap = (
            (gap_pct < -GAP_CONT_MAX)
            & (gap_pct >= -GAP_CONT_LARGE_MAX)
            & (vol_ratio >= GAP_CONT_VOL_LARGE)
            & (close_loc <= (1 - GAP_CONT_CLOSE_LARGE))
            & range_exp
        )
        signal = (mid_gap | large_gap) & trend_ok & (~open_ext) & (~chase_ext)
        signal = signal.fillna(False)
        signal.iloc[:49] = False
        return signal.astype(bool)

    def stop_loss_series(self, df: pd.DataFrame) -> pd.Series:
        entry = self.signal(df).astype(int)
        idx = pd.Series(range(len(df)), index=df.index)
        entry_id = entry.cumsum()
        last_entry = idx.where(entry == 1).ffill()
        bars_since = (idx - last_entry).fillna(999)
        entry_high = df["High"].where(entry == 1).ffill()
        entry_price = df["Close"].where(entry == 1).ffill()

        atr = self.atr_series(df, period=14)
        ema20 = self.ema_series(df, length=20)
        if atr is None or ema20 is None:
            return None
        close = df["Close"]
        high = df["High"]
        stop_atr = close + (atr * 1.5)
        trail = ema20 + (atr * 0.5)
        stop_mode = getattr(self, "stop_mode", "tightest")
        if stop_mode == "tightest":
            stop_level = pd.concat([high, stop_atr, trail], axis=1).min(axis=1)
            tight_mask = bars_since <= (self.tight_stop_bars - 1)
            stop_level = stop_level.where(~tight_mask, entry_high)
            if self.max_initial_r_pct:
                cap_stop = entry_price + (entry_price * self.max_initial_r_pct)
                stop_level = pd.concat([stop_level, cap_stop], axis=1).min(axis=1)
        else:
            initial_stop = stop_atr if stop_mode == "atr_first" else entry_high
            if self.max_initial_r_pct:
                cap_stop = entry_price + (entry_price * self.max_initial_r_pct)
                initial_stop = pd.concat([initial_stop, cap_stop], axis=1).min(axis=1)
            initial_risk = initial_stop - entry_price
            day1_confirm = (bars_since == 1) & (close < entry_price)
            r_trigger = close <= (entry_price - (initial_risk * 0.75))
            validation = (day1_confirm | r_trigger)
            validated = validation.groupby(entry_id).cummax()
            validated = validated.where(entry_id > 0, False)
            trail_stop = pd.concat([initial_stop, trail], axis=1).min(axis=1)
            stop_level = initial_stop.where(~validated, trail_stop)
        return stop_level.round(2)

    def exit_signal(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 3:
            return pd.Series(False, index=df.index)
        entry = self.signal(df)
        open_ = df["Open"]
        close = df["Close"]
        low = df["Low"]
        high = df["High"]
        prev_close = close.shift(1)
        gap_pct = (open_ - prev_close) / prev_close
        large_gap = (gap_pct < -GAP_CONT_MAX) & (gap_pct >= -GAP_CONT_LARGE_MAX)
        day1_fail = entry.shift(1) & (close > high.shift(1))
        low_roll3 = low.rolling(3).min()
        day2_no_follow = entry.shift(2) & (low_roll3 >= low.shift(2))
        signal = pd.Series(False, index=df.index)
        if self.exit_on_d1_close_above_gap_high:
            signal = signal | day1_fail
        if self.exit_on_d2_no_lower_low:
            signal = signal | day2_no_follow
        if self.large_gap_follow_through:
            entry_large = entry & large_gap
            day1_large = entry_large.shift(1)
            day1_follow_fail = day1_large & (
                (low >= low.shift(1)) | (close >= close.shift(1))
            )
            signal = signal | day1_follow_fail
        signal = signal.fillna(False)
        return signal.astype(bool)

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 2:
            return pd.Series(0.0, index=df.index)
        close = df["Close"]
        open_ = df["Open"]
        volume = df["Volume"]
        prev_close = close.shift(1)
        gap_pct = (open_ - prev_close) / prev_close
        avg_vol = self.volume_sma_series(df, length=20)
        vol_ratio = volume / avg_vol
        close_loc = _close_location(df)
        score = (gap_pct.abs().fillna(0.0) * 100) + (vol_ratio.fillna(0.0) * 2.0) + ((1 - close_loc).fillna(0.0) * 10.0)
        return score.fillna(0.0)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if len(df) < 50 or not _is_daily(df):
            return False, {}
        close = df["Close"]
        open_ = df["Open"]
        volume = df["Volume"]
        prev_close = close.iloc[-2]
        gap_pct = (open_.iloc[-1] - prev_close) / prev_close
        avg_vol = self.volume_sma_series(df, length=20).iloc[-1]
        if pd.isna(avg_vol) or avg_vol == 0:
            return False, {}
        vol_ratio = volume.iloc[-1] / avg_vol
        close_loc = _close_location(df).iloc[-1]
        day_range = df["High"].iloc[-1] - df["Low"].iloc[-1]

        ema20 = self.ema_series(df, length=20)
        ema50 = self.ema_series(df, length=50)
        if ema20 is None or ema50 is None:
            return False, {}
        if ema20.iloc[-1] >= ema50.iloc[-1] or ema50.iloc[-1] >= ema50.iloc[-2]:
            return False, {}

        atr = self.atr_series(df, period=14)
        range_exp = atr is not None and (day_range > (atr.iloc[-1] * 1.2))
        open_ext = open_.iloc[-1] < (ema20.iloc[-1] * MIN_OPEN_EMA20_PCT)
        chase_ext = False
        if atr is not None:
            open_ext = open_ext or (open_.iloc[-1] < (ema20.iloc[-1] - (atr.iloc[-1] * MAX_OPEN_EMA20_ATR)))
            if atr.iloc[-1] > 0:
                body_atr = (open_.iloc[-1] - close.iloc[-1]) / atr.iloc[-1]
                chase_ext = body_atr > MAX_OPEN_EXTENSION_ATR
                chase_override = (vol_ratio >= CHASE_VOL_RATIO) and (close_loc <= (1 - CHASE_CLOSE_LOC))
                chase_ext = chase_ext and (not chase_override)
        mid_gap = (
            (gap_pct <= -GAP_CONT_MIN)
            and (gap_pct >= -GAP_CONT_MAX)
            and (vol_ratio >= GAP_CONT_VOL_MIN)
            and (close_loc <= (1 - GAP_CONT_CLOSE_MIN))
        )
        large_gap = (
            (gap_pct < -GAP_CONT_MAX)
            and (gap_pct >= -GAP_CONT_LARGE_MAX)
            and (vol_ratio >= GAP_CONT_VOL_LARGE)
            and (close_loc <= (1 - GAP_CONT_CLOSE_LARGE))
            and range_exp
        )
        if (mid_gap or large_gap) and not open_ext and not chase_ext:
            stop_level = self.calculate_atr_stop(df, direction='short', multiplier=2.0)
            score = (abs(gap_pct) * 100) + (vol_ratio * 2.0) + ((1 - close_loc) * 10.0)
            gap_bucket = "large" if large_gap else "mid"
            return True, {
                "pattern": "Gap Down Continuation",
                "gap_pct": round(gap_pct * 100, 2),
                "vol_ratio": round(vol_ratio, 2),
                "close_loc": round(close_loc, 2),
                "stop_loss": stop_level,
                "score": round(score, 2),
                "gap_bucket": gap_bucket,
                "sentiment": "BEARISH",
            }
        return False, {}


class GapUpFade(BaseStrategy):
    """
    Large gap up with weak close (fade).
    """
    sentiment = "BEARISH"
    direction = "short"
    stop_multiplier = 2.0
    take_profit_multiplier = 2.0
    time_stop_days = 5

    def signal(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 50 or not _is_daily(df):
            return pd.Series(False, index=df.index)
        close = df["Close"]
        open_ = df["Open"]
        volume = df["Volume"]
        prev_close = close.shift(1)
        gap_pct = (open_ - prev_close) / prev_close
        avg_vol = self.volume_sma_series(df, length=20)
        vol_ratio = volume / avg_vol
        close_loc = _close_location(df)
        signal = (
            (gap_pct >= GAP_FADE_PCT)
            & (vol_ratio >= VOL_MULT)
            & (close_loc <= 0.3)
        )
        signal = signal.fillna(False)
        signal.iloc[:49] = False
        return signal.astype(bool)

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 2:
            return pd.Series(0.0, index=df.index)
        close = df["Close"]
        open_ = df["Open"]
        prev_close = close.shift(1)
        gap_pct = (open_ - prev_close) / prev_close
        score = gap_pct.fillna(0.0).abs() * 100
        return score.fillna(0.0)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if len(df) < 50 or not _is_daily(df):
            return False, {}
        close = df["Close"]
        open_ = df["Open"]
        volume = df["Volume"]
        prev_close = close.iloc[-2]
        gap_pct = (open_.iloc[-1] - prev_close) / prev_close
        avg_vol = self.volume_sma_series(df, length=20).iloc[-1]
        if pd.isna(avg_vol) or avg_vol == 0:
            return False, {}
        vol_ratio = volume.iloc[-1] / avg_vol
        close_loc = _close_location(df).iloc[-1]

        if gap_pct >= GAP_FADE_PCT and vol_ratio >= VOL_MULT and close_loc <= 0.3:
            stop_level = self.calculate_atr_stop(df, direction='short', multiplier=2.0)
            score = (abs(gap_pct) * 100) + (vol_ratio * 2.0)
            return True, {
                "pattern": "Gap Up Fade",
                "gap_pct": round(gap_pct * 100, 2),
                "vol_ratio": round(vol_ratio, 2),
                "close_loc": round(close_loc, 2),
                "stop_loss": stop_level,
                "score": round(score, 2),
                "sentiment": "BEARISH",
            }
        return False, {}


class GapDownFade(BaseStrategy):
    """
    Large gap down with strong close (fade).
    """
    sentiment = "BULLISH"
    direction = "long"
    stop_multiplier = 2.0
    take_profit_multiplier = 2.0
    time_stop_days = 5

    def signal(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 50 or not _is_daily(df):
            return pd.Series(False, index=df.index)
        close = df["Close"]
        open_ = df["Open"]
        volume = df["Volume"]
        prev_close = close.shift(1)
        gap_pct = (open_ - prev_close) / prev_close
        avg_vol = self.volume_sma_series(df, length=20)
        vol_ratio = volume / avg_vol
        close_loc = _close_location(df)
        signal = (
            (gap_pct <= -GAP_FADE_PCT)
            & (vol_ratio >= VOL_MULT)
            & (close_loc >= 0.7)
        )
        signal = signal.fillna(False)
        signal.iloc[:49] = False
        return signal.astype(bool)

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < 2:
            return pd.Series(0.0, index=df.index)
        close = df["Close"]
        open_ = df["Open"]
        prev_close = close.shift(1)
        gap_pct = (open_ - prev_close) / prev_close
        score = gap_pct.fillna(0.0).abs() * 100
        return score.fillna(0.0)

    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if len(df) < 50 or not _is_daily(df):
            return False, {}
        close = df["Close"]
        open_ = df["Open"]
        volume = df["Volume"]
        prev_close = close.iloc[-2]
        gap_pct = (open_.iloc[-1] - prev_close) / prev_close
        avg_vol = self.volume_sma_series(df, length=20).iloc[-1]
        if pd.isna(avg_vol) or avg_vol == 0:
            return False, {}
        vol_ratio = volume.iloc[-1] / avg_vol
        close_loc = _close_location(df).iloc[-1]

        if gap_pct <= -GAP_FADE_PCT and vol_ratio >= VOL_MULT and close_loc >= 0.7:
            stop_level = self.calculate_atr_stop(df, direction='long', multiplier=2.0)
            score = (abs(gap_pct) * 100) + (vol_ratio * 2.0)
            return True, {
                "pattern": "Gap Down Fade",
                "gap_pct": round(gap_pct * 100, 2),
                "vol_ratio": round(vol_ratio, 2),
                "close_loc": round(close_loc, 2),
                "stop_loss": stop_level,
                "score": round(score, 2),
                "sentiment": "BULLISH",
            }
        return False, {}
