from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, HTTPException

from trade_journal_viz.server.app.deps import get_provider

router = APIRouter(prefix="/runs/{run_id}/filters", tags=["filters"])

provider = get_provider()


def _unique_sorted(series: pd.Series) -> List[str]:
    if series.empty:
        return []
    cleaned = (
        series.dropna()
        .astype(str)
        .map(str.strip)
        .loc[lambda value: value != ""]
        .loc[lambda value: value.str.lower() != "nan"]
    )
    values = cleaned.unique().tolist()
    return sorted(values, key=str.casefold)


def _date_range(df: pd.DataFrame, col: str) -> Optional[Dict[str, str]]:
    if df.empty or col not in df.columns:
        return None
    min_date = df[col].min()
    max_date = df[col].max()
    if pd.isna(min_date) or pd.isna(max_date):
        return None
    return {
        "min": min_date.strftime("%Y-%m-%d"),
        "max": max_date.strftime("%Y-%m-%d"),
    }


@router.get("")
def filter_options(run_id: str):
    try:
        dataset = provider.get(run_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    equity_df = dataset.table("equity_books_daily")
    positions_df = dataset.table("positions")
    trades_df = dataset.table("trades")

    date_source = equity_df if not equity_df.empty else trades_df
    date_range = _date_range(date_source, "date")

    books_source = positions_df if not positions_df.empty else trades_df
    strategies_source = positions_df if not positions_df.empty else trades_df
    symbols_source = positions_df if not positions_df.empty else trades_df
    exit_source = positions_df if "exit_reason" in positions_df.columns and not positions_df.empty else trades_df

    stop_source = positions_df if "stop_type_at_exit" in positions_df.columns and not positions_df.empty else trades_df

    return {
        "date_range": date_range,
        "books": _unique_sorted(books_source.get("book", pd.Series(dtype=str))),
        "strategies": _unique_sorted(strategies_source.get("strategy", pd.Series(dtype=str))),
        "symbols": _unique_sorted(symbols_source.get("symbol", pd.Series(dtype=str))),
        "exit_reasons": _unique_sorted(
            exit_source.get("exit_reason", exit_source.get("reason", pd.Series(dtype=str)))
        ),
        "stop_types": _unique_sorted(stop_source.get("stop_type_at_exit", pd.Series(dtype=str))),
    }
