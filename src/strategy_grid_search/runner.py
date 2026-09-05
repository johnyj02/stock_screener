import hashlib
import json
import os
import threading
from typing import Any, Dict, List, Tuple, Callable, Optional

import pandas as pd
import yaml

from strategy_backtester.core.engine import BacktestEngine
from stock_screener.core.data import build_data_provider
from stock_screener.core.indicators import add_market_context_columns
from stock_screener.core.regime_features import add_intraday_orb_day_type
from strategy_backtester.config import (
    build_strategy_instances,
    filter_engine_kwargs,
    resolve_tickers,
)

_PREFETCH_CACHE: Dict[str, Dict[str, pd.DataFrame]] = {}
_PREFETCH_LOCK = threading.Lock()


def _build_fetch_universe(engine: BacktestEngine, include_optional: bool = False) -> List[str]:
    regime_cfg = engine._resolve_regime_config()
    regime_symbols = engine._regime_context_symbols(regime_cfg)
    extra_symbols = [engine.regime_symbol]
    if engine._include_optional_symbol(engine.hedge_symbol):
        extra_symbols.append(engine.hedge_symbol)
    extra_symbols.extend(engine._benchmark_symbols())
    extra_symbols.extend(engine._market_symbols(include_optional=include_optional))
    extra_symbols.extend(regime_symbols)
    return list(dict.fromkeys(engine.universe + extra_symbols))


def _prefetch_key(engine: BacktestEngine, fetch_universe: List[str]) -> str:
    signature = None
    if hasattr(engine.data_provider, "prefetch_signature"):
        try:
            signature = engine.data_provider.prefetch_signature()
        except Exception:
            signature = None
    payload = {
        "interval": engine.interval,
        "end_date": engine.end_date.isoformat() if engine.end_date is not None else None,
        "force_update_cache": bool(engine.force_update_cache),
        "symbols": sorted(set(fetch_universe)),
        "data_source": signature,
    }
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()


def _prefetch_full_data(engine: BacktestEngine, fetch_universe: List[str]) -> Dict[str, pd.DataFrame]:
    full_data = engine.data_provider.fetch_batch_data(
        fetch_universe,
        period="max",
        interval=engine.interval,
        force_update=engine.force_update_cache,
        as_of_date=engine.end_date,
    )
    if not full_data:
        return {}
    engine._add_relative_strength_columns(full_data)
    add_market_context_columns(full_data)
    add_intraday_orb_day_type(full_data, engine.interval, config=engine.regime_config)
    return full_data


