from __future__ import annotations

from typing import Dict

import pandas as pd


def build_positions_table(positions_df: pd.DataFrame, offset: int = 0, limit: int = 200) -> Dict[str, object]:
    if positions_df.empty:
        return {"total": 0, "rows": []}
    total = len(positions_df)
    df = positions_df.sort_values("entry_date", ascending=False).iloc[offset : offset + limit]
    rows = []
    for _, row in df.iterrows():
        rows.append(
            {
                "position_id": int(row.get("position_id")),
                "book": row.get("book"),
                "strategy": row.get("strategy"),
                "symbol": row.get("symbol"),
                "entry_date": row.get("entry_date").strftime("%Y-%m-%d")
                if pd.notna(row.get("entry_date"))
                else "",
                "exit_date": row.get("exit_date").strftime("%Y-%m-%d")
                if pd.notna(row.get("exit_date"))
                else "",
                "holding_days": float(row.get("holding_days", 0.0)),
                "initial_risk": float(row.get("initial_risk", 0.0)),
                "mfe_r": float(row.get("mfe_r", 0.0)),
                "mae_r": float(row.get("mae_r", 0.0)),
                "peak_r": float(row.get("peak_r", 0.0)),
                "exit_r": float(row.get("exit_r", 0.0)),
                "giveback_r": float(row.get("giveback_r", 0.0)),
                "exit_reason": row.get("exit_reason"),
                "stop_type_at_exit": row.get("stop_type_at_exit"),
                "realized_pnl": float(row.get("realized_pnl", 0.0)),
            }
        )
    return {"total": total, "rows": rows}


def build_position_detail(
    positions_df: pd.DataFrame, trades_df: pd.DataFrame, position_id: int
) -> Dict[str, object]:
    position_row = positions_df[positions_df["position_id"] == position_id]
    if position_row.empty:
        return {"position": None, "trades": []}
    row = position_row.iloc[0]
    position = {
        "position_id": int(row.get("position_id")),
        "entry_id": int(row.get("entry_id")),
        "book": row.get("book"),
        "strategy": row.get("strategy"),
        "symbol": row.get("symbol"),
        "entry_date": row.get("entry_date").strftime("%Y-%m-%d")
        if pd.notna(row.get("entry_date"))
        else "",
        "exit_date": row.get("exit_date").strftime("%Y-%m-%d")
        if pd.notna(row.get("exit_date"))
        else "",
        "entry_price": float(row.get("entry_price", 0.0)),
        "exit_price": float(row.get("exit_price", 0.0)),
        "holding_days": float(row.get("holding_days", 0.0)),
        "initial_risk": float(row.get("initial_risk", 0.0)),
        "adds_taken": float(row.get("adds_taken", 0.0)),
        "mfe_r": float(row.get("mfe_r", 0.0)),
        "mae_r": float(row.get("mae_r", 0.0)),
        "peak_r": float(row.get("peak_r", 0.0)),
        "exit_r": float(row.get("exit_r", 0.0)),
        "giveback_r": float(row.get("giveback_r", 0.0)),
        "exit_reason": row.get("exit_reason"),
        "stop_type_at_exit": row.get("stop_type_at_exit"),
        "realized_pnl": float(row.get("realized_pnl", 0.0)),
        "return_pct": float(row.get("return_pct", 0.0)),
        "costs_commission": float(row.get("costs_commission", 0.0)),
        "costs_slippage": float(row.get("costs_slippage", 0.0)),
        "allocator_state_at_entry": row.get("allocator_state_at_entry"),
        "allocator_state_at_exit": row.get("allocator_state_at_exit"),
        "risk_budget_pct_at_entry": float(row.get("risk_budget_pct_at_entry", 0.0)),
        "risk_budget_dollars_at_entry": float(row.get("risk_budget_dollars_at_entry", 0.0)),
    }

    trades = trades_df[trades_df["position_id"] == position_id]
    trade_rows = []
    for _, trow in trades.iterrows():
        trade_rows.append(
            {
                "date": trow.get("date").strftime("%Y-%m-%d") if pd.notna(trow.get("date")) else "",
                "symbol": trow.get("symbol"),
                "action": trow.get("action"),
                "qty": float(trow.get("qty", 0.0)),
                "price": float(trow.get("price", 0.0)),
                "exec_price": float(trow.get("exec_price", 0.0)),
                "commission": float(trow.get("commission", 0.0)),
                "slippage_cost": float(trow.get("slippage_cost", 0.0)),
                "book": trow.get("book"),
                "allocator_state": trow.get("allocator_state"),
                "reason": trow.get("reason"),
                "exit_r": float(trow.get("exit_r", 0.0)),
                "peak_r": float(trow.get("peak_r", 0.0)),
                "giveback_r": float(trow.get("giveback_r", 0.0)),
                "stop_type_at_exit": trow.get("stop_type_at_exit"),
                "add_on": trow.get("add_on"),
                "add_count": float(trow.get("add_count", 0.0)),
            }
        )
    return {"position": position, "trades": trade_rows}
