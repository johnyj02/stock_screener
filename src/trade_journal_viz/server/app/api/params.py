from __future__ import annotations

from typing import List, Optional

from fastapi import Query

from trade_journal_viz.server.app.core.filters import FilterParams


def filters_from_query(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    book: Optional[List[str]] = Query(None),
    strategy: Optional[List[str]] = Query(None),
    symbol: Optional[List[str]] = Query(None),
    allocator_state: Optional[List[str]] = Query(None),
    exit_reason: Optional[List[str]] = Query(None),
    stop_type: Optional[List[str]] = Query(None),
) -> FilterParams:
    return FilterParams(
        start_date=start_date,
        end_date=end_date,
        books=book,
        strategies=strategy,
        symbols=symbol,
        allocator_states=allocator_state,
        exit_reasons=exit_reason,
        stop_types=stop_type,
    )
