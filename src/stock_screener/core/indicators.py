"""
Shared indicator precompute helpers.
"""
from typing import Dict, Iterable, Tuple

import numpy as np
import pandas as pd
import pandas_ta as ta


def _atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def _ensure_columns(df: pd.DataFrame, cols: Iterable[str]) -> bool:
    return all(col in df.columns for col in cols)

def _pivot_values(series: pd.Series, left: int, right: int, mode: str) -> Tuple[pd.Series, pd.Series]:
    window = left + right + 1
    if window <= 1:
        mask = pd.Series(False, index=series.index)
        return series.where(mask), mask
    if mode == "high":
        roll = series.rolling(window, center=True).max()
    else:
        roll = series.rolling(window, center=True).min()
    mask = (series == roll)
    values = series.where(mask)
    if right > 0:
        mask = mask.shift(right, fill_value=False)
        values = values.shift(right)
    return values, mask


def _anchored_vwap(price: pd.Series, volume: pd.Series, anchor_mask: pd.Series) -> pd.Series:
    if price is None or volume is None:
        return pd.Series(index=anchor_mask.index, dtype="float64")
    anchor_bool = anchor_mask.to_numpy(dtype=bool, na_value=False)
    anchor_id = pd.Series(anchor_bool, index=anchor_mask.index).astype(int).cumsum()
    pv = price * volume
    cum_pv = pv.groupby(anchor_id).cumsum()
    cum_vol = volume.groupby(anchor_id).cumsum()
    avwap = cum_pv / cum_vol.where(cum_vol != 0)
    return avwap.where(anchor_id > 0)

def _session_vwap(price: pd.Series, volume: pd.Series, session_index: pd.Series) -> pd.Series:
    if price is None or volume is None:
        return pd.Series(index=session_index.index, dtype="float64")
    pv = price * volume
    cum_pv = pv.groupby(session_index).cumsum()
    cum_vol = volume.groupby(session_index).cumsum()
    return cum_pv / cum_vol.where(cum_vol != 0)


