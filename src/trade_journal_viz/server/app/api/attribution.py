from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from trade_journal_viz.server.app.api.params import filters_from_query
from trade_journal_viz.server.app.core.filters import FilterParams
from trade_journal_viz.server.app.core.serialize import sanitize_for_json
from trade_journal_viz.server.app.deps import get_provider
from trade_journal_viz.server.app.metrics.attribution import build_attribution_payload

router = APIRouter(prefix="/runs/{run_id}/attribution", tags=["attribution"])

provider = get_provider()


@router.get("")
def attribution(run_id: str, filters: FilterParams = Depends(filters_from_query)):
    try:
        dataset = provider.get(run_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    strategy_df = dataset.table("strategy_metrics_by_book")
    exit_reason_df = dataset.table("exit_reason_attribution")

    if filters.books:
        strategy_df = strategy_df[strategy_df["book"].isin(filters.books)]
        exit_reason_df = exit_reason_df[exit_reason_df["book"].isin(filters.books)]
    if filters.strategies:
        strategy_df = strategy_df[strategy_df["strategy"].isin(filters.strategies)]
        exit_reason_df = exit_reason_df[exit_reason_df["strategy"].isin(filters.strategies)]

    payload = build_attribution_payload(strategy_df, exit_reason_df)
    return sanitize_for_json(payload)