def _deepcopy_full_data(full_data: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    return {key: df.copy(deep=True) for key, df in full_data.items()}


def prefetch_cache(
    config: Dict[str, Any],
    config_path: str,
    prefetch_data: bool | None = None,
    include_optional: bool = False,
) -> bool:
    prefetch_enabled = True if prefetch_data is None else bool(prefetch_data)
    if not prefetch_enabled:
        return False

    backtest_cfg = config.get("backtest", {}) or {}
    start_date = backtest_cfg.get("start_date") or backtest_cfg.get("start")
    end_date = backtest_cfg.get("end_date") or backtest_cfg.get("end")
    interval = backtest_cfg.get("interval", "1d")
    if not start_date:
        raise ValueError("Config missing backtest.start_date")

    tickers = resolve_tickers(config_path, backtest_cfg)
    if not tickers:
        raise ValueError("Config did not provide any tickers.")

    strategies_cfg = config.get("strategies", [])
    strategies = build_strategy_instances(strategies_cfg)
    if not strategies:
        raise ValueError("No strategies configured or found in config.")

    engine_kwargs = filter_engine_kwargs(backtest_cfg.get("engine", {}), BacktestEngine)
    data_provider = build_data_provider(
        backtest_cfg.get("data_source"),
        config_path,
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

    fetch_universe = _build_fetch_universe(engine, include_optional=include_optional)
    cache_key = _prefetch_key(engine, fetch_universe)
    with _PREFETCH_LOCK:
        cached = _PREFETCH_CACHE.get(cache_key)
    if cached is None:
        cached = _prefetch_full_data(engine, fetch_universe)
        if cached:
            with _PREFETCH_LOCK:
                _PREFETCH_CACHE[cache_key] = cached
    return bool(cached)

def run_backtest(
    config: Dict[str, Any],
    config_path: str,
    run_dir: str | None = None,
    save_outputs: bool = False,
    prefetch_data: bool | None = None,
    read_only_cache: bool = False,
    stage_callback: Optional[Callable[[str, Optional[Dict[str, Any]]], None]] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    def _emit(stage: str, payload: Optional[Dict[str, Any]] = None) -> None:
        if stage_callback is None:
            return
        try:
            stage_callback(stage, payload)
        except Exception:
            pass

    _emit("load_tickers_start")
    backtest_cfg = config.get("backtest", {}) or {}
    start_date = backtest_cfg.get("start_date") or backtest_cfg.get("start")
    end_date = backtest_cfg.get("end_date") or backtest_cfg.get("end")
    interval = backtest_cfg.get("interval", "1d")
    if not start_date:
        raise ValueError("Config missing backtest.start_date")

    tickers = resolve_tickers(config_path, backtest_cfg)
    if not tickers:
        raise ValueError("Config did not provide any tickers.")
    _emit("load_tickers_done", {"tickers": len(tickers)})

    _emit("build_strategies_start")
    strategies_cfg = config.get("strategies", [])
    strategies = build_strategy_instances(strategies_cfg)
    if not strategies:
        raise ValueError("No strategies configured or found in config.")
    _emit("build_strategies_done", {"strategies": len(strategies)})

    engine_kwargs = filter_engine_kwargs(backtest_cfg.get("engine", {}), BacktestEngine)
    data_provider = build_data_provider(
        backtest_cfg.get("data_source"),
        config_path,
        tickers=tickers,
        read_only_cache=read_only_cache,
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
    _emit("engine_init_done")

    prefetch_enabled = True if prefetch_data is None else bool(prefetch_data)
    if prefetch_enabled:
        fetch_universe = _build_fetch_universe(engine)
        cache_key = _prefetch_key(engine, fetch_universe)
        _emit("prefetch_start", {"symbols": len(fetch_universe)})
        with _PREFETCH_LOCK:
            cached = _PREFETCH_CACHE.get(cache_key)
        if cached is None:
            cached = _prefetch_full_data(engine, fetch_universe)
            if cached:
                with _PREFETCH_LOCK:
                    _PREFETCH_CACHE[cache_key] = cached
        if cached:
            engine.full_data = _deepcopy_full_data(cached)
        _emit(
            "prefetch_done",
            {
                "cached": bool(cached),
                "symbols": len(cached) if cached else 0,
            },
        )

    engine.status_callback = stage_callback
    _emit("engine_run_start")
    engine.run()
    _emit("engine_run_done")
    equity, trades, summary, _, _ = engine.get_report()
    _emit("report_done")

    if save_outputs and run_dir:
        _emit("save_outputs_start")
        os.makedirs(run_dir, exist_ok=True)
        config_path_out = os.path.join(run_dir, "config.yaml")
        with open(config_path_out, "w", encoding="utf-8") as handle:
            yaml.safe_dump(config, handle, sort_keys=False)
        summary_path = os.path.join(run_dir, "summary.json")
        with open(summary_path, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, default=str)
        trades_path = os.path.join(run_dir, "trades.csv")
        trades_out = trades.copy()
        if "entry_metrics" in trades_out.columns:
            trades_out["entry_metrics"] = trades_out["entry_metrics"].apply(
                lambda v: json.dumps(v, default=str) if isinstance(v, dict) else v
            )
        trades_out.to_csv(trades_path, index=False)
        _emit("save_outputs_done")

    return equity, trades, summary
