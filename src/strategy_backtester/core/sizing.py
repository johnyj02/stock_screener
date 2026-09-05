from typing import Dict, Optional

import pandas as pd


def portfolio_vol_scale(
    equity_curve: list,
    lookback: int,
    target: float,
) -> float:
    if len(equity_curve) < lookback + 2:
        return 1.0
    equity_df = pd.DataFrame(equity_curve)
    if "equity" not in equity_df.columns:
        return 1.0
    returns = equity_df["equity"].pct_change().dropna()
    if len(returns) < lookback:
        return 1.0
    recent = returns.iloc[-lookback:]
    current_vol = recent.std() * (252 ** 0.5)
    if current_vol <= 0:
        return 1.0
    if current_vol <= target:
        return 1.0
    return max(target / current_vol, 0.1)


def resolve_stop_loss(
    date: pd.Timestamp,
    data_source: Dict[str, pd.DataFrame],
    ticker: str,
    entry_price: float,
    stop_loss: Optional[float],
    side: int,
    atr_lookback: int = 14,
    atr_mult: float = 2.0,
) -> float:
    if stop_loss is not None:
        return stop_loss
    if ticker not in data_source:
        return entry_price * (0.98 if side == 1 else 1.02)
    df = data_source[ticker]
    try:
        hist = df.loc[:date]
    except Exception:
        hist = df[df.index <= date]
    if hist.empty or len(hist) < (atr_lookback + 1):
        return entry_price * (0.98 if side == 1 else 1.02)

    high = hist["High"]
    low = hist["Low"]
    close = hist["Close"]
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(atr_lookback).mean().iloc[-1]
    if pd.isna(atr) or atr <= 0:
        return entry_price * (0.98 if side == 1 else 1.02)
    if side == 1:
        return entry_price - (atr * atr_mult)
    return entry_price + (atr * atr_mult)
