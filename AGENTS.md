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

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
