# Graph Report - stock_screener  (2026-09-06)

## Corpus Check
- 154 files · ~1,805,595 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1503 nodes · 3860 edges · 55 communities (35 shown, 9 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 117 edges (avg confidence: 0.93)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `30114bc1`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- BacktestEngine
- api.ts
- FilterParams
- Any
- .build
- walk_forward.py
- test_orb_and_structure_characterization.py
- ablation.py
- DataFrame
- add_common_indicators
- test_backtest_engine_characterization.py
- Trading variables and metrics
- DataFrame
- test_cache_and_data_provider.py
- DataFrame
- Trend portfolio with core and convex books
- DataFrame
- Graphify workflow for Codex
- IBKRDataProvider
- package.json
- CacheManager
- strategy.py
- RunDataset
- .get_description
- runs.py
- BaseStrategy
- DataFrame
- compilerOptions
- DataFrame
- BullishEngulfing
- DummyTrendStrategy
- update_ibkr_cache.py
- DataProvider
- strategy_backtester/charts.py
- EventCalendar
- compilerOptions
- .get_name
- scripts/__init__.py
- strategy_grid_search/__init__.py
- Claude project settings
- Codex project hooks
- metrics/overview.py
- .get_take_profit_level_pcts
- build_data_provider

## God Nodes (most connected - your core abstractions)
1. `BacktestEngine` - 131 edges
2. `BaseStrategy` - 97 edges
3. `_run_walk_forward()` - 38 edges
4. `IBKRDataProvider` - 33 edges
5. `_engine()` - 30 edges
6. `RegimeState` - 24 edges
7. `run_hybrid_search()` - 24 edges
8. `PatternSeriesMixin` - 23 edges
9. `main()` - 22 edges
10. `FilterParams` - 22 edges

## Surprising Connections (you probably didn't know these)
- `Complementary regime filters` --semantically_similar_to--> `Regime gates`  [INFERRED] [semantically similar]
  src/strategy_backtester/config/backtest_lmr_vtx_stack.yaml → docs/trading_variables.md
- `test_data_provider_uses_cache_and_adds_indicators()` --uses--> `DataProvider`  [INFERRED]
  tests/test_cache_and_data_provider.py → src/stock_screener/core/data.py
- `test_strategy_loader_skips_broken_modules()` --uses--> `StrategyLoader`  [INFERRED]
  tests/test_loader_and_reporting.py → src/stock_screener/core/loader.py
- `Shifted weekly levels` --semantically_similar_to--> `daily_ffill_shift1`  [INFERRED] [semantically similar]
  src/strategy_backtester/config/backtest_longterm_sr.yaml → README.md
- `Exit weak trends after insufficient favorable excursion` --semantically_similar_to--> `Sma200RsiOversoldFib`  [INFERRED] [semantically similar]
  src/strategy_backtester/config/backtest_trend_only_decay.yaml → strategies.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Shared book risk allocation** — src_strategy_backtester_config_backtest_combined_db_trend_book_allocator, src_strategy_backtester_config_backtest_longterm_sr_long_term_support_resistance, src_strategy_backtester_config_backtest_market_aligned_structure_marketalignedstructurebreak [INFERRED 0.95]
- **Execution sizing regime turnover experiments** — src_strategy_backtester_config_backtest_experiment_costs_execution_cost_experiment, src_strategy_backtester_config_backtest_experiment_deploy_deployment_sizing_experiment, src_strategy_backtester_config_backtest_experiment_shorts_risk_on_short_experiment, src_strategy_backtester_config_backtest_experiment_turnover_turnover_reduction_experiment [INFERRED 0.95]
- **Trend ablations isolate decay, EMA state, and later pyramiding with trailing** — src_strategy_backtester_config_backtest_trend_only_ablation_base_backtest, src_strategy_backtester_config_backtest_trend_only_decay_backtest, src_strategy_backtester_config_backtest_trend_only_state_backtest, src_strategy_backtester_config_backtest_trend_only_state_adds_trail_backtest [INFERRED 0.95]
- **Rolling evaluation with purge, embargo, warm starts, and correlation-filtered ensembles** — src_strategy_grid_search_configs_orb15_grid_search_trial, src_strategy_grid_search_configs_doublebottom_confirmations_grid_search_trial, src_strategy_grid_search_configs_lmr_vtx_stack_grid_search_trial, src_strategy_grid_search_configs_longterm_sr_grid_search_trial, src_strategy_grid_search_configs_sma200_rsi_fib_regime_grid_search_trial [INFERRED 0.95]

## Communities (55 total, 9 thin omitted)

### Community 0 - "BacktestEngine"
Cohesion: 0.06
Nodes (17): contract_multiplier(), is_future_symbol(), BacktestEngine, Any, DataFrame, Series, Timestamp, Apply the shared exit pipeline to every non-hedge position. (+9 more)

### Community 1 - "api.ts"
Cohesion: 0.06
Nodes (84): react, react-router-dom, recharts, @tanstack/react-query, App(), BarChartSimple(), BarChartSimpleProps, DrawdownChart() (+76 more)

### Community 2 - "FilterParams"
Cohesion: 0.06
Nodes (71): allocator(), get, attribution(), get, daily_activity(), _filter_positions_on_date(), _format_date(), _max_date() (+63 more)

### Community 3 - "Any"
Cohesion: 0.06
Nodes (32): _cluster_levels(), DoubleBottomRsiDivergence, DoubleTopRsiDivergence, FlagPennantContinuation, HeadAndShouldersReversal, _line_value(), _linear_fit(), LongTermSupportResistanceBreakRetest (+24 more)

### Community 4 - ".build"
Cohesion: 0.07
Nodes (35): build_regime_series(), Any, DataFrame, Index, Timestamp, RegimeBuildResult, RegimeModel, MultiFactorRegimeModelV1 (+27 more)

### Community 5 - "walk_forward.py"
Cohesion: 0.05
Nodes (105): Random, analyze_and_write(), _apply_params(), _filter_ok(), _hashable_value(), _importance_from_records(), load_journal(), Any (+97 more)

### Community 6 - "test_orb_and_structure_characterization.py"
Cohesion: 0.08
Nodes (48): date, _atr_15m_metrics(), _effective_exit_cutoff_time(), _market_risk_gate_series(), _market_trend_gate_series(), _normalize_skip_dates(), _Orb15Breakout, Orb15BreakoutLong (+40 more)

### Community 7 - "ablation.py"
Cohesion: 0.09
Nodes (56): _apply_param_override(), _filter_strategies(), _load_base_config(), main(), _meta_value(), _param_label(), _parse_strategy_targets(), Any (+48 more)

### Community 8 - "DataFrame"
Cohesion: 0.07
Nodes (28): AboveEmaState20, AboveEmaState50, CloseAboveEma20, CloseAboveEma50, DonchianBreakout, GoldenCross, NoMansLandFilter, PullbackToEma (+20 more)

### Community 9 - "add_common_indicators"
Cohesion: 0.07
Nodes (50): Any, DataFrame, Main execution engine for the stock screener., Run the screener on a list of tickers. Args: tickers: List of ticker symbols to…, ScreenerEngine, add_common_indicators(), add_market_context_columns(), _anchored_vwap() (+42 more)

### Community 10 - "test_backtest_engine_characterization.py"
Cohesion: 0.13
Nodes (35): _engine(), _NamedStrategy, _prices(), DataFrame, parametrize, _regime(), _Strategy, test_book_allocator_initializes_budget_and_state_labels() (+27 more)

### Community 11 - "Trading variables and metrics"
Cohesion: 0.06
Nodes (40): Execution costs, Futures margin capital basis, Profit protection, Regime gates, Risk-based sizing, Trading variables and metrics, Turnover metrics, Fixed parameters (+32 more)

### Community 12 - "DataFrame"
Cohesion: 0.10
Nodes (16): BearishRsiDivergence, MacdTurnaround, Any, DataFrame, Series, Momentum-based screening strategies., RSI < 30 indicates oversold conditions., Long WaveTrend oversold crossover; exit on its overbought crossover. (+8 more)

### Community 13 - "test_cache_and_data_provider.py"
Cohesion: 0.50
Nodes (4): DataFrame, _sample_df(), test_cache_manager_save_update_metadata(), test_data_provider_uses_cache_and_adds_indicators()

### Community 14 - "DataFrame"
Cohesion: 0.13
Nodes (20): _close_location(), GapDownContinuation, GapDownFade, GapUpContinuation, GapUpFade, _is_daily(), Any, DataFrame (+12 more)

### Community 15 - "Trend portfolio with core and convex books"
Cohesion: 0.07
Nodes (38): ORB futures backtest, SMA200 RSI Fibonacci backtest, Regime-filtered SMA200 RSI Fibonacci backtest, Trend ablation baseline with confirmation helpers, Trend portfolio with core and convex books, Core and convex book risk allocator, Trend experiment with decay exit, Exit weak trends after insufficient favorable excursion (+30 more)

### Community 16 - "DataFrame"
Cohesion: 0.12
Nodes (12): Breakdown20, MacdBearishCross, Any, DataFrame, Series, Bearish screening strategies (short-biased signals)., RSI > 70 and turning down (bearish momentum)., Close breaks below 20-day low with a down-sloping 20-day SMA. (+4 more)

### Community 17 - "Graphify workflow for Codex"
Cohesion: 0.09
Nodes (35): Claude Graphify skill registration, Claude URL ingestion and file watching, Claude graph exports and MCP integration, Claude semantic extraction schema, Claude GitHub cloning and repository graph merging, Claude commit hooks and project integration, Claude graph query, path, and explanation workflows, Claude Whisper media transcription (+27 more)

### Community 19 - "package.json"
Cohesion: 0.07
Nodes (26): react-dom, @types/react, @types/react-dom, typescript, vite, @vitejs/plugin-react, dependencies, react (+18 more)

### Community 20 - "CacheManager"
Cohesion: 0.12
Nodes (13): CacheManager, DataFrame, Exception, Path, Timestamp, Initialize the Cache Manager. Args: cache_dir: Directory to store cache files., Delete all files in cache., Generate file path for a ticker and interval. (+5 more)

### Community 22 - "RunDataset"
Cohesion: 0.16
Nodes (11): RuntimeError, DatasetCache, DatasetError, DatasetProvider, DataFrame, Path, RunDataset, Path (+3 more)

### Community 24 - "runs.py"
Cohesion: 0.14
Nodes (18): _charts_dir(), get_chart(), list_charts(), get, Path, get, run_files(), run_metadata() (+10 more)

### Community 25 - "BaseStrategy"
Cohesion: 0.09
Nodes (12): BaseStrategy, Any, DataFrame, Series, Abstract base class for all screening strategies. Changes: - Added get_name()…, Check if the given dataframe matches the strategy logic. Args: df: DataFrame…, Vectorized signal series. Override in strategies for performance., Vectorized signal scoring. Override in strategies for ranking. (+4 more)

### Community 26 - "DataFrame"
Cohesion: 0.17
Nodes (14): _bar_minutes(), BollingerSqueeze, Nr4Nr7, Nr4Nr7Daily, Nr4Nr7Intraday, Any, DataFrame, Series (+6 more)

### Community 27 - "compilerOptions"
Cohesion: 0.12
Nodes (16): compilerOptions, allowImportingTsExtensions, isolatedModules, jsx, lib, module, moduleResolution, noEmit (+8 more)

### Community 28 - "DataFrame"
Cohesion: 0.28
Nodes (5): _find_column(), DataFrame, DatetimeIndex, Series, StaticFileDataProvider

### Community 29 - "BullishEngulfing"
Cohesion: 0.22
Nodes (8): BullishEngulfing, Any, DataFrame, Series, Price action and pattern-based screening strategies., Bullish Engulfing Pattern: 1. Previous candle Red 2. Current candle Green 3.…, Unusual Volume Activity: Volume > 300% of 20-day Average AND Price Up., VolumeSpike

### Community 30 - "DummyTrendStrategy"
Cohesion: 0.24
Nodes (7): DummyTrendStrategy, _FakeDataProvider, DataFrame, Series, Simple always-on long strategy used to validate the vectorized backtest loop., test_vectorized_backtest_runs_on_prefetched_data(), _trend_df()

### Community 31 - "update_ibkr_cache.py"
Cohesion: 0.26
Nodes (11): Namespace, _load_tickers(), main(), _merge_and_save(), _parse_args(), DataFrame, Path, Timedelta (+3 more)

### Community 32 - "DataProvider"
Cohesion: 0.22
Nodes (4): DataProvider, Timedelta, Timestamp, Fetch historical data for a list of tickers with smart caching. Args: tickers:…

### Community 33 - "strategy_backtester/charts.py"
Cohesion: 0.14
Nodes (32): _align_timestamp(), _annotate_point(), build_charts_manifest(), _calc_rsi(), _calc_vwap(), _finalize_axes(), generate_position_charts(), generate_trade_charts() (+24 more)

### Community 34 - "EventCalendar"
Cohesion: 0.29
Nodes (4): CalendarEvent, EventCalendar, Timestamp, Manual event calendar loader for strategy skip days.

### Community 35 - "compilerOptions"
Cohesion: 0.25
Nodes (7): compilerOptions, allowSyntheticDefaultImports, composite, module, moduleResolution, skipLibCheck, include

### Community 52 - "metrics/overview.py"
Cohesion: 0.49
Nodes (10): build_overview_payload(), compute_book_kpis(), _compute_drawdown(), compute_drawdown_series(), compute_equity_series(), compute_monthly_returns(), compute_portfolio_kpis(), DataFrame (+2 more)

### Community 55 - "build_data_provider"
Cohesion: 0.23
Nodes (7): BaseDataProvider, build_data_provider(), _expand_static_files(), _interval_to_pandas_freq(), ABC, Core DataProvider for Stock Screener. Handles batch fetching of OHLCV data…, _resolve_path()

## Knowledge Gaps
- **90 isolated node(s):** `ApiSettings`, `name`, `private`, `version`, `type` (+85 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 278 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **9 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `BaseStrategy` connect `BaseStrategy` to `strategy_backtester/charts.py`, `Any`, `.get_name`, `test_orb_and_structure_characterization.py`, `ablation.py`, `DataFrame`, `add_common_indicators`, `test_backtest_engine_characterization.py`, `DataFrame`, `DataFrame`, `DataFrame`, `.get_take_profit_level_pcts`, `strategy.py`, `.get_description`, `DataFrame`, `BullishEngulfing`, `DummyTrendStrategy`?**
  _High betweenness centrality (0.332) - this node is a cross-community bridge._
- **Why does `BacktestEngine` connect `BacktestEngine` to `DataProvider`, `strategy_backtester/charts.py`, `EventCalendar`, `.build`, `walk_forward.py`, `ablation.py`, `add_common_indicators`, `test_backtest_engine_characterization.py`, `build_data_provider`, `DummyTrendStrategy`?**
  _High betweenness centrality (0.111) - this node is a cross-community bridge._
- **Why does `build_strategy_instances()` connect `ablation.py` to `add_common_indicators`, `BaseStrategy`?**
  _High betweenness centrality (0.067) - this node is a cross-community bridge._
- **Are the 16 inferred relationships involving `BacktestEngine` (e.g. with `BaseDataProvider` and `DataProvider`) actually correct?**
  _`BacktestEngine` has 16 INFERRED edges - model-reasoned connections that need verification._
- **Are the 10 inferred relationships involving `BaseStrategy` (e.g. with `StrategyLoader` and `_atr_15m_metrics()`) actually correct?**
  _`BaseStrategy` has 10 INFERRED edges - model-reasoned connections that need verification._
- **What connects `ApiSettings`, `name`, `private` to the rest of the system?**
  _90 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `BacktestEngine` be split into smaller, more focused modules?**
  _Cohesion score 0.06268030422239707 - nodes in this community are weakly interconnected._