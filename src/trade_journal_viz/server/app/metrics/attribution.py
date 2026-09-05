from __future__ import annotations

from typing import Dict, List

import pandas as pd


def build_strategy_leaderboard(strategy_df: pd.DataFrame) -> List[Dict[str, object]]:
    if strategy_df.empty:
        return []
    sorted_df = strategy_df.sort_values("total_pnl", ascending=False)
    out = []
    for _, row in sorted_df.iterrows():
        out.append(
            {
                "book": row.get("book"),
                "strategy": row.get("strategy"),
                "trades": float(row.get("trades", 0.0)),
                "win_rate_pct": float(row.get("win_rate_pct", 0.0)),
                "total_pnl": float(row.get("total_pnl", 0.0)),
                "avg_pnl": float(row.get("avg_pnl", 0.0)),
                "avg_win": float(row.get("avg_win", 0.0)),
                "avg_loss": float(row.get("avg_loss", 0.0)),
                "profit_factor": float(row.get("profit_factor", 0.0)),
                "avg_holding_days": float(row.get("avg_holding_days", 0.0)),
                "avg_trade_return_pct": float(row.get("avg_trade_return_pct", 0.0)),
                "payoff_ratio": float(row.get("payoff_ratio", 0.0)),
                "expectancy": float(row.get("expectancy", 0.0)),
                "pct_peak_r_ge_1": float(row.get("pct_peak_r_ge_1", 0.0)),
                "pct_exit_ge_0_5r_given_peak_1": float(
                    row.get("pct_exit_ge_0_5r_given_peak_1", 0.0)
                ),
            }
        )
    return out


def build_pnl_by_strategy(strategy_df: pd.DataFrame) -> List[Dict[str, object]]:
    if strategy_df.empty:
        return []
    grouped = strategy_df.groupby(["strategy", "book"], as_index=False)["total_pnl"].sum()
    out = []
    for _, row in grouped.iterrows():
        out.append(
            {
                "strategy": row.get("strategy"),
                "book": row.get("book"),
                "total_pnl": float(row.get("total_pnl", 0.0)),
            }
        )
    return out


def build_exit_reason_attribution(exit_df: pd.DataFrame) -> List[Dict[str, object]]:
    if exit_df.empty:
        return []
    out = []
    for _, row in exit_df.iterrows():
        out.append(
            {
                "book": row.get("book"),
                "reason": row.get("reason"),
                "stop_type_at_exit": row.get("stop_type_at_exit"),
                "count": float(row.get("count", 0.0)),
                "win_rate_pct": float(row.get("win_rate_pct", 0.0)),
                "total_pnl": float(row.get("total_pnl", 0.0)),
                "avg_exit_r": float(row.get("avg_exit_r", 0.0)),
                "avg_peak_r": float(row.get("avg_peak_r", 0.0)),
                "avg_giveback_r": float(row.get("avg_giveback_r", 0.0)),
                "strategy": row.get("strategy"),
            }
        )
    return out


def build_attribution_payload(
    strategy_df: pd.DataFrame, exit_reason_df: pd.DataFrame
) -> Dict[str, object]:
    return {
        "strategy_leaderboard": build_strategy_leaderboard(strategy_df),
        "pnl_by_strategy": build_pnl_by_strategy(strategy_df),
        "exit_reason_attribution": build_exit_reason_attribution(exit_reason_df),
    }
