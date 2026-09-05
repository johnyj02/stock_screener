import inspect
from typing import Any, Dict, Iterable, List, Optional

import yaml

from stock_screener.core.loader import StrategyLoader
from strategy_backtester.core.engine import BacktestEngine


def _is_simple_value(value: Any) -> bool:
    return isinstance(value, (int, float, bool, str, list, dict, type(None)))


def _value_type(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return "unknown"


def generate_registry(include_engine: bool = True, include_strategies: bool = True) -> Dict[str, Any]:
    registry: Dict[str, Any] = {}

    if include_engine:
        params = inspect.signature(BacktestEngine.__init__).parameters
        skip = {"self", "start_date", "end_date", "interval", "universe", "strategies"}
        for name, param in params.items():
            if name in skip:
                continue
            default = None if param.default is inspect._empty else param.default
            registry[f"engine.{name}"] = {
                "path": f"backtest.engine.{name}",
                "type": _value_type(default),
                "default": default,
                "description": "BacktestEngine parameter.",
            }

    if include_strategies:
        loader = StrategyLoader()
        for strategy in loader.load_strategies():
            cls_name = strategy.__class__.__name__
            for attr in dir(strategy):
                if attr.startswith("_"):
                    continue
                try:
                    value = getattr(strategy, attr)
                except Exception:
                    continue
                if callable(value) or not _is_simple_value(value):
                    continue
                key = f"{cls_name}.{attr}"
                if key in registry:
                    continue
                registry[key] = {
                    "path": f"strategies[{cls_name}].args.{attr}",
                    "type": _value_type(value),
                    "default": value,
                    "description": f"{cls_name} parameter.",
                }

    return registry


def load_registry(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Registry at {path} must be a mapping")
    return data


def save_registry(registry: Dict[str, Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(registry, handle, sort_keys=True)


def merge_registry(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, override in (overrides or {}).items():
        if key not in merged:
            merged[key] = override
            continue
        entry = dict(merged[key])
        entry.update(override or {})
        merged[key] = entry
    return merged


def _set_nested(mapping: Dict[str, Any], path: str, value: Any) -> None:
    keys = [k for k in path.split(".") if k]
    if not keys:
        return
    cursor = mapping
    for key in keys[:-1]:
        if key not in cursor or not isinstance(cursor[key], dict):
            cursor[key] = {}
        cursor = cursor[key]
    cursor[keys[-1]] = value


def apply_param(config: Dict[str, Any], path: str, value: Any) -> None:
    if ".strategies[" in path or path.startswith("strategies["):
        if ".strategies[" in path:
            prefix, rest = path.split(".strategies[", 1)
        else:
            prefix, rest = "", path[len("strategies[") :]
        class_part, suffix = rest.split("].", 1)
        class_names = [c.strip() for c in class_part.split(",") if c.strip()]

        target = config
        if prefix:
            for key in [k for k in prefix.split(".") if k]:
                target = target.setdefault(key, {})
        strategies = target.get("strategies") or []
        for entry in strategies:
            name = entry.get("class") or entry.get("name")
            if class_names and name not in class_names:
                continue
            if "args" not in entry or not isinstance(entry["args"], dict):
                entry["args"] = {}
            _set_nested(entry, suffix, value)
        return

    _set_nested(config, path, value)


def registry_keys_for_config(registry: Dict[str, Any], strategies: Iterable[str]) -> List[str]:
    wanted = set(strategies)
    matched = []
    for key, entry in registry.items():
        path = str(entry.get("path", ""))
        if "strategies[" in path:
            class_part = path.split("strategies[", 1)[1].split("]", 1)[0]
            names = {c.strip() for c in class_part.split(",") if c.strip()}
            if names & wanted:
                matched.append(key)
        else:
            matched.append(key)
    return sorted(set(matched))
