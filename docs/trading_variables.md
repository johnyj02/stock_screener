# Trading Variables and Metrics

This doc summarizes the most important sizing, risk, and reporting variables in the
backtester, with definitions, formulas, and how traders use them. It reflects current
behavior in `strategy_backtester`.

Key terms
- Notional (gross exposure): `price * qty * multiplier`
- Per-contract risk: `abs(entry - stop) * multiplier`
- Futures margin used: `abs(notional) * futures_margin_pct`

Backtest window and data

backtest.start_date / backtest.end_date
- Definition: Start/end of the simulation window.
- Usage: Choose a representative regime; for intraday data, keep the window within provider limits.

backtest.interval
- Definition: Bar size (`1d`, `5m`, etc.).
- Usage: Must match the strategy's intended timeframe; intraday intervals have limited history.

backtest.tickers / backtest.tickers_file
- Definition: Explicit list of symbols or a CSV with `Symbol` column.
- Usage: Scope the universe; ensure consistent symbols across strategies.

engine.use_vectorized
- Definition: Use vectorized signal generation in the backtester.
- Usage: Leave on for speed; disable only for debugging.

engine.force_update_cache
- Definition: Force fresh data downloads (ignore cache).
- Usage: Turn on when you want the latest data or the cache is stale.

Sizing and allocation (config)

engine.risk_per_trade_pct
- Definition: Percent of current equity risked per trade.
- Formula:
  - `risk_budget = current_equity * risk_per_trade_pct`
  - `qty = risk_budget / per_contract_risk` (futures qty is floored to int)
  - `notional = qty * price * multiplier`
- Importance: Primary position-sizing lever; it controls loss size at stop.
- Usage: Traders pick a small fixed % (e.g., 0.25% to 1%) to limit drawdowns.

engine.trade_size
- Definition: Hard cap on per-trade notional.
- Formula:
  - Equities: `trade_size_cap = trade_size`
  - Futures: `trade_size_cap = trade_size / futures_margin_pct`
- Importance: Prevents a single trade from getting too large even if risk allows it.
- Usage: Use when you want an absolute ceiling regardless of stop distance.

engine.max_alloc_pct
- Definition: Portfolio-level allocation cap based on current equity.
- Formula:
  - `max_alloc = current_equity * max_alloc_pct`
  - Remaining capacity is reduced by: equity long notional + futures margin used.
  - For futures, remaining margin capacity is divided by `futures_margin_pct` before it caps gross order notional.
- Importance: Limits total exposure across all open positions.
- Usage: Set lower to reduce concentration and keep cash/margin reserves.

strategy_max_position_pct
- Definition: Per-position cap for a strategy (per symbol/strategy).
- Formula:
  - Equities: `max_notional = current_equity * strategy_max_position_pct`
  - Futures: `max_notional = (current_equity * strategy_max_position_pct) / futures_margin_pct`
- Importance: Prevents any single strategy from consuming too much allocation.
- Usage: Use smaller values when multiple strategies run concurrently.

futures_margin_pct
- Definition: Margin requirement as a percent of futures notional.
- Formula: `margin_used = abs(notional) * futures_margin_pct`
- Importance: Translates futures notional into capital usage for caps.
- Usage: Set to your broker's initial margin / notional ratio (e.g., 0.05 to 0.15).

engine.min_trade_notional
- Definition: Minimum allowed notional per trade.
- Formula: trade rejected if `notional < min_trade_notional`.
- Importance: Filters out tiny trades that are noise or cost-inefficient.
- Usage: Raise to reduce churn; set to 0 to disable.

engine.min_equity_shares
- Definition: Minimum share count for equity trades.
- Formula: trade rejected if `qty < min_equity_shares`.
- Usage: Avoids tiny equity positions when rounding.

engine.equity_qty_rounding
- Definition: Rounding mode for equity share qty (`floor_int`, `round_int`, `ceil_int`, `none`).
- Usage: Use `floor_int` to avoid over-allocating; `none` for fractional-share brokers.

engine.position_qty_epsilon
- Definition: Small threshold under which a position is treated as closed.
- Usage: Avoids tiny residual positions from float rounding.

engine.portfolio_vol_target / engine.portfolio_vol_lookback
- Definition: Volatility targeting on notional sizing.
- Formula:
  - `current_vol = std(daily_returns, lookback) * sqrt(252)`
  - If `current_vol > target`, scale notional by `target / current_vol` (floor at 0.1).
- Importance: Dampens sizing in high-vol regimes and reduces drawdowns.
- Usage: Enable when you want smoother equity curves across regimes.

engine.max_new_positions_per_day
- Definition: Cap on number of new positions per day.
- Usage: Limits turnover and operational load.

