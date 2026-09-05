# Professional Stock Screener

A modular, class-based stock screener with 10+ advanced technical strategies.

## Setup

1.  **Install Dependencies**:
```bash
    python3 -m venv .venv 
    source .venv/bin/activate
    pip install -r requirements.txt
```

2.  **Run Screener**:
```bash
    # Run on top 50 S&P 500 stocks
    export PYTHONPATH="/mnt/d/Projects/stock_screener/src"
    python -m stock_screener.main --limit 50
    
    # Run on specific tickers
    python -m stock_screener.main --tickers AAPL,TSLA,NVDA
    # Run for futures
    python -m stock_screener.main --tickers GC=F,SI=F,BTC=F,SPY=F

    # Run with vectorized signals
    python -m stock_screener.main --vectorized

    # Run backtesting
    python -m strategy_backtester.main --start 2024-11-01 --tickers-file data/sp500.csv

    # Run backtesting with cofig
    python -m strategy_backtester.main --config src/strategy_backtester/config/backtest_orb15.yaml

    # Run Grid search 
    python -m strategy_grid_search.main --config src/strategy_grid_search/configs/sma200_rsi_fib_regime_grid_search.yaml

    # Run Walk forward grid search backtesting
    python -m strategy_grid_search.walk_forward --config src/strategy_grid_search/configs/sma200_rsi_fib_regime_grid_search.yaml
    
```
### Static data source (backtester only)
You can load local OHLCV CSVs instead of yfinance by adding a `backtest.data_source` block.

Example (Kaggle SPY 1-minute data, resampled to 5m):
```yaml
backtest:
  data_source:
    type: static
    static:
      files:
        - path: data/kaggle/SPY-1M/1_min_SPY_2008-2021.csv
          ticker: SPY
      file_interval: 1m
      resample_to: 5m
      timezone: America/Denver
      timestamp_col: date
      timestamp_format: "%Y%m%d  %H:%M:%S"
```

Notes:
- If only one file is provided and your config has exactly one ticker, `ticker` can be omitted.
- If a file contains a `symbol`/`ticker` column, that will be used automatically.
- Globs are supported in `path`; if a glob expands to multiple files, each must supply a ticker or contain a ticker column.
- Missing symbols hard-fail (no yfinance fallback).
- `hedge_symbol` is optional when using static data (it is only fetched if the static files declare it).
- `market_symbols` are optional when `regime_gating.enabled: false` (only fetched if declared in the static files).

### IBKR data source (backtester only)
Use Interactive Brokers (TWS) for historical data via `ib_insync`.

Example:
```yaml
backtest:
  data_source:
    type: ibkr
    ibkr:
      host: 127.0.0.1
      port: 7497
      client_id: 1
      cache_dir: .cache_ibkr
      allow_partial: true
      what_to_show: TRADES
      use_rth: false
      max_requests_per_min: 6
      min_sleep_seconds: 10.0
      request_timeout: 300.0
      futures_use_end_datetime: false
      futures_contract_mode: auto_roll
      include_expired: true
      contracts:
        ES=F:
          sec_type: FUT
          symbol: ES
          exchange: CME
          currency: USD
          last_trade_date_or_contract_month: "202406"
```

Notes:
- Requires TWS running with API enabled. (Default port 7497.)
- IBKR data is cached separately in `cache_dir`.
- If `allow_partial: true`, missing symbols log a warning and the run continues.
- For futures/forex/crypto, provide `contracts` overrides; IBKR symbols are not always inferable.
- Intraday history is limited by IBKR bar size. Defaults cap lookback by interval (override via `max_history_days` or `max_history_days_by_interval`).
- Futures tickers ending with `=F` default to `auto_roll` (specific `FUT` contracts) and roll by month using `endDateTime` per segment. `include_expired` defaults to true to allow older contract months.
- Set `futures_contract_mode: contfut` to use continuous futures (no contract month).
- To request a single specific contract month, set `contracts.*.sec_type: FUT` and `contracts.*.last_trade_date_or_contract_month` (or use `auto_futures_months_ahead`).
- By default, futures ignore `end_date` when requesting IBKR history (`futures_use_end_datetime: false`), which works best for near‑present ranges. Set `futures_use_end_datetime: true` if you need strict historical windows.
- For `CONTFUT`, the duration request uses the configured start/end window (no buffer) and is capped by the interval history limit.
- Default pacing enforces ~1 request every 10 seconds (override to slower only).
- Auto overrides: `ES=F`/`MES=F` use quarterly months, `GC=F` uses Feb/Apr/Jun/Aug/Oct/Dec, `SI=F` uses Mar/May/Jul/Sep/Dec (micro silver via `tradingClass: SIL`), and `BTC=F` maps to `MBT` (micro bitcoin). Override via `contracts` if needed.
- `request_timeout` controls the per-request wait time for `reqHistoricalData` (ib_insync defaults to 60s).
### Useful commands
```bash
pkill -KILL -f "strategy_grid_search.main"
```
Note: When `backtest.end_date` is set, cache freshness and incremental downloads use that date (not today).
Note: For futures, `futures_margin_pct` is used as the capital basis for `Max Invested`, `strategy_max_position_pct`, and `max_alloc_pct` (i.e., margin-based sizing instead of gross notional). `max_alloc_pct` uses current equity (not initial capital).