def _wavetrend_series(
    df: pd.DataFrame,
    channel_length: int = 10,
    average_length: int = 21,
    ma_length: int = 4,
    source: str = "ohlc4",
) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Return WaveTrend lines and oversold/overbought crossover signals."""
    sources = {
        "open": df.get("Open"),
        "high": df.get("High"),
        "low": df.get("Low"),
        "close": df.get("Close"),
        "hl2": (df["High"] + df["Low"]) / 2 if _ensure_columns(df, ("High", "Low")) else None,
        "hlc3": (df["High"] + df["Low"] + df["Close"]) / 3 if _ensure_columns(df, ("High", "Low", "Close")) else None,
        "ohlc4": (df["Open"] + df["High"] + df["Low"] + df["Close"]) / 4
        if _ensure_columns(df, ("Open", "High", "Low", "Close"))
        else None,
    }
    price = sources.get(source, sources.get("close"))
    if price is None:
        empty = pd.Series(index=df.index, dtype="float64")
        return empty, empty, empty, empty

    ap = price.ewm(span=channel_length, adjust=False, min_periods=channel_length).mean()
    deviation = (price - ap).abs()
    d = deviation.ewm(span=channel_length, adjust=False, min_periods=channel_length).mean()
    ci = (price - ap).where(d > 0).div(0.015 * d)
    wt1 = ci.ewm(span=average_length, adjust=False, min_periods=average_length).mean()
    wt2 = wt1.rolling(ma_length, min_periods=ma_length).mean()

    crossed_up = (wt1 > wt2) & (wt1.shift(1) <= wt2.shift(1))
    crossed_down = (wt1 < wt2) & (wt1.shift(1) >= wt2.shift(1))
    buy = pd.Series(float("nan"), index=df.index)
    sell = pd.Series(float("nan"), index=df.index)
    buy.loc[crossed_up & (wt1 < -50)] = -70.0
    sell.loc[crossed_down & (wt1 > 50)] = 70.0
    return wt1, wt2, buy, sell


def _nearest_levels(
    pivots: pd.Series,
    close: pd.Series,
    atr: pd.Series,
    lookback: int,
    mode: str,
    max_atr: float,
) -> pd.Series:
    pivots_arr = pivots.values
    close_arr = close.values
    atr_arr = atr.values if atr is not None else None
    out = np.full(len(close_arr), np.nan, dtype="float64")
    for i in range(len(close_arr)):
        start = max(0, i - lookback + 1)
        window = pivots_arr[start : i + 1]
        if window.size == 0:
            continue
        if mode == "high":
            candidates = window[window > close_arr[i]]
            if candidates.size == 0:
                continue
            level = candidates.min()
        else:
            candidates = window[window < close_arr[i]]
            if candidates.size == 0:
                continue
            level = candidates.max()
        if atr_arr is not None:
            atr_val = atr_arr[i]
            if pd.isna(atr_val) or atr_val <= 0:
                continue
            if abs(level - close_arr[i]) > (max_atr * atr_val):
                continue
        out[i] = float(level)
    return pd.Series(out, index=close.index, dtype="float64")


def add_common_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add commonly used indicator columns to the dataframe (in-place).
    """
    if df is None or df.empty:
        return df

    close = df.get("Close")
    high = df.get("High")
    low = df.get("Low")
    volume = df.get("Volume")
    if close is None or high is None or low is None:
        return df

    # WaveTrend oscillator and its oversold/overbought crossover signals.
    wt_cols = ("WT1_10_21_4", "WT2_10_21_4", "WT_BUY_10_21_4", "WT_SELL_10_21_4")
    if not _ensure_columns(df, wt_cols):
        wt1, wt2, wt_buy, wt_sell = _wavetrend_series(df)
        df[wt_cols[0]] = wt1
        df[wt_cols[1]] = wt2
        df[wt_cols[2]] = wt_buy
        df[wt_cols[3]] = wt_sell

    # RSI
    if "RSI_14" not in df.columns:
        rsi = ta.rsi(close, length=14)
        if rsi is not None:
            df["RSI_14"] = rsi

    # SMA / EMA
    for length in (20, 50, 200):
        sma_col = f"SMA_{length}"
        if sma_col not in df.columns:
            sma = ta.sma(close, length=length)
            if sma is not None:
                df[sma_col] = sma
        ema_col = f"EMA_{length}"
        if ema_col not in df.columns:
            ema = ta.ema(close, length=length)
            if ema is not None:
                df[ema_col] = ema

    # ATR
    if "ATR_14" not in df.columns:
        df["ATR_14"] = _atr_series(df, period=14)

    # MACD
    macd_cols = ["MACD_12_26_9", "MACDh_12_26_9", "MACDs_12_26_9"]
    if not _ensure_columns(df, macd_cols):
        macd = ta.macd(close, fast=12, slow=26, signal=9)
        if macd is not None:
            for col in macd_cols:
                if col in macd.columns and col not in df.columns:
                    df[col] = macd[col]

    # Bollinger Bands
    bb_cols = ["BBL_20_2.0", "BBM_20_2.0", "BBU_20_2.0", "BBB_20_2.0", "BBP_20_2.0"]
    if not _ensure_columns(df, bb_cols):
        bb = ta.bbands(close, length=20, std=2)
        if bb is not None:
            for col in bb_cols:
                if col in bb.columns and col not in df.columns:
                    df[col] = bb[col]

    # Supertrend
    st_cols = ["SUPERT_10_3.0", "SUPERTd_10_3.0", "SUPERTl_10_3.0", "SUPERTs_10_3.0"]
    if not _ensure_columns(df, st_cols):
        st = ta.supertrend(high, low, close, length=10, multiplier=3)
        if st is not None:
            for col in st_cols:
                if col in st.columns and col not in df.columns:
                    df[col] = st[col]

    # Volume SMA
    if volume is not None and "VOL_SMA_20" not in df.columns:
        df["VOL_SMA_20"] = volume.rolling(20).mean()
    # Dollar volume SMA (used by liquidity filters)
    if volume is not None:
        if "DOLLAR_VOL" not in df.columns:
            df["DOLLAR_VOL"] = close * volume
        if "DOLLAR_VOL_SMA_20" not in df.columns:
            df["DOLLAR_VOL_SMA_20"] = df["DOLLAR_VOL"].rolling(20).mean()
        if "FUT_VOL_SMA_20" not in df.columns:
            df["FUT_VOL_SMA_20"] = volume.rolling(20).mean()
        if "VWAP" not in df.columns:
            typical = (high + low + close) / 3.0
            session = pd.to_datetime(df.index, errors="coerce")
            if not session.isna().all():
                df["VWAP"] = _session_vwap(typical, volume, session.normalize())

    # Support / Resistance via swing pivots (confirmed)
    swing_left = 3
    swing_right = 3
    pivot_high_vals, pivot_high_mask = _pivot_values(high, swing_left, swing_right, "high")
    pivot_low_vals, pivot_low_mask = _pivot_values(low, swing_left, swing_right, "low")

    for lookback in (20, 40, 60):
        res_col = f"SR_HIGH_{lookback}"
        sup_col = f"SR_LOW_{lookback}"
        if res_col not in df.columns:
            df[res_col] = pivot_high_vals.rolling(lookback).max()
        if sup_col not in df.columns:
            df[sup_col] = pivot_low_vals.rolling(lookback).min()

    # Nearest support/resistance above/below price within a max ATR distance
    if "ATR_14" in df.columns:
        atr = df["ATR_14"]
    else:
        atr = _atr_series(df, period=14)
    max_atr = 3.0
    for lookback in (20, 40, 60):
        near_high_col = f"SR_NEAR_HIGH_{lookback}"
        near_low_col = f"SR_NEAR_LOW_{lookback}"
        if near_high_col not in df.columns:
            df[near_high_col] = _nearest_levels(pivot_high_vals, close, atr, lookback, "high", max_atr)
        if near_low_col not in df.columns:
            df[near_low_col] = _nearest_levels(pivot_low_vals, close, atr, lookback, "low", max_atr)

    # Anchored VWAP from confirmed swing highs/lows
    if volume is not None:
        typical = (high + low + close) / 3.0
        if "AVWAP_SWING_LOW" not in df.columns:
            df["AVWAP_SWING_LOW"] = _anchored_vwap(typical, volume, pivot_low_mask)
        if "AVWAP_SWING_HIGH" not in df.columns:
            df["AVWAP_SWING_HIGH"] = _anchored_vwap(typical, volume, pivot_high_mask)

    return df


