from __future__ import annotations

from typing import Dict, Optional

import pandas as pd
from fastapi import APIRouter, HTTPException

from trade_journal_viz.server.app.core.serialize import sanitize_for_json
from trade_journal_viz.server.app.deps import get_provider

router = APIRouter(prefix="/runs/{run_id}/daily-activity", tags=["daily-activity"])

provider = get_provider()


def _max_date(df: pd.DataFrame, col: str) -> Optional[pd.Timestamp]:
    if df.empty or col not in df.columns:
        return None
    series = pd.to_datetime(df[col], errors="coerce").dropna()
    if series.empty:
        return None
    return series.max()


def _resolve_as_of_date(
    equity_df: pd.DataFrame, trades_df: pd.DataFrame, positions_df: pd.DataFrame
) -> Optional[pd.Timestamp]:
    for df, col in (
        (equity_df, "date"),
        (trades_df, "date"),
        (positions_df, "exit_date"),
        (positions_df, "entry_date"),
    ):
        max_date = _max_date(df, col)
        if max_date is not None:
            return max_date
    return None


def _format_date(value: object) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    try:
        return pd.to_datetime(value).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _safe_float(value: object) -> float:
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
    if isinstance(value, (int, float)):
        try:
            if pd.isna(value):
                return 0.0
        except TypeError:
            return 0.0
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _position_row(row: pd.Series) -> Dict[str, object]:
    return {
        "position_id": int(row.get("position_id", 0) or 0),
        "symbol": row.get("symbol") or "",
        "book": row.get("book") or "",
        "strategy": row.get("strategy") or "",
        "entry_date": _format_date(row.get("entry_date")),
        "exit_date": _format_date(row.get("exit_date")),
        "entry_price": _safe_float(row.get("entry_price")),
        "exit_price": _safe_float(row.get("exit_price")),
        "holding_days": _safe_float(row.get("holding_days")),
        "exit_reason": row.get("exit_reason") or "",
        "stop_type_at_exit": row.get("stop_type_at_exit") or "",
        "realized_pnl": _safe_float(row.get("realized_pnl")),
        "exit_r": _safe_float(row.get("exit_r")),
        "giveback_r": _safe_float(row.get("giveback_r")),
    }


def _filter_positions_on_date(df: pd.DataFrame, col: str, as_of: pd.Timestamp) -> pd.DataFrame:
    if df.empty or col not in df.columns:
        return df.iloc[0:0]
    series = pd.to_datetime(df[col], errors="coerce")
    mask = series.dt.normalize() == as_of.normalize()
    return df.loc[mask]


@router.get("")
def daily_activity(run_id: str):
    try:
        dataset = provider.get(run_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    equity_df = dataset.table("equity_books_daily")
    trades_df = dataset.table("trades")
    positions_df = dataset.table("positions")

    as_of_date = _resolve_as_of_date(equity_df, trades_df, positions_df)
    if as_of_date is None:
        payload = {"as_of_date": "", "opened": [], "closed": []}
        return sanitize_for_json(payload)

    opened_df = _filter_positions_on_date(positions_df, "entry_date", as_of_date)
    closed_df = _filter_positions_on_date(positions_df, "exit_date", as_of_date)

    opened = [_position_row(row) for _, row in opened_df.iterrows()]
    closed = [_position_row(row) for _, row in closed_df.iterrows()]

    payload = {
        "as_of_date": as_of_date.strftime("%Y-%m-%d"),
        "opened": opened,
        "closed": closed,
    }
    return sanitize_for_json(payload)