engine.cooldown_days / stop_cooldown_days / loss_cooldown_days
- Definition: Entry cooldowns after any exit, stop exit, or loss exit.
- Usage: Reduces churn and avoids re-entering during unstable conditions.

Regime and hedging controls

engine.regime
- Definition: Regime model configuration block (model, symbols, params, missing-data policy).
- Usage: Use `simple_sma` for legacy behavior or `multi_factor_v1` for multi-axis regimes.

engine.regime.model
- Definition: Active regime model name.
- Usage: `"simple_sma"` (legacy SMA200) or `"multi_factor_v1"` (trend/vol/credit/rates composite).

engine.regime.symbols.market / vol / credit / rates
- Definition: Context symbols used by the regime model.
- Usage: Defaults: market=SPY, vol=VIXY, credit=HYG, rates=TLT.

engine.regime.params.risk_on_threshold / hysteresis_on / hysteresis_off
- Definition: Thresholds used to convert `risk_on_prob` into a boolean `risk_on` with hysteresis.
- Hysteresis: Use a higher threshold to turn risk-on **on** and a lower threshold to turn it **off**, which reduces flip-flopping near the boundary.
- Usage: Prevents flip-flopping around the threshold.

risk_on_prob
- Definition: Probability-like regime score in [0, 1] derived from trend/vol/credit/rates signals.
- Usage: Higher values indicate stronger risk-on conditions; used for gating and risk scaling.

engine.regime.params.hedge_max_pct
- Definition: Maximum hedge notional as a percentage of long exposure.
- Formula: `hedge_pct = (1 - risk_on_prob) * hedge_max_pct`
- Usage: Scales hedge size smoothly in risk-off regimes.

engine.regime.params.intraday_mode
- Definition: How regimes are applied to intraday data.
- Usage: `daily_ffill_shift1` computes regimes on daily bars and shifts by one day to avoid leakage.

engine.regime.params.risk_mult_buckets
- Definition: Risk scaling buckets based on `risk_on_prob`.
- Usage: Example defaults: 0.8→1.0x, 0.6→0.7x, 0.4→0.4x, <0.4→0.0x.

engine.regime.params.apply_exposure_caps
- Definition: Optional exposure cap scaling by `risk_on_prob`.
- Formula: `max_alloc_pct *= (0.5 + 0.5 * risk_on_prob)`
- Usage: Keep off unless you want regime to clamp total exposure.

engine.regime.missing_data_policy
- Definition: Controls soft vs hard failure when regime inputs are missing.
- Usage: `policy` (soft|hard), `max_bad_days_pct`, `min_components`, `warn_limit_per_symbol`.

engine.regime_symbol
- Definition: Symbol used to compute regime state (risk-on/off).
- Usage: Usually a broad index (e.g., SPY).

engine.hedge_symbol
- Definition: Futures symbol used for hedging when regime is risk-off.
- Usage: Set to a liquid index future if hedging is desired.

engine.allow_risk_off_entries
- Definition: Allow new long entries when the regime is risk-off.
- Usage: Disable to avoid fighting the tape in weak regimes.

engine.risk_off_size_mult
- Definition: Position-size multiplier applied when entering during risk-off (if allowed).
- Formula: `notional *= risk_off_size_mult` for long entries in risk-off.
- Usage: Use < 1.0 to scale down risk during risk-off.

engine.exit_on_risk_off / strategy.use_regime_exit
- Definition: Exit positions when regime turns risk-off (engine flag + strategy flag).
- Usage: Enable for defensive behavior; disable to let trades run.

engine.allow_short_risk_on / engine.allow_short_equity
- Definition: Allow shorts when regime is risk-on; allow equity shorts at all.
- Usage: Enable only if your short models are robust in risk-on environments.

engine.regime_stability_days
- Definition: Required consecutive risk-on days before allowing trades (if enabled by strategy).
- Usage: Filters out whipsaws around regime flips.

engine.regime_flip_band_pct / engine.regime_flip_slope_pct
- Definition: Buffer used to avoid trading when regime is "on the fence."
- Usage: Higher values reduce trades around trend inflection points.

allowed_trend_states
- Definition: Strategy gate allowing only specific trend regimes (e.g., ["UP", "RANGE"]).
- Usage: Blocks entries when the trend state is outside the list.

max_vol_state
- Definition: Strategy gate that blocks entries if volatility exceeds a max state.
- Usage: Example: `HIGH` blocks `CRISIS` only; `NORMAL` blocks `HIGH` and `CRISIS`.

min_risk_on_prob
- Definition: Minimum `risk_on_prob` required for entries.
- Usage: Use when a strategy needs strong regime confirmation.

