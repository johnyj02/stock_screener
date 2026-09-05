import json
import os
from typing import Any, Dict, List

import yaml

from .registry import apply_param


def load_journal(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    records: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                continue
    return records


def _filter_ok(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [r for r in records if r.get("status") == "ok" and r.get("score") is not None]


def _rank_records(records: List[Dict[str, Any]], top_n: int) -> List[Dict[str, Any]]:
    ordered = sorted(records, key=lambda r: float(r.get("score", -1e12)), reverse=True)
    return ordered[: max(1, top_n)]


def _spearman(xs: List[float], ys: List[float]) -> float | None:
    if len(xs) < 3:
        return None
    # rank data
    x_rank = {v: i for i, v in enumerate(sorted(set(xs)))}
    y_rank = {v: i for i, v in enumerate(sorted(set(ys)))}
    xr = [x_rank[v] for v in xs]
    yr = [y_rank[v] for v in ys]
    n = len(xs)
    mean_x = sum(xr) / n
    mean_y = sum(yr) / n
    cov = sum((xr[i] - mean_x) * (yr[i] - mean_y) for i in range(n))
    var_x = sum((x - mean_x) ** 2 for x in xr)
    var_y = sum((y - mean_y) ** 2 for y in yr)
    if var_x == 0 or var_y == 0:
        return None
    return cov / (var_x ** 0.5 * var_y ** 0.5)


def _hashable_value(value: Any) -> Any:
    if isinstance(value, (type(None), bool, int, float, str)):
        return value
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except TypeError:
        return str(value)


def _importance_from_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not records:
        return []
    rows = []
    for rec in records:
        params = rec.get("params") or {}
        score = rec.get("score")
        if score is None:
            continue
        rows.append((params, float(score)))

    keys = sorted({k for params, _ in rows for k in params.keys()})
    results: List[Dict[str, Any]] = []
    for key in keys:
        values = [params.get(key) for params, _ in rows]
        scores = [score for _, score in rows]
        # numeric vs categorical
        numeric = True
        for v in values:
            if v is None:
                numeric = False
                break
            if isinstance(v, bool):
                continue
            if not isinstance(v, (int, float)):
                numeric = False
                break
        if numeric:
            xs = [float(v) for v in values]
            corr = _spearman(xs, scores)
            results.append(
                {
                    "param": key,
                    "importance": abs(corr) if corr is not None else 0.0,
                    "method": "spearman",
                    "detail": corr,
                }
            )
        else:
            # categorical: use range of mean scores
            buckets: Dict[Any, List[float]] = {}
            for v, score in zip(values, scores):
                buckets.setdefault(_hashable_value(v), []).append(score)
            means = {k: sum(v) / len(v) for k, v in buckets.items()}
            if means:
                spread = max(means.values()) - min(means.values())
            else:
                spread = 0.0
            results.append(
                {
                    "param": key,
                    "importance": spread,
                    "method": "mean_spread",
                    "detail": means,
                }
            )

    results.sort(key=lambda r: r["importance"], reverse=True)
    return results


def _apply_params(base_config: Dict[str, Any], params: Dict[str, Any], spaces: List[Dict[str, Any]]) -> Dict[str, Any]:
    config = yaml.safe_load(yaml.safe_dump(base_config, sort_keys=False)) or {}
    path_map = {space["key"]: space["path"] for space in spaces}
    for key, value in params.items():
        path = path_map.get(key)
        if not path:
            continue
        apply_param(config, path, value)
    return config


def analyze_and_write(
    base_config: Dict[str, Any],
    spaces: List[Dict[str, Any]],
    journal_path: str,
    results_dir: str,
    top_n: int,
) -> Dict[str, Any]:
    records = load_journal(journal_path)
    ok_records = _filter_ok(records)
    if not ok_records:
        return {"status": "empty"}

    top = _rank_records(ok_records, top_n)
    best = top[0]
    importance = _importance_from_records(ok_records)

    os.makedirs(results_dir, exist_ok=True)
    best_config = _apply_params(base_config, best.get("params", {}), spaces)
    best_config_path = os.path.join(results_dir, "best_config.yaml")
    with open(best_config_path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(best_config, handle, sort_keys=False)

    summary = {
        "best_score": best.get("score"),
        "best_metrics": best.get("metrics"),
        "best_params": best.get("params"),
        "trials_ok": len(ok_records),
        "trials_total": len(records),
    }
    with open(os.path.join(results_dir, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, default=str)

    # Write top results
    top_path = os.path.join(results_dir, "top_results.json")
    with open(top_path, "w", encoding="utf-8") as handle:
        json.dump(top, handle, indent=2, default=str)

    # Write importance
    imp_path = os.path.join(results_dir, "param_importance.json")
    with open(imp_path, "w", encoding="utf-8") as handle:
        json.dump(importance, handle, indent=2, default=str)

    return summary
