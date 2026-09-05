from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from trade_journal_viz.server.app.api.params import filters_from_query
from trade_journal_viz.server.app.core.filters import FilterParams, filter_positions
from trade_journal_viz.server.app.core.serialize import sanitize_for_json
from trade_journal_viz.server.app.deps import get_provider
from trade_journal_viz.server.app.metrics.journal import build_position_detail, build_positions_table

router = APIRouter(prefix="/runs/{run_id}/journal", tags=["journal"])

provider = get_provider()


@router.get("/positions")
def positions_list(
    run_id: str,
    filters: FilterParams = Depends(filters_from_query),
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
):
    try:
        dataset = provider.get(run_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    positions_df = filter_positions(dataset.table("positions"), filters)
    payload = build_positions_table(positions_df, offset=offset, limit=limit)
    return sanitize_for_json(payload)


@router.get("/positions/{position_id}")
def position_detail(run_id: str, position_id: int):
    try:
        dataset = provider.get(run_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    positions_df = dataset.table("positions")
    trades_df = dataset.table("trades")
    payload = build_position_detail(positions_df, trades_df, position_id)
    return sanitize_for_json(payload)
