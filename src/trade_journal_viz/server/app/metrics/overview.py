from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd


def _compute_drawdown(series: pd.Series) -> pd.Series:
    if series.empty:
        return series
    running_max = series.cummax()
    drawdown = (series / running_max - 1.0) * 100.0
    return drawdown


def _safe_float(value: float) -> float:
    if value is None:
        return 0.0
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "" or stripped.lower() in {"nan", "none"}:
            return 0.0
        try:
            return float(stripped)
        except ValueError:
            return 0.0
    if isinstance(value, (int, float, np.number)):
        try:
            if np.isnan(value):
                return 0.0
        except TypeError:
            return 0.0
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def compute_equity_series(equity_df: pd.DataFrame) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    if equity_df.empty:
        return out
    for _, row in equity_df.iterrows():
        out.append(
            {
                "date": row["date"].strftime("%Y-%m-%d") if pd.notna(row["date"]) else "",
                "total": _safe_float(row.get("equity_total")),
                "core": _safe_float(row.get("equity_dollars_core", row.get("equity_core"))),
                "convex": _safe_float(row.get("equity_dollars_convex", row.get("equity_convex"))),
            }
        )
    return out


def compute_drawdown_series(equity_df: pd.DataFrame) -> List[Dict[str, object]]:
    if equity_df.empty:
        return []
    core_series = equity_df.get("equity_dollars_core", equity_df.get("equity_core"))
    convex_series = equity_df.get("equity_dollars_convex", equity_df.get("equity_convex"))

    total_dd = _compute_drawdown(equity_df["equity_total"])
    core_dd = _compute_drawdown(core_series)
    convex_dd = _compute_drawdown(convex_series)

    out: List[Dict[str, object]] = []
    for pos, row in enumerate(equity_df.itertuples(index=False)):
        out.append(
            {
                "date": row.date.strftime("%Y-%m-%d") if pd.notna(row.date) else "",
                "total": _safe_float(total_dd.iloc[pos]),
                "core": _safe_float(core_dd.iloc[pos]) if core_dd is not None else 0.0,
                "convex": _safe_float(convex_dd.iloc[pos]) if convex_dd is not None else 0.0,
            }
        )
    return out


def compute_monthly_returns(equity_df: pd.DataFrame) -> List[Dict[str, object]]:
    if equity_df.empty:
        return []
    df = equity_df[["date", "equity_total"]].dropna().copy()
    if df.empty:
        return []
    df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()
    grouped = df.groupby("month").agg(first=("equity_total", "first"), last=("equity_total", "last"))
    grouped["return_pct"] = (grouped["last"] / grouped["first"] - 1.0) * 100.0
    out: List[Dict[str, object]] = []
    for idx, row in grouped.reset_index().iterrows():
        out.append(
            {
                "month": row["month"].strftime("%Y-%m"),
                "return_pct": _safe_float(row["return_pct"]),
            }
        )
    return out


def compute_portfolio_kpis(equity_df: pd.DataFrame) -> Dict[str, float]:
    if equity_df.empty:
        return {}
    df = equity_df[["date", "equity_total"]].dropna().copy()
    if df.empty:
        return {}
    df = df.sort_values("date")
    start = df.iloc[0]["equity_total"]
    end = df.iloc[-1]["equity_total"]
    total_return_pct = (end / start - 1.0) * 100.0 if start else 0.0
    days = max((df.iloc[-1]["date"] - df.iloc[0]["date"]).days, 1)
    years = days / 365.25
    cagr_pct = ((end / start) ** (1 / years) - 1.0) * 100.0 if start and years > 0 else 0.0

    daily_returns = df["equity_total"].pct_change().dropna()
    vol = daily_returns.std() * np.sqrt(252) * 100.0 if not daily_returns.empty else 0.0
    sharpe = (
        (daily_returns.mean() / daily_returns.std()) * np.sqrt(252)
        if daily_returns.std() and not np.isnan(daily_returns.std())
        else 0.0
    )
    max_drawdown_pct = float(_compute_drawdown(df["equity_total"]).min()) if not df.empty else 0.0

    return {
        "total_return_pct": _safe_float(total_return_pct),
        "cagr_pct": _safe_float(cagr_pct),
        "annualized_volatility_pct": _safe_float(vol),
        "sharpe_ratio": _safe_float(sharpe),
        "max_drawdown_pct": _safe_float(max_drawdown_pct),
    }


def compute_book_kpis(book_metrics_df: pd.DataFrame) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    if book_metrics_df.empty:
        return out
    for _, row in book_metrics_df.iterrows():
        book = str(row["book"])
        out[book] = {
            "total_return_pct": _safe_float(row.get("total_return_pct")),
            "cagr_pct": _safe_float(row.get("annualized_return_pct")),
            "annualized_volatility_pct": _safe_float(row.get("annualized_volatility_pct")),
            "sharpe_ratio": _safe_float(row.get("sharpe_ratio")),
            "sortino_ratio": _safe_float(row.get("sortino_ratio")),
            "calmar_ratio": _safe_float(row.get("calmar_ratio")),
            "max_drawdown_pct": _safe_float(row.get("max_drawdown_pct")),
            "time_in_market_pct": _safe_float(row.get("time_in_market_pct")),
            "turnover_pct": _safe_float(row.get("turnover_pct")),
            "total_trades": _safe_float(row.get("total_trades")),
            "win_rate_pct": _safe_float(row.get("win_rate_pct")),
            "avg_win": _safe_float(row.get("avg_win")),
            "avg_loss": _safe_float(row.get("avg_loss")),
            "profit_factor": _safe_float(row.get("profit_factor")),
            "avg_holding_days": _safe_float(row.get("avg_holding_days")),
            "avg_trade_return_pct": _safe_float(row.get("avg_trade_return_pct")),
            "payoff_ratio": _safe_float(row.get("payoff_ratio")),
            "expectancy": _safe_float(row.get("expectancy")),
            "total_pnl": _safe_float(row.get("total_pnl")),
            "total_commission": _safe_float(row.get("total_commission")),
            "total_slippage_cost": _safe_float(row.get("total_slippage_cost")),
        }
    return out


def build_overview_payload(equity_df: pd.DataFrame, book_metrics_df: pd.DataFrame) -> Dict[str, object]:
    equity_series = compute_equity_series(equity_df)
    drawdown_series = compute_drawdown_series(equity_df)
    monthly_returns = compute_monthly_returns(equity_df)
    portfolio_kpis = compute_portfolio_kpis(equity_df)
    book_kpis = compute_book_kpis(book_metrics_df)

    kpis = {"portfolio": portfolio_kpis}
    kpis.update(book_kpis)

    return {
        "kpis": kpis,
        "equity": equity_series,
        "drawdown": drawdown_series,
        "monthly_returns": monthly_returns,
    }
