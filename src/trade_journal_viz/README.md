# Trade Journal Viz

Institutional-style trading journal and insights dashboard for backtest runs.

This package is intentionally read-only against the backtester output and reads CSV files directly. It does not modify the backtester or strategies and does not convert CSV to Parquet.

## Layout

- `server/` FastAPI app that loads run folders from disk and serves metrics for dashboards.
- `web/` React UI (Vite + TypeScript) with a dark institutional theme.

## Run folders

Expected run folder structure (CSV only):

```
results/
  backtest_20260103_074846/
    allocator_rejections_daily.csv
    allocator_state_changes.csv
    book_daily_attribution.csv
    book_metrics.csv
    config.yaml
    equity_books_daily.csv
    exit_reason_attribution.csv
    positions.csv
    strategy_metrics_by_book.csv
    trades.csv
```

## Server

Set the runs root with `TRADE_JOURNAL_RUNS_ROOT` or let it default to the repo `results/` folder.

```
cd /mnt/d/Projects/stock_screener
source .venv/bin/activate
export TRADE_JOURNAL_RUNS_ROOT=/mnt/d/Projects/stock_screener/results
PYTHONPATH=/mnt/d/Projects/stock_screener/src \
  uvicorn trade_journal_viz.server.app.main:app --reload --port 8000 \
  --reload-dir /mnt/d/Projects/stock_screener/src/trade_journal_viz/server
```

API health check: `http://localhost:8000/api/health`  
Docs: `http://localhost:8000/docs`  
Note: `/` and `/favicon.ico` intentionally return 404 because the API only serves `/api/*`.

## Web

```
cd /mnt/d/Projects/stock_screener/src/trade_journal_viz/web
npm install
npm run dev
```

The UI expects the API at `http://localhost:8000/api`. Override with `VITE_API_BASE`.
