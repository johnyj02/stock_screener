import argparse
import json
import logging
import os
from datetime import datetime, timezone
from typing import List

import pandas as pd
import yaml
from tabulate import tabulate
from strategy_backtester.core.engine import BacktestEngine
from strategy_backtester.core.reporting import (
    build_book_daily_attribution,
    build_equity_books_daily,
    build_book_metrics,
    build_exit_reason_attribution,
    build_positions_df,
    build_rejections_df,
    build_regime_attribution,
    build_strategy_metrics_by_book,
)
from strategy_backtester.charts import build_charts_manifest, generate_position_charts, generate_trade_charts
from strategy_backtester.config import (
    build_strategy_instances,
    filter_engine_kwargs,
    load_tickers,
    load_backtest_config,
    parse_ticker_list,
    resolve_tickers,
    unique_tickers,
)
from stock_screener.core.data import build_data_provider

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def _format_summary_lines(summary: dict, total_trades: int) -> List[str]:
    lines = ["--- Backtest Report ---"]
    if not summary:
        lines.append("No summary available.")
        lines.append(f"Total Trades: {total_trades}")
        return lines
    def sval(key: str, default: float = 0.0) -> float:
        try:
            return float(summary.get(key, default))
        except (TypeError, ValueError):
            return default
    def ival(key: str, default: int = 0) -> int:
        try:
            return int(summary.get(key, default))
        except (TypeError, ValueError):
            return default
    def recovery_line() -> str:
        ttr_value = summary.get("time_to_recovery_days")
        ttr_open = bool(summary.get("time_to_recovery_open", False))
        if ttr_value is None:
            return "Time to Recovery: N/A"
        try:
            ttr_days = int(round(float(ttr_value)))
        except (TypeError, ValueError):
            return "Time to Recovery: N/A"
        suffix = " (open)" if ttr_open else ""
        return f"Time to Recovery: {ttr_days} days{suffix}"

    lines.extend(
        [
            f"Initial Equity: ${sval('initial_equity'):,.2f}",
            f"Final Equity:   ${sval('final_equity'):,.2f}",
            f"Total Return:   {sval('total_return_pct'):.2f}%",
            f"Annualized:     {sval('annualized_return_pct'):.2f}%",
            f"Max Drawdown:   {sval('max_drawdown_pct'):.2f}%",
            recovery_line(),
            f"Sharpe Ratio:   {sval('sharpe_ratio'):.2f}",
            f"Sortino Ratio:  {sval('sortino_ratio'):.2f}",
            f"Calmar Ratio:   {sval('calmar_ratio'):.2f}",
            f"Ulcer Index:    {sval('ulcer_index'):.4f}",
            f"Win Rate:       {sval('win_rate_pct'):.1f}%",
            f"Avg Trade Ret:  {sval('avg_trade_return_pct'):.2f}%",
            f"Payoff Ratio:   {sval('payoff_ratio'):.2f}",
            f"Expectancy:     {sval('expectancy'):.2f}",
            f"Avg PnL/Trade:  {sval('avg_pnl'):.2f}",
            f"Turnover:       {sval('turnover_pct'):.2f}%",
            f"Turnover (M):   {sval('turnover_margin_pct'):.2f}%",
            f"Turnover Ann.:  {sval('turnover_annualized_pct'):.2f}%",
            f"Turnover M Ann:{sval('turnover_margin_annualized_pct'):.2f}%",
        ]
    )
    if "max_invested" in summary:
        lines.append(f"Max Invested:   ${sval('max_invested'):,.2f}")
        lines.append(f"Max Invested%:  {sval('max_invested_pct'):.1f}%")
        lines.append(f"Invested P95%:  {sval('invested_pct_p95'):.1f}%")
    lines.extend(
        [
            f"Vol Scale min:  {sval('vol_scale_min'):.2f}",
            f"Vol Scale med:  {sval('vol_scale_median'):.2f}",
            f"Vol Scale max:  {sval('vol_scale_max'):.2f}",
            f"Total Comm:     {sval('total_commission'):.2f}",
            f"Total Slippage: {sval('total_slippage_cost'):.2f}",
            f"Max Consec L:   {ival('max_consecutive_losses')}",
            f"Time In Market: {sval('time_in_market_pct'):.1f}%",
            f"Worst Month:    {sval('worst_month_pct'):.2f}%",
            f"Worst Quarter:  {sval('worst_quarter_pct'):.2f}%",
            f"Roll Sharpe µ:  {sval('rolling_sharpe_mean'):.2f}",
            f"Roll Sharpe min:{sval('rolling_sharpe_min'):.2f}",
            f"Roll 6m Ret µ:  {sval('rolling_return_6m_mean_pct'):.2f}%",
            f"Roll 6m Ret min:{sval('rolling_return_6m_min_pct'):.2f}%",
            f"Skewness:       {sval('skewness'):.2f}",
            f"Kurtosis:       {sval('kurtosis'):.2f}",
            f"Total Trades: {total_trades}",
        ]
    )
    return lines


