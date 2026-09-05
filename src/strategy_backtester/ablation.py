import argparse
import copy
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import pandas as pd
import yaml
from tabulate import tabulate

from strategy_backtester.core.engine import BacktestEngine
from stock_screener.core.data import build_data_provider
from strategy_backtester.core.reporting import (
    build_book_daily_attribution,
    build_book_metrics,
    build_exit_reason_attribution,
    build_positions_df,
    build_rejections_df,
    build_strategy_metrics_by_book,
)
from strategy_backtester.config import (
    build_strategy_instances,
    filter_engine_kwargs,
    load_backtest_config,
    resolve_path,
    resolve_tickers,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _strategy_name(entry: Dict[str, Any]) -> str:
    return entry.get("class") or entry.get("name") or ""


def _filter_strategies(config: Dict[str, Any], keep: List[str] = None, drop: List[str] = None) -> None:
    strategies = config.get("strategies", [])
    filtered = []
    for entry in strategies:
        name = _strategy_name(entry)
        if keep is not None and name not in keep:
            continue
        if drop is not None and name in drop:
            continue
        filtered.append(entry)
    config["strategies"] = filtered


def _parse_strategy_targets(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        if value.strip().lower() in {"*", "all"}:
            return ["*"]
        return [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


def _apply_param_override(config: Dict[str, Any], spec: Dict[str, Any], value: Any) -> None:
    scope = str(spec.get("scope", "")).lower()
    key = spec.get("key")
    if not key:
        return
    if scope == "engine":
        config.setdefault("backtest", {}).setdefault("engine", {})[key] = value
        return
    if scope == "backtest":
        config.setdefault("backtest", {})[key] = value
        return
    if scope == "strategy":
        targets = _parse_strategy_targets(
            spec.get("strategy") or spec.get("strategies") or spec.get("match")
        )
        for entry in config.get("strategies", []):
            name = _strategy_name(entry)
            if targets and "*" not in targets and name not in targets:
                continue
            entry.setdefault("args", {})[key] = value


def _param_label(spec: Dict[str, Any]) -> str:
    label = spec.get("label")
    if label:
        return str(label)
    scope = str(spec.get("scope", "param")).lower()
    key = spec.get("key", "value")
    if scope == "strategy":
        target = spec.get("strategy") or spec.get("strategies") or spec.get("match") or "*"
        if isinstance(target, list):
            target = "-".join(target)
        return f"{target}.{key}"
    return f"{scope}.{key}"


def _load_base_config(path: str) -> Dict[str, Any]:
    config = load_backtest_config(path)
    if "backtest" not in config:
        raise ValueError("Base config missing 'backtest' section.")
    if "strategies" not in config:
        raise ValueError("Base config missing 'strategies' section.")
    return config


def _run_backtest(config: Dict[str, Any], base_path: str) -> Tuple[BacktestEngine, pd.DataFrame, pd.DataFrame, Dict[str, Any], pd.DataFrame, pd.DataFrame]:
    backtest_cfg = config.get("backtest", {})
    start_date = backtest_cfg.get("start_date") or backtest_cfg.get("start")
    end_date = backtest_cfg.get("end_date") or backtest_cfg.get("end")
    interval = backtest_cfg.get("interval", "1d")
    if not start_date:
        raise ValueError("Backtest config missing start_date.")
    tickers = resolve_tickers(base_path, backtest_cfg)
    if not tickers:
        raise ValueError("No tickers found for backtest.")

    strategies_cfg = config.get("strategies", [])
    strategies = build_strategy_instances(strategies_cfg)
    if not strategies:
        raise ValueError("No strategies enabled for backtest.")

    engine_kwargs = filter_engine_kwargs(backtest_cfg.get("engine", {}), BacktestEngine)
    data_provider = build_data_provider(
        backtest_cfg.get("data_source"),
        base_path,
        tickers=tickers,
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
    engine.run()
    equity, trades, summary, by_strategy, by_ticker = engine.get_report()
    return engine, equity, trades, summary, by_strategy, by_ticker


def _write_run_outputs(
    output_dir: str,
    run_id: str,
    config: Dict[str, Any],
    summary: Dict[str, Any],
    by_strategy: pd.DataFrame,
    by_ticker: pd.DataFrame,
    trades: pd.DataFrame,
    save_trades: bool,
    extra_outputs: Dict[str, pd.DataFrame] = None,
):
    run_dir = os.path.join(output_dir, "runs", run_id)
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "config.yaml"), "w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, sort_keys=False)
    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, default=str)

    if not by_strategy.empty:
        by_strategy.to_csv(os.path.join(run_dir, "by_strategy.csv"), index=False)
    if not by_ticker.empty:
        by_ticker.to_csv(os.path.join(run_dir, "by_ticker.csv"), index=False)
    if save_trades and not trades.empty:
        trades_out = trades.copy()
        if "entry_metrics" in trades_out.columns:
            trades_out["entry_metrics"] = trades_out["entry_metrics"].apply(
                lambda v: json.dumps(v, default=str) if isinstance(v, dict) else v
            )
        trades_out.to_csv(os.path.join(run_dir, "trades.csv"), index=False)

    if extra_outputs:
        for filename, df in extra_outputs.items():
            if df is None or df.empty:
                continue
            df.to_csv(os.path.join(run_dir, filename), index=False)


def _summary_row(run_id: str, mode: str, meta: Dict[str, Any], summary: Dict[str, Any]) -> Dict[str, Any]:
    row = {
        "run_id": run_id,
        "mode": mode,
        "total_return_pct": summary.get("total_return_pct", 0.0),
        "annualized_return_pct": summary.get("annualized_return_pct", 0.0),
        "max_drawdown_pct": summary.get("max_drawdown_pct", 0.0),
        "time_to_recovery_days": summary.get("time_to_recovery_days"),
        "time_to_recovery_open": summary.get("time_to_recovery_open", False),
        "sharpe_ratio": summary.get("sharpe_ratio", 0.0),
        "sortino_ratio": summary.get("sortino_ratio", 0.0),
        "calmar_ratio": summary.get("calmar_ratio", 0.0),
        "win_rate_pct": summary.get("win_rate_pct", 0.0),
        "expectancy": summary.get("expectancy", 0.0),
        "payoff_ratio": summary.get("payoff_ratio", 0.0),
        "avg_trade_return_pct": summary.get("avg_trade_return_pct", 0.0),
        "avg_pnl": summary.get("avg_pnl", 0.0),
        "total_trades": summary.get("total_trades", 0),
        "turnover_pct": summary.get("turnover_pct", 0.0),
        "time_in_market_pct": summary.get("time_in_market_pct", 0.0),
    }
    row.update({k: _meta_value(v) for k, v in meta.items()})
    return row


def _meta_value(value: Any) -> Any:
    if isinstance(value, (list, tuple, set, dict)):
        try:
            return json.dumps(value, default=str)
        except Exception:
            return str(value)
    return value


def main():
    parser = argparse.ArgumentParser(description="Backtest ablation runner")
    parser.add_argument("--config", type=str, required=True, help="Path to ablation config YAML")
    args = parser.parse_args()

    ablation_cfg = load_backtest_config(args.config)
    settings = ablation_cfg.get("ablation", {})
    base_config_path = settings.get("base_config")
    if not base_config_path:
        raise ValueError("Ablation config missing ablation.base_config.")
    base_path = resolve_path(args.config, base_config_path)
    base_config = _load_base_config(base_path)

    output_dir = settings.get("output_dir") or "../../../results/ablations"
    output_dir = resolve_path(args.config, output_dir)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_dir = os.path.join(output_dir, timestamp)
    os.makedirs(output_dir, exist_ok=True)

    include_baseline = bool(settings.get("include_baseline", True))
    save_trades = bool(settings.get("save_trades", False))
    print_strategy_tables = bool(settings.get("print_strategy_tables", False))

    modes = settings.get("modes") or []
    modes = [str(m).lower() for m in modes]
    if not modes:
        modes = ["loo", "aoi", "group", "time_split", "param_grid"]

    strategies_cfg = base_config.get("strategies", [])
    strategy_names = [_strategy_name(s) for s in strategies_cfg if _strategy_name(s)]

    run_specs: List[Dict[str, Any]] = []

    if include_baseline:
        run_specs.append(
            {
                "run_id": "baseline",
                "mode": "baseline",
                "config": copy.deepcopy(base_config),
                "meta": {},
            }
        )

    if "loo" in modes:
        for name in strategy_names:
            config = copy.deepcopy(base_config)
            _filter_strategies(config, drop=[name])
            run_specs.append(
                {
                    "run_id": f"loo_no_{name}",
                    "mode": "loo",
                    "config": config,
                    "meta": {"dropped_strategy": name},
                }
            )

    if "aoi" in modes:
        for name in strategy_names:
            config = copy.deepcopy(base_config)
            _filter_strategies(config, keep=[name])
            run_specs.append(
                {
                    "run_id": f"aoi_only_{name}",
                    "mode": "aoi",
                    "config": config,
                    "meta": {"only_strategy": name},
                }
            )

    if "group" in modes:
        groups = settings.get("groups", {})
        for group_name, members in groups.items():
            if not members:
                continue
            config = copy.deepcopy(base_config)
            _filter_strategies(config, drop=[str(m) for m in members])
            run_specs.append(
                {
                    "run_id": f"group_drop_{group_name}",
                    "mode": "group",
                    "config": config,
                    "meta": {"dropped_group": group_name},
                }
            )

    if "time_split" in modes:
        time_splits = settings.get("time_splits", [])
        for split in time_splits:
            start = split.get("start")
            end = split.get("end")
            if not start or not end:
                continue
            config = copy.deepcopy(base_config)
            config.setdefault("backtest", {})["start_date"] = start
            config.setdefault("backtest", {})["end_date"] = end
            run_specs.append(
                {
                    "run_id": f"time_{start}_to_{end}",
                    "mode": "time_split",
                    "config": config,
                    "meta": {"start_date": start, "end_date": end},
                }
            )

    if "param_set" in modes or "param_sets" in modes:
        param_sets = settings.get("param_sets", [])
        for idx, entry in enumerate(param_sets, start=1):
            if not isinstance(entry, dict):
                continue
            params = entry.get("params", [])
            if not params:
                continue
            config = copy.deepcopy(base_config)
            meta = {}
            for spec in params:
                if not isinstance(spec, dict):
                    continue
                value = spec.get("value")
                _apply_param_override(config, spec, value)
                meta[_param_label(spec)] = value
            name = entry.get("name") or f"set_{idx:02d}"
            name_slug = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in str(name))
            run_specs.append(
                {
                    "run_id": f"param_set_{idx:02d}_{name_slug}",
                    "mode": "param_set",
                    "config": config,
                    "meta": meta,
                }
            )

    if "param_grid" in modes or "grid" in modes:
        param_specs = settings.get("param_grid", [])
        param_specs = [s for s in param_specs if isinstance(s, dict)]
        if param_specs:
            labels = [_param_label(s) for s in param_specs]
            value_lists = [s.get("values", []) for s in param_specs]
            combos = list(__import__("itertools").product(*value_lists))
            for idx, combo in enumerate(combos, start=1):
                config = copy.deepcopy(base_config)
                meta = {}
                for spec, label, value in zip(param_specs, labels, combo):
                    _apply_param_override(config, spec, value)
                    meta[label] = value
                run_specs.append(
                    {
                        "run_id": f"grid_{idx:04d}",
                        "mode": "param_grid",
                        "config": config,
                        "meta": meta,
                    }
                )

    summary_rows = []
    by_strategy_rows = []
    by_ticker_rows = []

    for spec in run_specs:
        run_id = spec["run_id"]
        mode = spec["mode"]
        config = spec["config"]
        meta = spec.get("meta", {})
        logger.info("Running ablation %s (%s)...", run_id, mode)
        engine, equity, trades, summary, by_strategy, by_ticker = _run_backtest(config, base_path)
        book_daily_df = pd.DataFrame(engine.book_daily_records)
        book_attribution_df = build_book_daily_attribution(book_daily_df)
        rejections_df = build_rejections_df(engine.rejection_stats)
        state_changes_df = pd.DataFrame(engine.book_state_changes)
        positions_df = build_positions_df(trades)
        book_metrics_df = build_book_metrics(book_daily_df, trades)
        strategy_book_df = build_strategy_metrics_by_book(trades)
        exit_reason_df = build_exit_reason_attribution(trades)

        _write_run_outputs(
            output_dir,
            run_id,
            config,
            summary,
            by_strategy,
            by_ticker,
            trades,
            save_trades,
            extra_outputs={
                "equity_books_daily.csv": book_daily_df,
                "book_daily_attribution.csv": book_attribution_df,
                "allocator_rejections_daily.csv": rejections_df,
                "allocator_state_changes.csv": state_changes_df,
                "positions.csv": positions_df,
                "book_metrics.csv": book_metrics_df,
                "strategy_metrics_by_book.csv": strategy_book_df,
                "exit_reason_attribution.csv": exit_reason_df,
            },
        )

        summary_rows.append(_summary_row(run_id, mode, meta, summary))

        if not by_strategy.empty:
            by_strategy_run = by_strategy.copy()
            by_strategy_run.insert(0, "run_id", run_id)
            by_strategy_run.insert(1, "mode", mode)
            for key, value in meta.items():
                by_strategy_run[key] = _meta_value(value)
            by_strategy_rows.append(by_strategy_run)

        if not by_ticker.empty:
            by_ticker_run = by_ticker.copy()
            by_ticker_run.insert(0, "run_id", run_id)
            by_ticker_run.insert(1, "mode", mode)
            for key, value in meta.items():
                by_ticker_run[key] = _meta_value(value)
            by_ticker_rows.append(by_ticker_run)

    summary_df = pd.DataFrame(summary_rows)
    summary_path = os.path.join(output_dir, "summary.csv")
    summary_df.to_csv(summary_path, index=False)

    if by_strategy_rows:
        by_strategy_all = pd.concat(by_strategy_rows, ignore_index=True)
        by_strategy_all.to_csv(os.path.join(output_dir, "by_strategy_all.csv"), index=False)
    else:
        by_strategy_all = pd.DataFrame()

    if by_ticker_rows:
        by_ticker_all = pd.concat(by_ticker_rows, ignore_index=True)
        by_ticker_all.to_csv(os.path.join(output_dir, "by_ticker_all.csv"), index=False)

    print("\n--- Ablation Summary ---")
    if not summary_df.empty:
        display_cols = [
            "run_id",
            "mode",
            "total_return_pct",
            "annualized_return_pct",
            "max_drawdown_pct",
            "sharpe_ratio",
            "win_rate_pct",
            "total_trades",
        ]
        display_cols = [c for c in display_cols if c in summary_df.columns]
        print(tabulate(summary_df[display_cols], headers="keys", tablefmt="grid", showindex=False))

    if print_strategy_tables and not by_strategy_all.empty:
        print("\n--- Strategy Performance (All Runs) ---")
        show_cols = ["run_id", "mode", "strategy", "trades", "win_rate_pct", "total_pnl", "profit_factor"]
        show_cols = [c for c in show_cols if c in by_strategy_all.columns]
        print(tabulate(by_strategy_all[show_cols], headers="keys", tablefmt="grid", showindex=False))

    print(f"\nSaved ablation outputs to: {output_dir}")


if __name__ == "__main__":
    main()