Sizing definitions (formulas)
- `engine.risk_per_trade_pct`: per‑trade risk budget.  
  `risk_budget = current_equity * risk_per_trade_pct`  
  `per_contract_risk = |entry - stop| * multiplier`  
  `qty = risk_budget / per_contract_risk` (futures qty is floored to int)
- `engine.trade_size`: hard cap on per‑trade notional.  
  Equities: `trade_size_cap = trade_size`  
  Futures: `trade_size_cap = trade_size / futures_margin_pct`
- `engine.max_alloc_pct`: portfolio‑level allocation cap (current equity).  
  `max_alloc = current_equity * max_alloc_pct`  
  Remaining capacity is reduced by equity long notional + futures margin used.
- `strategy_max_position_pct`: per‑position cap.  
  Equities: `max_notional = current_equity * strategy_max_position_pct`  
  Futures: `max_notional = (current_equity * strategy_max_position_pct) / futures_margin_pct`

Turnover metrics:
- `turnover_pct`: gross notional turnover (futures uses full notional).
- `turnover_margin_pct`: turnover adjusted by futures margin (equities use 1.0).
- Annualized variants are available: `turnover_annualized_pct`, `turnover_margin_annualized_pct`.

See `docs/trading_variables.md` for a fuller glossary of sizing rules and report metrics.

Regime models (optional)
You can keep legacy SMA regimes or switch to a multi-factor regime model without breaking existing configs.

Example config (engine block):
```yaml
engine:
  regime:
    model: "simple_sma"   # or "multi_factor_v1"
    symbols:
      market: "SPY"
      vol: "VIXY"
      credit: "HYG"
      rates: "TLT"
    params:
      risk_on_threshold: 0.60
      hysteresis_on: 0.65
      hysteresis_off: 0.55
      hedge_max_pct: 0.50
      intraday_mode: "daily_ffill_shift1"
      size_by_regime_default: false
      apply_exposure_caps: false
    missing_data_policy:
      policy: "soft"
      max_bad_days_pct: 0.05
      min_components: 2
      warn_limit_per_symbol: 1
```

Strategy-level regime gates (optional):
- `allowed_trend_states`: limit entries to specific trend regimes.
- `max_vol_state`: block entries above a volatility regime (e.g., HIGH).
- `min_risk_on_prob`: require a minimum risk-on probability.
- `size_by_regime`: scale risk sizing by regime probability.
- `allow_entries_in_risk_off`: per-strategy override to block risk-off entries.

Regime attribution output:
- `regime_attribution.csv` in the backtest results folder.

## Running Tests

1. Install dev dependencies:
```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements-dev.txt
```
2. Run the suite (sets module path so `src/` imports resolve):
```bash
   export PYTHONPATH="$(pwd)/src"
   pytest
```

## Parameter Search (Grid/Random/Optuna)

This repo includes a separate package for tuning backtest configs without changing the core backtester.

