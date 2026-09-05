from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from trade_journal_viz.server.app.api.params import filters_from_query
from trade_journal_viz.server.app.core.filters import (
    FilterParams,
    filter_equity,
    filter_rejections,
    filter_state_changes,
)
from trade_journal_viz.server.app.core.serialize import sanitize_for_json
from trade_journal_viz.server.app.deps import get_provider
from trade_journal_viz.server.app.metrics.allocator import build_allocator_payload

router = APIRouter(prefix="/runs/{run_id}/allocator", tags=["allocator"])

provider = get_provider()


@router.get("")
def allocator(run_id: str, filters: FilterParams = Depends(filters_from_query)):
    try:
        dataset = provider.get(run_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    equity_df = filter_equity(dataset.table("equity_books_daily"), filters)
    rejections_df = filter_rejections(dataset.table("allocator_rejections_daily"), filters)
    events_df = filter_state_changes(dataset.table("allocator_state_changes"), filters)

    payload = build_allocator_payload(equity_df, rejections_df, events_df)
    return sanitize_for_json(payload)