def main():
    parser = argparse.ArgumentParser(description="Stock Strategy Backtester")
    parser.add_argument("--config", type=str, help="Path to backtest config YAML")
    parser.add_argument("--tickers-file", type=str, default="data/sp500.csv", help="CSV file with tickers")
    parser.add_argument("--tickers", type=str, help="Comma-separated tickers to include")
    parser.add_argument("--start", type=str, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, help="End date YYYY-MM-DD")
    parser.add_argument("--interval", type=str, default="1d", help="Data interval")
    
    args = parser.parse_args()
    config_payload = None

    if args.config:
        try:
            config = load_backtest_config(args.config)
        except Exception as exc:
            logger.error("Failed to load config %s: %s", args.config, exc)
            return

        backtest_cfg = config.get("backtest", {})
        start_date = backtest_cfg.get("start_date") or backtest_cfg.get("start")
        end_date = backtest_cfg.get("end_date") or backtest_cfg.get("end")
        interval = backtest_cfg.get("interval", "1d")
        if not start_date:
            logger.error("Config missing backtest.start_date")
            return

        tickers = resolve_tickers(args.config, backtest_cfg)
        if not tickers:
            logger.error("Config did not provide any tickers.")
            return

        strategies_cfg = config.get("strategies", [])
        strategies = build_strategy_instances(strategies_cfg)
        if not strategies:
            logger.error("No strategies configured or found in config.")
            return
        config_payload = config
        try:
            data_provider = build_data_provider(
                backtest_cfg.get("data_source"),
                config_path=args.config,
                tickers=tickers,
            )
        except Exception as exc:
            logger.error("Failed to initialize data_source: %s", exc)
            return
        engine_kwargs = filter_engine_kwargs(backtest_cfg.get("engine", {}), BacktestEngine)
        logger.info(
            "Initialized Backtester from config with %s tickers, %s strategies.",
            len(tickers),
            len(strategies),
        )
        engine = BacktestEngine(
            start_date=start_date,
            end_date=end_date,
            interval=interval,
            universe=tickers,
            strategies=strategies,
            data_provider=data_provider,
            **engine_kwargs,
        )
    else:
        if not args.start:
            parser.error("--start is required when --config is not provided.")
    
        tickers = load_tickers(args.tickers_file)
        tickers.extend(parse_ticker_list(args.tickers))
        tickers = unique_tickers(tickers)
        if not tickers:
            tickers = ['SPY', 'QQQ', 'AAPL', 'MSFT', 'NVDA']
            logger.warning("Using default ticker list: %s", tickers)

        logger.info("Initialized Backtester with %s tickers. Start: %s", len(tickers), args.start)
        engine = BacktestEngine(
            start_date=args.start,
            end_date=args.end,
            interval=args.interval,
            universe=tickers,
        )
        config_payload = {
            "backtest": {
                "start_date": args.start,
                "end_date": args.end,
                "interval": args.interval,
                "tickers_file": args.tickers_file,
                "tickers": parse_ticker_list(args.tickers),
            },
            "engine": {},
        }
    engine.run()
    
    # Report
    equity, trades, summary, by_strategy, by_ticker = engine.get_report()
    
    summary_lines = _format_summary_lines(summary, len(trades))
    print()
    for line in summary_lines:
        print(line)
    if not trades.empty:
        print(trades.tail(10)) # Show last 10 trades

    if not by_strategy.empty:
        print("\n--- Strategy Performance ---")
        print(tabulate(by_strategy, headers="keys", tablefmt="grid", showindex=False))

    if not by_ticker.empty:
        print("\n--- Ticker Performance ---")
        print(tabulate(by_ticker, headers="keys", tablefmt="grid", showindex=False))

    results_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "results"))
    os.makedirs(results_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(results_dir, f"backtest_{timestamp}")
    os.makedirs(run_dir, exist_ok=True)
    config_path = os.path.join(run_dir, "config.yaml")
    if args.config:
        try:
            with open(os.path.abspath(args.config), "r", encoding="utf-8") as handle:
                raw_config = handle.read()
            with open(config_path, "w", encoding="utf-8") as handle:
                handle.write(raw_config)
        except Exception as exc:
            logger.warning("Failed to copy config file: %s", exc)
    elif config_payload:
        with open(config_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(config_payload, handle, sort_keys=False)

    summary_txt_path = os.path.join(run_dir, "summary.txt")
    with open(summary_txt_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(summary_lines) + "\n")

    trades_path = os.path.join(run_dir, "trades.csv")
    trades_out = trades.copy()
    if "entry_metrics" in trades_out.columns:
        trades_out["entry_metrics"] = trades_out["entry_metrics"].apply(
            lambda v: json.dumps(v, default=str) if isinstance(v, dict) else v
        )
    trades_out.to_csv(trades_path, index=False)
    print(f"\nSaved trade log: {trades_path}")

    book_daily_df = build_equity_books_daily(engine.book_daily_records, equity)
    if not book_daily_df.empty:
        book_daily_path = os.path.join(run_dir, "equity_books_daily.csv")
        book_daily_df.to_csv(book_daily_path, index=False)
        print(f"Saved book daily equity: {book_daily_path}")
        book_attribution_df = build_book_daily_attribution(book_daily_df)
        if not book_attribution_df.empty:
            book_attribution_path = os.path.join(run_dir, "book_daily_attribution.csv")
            book_attribution_df.to_csv(book_attribution_path, index=False)
            print(f"Saved book daily attribution: {book_attribution_path}")

    rejections_df = build_rejections_df(engine.rejection_stats)
    if not rejections_df.empty:
        rejections_path = os.path.join(run_dir, "allocator_rejections_daily.csv")
        rejections_df.to_csv(rejections_path, index=False)
        print(f"Saved allocator rejections: {rejections_path}")

    state_changes_df = pd.DataFrame(engine.book_state_changes)
    if not state_changes_df.empty:
        state_changes_path = os.path.join(run_dir, "allocator_state_changes.csv")
        state_changes_df.to_csv(state_changes_path, index=False)
        print(f"Saved allocator state changes: {state_changes_path}")

    positions_df = build_positions_df(trades)
    if not positions_df.empty:
        positions_path = os.path.join(run_dir, "positions.csv")
        positions_df.to_csv(positions_path, index=False)
        print(f"Saved positions: {positions_path}")

    book_metrics_df = build_book_metrics(book_daily_df, trades)
    if not book_metrics_df.empty:
        book_metrics_path = os.path.join(run_dir, "book_metrics.csv")
        book_metrics_df.to_csv(book_metrics_path, index=False)
        print(f"Saved book metrics: {book_metrics_path}")

    strategy_book_df = build_strategy_metrics_by_book(trades)
    if not strategy_book_df.empty:
        strategy_book_path = os.path.join(run_dir, "strategy_metrics_by_book.csv")
        strategy_book_df.to_csv(strategy_book_path, index=False)
        print(f"Saved strategy metrics by book: {strategy_book_path}")

    exit_reason_df = build_exit_reason_attribution(trades)
    if not exit_reason_df.empty:
        exit_reason_path = os.path.join(run_dir, "exit_reason_attribution.csv")
        exit_reason_df.to_csv(exit_reason_path, index=False)
        print(f"Saved exit reason attribution: {exit_reason_path}")

    regime_attr_df = build_regime_attribution(trades, equity)
    if not regime_attr_df.empty:
        regime_attr_path = os.path.join(run_dir, "regime_attribution.csv")
        regime_attr_df.to_csv(regime_attr_path, index=False)
        print(f"Saved regime attribution: {regime_attr_path}")

    charts_cfg = {}
    if isinstance(config_payload, dict):
        charts_cfg = config_payload.get("charts", {}) or {}
    if charts_cfg.get("enabled", False) and charts_cfg.get("mode", "post") == "post":
        chart_output_dir = os.path.join(run_dir, charts_cfg.get("output_dir", "charts"))
        chart_tickers_mode = charts_cfg.get("tickers", "traded_only")
        chart_tickers = None
        if chart_tickers_mode == "traded_only":
            if "symbol" in trades.columns:
                chart_tickers = sorted(trades["symbol"].dropna().unique().tolist())
        elif chart_tickers_mode == "all":
            chart_tickers = tickers
        data_by_ticker = getattr(engine, "full_data", {}) or {}
        if not data_by_ticker:
            logger.warning("Chart generation skipped (missing price data).")
        else:
            figsize = charts_cfg.get("figsize") or [12, 6]
            try:
                figsize = (float(figsize[0]), float(figsize[1]))
            except Exception:
                figsize = (12.0, 6.0)
            ema_periods = charts_cfg.get("ema_periods") or [20, 50]
            sma_periods = charts_cfg.get("sma_periods") or [200]
            include_volume = bool(charts_cfg.get("include_volume", True))
            include_rsi = bool(charts_cfg.get("include_rsi", False))
            rsi_length = int(charts_cfg.get("rsi_length", 14) or 14)
            rsi_overbought = float(charts_cfg.get("rsi_overbought", 70.0) or 70.0)
            rsi_oversold = float(charts_cfg.get("rsi_oversold", 30.0) or 30.0)
            positions_df = build_positions_df(trades)
            if charts_cfg.get("per_position", False):
                generate_position_charts(
                    positions_df,
                    trades,
                    data_by_ticker,
                    chart_output_dir,
                    window_bars_before=int(charts_cfg.get("window_bars_before", 40) or 40),
                    window_bars_after=int(charts_cfg.get("window_bars_after", 40) or 40),
                    style=charts_cfg.get("style", "candles"),
                    file_format=charts_cfg.get("format", "png"),
                    dpi=int(charts_cfg.get("dpi", 200) or 200),
                    figsize=figsize,
                    include_adds=bool(charts_cfg.get("include_adds", True)),
                    include_partials=bool(charts_cfg.get("include_partials", True)),
                    ema_periods=ema_periods,
                    sma_periods=sma_periods,
                    include_volume=include_volume,
                    include_rsi=include_rsi,
                    rsi_length=rsi_length,
                    rsi_overbought=rsi_overbought,
                    rsi_oversold=rsi_oversold,
                    max_positions=charts_cfg.get("max_positions"),
                )
            if charts_cfg.get("include_full", False):
                generate_trade_charts(
                    trades,
                    data_by_ticker,
                    chart_output_dir,
                    tickers=chart_tickers,
                    start_date=engine.start_date,
                    end_date=engine.end_date,
                    style=charts_cfg.get("style", "candles"),
                    file_format=charts_cfg.get("format", "png"),
                    dpi=int(charts_cfg.get("dpi", 200) or 200),
                    figsize=figsize,
                    include_adds=bool(charts_cfg.get("include_adds", True)),
                    include_partials=bool(charts_cfg.get("include_partials", True)),
                    ema_periods=ema_periods,
                    sma_periods=sma_periods,
                    include_volume=include_volume,
                    include_rsi=include_rsi,
                    rsi_length=rsi_length,
                    rsi_overbought=rsi_overbought,
                    rsi_oversold=rsi_oversold,
                )
            if charts_cfg.get("per_position", False):
                manifest_path = os.path.join(run_dir, "charts_manifest.csv")
                build_charts_manifest(
                    positions_df,
                    chart_output_dir,
                    file_format=charts_cfg.get("format", "png"),
                    manifest_path=manifest_path,
                )
            print(f"Saved trade charts: {chart_output_dir}")

if __name__ == "__main__":
    main()
