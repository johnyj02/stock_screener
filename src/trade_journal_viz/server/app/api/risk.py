from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from trade_journal_viz.server.app.api.params import filters_from_query
from trade_journal_viz.server.app.core.filters import FilterParams, filter_equity, filter_positions
from trade_journal_viz.server.app.core.serialize import sanitize_for_json
from trade_journal_viz.server.app.deps import get_provider
from trade_journal_viz.server.app.metrics.risk import build_risk_payload

router = APIRouter(prefix="/runs/{run_id}/risk", tags=["risk"])

provider = get_provider()


@router.get("")
def risk(run_id: str, filters: FilterParams = Depends(filters_from_query)):
    try:
        dataset = provider.get(run_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    equity_df = filter_equity(dataset.table("equity_books_daily"), filters)
    positions_df = filter_positions(dataset.table("positions"), filters)
    payload = build_risk_payload(equity_df, positions_df)
    return sanitize_for_json(payload)
