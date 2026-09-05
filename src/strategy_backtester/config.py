import inspect
import logging
import os
from typing import Any, Dict, List

import pandas as pd
import yaml

from stock_screener.core.loader import StrategyLoader
from stock_screener.core.strategy import BaseStrategy

logger = logging.getLogger(__name__)


def load_backtest_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("Backtest config must be a YAML mapping.")
    return data


def resolve_path(config_path: str, target: str) -> str:
    if os.path.isabs(target):
        return target
    base_dir = os.path.dirname(os.path.abspath(config_path))
    return os.path.abspath(os.path.join(base_dir, target))


def load_tickers(file_path: str) -> List[str]:
    try:
        frame = pd.read_csv(file_path)
    except Exception as exc:
        logger.error("Failed to load tickers from %s: %s", file_path, exc)
        return []
    return frame["Symbol"].tolist() if "Symbol" in frame.columns else []


def parse_ticker_list(value: str) -> List[str]:
    return [ticker.strip() for ticker in value.split(",") if ticker.strip()] if value else []


def unique_tickers(values: List[str]) -> List[str]:
    return list(dict.fromkeys(value for value in values if value))


def resolve_tickers(config_path: str, backtest_config: Dict[str, Any]) -> List[str]:
    configured = backtest_config.get("tickers", []) or []
    tickers = parse_ticker_list(configured) if isinstance(configured, str) else list(configured)
    if tickers_file := backtest_config.get("tickers_file"):
        tickers.extend(load_tickers(resolve_path(config_path, tickers_file)))
    return unique_tickers(tickers)


def build_strategy_instances(entries: List[Any]) -> List[BaseStrategy]:
    loader = StrategyLoader()
    available = {s.get_name(): s for s in loader.load_strategies()}
    selected: List[BaseStrategy] = []

    for entry in entries or []:
        if isinstance(entry, str):
            name = entry
            enabled = True
            args = {}
        elif isinstance(entry, dict):
            name = entry.get("class") or entry.get("name")
            enabled = entry.get("enabled", True)
            args = entry.get("args") or {}
        else:
            logger.warning("Invalid strategy config entry: %s", entry)
            continue

        if not name:
            logger.warning("Strategy entry missing class/name: %s", entry)
            continue
        if not enabled:
            continue

        strategy = available.get(name)
        if strategy is None:
            logger.warning("Strategy not found: %s", name)
            continue

        for key, value in (args or {}).items():
            if hasattr(strategy, key):
                setattr(strategy, key, value)
            else:
                logger.warning("Unknown strategy arg '%s' for %s", key, name)
                setattr(strategy, key, value)

        selected.append(strategy)

    return selected


def filter_engine_kwargs(engine_config: Dict[str, Any], engine_cls) -> Dict[str, Any]:
    if not engine_config:
        return {}
    params = set(inspect.signature(engine_cls.__init__).parameters.keys())
    params.discard("self")
    params.discard("start_date")
    params.discard("end_date")
    params.discard("interval")
    params.discard("universe")
    params.discard("strategies")
    allowed = params
    filtered = {k: v for k, v in engine_config.items() if k in allowed}
    unknown = set(engine_config.keys()) - allowed
    if unknown:
        logger.warning("Unknown engine config keys ignored: %s", ", ".join(sorted(unknown)))
    return filtered