size_by_regime
- Definition: Whether to scale risk sizing by regime probability.
- Usage: If true, `risk_per_trade_pct` is multiplied by the regime risk bucket.

allow_entries_in_risk_off
- Definition: Strategy-level override to block entries in risk-off (even if engine allows).
- Usage: Set false for momentum/trend systems; true for defensive/mean-reversion systems.

Strategy-level exit and management flags (common)

use_stop_loss / use_fallback_stop
- Definition: Use strategy stop loss; fallback stop uses ATR when no stop is provided.
- Usage: Keep stop loss on to define risk; fallback stop keeps sizing valid.

use_take_profit / take_profit_multiplier / tp_r_multiple
- Definition: Enable take-profit and set target distance (ATR or R-multiple).
- Usage: Use smaller targets for mean reversion, larger for trend.

use_time_stop / time_stop_days
- Definition: Exit after a fixed number of bars/days.
- Usage: Prevents stale trades from tying up capital.

use_breakeven / breakeven_r / breakeven_trigger_r / breakeven_delay_bars
- Definition: Move stop toward breakeven after a profit threshold and delay.
- Usage: Protects capital when a trade moves in your favor.

trailing_enabled / trailing_start_r / trailing_* settings
- Definition: Trail stops after a minimum profit.
- Usage: Lets winners run while locking in gains.

partial_take_profit_enabled / partial_take_profit_r / partial_take_profit_pct
- Definition: Scale out at a profit threshold.
- Usage: Reduces variance and improves win rate at cost of upside.

giveback_enabled / giveback_trigger_r / giveback_floor_r
- Definition: Exit if a trade gives back a specified amount after a peak.
- Usage: Protects profits in volatile names.

decay_exit_enabled / decay_exit_days / decay_exit_mfe_r
- Definition: Exit if a trade stalls for too long after reaching MFE.
- Usage: Keeps capital rotating into fresher setups.

Execution costs and fills

commission_pct / commission_per_trade
- Formula: `commission = commission_per_trade + (notional * commission_pct)`
- Importance: Costs reduce net returns and can flip marginal trades negative.
- Usage: Set to realistic broker fees.

slippage_bps
- Definition: Slippage in basis points applied to execution price.
- Formula:
  - Long entry: `price * (1 + slip)`
  - Long exit: `price * (1 - slip)`
  - Short entry: `price * (1 - slip)`
  - Short exit: `price * (1 + slip)`
- Usage: Use realistic values to avoid overstating performance.

Liquidity filters

engine.liquidity_lookback
- Definition: Lookback window for liquidity checks.
- Usage: Increase for more stable liquidity estimates.

engine.min_avg_dollar_vol (equities)
- Formula: `mean(Close * Volume) over lookback >= min_avg_dollar_vol`
- Usage: Filter out illiquid stocks.

engine.min_avg_volume_futures (futures)
- Formula: `mean(Volume) over lookback >= min_avg_volume_futures`
- Usage: Filter out thin futures contracts.

Reporting metrics (summary)

initial_equity / final_equity
- Definition: Starting/ending equity in the backtest.
- Usage: Quick sanity check on total growth.

total_return_pct
- Formula: `(final_equity / initial_equity - 1) * 100`
- Usage: Absolute performance over the backtest window.

annualized_return_pct
- Formula: `(final_equity / initial_equity) ** (365.25 / days) - 1`
- Usage: Compare strategies over different time spans.

annualized_volatility_pct
- Formula: `std(daily_returns) * sqrt(252) * 100`
- Usage: Higher volatility implies larger swings in equity.

sharpe_ratio
- Formula: `(mean(daily_returns) * 252) / annualized_volatility`
- Meaning: Risk-adjusted return; higher means more return per unit of volatility.
- Usage: Traders compare strategies with similar timeframes; >1 is usually acceptable, >2 strong.

sortino_ratio
- Formula: `(mean(daily_returns) * 252) / downside_dev` (negative returns only)
- Meaning: Risk-adjusted return focused on downside.
- Usage: Prefer when drawdowns matter more than upside volatility.

max_drawdown_pct
- Formula: `min(equity / rolling_max - 1) * 100`
- Meaning: Worst peak-to-trough loss.
- Usage: Central risk metric; keep within tolerance.

ulcer_index
- Formula: `sqrt(mean(drawdown^2))`
- Meaning: Combines depth and duration of drawdowns.
- Usage: Lower is better for smooth equity curves.

calmar_ratio
- Formula: `annualized_return / abs(max_drawdown)`
- Meaning: Return per unit of drawdown.
- Usage: Good for comparing trend systems with different drawdown profiles.