def _trend_state_from_pivots(
    high: pd.Series,
    low: pd.Series,
    left: int = 2,
    right: int = 2,
) -> pd.Series:
    pivot_high_vals, pivot_high_mask = _pivot_values(high, left, right, "high")
    pivot_low_vals, pivot_low_mask = _pivot_values(low, left, right, "low")
    state = np.full(len(high), np.nan, dtype="float64")
    last_highs: list = []
    last_lows: list = []
    for i in range(len(high)):
        if pivot_high_mask.iat[i]:
            value = pivot_high_vals.iat[i]
            if not pd.isna(value):
                last_highs.append(float(value))
                if len(last_highs) > 2:
                    last_highs.pop(0)
        if pivot_low_mask.iat[i]:
            value = pivot_low_vals.iat[i]
            if not pd.isna(value):
                last_lows.append(float(value))
                if len(last_lows) > 2:
                    last_lows.pop(0)
        if len(last_highs) == 2 and len(last_lows) == 2:
            if last_highs[1] > last_highs[0] and last_lows[1] > last_lows[0]:
                state[i] = 1.0
            elif last_highs[1] < last_highs[0] and last_lows[1] < last_lows[0]:
                state[i] = -1.0
    return pd.Series(state, index=high.index).ffill().fillna(0.0).astype(int)


def _market_trend_flags(df: pd.DataFrame, left: int = 2, right: int = 2) -> Dict[str, pd.Series]:
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    ema10 = ta.ema(close, length=10)
    ema20 = ta.ema(close, length=20)
    ema50 = ta.ema(close, length=50)
    if ema10 is None or ema20 is None or ema50 is None:
        empty = pd.Series(False, index=df.index, dtype="bool")
        return {"up": empty, "down": empty}
    state = _trend_state_from_pivots(high, low, left=left, right=right)
    ema_ready = ema10.notna() & ema20.notna() & ema50.notna()
    up = (state == 1) & ema_ready & (close > ema50) & (ema10 > ema20) & (ema20 > ema50)
    down = (state == -1) & ema_ready & (close < ema50) & (ema10 < ema20) & (ema20 < ema50)
    return {
        "up": up.fillna(False).astype(bool),
        "down": down.fillna(False).astype(bool),
    }


