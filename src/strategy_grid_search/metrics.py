from typing import Any, Dict, List, Tuple

import yaml


DEFAULT_METRICS: Dict[str, Dict[str, Any]] = {
    "total_return_pct": {
        "source": "total_return_pct",
        "description": "Total return percent.",
        "direction": "maximize",
    },
    "sharpe_ratio": {
        "source": "sharpe_ratio",
        "description": "Annualized Sharpe ratio.",
        "direction": "maximize",
    },
    "max_drawdown_pct": {
        "source": "max_drawdown_pct",
        "description": "Maximum drawdown percent.",
        "direction": "minimize",
    },
}


def load_metrics_registry(path: str | None) -> Dict[str, Any]:
    if not path:
        return dict(DEFAULT_METRICS)
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("metrics registry must be a mapping")
    merged = dict(DEFAULT_METRICS)
    merged.update(data)
    return merged


def _resolve_metric_value(summary: Dict[str, Any], metric: str, registry: Dict[str, Any]) -> float | None:
    entry = registry.get(metric, {})
    source_key = entry.get("source", metric)
    value = summary.get(source_key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compute_score(
    summary: Dict[str, Any],
    objectives: List[Dict[str, Any]],
    registry: Dict[str, Any],
) -> Tuple[float, Dict[str, float | None]]:
    score = 0.0
    values: Dict[str, float | None] = {}
    for obj in objectives:
        metric = obj.get("metric")
        if not metric:
            continue
        value = _resolve_metric_value(summary, metric, registry)
        values[metric] = value
        if value is None:
            continue
        direction = obj.get("direction") or registry.get(metric, {}).get("direction", "maximize")
        weight = obj.get("weight", 1.0)
        sign = 1.0 if direction == "maximize" else -1.0
        score += float(weight) * sign * value
    return score, values
