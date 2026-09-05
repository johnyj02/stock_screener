from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd


def _histogram(values: pd.Series, bins: List[float]) -> List[Dict[str, object]]:
    counts, edges = np.histogram(values.dropna(), bins=bins)
    out = []
    for idx, count in enumerate(counts):
        out.append(
            {
                "bin_start": float(edges[idx]),
                "bin_end": float(edges[idx + 1]),
                "count": int(count),
            }
        )
    return out


def build_giveback_distribution(positions_df: pd.DataFrame) -> List[Dict[str, object]]:
    if positions_df.empty or "giveback_r" not in positions_df.columns:
        return []
    bins = [-10.0, -5.0, -2.0, -1.0, 0.0, 1.0, 2.0, 5.0, 10.0, 20.0]
    return _histogram(positions_df["giveback_r"], bins)


def build_winner_retention(positions_df: pd.DataFrame) -> List[Dict[str, object]]:
    if positions_df.empty:
        return []
    df = positions_df.copy()
    df = df[pd.notna(df.get("peak_r")) & pd.notna(df.get("exit_r"))]
    if df.empty:
        return []

    peak_bins = [0.0, 1.0, 2.0, 3.0, 5.0, 10.0, np.inf]
    exit_bins = [-5.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 5.0, 10.0, np.inf]

    df["peak_bin"] = pd.cut(df["peak_r"], peak_bins, right=False)
    df["exit_bin"] = pd.cut(df["exit_r"], exit_bins, right=False)

    grouped = df.groupby(["peak_bin", "exit_bin"], dropna=True).size().reset_index(name="count")
    out = []
    for _, row in grouped.iterrows():
        out.append(
            {
                "peak_bin": str(row["peak_bin"]),
                "exit_bin": str(row["exit_bin"]),
                "count": int(row["count"]),
            }
        )
    return out


def build_worst_givebacks(positions_df: pd.DataFrame, limit: int = 20) -> List[Dict[str, object]]:
    if positions_df.empty:
        return []
    df = positions_df.sort_values("giveback_r", ascending=False).head(limit)
    out = []
    for _, row in df.iterrows():
        out.append(
            {
                "position_id": int(row.get("position_id")),
                "symbol": row.get("symbol"),
                "book": row.get("book"),
                "strategy": row.get("strategy"),
                "entry_date": row.get("entry_date").strftime("%Y-%m-%d")
                if pd.notna(row.get("entry_date"))
                else "",
                "exit_date": row.get("exit_date").strftime("%Y-%m-%d")
                if pd.notna(row.get("exit_date"))
                else "",
                "giveback_r": float(row.get("giveback_r", 0.0)),
                "peak_r": float(row.get("peak_r", 0.0)),
                "exit_r": float(row.get("exit_r", 0.0)),
                "realized_pnl": float(row.get("realized_pnl", 0.0)),
            }
        )
    return out


def build_exits_payload(
    positions_df: pd.DataFrame, exit_reason_df: pd.DataFrame
) -> Dict[str, object]:
    exit_reason = []
    for _, row in exit_reason_df.iterrows():
        exit_reason.append(
            {
                "book": row.get("book"),
                "reason": row.get("reason"),
                "stop_type_at_exit": row.get("stop_type_at_exit"),
                "count": float(row.get("count", 0.0)),
                "total_pnl": float(row.get("total_pnl", 0.0)),
            }
        )

    return {
        "exit_reason": exit_reason,
        "giveback_distribution": build_giveback_distribution(positions_df),
        "winner_retention": build_winner_retention(positions_df),
        "worst_givebacks": build_worst_givebacks(positions_df),
    }