def add_market_context_columns(
    data_map: Dict[str, pd.DataFrame],
    spy_symbol: str = "SPY",
    qqq_symbol: str = "QQQ",
    uvxy_symbol: str = "UVXY",
    trend_pivot_left: int = 2,
    trend_pivot_right: int = 2,
) -> None:
    spy_df = data_map.get(spy_symbol)
    qqq_df = data_map.get(qqq_symbol)
    uvxy_df = data_map.get(uvxy_symbol)
    if spy_df is None or qqq_df is None:
        return

    spy_flags = _market_trend_flags(spy_df, left=trend_pivot_left, right=trend_pivot_right)
    qqq_flags = _market_trend_flags(qqq_df, left=trend_pivot_left, right=trend_pivot_right)

    spy_close = spy_df["Close"]
    sma200 = spy_df["SMA_200"] if "SMA_200" in spy_df.columns else ta.sma(spy_close, length=200)
    sma50 = spy_df["SMA_50"] if "SMA_50" in spy_df.columns else ta.sma(spy_close, length=50)
    if sma200 is None:
        sma200 = pd.Series(index=spy_df.index, dtype="float64")
    if sma50 is None:
        sma50 = pd.Series(index=spy_df.index, dtype="float64")
    slope_window = 20
    sma200_slope = (sma200 - sma200.shift(slope_window)) / sma200.shift(slope_window)
    trend_ok = (spy_close > sma200) & (sma200_slope > 0)

    atr20 = ta.atr(spy_df["High"], spy_df["Low"], spy_df["Close"], length=20)
    if atr20 is None:
        atr20 = _atr_series(spy_df, period=20)
    vol_ratio = atr20 / spy_close
    vol_median = vol_ratio.rolling(252).median()
    vol_ok = vol_ratio < vol_median

    breadth_ok = spy_close > sma50
    risk_on_mean_reversion = trend_ok & vol_ok & breadth_ok

    uvxy_spike = pd.Series(False, index=spy_df.index, dtype="bool")
    uvxy_above_ema20 = pd.Series(False, index=spy_df.index, dtype="bool")
    if uvxy_df is not None and not uvxy_df.empty:
        uvxy_close = uvxy_df["Close"]
        uvxy_ema20 = ta.ema(uvxy_close, length=20)
        uvxy_above_ema20 = (uvxy_close > uvxy_ema20).fillna(False).astype(bool)
        prior_high = uvxy_close.rolling(5).max().shift(1)
        uvxy_higher_highs = (uvxy_close > prior_high).fillna(False).astype(bool)
        uvxy_spike = (uvxy_above_ema20 | uvxy_higher_highs).fillna(False).astype(bool)

    market_long_ok = spy_flags["up"] & qqq_flags["up"] & (~uvxy_spike)

    for df in data_map.values():
        idx = df.index
        def _align_bool(series: pd.Series, align_idx: pd.Index, default: bool = False) -> np.ndarray:
            aligned = series.reindex(align_idx)
            aligned = aligned.astype("boolean").ffill().fillna(default)
            return aligned.to_numpy(dtype=bool)

        df["SPY_TREND_UP"] = _align_bool(spy_flags["up"], idx)
        df["SPY_TREND_DOWN"] = _align_bool(spy_flags["down"], idx)
        df["QQQ_TREND_UP"] = _align_bool(qqq_flags["up"], idx)
        df["QQQ_TREND_DOWN"] = _align_bool(qqq_flags["down"], idx)
        df["UVXY_SPIKE"] = _align_bool(uvxy_spike, idx)
        df["UVXY_ABOVE_EMA20"] = _align_bool(uvxy_above_ema20, idx)
        df["MKT_RISK_ON_FOR_LONGS"] = _align_bool(market_long_ok, idx)
        df["MKT_TREND_OK"] = _align_bool(trend_ok, idx)
        df["MKT_VOL_OK"] = _align_bool(vol_ok, idx)
        df["MKT_BREADTH_OK"] = _align_bool(breadth_ok, idx)
        df["MKT_RISK_ON_MEAN_REVERSION"] = _align_bool(risk_on_mean_reversion, idx)
