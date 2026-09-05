from __future__ import annotations

from typing import Dict, List

import pandas as pd


def build_state_timeline(equity_df: pd.DataFrame) -> List[Dict[str, object]]:
    if equity_df.empty:
        return []
    out: List[Dict[str, object]] = []
    for _, row in equity_df.iterrows():
        date_str = row["date"].strftime("%Y-%m-%d") if pd.notna(row["date"]) else ""
        for book in ("core", "convex"):
            col = f"allocator_state_{book}"
            if col in equity_df.columns:
                out.append(
                    {
                        "date": date_str,
                        "book": book,
                        "state": row.get(col, ""),
                    }
                )
    return out


def build_rejections_long(rejections_df: pd.DataFrame) -> List[Dict[str, object]]:
    if rejections_df.empty:
        return []
    rejection_cols = [c for c in rejections_df.columns if c.startswith("rejected_")]
    if not rejection_cols:
        return []
    melted = rejections_df.melt(
        id_vars=["date", "book"],
        value_vars=rejection_cols,
        var_name="reason",
        value_name="count",
    )
    out = []
    for _, row in melted.iterrows():
        out.append(
            {
                "date": row["date"].strftime("%Y-%m-%d") if pd.notna(row["date"]) else "",
                "book": row["book"],
                "reason": row["reason"].replace("rejected_", ""),
                "count": float(row["count"]),
            }
        )
    return out


def build_acceptance_rate(rejections_df: pd.DataFrame) -> List[Dict[str, object]]:
    if rejections_df.empty:
        return []
    out = []
    for _, row in rejections_df.iterrows():
        signals = float(row.get("signals_seen", 0.0))
        accepted = float(row.get("accepted", 0.0))
        rate = accepted / signals if signals else 0.0
        out.append(
            {
                "date": row["date"].strftime("%Y-%m-%d") if pd.notna(row["date"]) else "",
                "book": row.get("book"),
                "acceptance_rate": rate,
            }
        )
    return out


def build_events(events_df: pd.DataFrame) -> List[Dict[str, object]]:
    if events_df.empty:
        return []
    out = []
    for _, row in events_df.iterrows():
        out.append(
            {
                "date": row["date"].strftime("%Y-%m-%d") if pd.notna(row["date"]) else "",
                "book": row.get("book"),
                "prev_state": row.get("prev_state"),
                "new_state": row.get("new_state"),
                "trigger": row.get("trigger"),
                "book_drawdown_pct": float(row.get("book_drawdown_pct", 0.0)),
                "portfolio_drawdown_pct": float(row.get("portfolio_drawdown_pct", 0.0)),
                "risk_budget_before": float(row.get("risk_budget_before", 0.0)),
                "risk_budget_after": float(row.get("risk_budget_after", 0.0)),
            }
        )
    return out


def build_allocator_payload(
    equity_df: pd.DataFrame, rejections_df: pd.DataFrame, events_df: pd.DataFrame
) -> Dict[str, object]:
    return {
        "state_timeline": build_state_timeline(equity_df),
        "rejections": build_rejections_long(rejections_df),
        "acceptance_rate": build_acceptance_rate(rejections_df),
        "events": build_events(events_df),
    }
