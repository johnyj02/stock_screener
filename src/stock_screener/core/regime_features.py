from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
import pandas_ta as ta

from stock_screener.core.indicators import _atr_series


def _cfg_int(cfg: Dict[str, Any], key: str, default: int) -> int:
    try:
        return int(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


def _cfg_float(cfg: Dict[str, Any], key: str, default: float) -> float:
    try:
        return float(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


def _is_intraday(interval: str) -> bool:
    if not interval:
        return False
    interval = str(interval).lower()
    return interval.endswith("m") or interval.endswith("h")


def _daily_frame(df: Optional[pd.DataFrame], interval: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    if not cols:
        return pd.DataFrame()
    data = df[cols].copy()
    if not _is_intraday(interval):
        return data.dropna(how="all")
    idx = data.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")
    local = data.copy()
    local.index = idx.tz_convert("America/New_York")
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    if "Volume" in local.columns:
        agg["Volume"] = "sum"
    daily = local.resample("1D").agg(agg)
    daily = daily.dropna(how="all")
    daily.index = daily.index.tz_localize(None)
    return daily


def _rolling_slope_pct(series: pd.Series, window: int) -> pd.Series:
    if window <= 0:
        return pd.Series(index=series.index, dtype="float64")
    shifted = series.shift(window)
    return (series - shifted) / shifted.replace(0, np.nan)


def _adx_series(df: pd.DataFrame, length: int = 14) -> pd.Series:
    if df is None or df.empty or not all(col in df.columns for col in ["High", "Low", "Close"]):
        return pd.Series(index=df.index, dtype="float64")
    adx = ta.adx(df["High"], df["Low"], df["Close"], length=length)
    if adx is None or adx.empty:
        return pd.Series(index=df.index, dtype="float64")
    col = f"ADX_{length}"
    if col in adx.columns:
        return adx[col]
    # fallback: first column
    return adx.iloc[:, 0]


def _percentile(series: pd.Series, window: int, q: float) -> pd.Series:
    if series is None or series.empty:
        return pd.Series(index=series.index, dtype="float64")
    return series.rolling(window, min_periods=max(1, int(window * 0.2))).quantile(q)


def _bearish_divergence(close: pd.Series, rsi: pd.Series, lookback: int = 20) -> pd.Series:
    if close is None or rsi is None or close.empty:
        return pd.Series(index=close.index if close is not None else None, dtype="bool")
    half = max(3, lookback // 2)
    recent_high = close.rolling(half).max()
    prior_high = close.shift(half).rolling(half).max()
    recent_rsi = rsi.rolling(half).max()
    prior_rsi = rsi.shift(half).rolling(half).max()
    return (recent_high > prior_high) & (recent_rsi < prior_rsi)


def _inside_bar_flags(high: pd.Series, low: pd.Series) -> pd.Series:
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    return (high <= prev_high) & (low >= prev_low)


def _vwap_series(df: pd.DataFrame) -> pd.Series:
    if df is None or df.empty or "Close" not in df.columns:
        return pd.Series(index=df.index, dtype="float64")
    high = df.get("High", df["Close"])
    low = df.get("Low", df["Close"])
    close = df["Close"]
    volume = df.get("Volume")
    typical = (high + low + close) / 3.0
    if volume is None:
        return typical
    idx = df.index
    if idx.tz is None:
        idx_local = idx.tz_localize("UTC").tz_convert("America/New_York")
    else:
        idx_local = idx.tz_convert("America/New_York")
    session_index = pd.Series(idx_local.normalize(), index=df.index)
    pv = typical * volume
    cum_pv = pv.groupby(session_index).cumsum()
    cum_vol = volume.groupby(session_index).cumsum()
    return cum_pv / cum_vol.where(cum_vol != 0)


def compute_daily_regimes(
    data_map: Dict[str, pd.DataFrame],
    market_symbol: str,
    vol_symbol: Optional[str],
    interval: str,
    config: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    cfg = config or {}
    features = cfg.get("features") if isinstance(cfg.get("features"), dict) else cfg
    trend_cfg = features.get("trend", {}) if isinstance(features, dict) else {}
    vol_cfg = features.get("vol", {}) if isinstance(features, dict) else {}
    liq_cfg = features.get("liquidity", {}) if isinstance(features, dict) else {}
    risk_cfg = features.get("risk", {}) if isinstance(features, dict) else {}
    daily_mkt = _daily_frame(data_map.get(market_symbol), interval)
    if daily_mkt.empty:
        return pd.DataFrame()

    daily_vol = _daily_frame(data_map.get(vol_symbol), interval) if vol_symbol else pd.DataFrame()

    close = daily_mkt["Close"]
    high = daily_mkt["High"]
    low = daily_mkt["Low"]
    open_px = daily_mkt.get("Open", close)
    volume = daily_mkt.get("Volume")

    sma_fast = _cfg_int(trend_cfg, "sma_fast", 50)
    sma_slow = _cfg_int(trend_cfg, "sma_slow", 200)
    slope_window = _cfg_int(trend_cfg, "slope_window", 20)
    adx_len = _cfg_int(trend_cfg, "adx_length", 14)
    hhhl_lookback = _cfg_int(trend_cfg, "hhhl_lookback", 20)
    hhhl_min = _cfg_int(trend_cfg, "hhhl_min", 3)
    overlap_ratio_min = _cfg_float(trend_cfg, "overlap_ratio_min", 0.4)

    sma50 = ta.sma(close, length=sma_fast)
    sma200 = ta.sma(close, length=sma_slow)
    sma50 = sma50 if sma50 is not None else close.rolling(50).mean()
    sma200 = sma200 if sma200 is not None else close.rolling(200).mean()
    slope50 = _rolling_slope_pct(sma50, slope_window)
    slope200 = _rolling_slope_pct(sma200, slope_window)

    atr_len = _cfg_int(vol_cfg, "atr_length", 14)
    atr_mr_len = _cfg_int(vol_cfg, "atr_mean_reversion_length", 20)
    atr14 = _atr_series(daily_mkt, period=atr_len)
    atr_mr = _atr_series(daily_mkt, period=atr_mr_len)
    adx14 = _adx_series(daily_mkt, length=adx_len)

    hh_hl = (high > high.shift(1)) & (low > low.shift(1))
    ll_lh = (high < high.shift(1)) & (low < low.shift(1))
    hh_hl_count = hh_hl.rolling(hhhl_lookback).sum()
    ll_lh_count = ll_lh.rolling(hhhl_lookback).sum()

    overlap = (low <= high.shift(1)) & (high >= low.shift(1))
    overlap_ratio = overlap.rolling(hhhl_lookback).mean()

    rsi = ta.rsi(close, length=14)
    if rsi is None:
        rsi = pd.Series(index=close.index, dtype="float64")
    bearish_div = _bearish_divergence(close, rsi, lookback=20)

    trend_strong_up = (close > sma50) & (sma50 > sma200) & (slope50 > 0) & (adx14 > 25) & (hh_hl_count >= hhhl_min)
    trend_weak_up = (close > sma50) & (slope50 > 0) & (adx14.between(15, 25)) & (overlap_ratio >= overlap_ratio_min)
    trend_strong_down = (close < sma50) & (sma50 < sma200) & (slope50 < 0) & (adx14 > 25) & (ll_lh_count >= hhhl_min)
    trend_weak_down = (close < sma50) & (slope50 < 0) & (adx14.between(15, 25)) & bearish_div.fillna(False)

    range_lookback = _cfg_int(vol_cfg, "range_lookback", 20)
    range_width = high.rolling(range_lookback).max() - low.rolling(range_lookback).min()

    range_break = (close > high.rolling(range_lookback).max().shift(1)) | (close < low.rolling(range_lookback).min().shift(1))
    transition_cond = (
        (range_width > range_width.shift(10))
        & (atr14 > atr14.shift(3))
        & (adx14 > adx14.shift(3))
        & (adx14 < 25)
        & range_break.fillna(False)
    )

    trend_label = pd.Series("range", index=close.index)
    trend_label[transition_cond] = "transition"
    trend_label[trend_weak_down] = "weak_down"
    trend_label[trend_weak_up] = "weak_up"
    trend_label[trend_strong_down] = "strong_down"
    trend_label[trend_strong_up] = "strong_up"

    trend_score = pd.Series(0.5, index=close.index)
    trend_score[trend_label == "strong_up"] = 1.0
    trend_score[trend_label == "weak_up"] = 0.75
    trend_score[trend_label == "weak_down"] = 0.25
    trend_score[trend_label == "strong_down"] = 0.0

    body = (close - open_px).abs()
    body_mean = body.rolling(5).mean()
    inside_bars = _inside_bar_flags(high, low).rolling(5).sum()

    pct_lookback = _cfg_int(vol_cfg, "percentile_lookback", 252)
    atr_p20 = _percentile(atr14, pct_lookback, 0.2)
    atr_p80 = _percentile(atr14, pct_lookback, 0.8)
    atr_p95 = _percentile(atr14, pct_lookback, 0.95)

    gap = (open_px - close.shift(1)).abs()
    candle_range = (high - low)
    gap_mult = _cfg_float(vol_cfg, "gap_atr_mult", 0.75)
    candle_mult = _cfg_float(vol_cfg, "candle_atr_mult", 1.5)
    high_vol = (atr14 > atr_p80) & (gap > gap_mult * atr14) & (candle_range > candle_mult * atr14)
    low_vol = (atr14 < atr_p20) & (body_mean < 0.6 * atr14) & (inside_bars >= 2)

    atr_rise = (atr14 > atr14.shift(1)) & (atr14.shift(1) > atr14.shift(2)) & (atr14.shift(2) > atr14.shift(3))
    atr_fall = (atr14 < atr14.shift(1)) & (atr14.shift(1) < atr14.shift(2)) & (atr14.shift(2) < atr14.shift(3))
    range_breakout = range_break.fillna(False)
    atr_sma20 = atr14.rolling(20).mean()
    vol_expansion = atr_rise & (atr14 > atr_sma20) & range_breakout
    bb = ta.bbands(close, length=20, std=2)
    if bb is not None and "BBB_20_2.0" in bb.columns:
        bb_width = bb["BBB_20_2.0"]
    else:
        bb_width = pd.Series(index=close.index, dtype="float64")
    bb_p25 = _percentile(bb_width, 252, 0.25)
    vol_contraction = atr_fall & (range_width < range_width.shift(3)) & (bb_width < bb_p25)

    vol_label = pd.Series("normal", index=close.index)
    vol_label[vol_contraction] = "contracting"
    vol_label[low_vol] = "low"
    vol_label[high_vol] = "high"
    vol_label[vol_expansion] = "expanding"
    vol_label[atr14 > atr_p95] = "crisis"

    vol_score = pd.Series(0.5, index=close.index)
    vol_score[vol_label == "low"] = 1.0
    vol_score[vol_label == "contracting"] = 0.8
    vol_score[vol_label == "high"] = 0.3
    vol_score[vol_label == "expanding"] = 0.2
    vol_score[vol_label == "crisis"] = 0.0

    vol_sma_len = _cfg_int(liq_cfg, "volume_sma_length", 20)
    vol_ratio_high = _cfg_float(liq_cfg, "vol_ratio_high", 1.0)
    vol_ratio_low = _cfg_float(liq_cfg, "vol_ratio_low", 0.7)
    vwap_cross_threshold = _cfg_int(liq_cfg, "vwap_cross_threshold", 4)
    wick_ratio_high = _cfg_float(liq_cfg, "wick_ratio_high", 1.5)
    vol_sma20 = volume.rolling(vol_sma_len).mean() if volume is not None else pd.Series(index=close.index, dtype="float64")
    vol_ratio = volume / vol_sma20.replace(0, np.nan) if volume is not None else pd.Series(index=close.index, dtype="float64")
    vwap = (high + low + close) / 3.0
    vwap_sign = np.sign(close - vwap)
    vwap_crosses = (vwap_sign * vwap_sign.shift(1) < 0).rolling(20).sum()
    wick_ratio = (high - low) / body.replace(0, np.nan)
    wick_mean = wick_ratio.rolling(10).mean()
    streak_change = (vwap_sign != vwap_sign.shift(1)).cumsum()
    streak = vwap_sign.groupby(streak_change).cumcount() + 1
    vwap_respected = streak <= 2
    follow_through = ((close > close.shift(1)) & (close.shift(1) > close.shift(2))) | (
        (close < close.shift(1)) & (close.shift(1) < close.shift(2))
    )
    follow_ok = follow_through.rolling(5).sum() >= 1

    high_liq = (vol_ratio > vol_ratio_high) & vwap_respected & follow_ok
    low_liq = (vol_ratio < vol_ratio_low) & (vwap_crosses > vwap_cross_threshold) & (wick_mean > wick_ratio_high)

    liq_label = pd.Series("normal", index=close.index)
    liq_label[low_liq] = "low"
    liq_label[high_liq] = "high"

    liq_score = pd.Series(0.5, index=close.index)
    liq_score[liq_label == "high"] = 1.0
    liq_score[liq_label == "low"] = 0.0

    vol_proxy = daily_vol.get("Close") if not daily_vol.empty else (atr_mr / close)
    vol_proxy = vol_proxy.reindex(close.index)
    vol_proxy_window = _cfg_int(risk_cfg, "vol_proxy_sma_length", 20)
    vol_proxy_sma20 = vol_proxy.rolling(vol_proxy_window).mean()
    vol_proxy_mean = vol_proxy.rolling(vol_proxy_window).mean()
    vol_proxy_std = vol_proxy.rolling(vol_proxy_window).std()
    vol_proxy_spike = vol_proxy > (vol_proxy_mean + 2 * vol_proxy_std)

    breadth_mode = str(risk_cfg.get("breadth_mode", "sma50")).lower()
    if breadth_mode == "sma50":
        breadth_ok = close > sma50
    else:
        breadth_ok = close > sma50
    trend_ok = (close > sma200) & (slope200 > 0)
    vol_ok = (atr_mr / close) < (atr_mr / close).rolling(pct_lookback).median()
    risk_on_mean_reversion = trend_ok & vol_ok & breadth_ok
    risk_on = (close > sma50) & (vol_proxy < vol_proxy_sma20) & breadth_ok
    risk_off = (close < sma50) & (vol_proxy > vol_proxy_sma20)
    crisis = vol_proxy_spike.fillna(False) & (atr14 > atr_p95) & (gap > 1.5 * atr14)

    risk_label = pd.Series("neutral", index=close.index)
    risk_label[risk_off] = "risk_off"
    risk_label[risk_on] = "risk_on"
    risk_label[crisis] = "crisis"

    risk_score = pd.Series(0.5, index=close.index)
    risk_score[risk_label == "risk_on"] = 1.0
    risk_score[risk_label == "risk_off"] = 0.0
    risk_score[risk_label == "crisis"] = 0.1

    daily = pd.DataFrame(
        {
            "MKT_TREND_LABEL": trend_label,
            "MKT_TREND_SCORE": trend_score,
            "MKT_VOL_LABEL": vol_label,
            "MKT_VOL_SCORE": vol_score,
            "MKT_LIQ_LABEL": liq_label,
            "MKT_LIQ_SCORE": liq_score,
            "MKT_RISK_LABEL": risk_label,
            "MKT_RISK_SCORE": risk_score,
            "MKT_RISK_ON_MEAN_REVERSION": risk_on_mean_reversion.astype(bool),
        },
        index=close.index,
    )
    return daily


def align_daily_to_index(daily_df: pd.DataFrame, index: pd.DatetimeIndex) -> pd.DataFrame:
    if daily_df.empty:
        return pd.DataFrame(index=index)
    idx = index
    if idx.tz is None:
        idx_local = idx.tz_localize("UTC").tz_convert("America/New_York")
    else:
        idx_local = idx.tz_convert("America/New_York")
    day_index = idx_local.normalize().tz_localize(None)
    aligned = daily_df.reindex(day_index, method="ffill")
    aligned.index = index
    return aligned


def add_intraday_orb_day_type(
    data_map: Dict[str, pd.DataFrame],
    interval: str,
    config: Optional[Dict[str, Any]] = None,
) -> None:
    if not _is_intraday(interval):
        return
    cfg = config or {}
    orb_cfg = cfg.get("intraday", {}).get("orb_day_type", {}) if isinstance(cfg.get("intraday", {}), dict) else {}
    if orb_cfg is False:
        return
    orb_start = dt.time(9, 30)
    orb_end = dt.time(9, 45)
    hold_bars = int(orb_cfg.get("hold_bars", 3) or 3)
    fakeout_window_minutes = int(orb_cfg.get("fakeout_window_minutes", 45) or 45)
    vwap_retrace_atr = float(orb_cfg.get("vwap_retrace_atr", 0.5) or 0.5)
    balanced_range_mult = float(orb_cfg.get("balanced_range_mult", 1.5) or 1.5)
    vwap_cross_threshold = int(orb_cfg.get("vwap_cross_threshold", 4) or 4)
    event_dates = orb_cfg.get("event_dates") or []
    event_dates = {pd.Timestamp(d).date() for d in event_dates if d}

    for ticker, df in data_map.items():
        if df is None or df.empty:
            continue
        if "ORB_DAY_TYPE" in df.columns:
            continue
        if not all(col in df.columns for col in ["Open", "High", "Low", "Close"]):
            continue
        idx = df.index
        if idx.tz is None:
            idx_local = idx.tz_localize("UTC").tz_convert("America/New_York")
        else:
            idx_local = idx.tz_convert("America/New_York")
        local = df.copy()
        local.index = idx_local
        vwap = local["VWAP"] if "VWAP" in local.columns else _vwap_series(local)
        atr = local["ATR_14"] if "ATR_14" in local.columns else _atr_series(local, 14)

        labels = pd.Series("unclassified", index=local.index)
        for session_date, day_df in local.groupby(local.index.date):
            if day_df.empty:
                continue
            if session_date in event_dates:
                labels.loc[day_df.index] = "news"
                continue
            orb_window = day_df.between_time(orb_start, orb_end, inclusive="left")
            if orb_window.empty:
                continue
            orb_high = float(orb_window["High"].max())
            orb_low = float(orb_window["Low"].min())
            orb_size = max(orb_high - orb_low, 1e-9)
            post = day_df[day_df.index.time >= orb_end]
            breakout_idx = None
            breakout_side = None
            for ts, row in post.iterrows():
                if row["Close"] > orb_high:
                    breakout_idx = ts
                    breakout_side = "up"
                    break
                if row["Close"] < orb_low:
                    breakout_idx = ts
                    breakout_side = "down"
                    break
            day_label = "unclassified"
            if breakout_idx is not None:
                breakout_loc = post.index.get_loc(breakout_idx)
                hold_slice = post.iloc[breakout_loc : breakout_loc + hold_bars]
                if breakout_side == "up":
                    holds = (hold_slice["Close"] > orb_high).all()
                    retrace = (post["Close"] < (vwap.loc[post.index] - vwap_retrace_atr * atr.loc[post.index])).any()
                else:
                    holds = (hold_slice["Close"] < orb_low).all()
                    retrace = (post["Close"] > (vwap.loc[post.index] + vwap_retrace_atr * atr.loc[post.index])).any()
                if holds and not retrace:
                    day_label = "open_trend"
                else:
                    fail_window = int(max(1, fakeout_window_minutes / max(1, int(interval[:-1]))))
                    reenter = False
                    opposite = False
                    fail_slice = post.iloc[breakout_loc : breakout_loc + fail_window]
                    reenter = ((fail_slice["Close"] <= orb_high) & (fail_slice["Close"] >= orb_low)).any()
                    if breakout_side == "up":
                        opposite = (fail_slice["Close"] < orb_low).any()
                    else:
                        opposite = (fail_slice["Close"] > orb_high).any()
                    if reenter and opposite:
                        day_label = "open_fakeout"

            if day_label == "unclassified":
                vwap_sign = np.sign(day_df["Close"] - vwap.loc[day_df.index])
                vwap_crosses = (vwap_sign * vwap_sign.shift(1) < 0).sum()
                day_range = float(day_df["High"].max() - day_df["Low"].min())
                if vwap_crosses > vwap_cross_threshold and day_range <= balanced_range_mult * orb_size:
                    day_label = "balanced"
            labels.loc[day_df.index] = day_label

        aligned = labels.reindex(local.index, method="ffill")
        df["ORB_DAY_TYPE"] = aligned.to_numpy(dtype=str)
