from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

import pandas as pd


@dataclass
class FilterParams:
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    books: Optional[Iterable[str]] = None
    strategies: Optional[Iterable[str]] = None
    symbols: Optional[Iterable[str]] = None
    allocator_states: Optional[Iterable[str]] = None
    exit_reasons: Optional[Iterable[str]] = None
    stop_types: Optional[Iterable[str]] = None


def _apply_date_filter(df: pd.DataFrame, date_col: str, params: FilterParams) -> pd.DataFrame:
    if date_col not in df.columns:
        return df
    out = df
    if params.start_date:
        out = out[out[date_col] >= pd.to_datetime(params.start_date)]
    if params.end_date:
        out = out[out[date_col] <= pd.to_datetime(params.end_date)]
    return out


def _apply_in_filter(df: pd.DataFrame, col: str, values: Optional[Iterable[str]]) -> pd.DataFrame:
    if not values or col not in df.columns:
        return df
    return df[df[col].isin(values)]


def filter_trades(df: pd.DataFrame, params: FilterParams) -> pd.DataFrame:
    out = _apply_date_filter(df, "date", params)
    out = _apply_in_filter(out, "book", params.books)
    out = _apply_in_filter(out, "strategy", params.strategies)
    out = _apply_in_filter(out, "symbol", params.symbols)
    out = _apply_in_filter(out, "allocator_state", params.allocator_states)
    out = _apply_in_filter(out, "reason", params.exit_reasons)
    out = _apply_in_filter(out, "stop_type_at_exit", params.stop_types)
    return out


def filter_positions(df: pd.DataFrame, params: FilterParams) -> pd.DataFrame:
    out = _apply_date_filter(df, "entry_date", params)
    out = _apply_in_filter(out, "book", params.books)
    out = _apply_in_filter(out, "strategy", params.strategies)
    out = _apply_in_filter(out, "symbol", params.symbols)
    out = _apply_in_filter(out, "allocator_state_at_entry", params.allocator_states)
    out = _apply_in_filter(out, "exit_reason", params.exit_reasons)
    out = _apply_in_filter(out, "stop_type_at_exit", params.stop_types)
    return out


def filter_book_daily(df: pd.DataFrame, params: FilterParams) -> pd.DataFrame:
    out = _apply_date_filter(df, "date", params)
    out = _apply_in_filter(out, "book", params.books)
    out = _apply_in_filter(out, "allocator_state", params.allocator_states)
    return out


def filter_rejections(df: pd.DataFrame, params: FilterParams) -> pd.DataFrame:
    out = _apply_date_filter(df, "date", params)
    out = _apply_in_filter(out, "book", params.books)
    return out


def filter_state_changes(df: pd.DataFrame, params: FilterParams) -> pd.DataFrame:
    out = _apply_date_filter(df, "date", params)
    out = _apply_in_filter(out, "book", params.books)
    return out


def filter_equity(df: pd.DataFrame, params: FilterParams) -> pd.DataFrame:
    return _apply_date_filter(df, "date", params)