```bash
# Generate a full parameter registry (all strategy + engine params)
export PYTHONPATH="/mnt/d/Projects/stock_screener/src"
python -m strategy_grid_search.main --generate-registry --registry-out param_registry.yaml

# List tunable params for a specific backtest config
python -m strategy_grid_search.main --list-params --base-config src/strategy_backtester/config/backtest_orb15.yaml

# If you generated a registry in a custom location, pass it explicitly
python -m strategy_grid_search.main --list-params --registry-out src/strategy_grid_search/param_registry/param_registry.yaml

# Run an Optuna search (random startup + TPE)
python -m strategy_grid_search.main --config src/strategy_grid_search/configs/orb15_grid_search.yaml

# Run walk-forward optimization (uses walk_forward block in the same config)
python -m strategy_grid_search.walk_forward --config src/strategy_grid_search/configs/orb15_grid_search.yaml
```

Notes:
- Random phase can be parallelized via `search.parallel_workers`.
- Optuna is optional; install it if you want tree-based search (`pip install optuna`).
- Intermediate results are written to a journal (`src/strategy_grid_search/results/grid_search/<trial_name>/journal.jsonl`) and resumes are supported.
- Use `trial_name` in the search config to isolate runs; each trial writes to its own folder and Optuna DB.
- Paths in the search config (e.g., `base_config`, `registry_overrides`) can be relative to the search config file or repo root.
- `search.random_pct` controls the random phase before Optuna; Optuna then runs the remaining trials.
- Use `search.progress_every` (default 20) to print progress updates every N trials.
- Set `search.quick_eval.record_pruned: true` to record quick-eval prunes in the journal (skipped on resume for the same run).
- `search.prefetch_data` (default true) preloads market data once per worker and deep-copies it per trial to avoid repeated fetches.
- SQLite Optuna storage uses WAL + a 60s timeout by default; override with `optuna.sqlite_timeout`, `optuna.sqlite_journal_mode`, `optuna.sqlite_synchronous`.
- Set `STRATEGY_LOADER_DISABLE_CACHE=1` to force strategy re-scan on every load (disables the per-process cache).
- When resuming, prior trials in the journal are reused and duplicates are skipped.
- Optuna trials skip parameter sets already seen in the journal to avoid repeats.
- Optuna stops after 10 consecutive duplicate prunes (override with `search.duplicate_prune_limit`).
- Use `search.drawdown_cap_pct` to prune any trial whose `max_drawdown_pct` exceeds the cap (absolute value).
- Walk-forward outputs are written under `results/grid_search/<trial_name>/wfv/<run_id>/`.
- Walk-forward can warm-start from the previous walk (`walk_forward.warm_start`) and run a quick-eval first pass (`walk_forward.search_overrides.quick_eval`).
- Quick-eval runs a short backtest window to filter candidates before the full run; set `search.quick_eval.train_fraction` (or `train_days`), `keep_pct`, and optional `min_score`.

Timeout examples (grid search):
```yaml
search:
  trial_timeout_s: 420            # per-trial hard timeout (seconds)
  worker_stall_timeout_s: 600     # watchdog: worker considered stalled after N seconds
  worker_stall_check_s: 30        # watchdog check interval
  worker_stall_kill: true         # kill stalled worker process
  quick_eval:
    enabled: true
    timeout_s: 180                # quick-eval timeout (defaults to trial_timeout_s)
```

Timeout examples (walk-forward overrides):
```yaml
walk_forward:
  search_overrides:
    trial_timeout_s: 420
    quick_eval:
      timeout_s: 180
```

After a run, the tuner writes:
- `best_config.yaml` (ready-to-run backtest config with best params)
- `summary.json` (best score/metrics + trial counts)
- `top_results.json` (top-N trials)
- `param_importance.json` (simple importance estimates)

## Adding New Strategies

Simply create a new python file in `src/stock_screener/strategies/` (e.g., `my_custom.py`).
Inherit from `BaseStrategy` and implement `check()`.

```python
from stock_screener.core.strategy import BaseStrategy

class MyStrategy(BaseStrategy):
    def check(self, df):
        # Your logic here
        if some_condition:
            return True, {'metric': 123}
        return False, {}
```

It will be automatically discovered next time you run!

## Included Strategies

**Trend**
- Golden Cross
- Supertrend Reversal
- EMA Pullback

**Momentum**
- RSI Oversold
- MACD Turnaround
- VPT Breakout
- Bearish RSI Divergence

**Volatility**
- Bollinger Squeeze
- NR4 / NR7

**Pattern**
- Bullish Engulfing
- Volume Spike
