from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from trade_journal_viz.server.app.api.allocator import router as allocator_router
from trade_journal_viz.server.app.api.attribution import router as attribution_router
from trade_journal_viz.server.app.api.daily_activity import router as daily_activity_router
from trade_journal_viz.server.app.api.charts import router as charts_router
from trade_journal_viz.server.app.api.exits import router as exits_router
from trade_journal_viz.server.app.api.filters import router as filters_router
from trade_journal_viz.server.app.api.journal import router as journal_router
from trade_journal_viz.server.app.api.overview import router as overview_router
from trade_journal_viz.server.app.api.risk import router as risk_router
from trade_journal_viz.server.app.api.runs import router as runs_router

app = FastAPI(title="Trade Journal Viz API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


app.include_router(runs_router, prefix="/api")
app.include_router(overview_router, prefix="/api")
app.include_router(risk_router, prefix="/api")
app.include_router(allocator_router, prefix="/api")
app.include_router(attribution_router, prefix="/api")
app.include_router(exits_router, prefix="/api")
app.include_router(journal_router, prefix="/api")
app.include_router(filters_router, prefix="/api")
app.include_router(charts_router, prefix="/api")
app.include_router(daily_activity_router, prefix="/api")
