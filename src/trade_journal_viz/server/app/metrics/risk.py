from __future__ import annotations

from typing import Dict, List

import pandas as pd


def _series_from_equity(df: pd.DataFrame, core_col: str, convex_col: str) -> List[Dict[str, object]]:
    if df.empty:
        return []
    out = []
    for _, row in df.iterrows():
        out.append(
            {
                "date": row["date"].strftime("%Y-%m-%d") if pd.notna(row["date"]) else "",
                "core": float(row.get(core_col, 0.0)),
                "convex": float(row.get(convex_col, 0.0)),
            }
        )
    return out


def build_top_contributors(positions_df: pd.DataFrame, limit: int = 15) -> List[Dict[str, object]]:
    if positions_df.empty:
        return []
    grouped = positions_df.groupby("symbol", as_index=False).agg(
        total_pnl=("realized_pnl", "sum"),
        trades=("position_id", "count"),
    )
    grouped = grouped.sort_values("total_pnl", ascending=False).head(limit)
    out = []
    for _, row in grouped.iterrows():
        out.append(
            {
                "symbol": row.get("symbol"),
                "total_pnl": float(row.get("total_pnl", 0.0)),
                "trades": float(row.get("trades", 0.0)),
            }
        )
    return out


def build_risk_payload(equity_df: pd.DataFrame, positions_df: pd.DataFrame) -> Dict[str, object]:
    return {
        "exposure": _series_from_equity(equity_df, "gross_exposure_core", "gross_exposure_convex"),
        "open_risk": _series_from_equity(equity_df, "open_risk_dollars_core", "open_risk_dollars_convex"),
        "open_positions": _series_from_equity(equity_df, "open_positions_core", "open_positions_convex"),
        "top_contributors": build_top_contributors(positions_df),
    }