turnover_pct
- Formula: `sum(abs(trade_notional)) / avg_equity * 100`
- Meaning: Gross notional turnover (futures uses full notional).
- Usage: High turnover increases costs and operational load.

turnover_margin_pct
- Formula: `sum(abs(trade_notional) * margin_pct) / avg_equity * 100`
- Meaning: Margin-adjusted turnover (futures uses margin_pct; equities use 1.0).
- Usage: Better capital-usage view for futures.

turnover_annualized_pct
- Formula: `turnover_pct * (252 / trading_days)`
- Meaning: Annualized gross turnover.
- Usage: Compare turnover across different backtest lengths.

turnover_margin_annualized_pct
- Formula: `turnover_margin_pct * (252 / trading_days)`
- Meaning: Annualized margin-adjusted turnover.
- Usage: Compare capital turnover across strategies and windows.

time_in_market_pct
- Definition: Percent of days with positions > 0.
- Usage: Lower values mean more idle time; useful for capital planning.

win_rate_pct
- Formula: `wins / total_sells * 100`
- Usage: Higher win rate can mean smoother equity but not necessarily higher return.

profit_factor
- Formula: `sum(win_pnl) / abs(sum(loss_pnl))`
- Usage: >1 indicates profitability; higher is better.

payoff_ratio
- Formula: `avg_win / abs(avg_loss)`
- Usage: Shows average winner size vs loser size.

expectancy
- Formula: `(win_rate * avg_win) + ((1 - win_rate) * avg_loss)`
- Usage: Expected PnL per trade; positive is required.

avg_trade_return_pct
- Definition: Average % return per completed trade.
- Usage: Useful for comparing strategies with different hold times.

avg_holding_days
- Definition: Average holding period of closed trades.
- Usage: Helps match strategy to account type and capital usage.

avg_win / avg_loss / avg_pnl / total_pnl
- Definition: Mean PnL of wins, losses, all trades, and total PnL.
- Usage: Diagnose whether edge is win rate or payoff driven.

max_consecutive_losses
- Definition: Worst streak of losing trades.
- Usage: Helps set risk limits and confidence thresholds.

avg_commission / total_commission / avg_slippage_cost / total_slippage_cost
- Definition: Average/total costs from commissions and slippage.
- Usage: Compare net vs gross performance and stress-test costs.

vol_scale_min / vol_scale_median / vol_scale_max
- Definition: Volatility scaling factors applied to notional sizing.
- Usage: Shows how often sizing was reduced due to high volatility.

worst_month_pct / worst_quarter_pct
- Definition: Worst month/quarter return.
- Usage: Measures tail risk over meaningful windows.

rolling_sharpe_min / rolling_sharpe_mean
- Definition: Rolling 6-month Sharpe stats.
- Usage: Stability check; large negative mins imply unstable performance.

rolling_return_6m_min_pct / rolling_return_6m_mean_pct
- Definition: Rolling 6-month return stats.
- Usage: Shows consistency over medium horizons.

skewness / kurtosis
- Definition: Distribution shape of daily returns (tail risk).
- Usage: High positive skew can be desirable; high kurtosis implies fat tails.

max_invested
- Definition: Max invested exposure (equity long notional + futures margin used).
- Usage: Indicates peak capital usage.

max_invested_pct / invested_pct_p95 / invested_pct_p99 / invested_pct_median
- Definition: Max and percentile invested exposure vs equity.
- Usage: Gauges typical vs peak capital utilization.

total_trades
- Definition: Count of completed trades (sells/cover).
- Usage: Ensures the sample size is meaningful.

Regime fields (trade/equity attribution)

entry_regime_state / exit_regime_state
- Definition: Regime label at entry/exit (risk_on, mild, confirmed, crisis, unknown).
- Usage: Attribute outcomes to regime context.

entry_risk_on_prob / exit_risk_on_prob
- Definition: Risk-on probability at entry/exit (0 to 1).
- Usage: Bucket results by regime strength.

entry_trend_state / exit_trend_state
- Definition: Trend regime at entry/exit (UP, DOWN, RANGE).
- Usage: Evaluate strategy performance by trend regime.

entry_vol_state / exit_vol_state
- Definition: Volatility regime at entry/exit (LOW, NORMAL, HIGH, CRISIS).
- Usage: Diagnose volatility sensitivity.

entry_stress / exit_stress
- Definition: Stress score (0 to 1) derived from volatility signals.
- Usage: Track behavior during high-stress periods.

regime_usable
- Definition: Whether regime inputs were sufficient on a given day.
- Usage: Flags days excluded from regime-based gating or sizing.
