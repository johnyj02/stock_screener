# Grid Search Guidelines

This file lists parameters that should not be tuned by default, plus parameters
that are currently fixed (single value) in the ORB grid search config. Use this
as a checklist before creating or updating any grid-search config.

Do Not Tune (default)
- Backtest window and universe
  - backtest.start_date
  - backtest.end_date
  - backtest.interval
  - backtest.tickers
  - backtest.tickers_file
- Data and execution plumbing
  - engine.use_vectorized
  - engine.force_update_cache
  - engine.position_qty_epsilon
  - engine.equity_qty_rounding
  - engine.min_equity_shares
- Market context / dependencies
  - engine.regime_symbol
  - engine.hedge_symbol
  - strategies[].args.market_symbols
  - strategies[].args.session_timezone
  - strategies[].args.index_timezone
  - strategies[].args.skip_events_file
- Debug and logging flags
  - strategies[].args.debug_orb
  - strategies[].args.debug_allocation
- Charting output
  - charts.*
- Search plumbing (not strategy behavior)
  - trial_name
  - base_config
  - metrics_registry
  - search.*
  - output.*
- Trading costs (keep fixed unless explicitly stress-testing)
  - engine.commission_pct
  - engine.commission_per_trade
  - engine.slippage_bps
- others
  - Orb15BreakoutLong.tp_management.commission_aware
  - Orb15BreakoutShort.tp_management.commission_aware
  - engine.trade_size
  - engine.max_new_positions_per_day
  - engine.cooldown_days
  - engine.liquidity_lookback
  - engine.min_avg_dollar_vol
  - engine.min_trade_notional
  - engine.min_equity_shares
  - engine.position_qty_epsilon
  - engine.skip_trade_if_notional_lt_min
  - engine.equity_qty_rounding
  - engine.futures_margin_pct

Regime tuning
These are the only regime parameters worth tuning by default. Keep the ranges small.
- engine.regime.params.risk_on_threshold
- engine.regime.params.hysteresis_on
- engine.regime.params.hysteresis_off
- engine.regime.params.hedge_max_pct
- engine.regime.params.risk_mult_buckets
- engine.regime.params.apply_exposure_caps

Regime tuning (do not tune)
- engine.regime.symbols.market
- engine.regime.symbols.vol
- engine.regime.symbols.credit
- engine.regime.symbols.rates
- engine.regime.params.intraday_mode
- engine.regime.missing_data_policy.policy
- engine.regime.missing_data_policy.max_bad_days_pct
- engine.regime.missing_data_policy.min_components
- engine.regime.missing_data_policy.warn_limit_per_symbol
