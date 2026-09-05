# AGENTS.md

Project guidance for coding agents working in this repo.

## Project layout
- `src/stock_screener/`: screener core + strategies.
- `src/strategy_backtester/`: backtest engine + configs.
- `tests/`: pytest suite.
- `data/`: tickers, calendars, inputs.
- `results/`: backtest outputs (do not edit unless requested).

## Workflow expectations
- Prefer `rg` for search.
- Use minimal, targeted edits; avoid touching unrelated files.
- Keep edits ASCII unless a file already uses Unicode.
- Config YAMLs are OK to modify when the user is actively working on them. 
- Do not delete caches or results unless requested.
- Intraday data has limited history; expect yfinance limits (e.g., 5m ~60 days).
- Use strategies.md to check for details of a strategy that you are dealing with (if present in strategies.md).
- Before creating or editing grid search configs, review `grid_search.md` for parameters that should not be tuned and any single-value defaults to keep fixed.

## Documentation updates (required)
- If you change behavior, defaults, or usage, update `README.md`.
- If you add/remove/modify strategies or strategy behavior, update `strategies.md`.
- If you change sizing rules or metric definitions, update `docs/trading_variables.md` (and keep it in sync with `README.md`).
- Keep docs concise; avoid large code dumps.

## Testing
- Use the project venv when running tests.
- Run tests with:
  - `export PYTHONPATH="$(pwd)/src"`
  - `pytest`
- New tests should avoid network access (use fakes/mocks).

## Backtesting
- Prefer config-driven runs (e.g., `python -m strategy_backtester.main --config ...`).
- Do not commit backtest outputs unless requested.
