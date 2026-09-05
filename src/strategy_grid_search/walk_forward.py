import argparse
import json
import logging
import math
import os
import hashlib
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from strategy_backtester.core.metrics import equity_metrics

from .analysis import analyze_and_write
from .config import load_search_config, load_yaml, resolve_path_from_config
from .metrics import load_metrics_registry
from .registry import apply_param, generate_registry, load_registry, merge_registry
from .runner import run_backtest
from .search import build_param_space, run_hybrid_search

logger = logging.getLogger(__name__)


@dataclass
class WalkSplit:
    walk_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_end_raw: str


def _record_key(record: Dict[str, Any]) -> str:
    key = record.get("param_hash")
    if key:
        return str(key)
    params = record.get("params") or {}
    return json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)


def _cache_key(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _returns_cache_path(train_dir: str, cache_key: str) -> str:
    return os.path.join(train_dir, "returns_cache", f"{cache_key}.csv")


def _load_cached_returns(train_dir: str, cache_key: str) -> Optional[pd.Series]:
    path = _returns_cache_path(train_dir, cache_key)
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
    except Exception:
        return None
    if df.empty:
        return None
    if "date" not in df.columns:
        if "Unnamed: 0" in df.columns:
            df = df.rename(columns={"Unnamed: 0": "date"})
        elif len(df.columns) >= 2:
            df = df.rename(columns={df.columns[0]: "date", df.columns[1]: "return"})
    if "date" not in df.columns or "return" not in df.columns:
        return None
    dates = pd.to_datetime(df["date"], errors="coerce")
    if dates.isna().all():
        return None
    series = pd.Series(df["return"].values, index=dates)
    series.name = "return"
    return series


def _store_cached_returns(train_dir: str, cache_key: str, returns: pd.Series) -> None:
    if returns is None or returns.empty:
        return
    cache_dir = os.path.join(train_dir, "returns_cache")
    os.makedirs(cache_dir, exist_ok=True)
    df = returns.reset_index()
    df.columns = ["date", "return"]
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    df.to_csv(_returns_cache_path(train_dir, cache_key), index=False)


def _merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _params_hash(params: Dict[str, Any]) -> str:
    payload = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _dedupe_params(params_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    unique = []
    for params in params_list:
        phash = _params_hash(params)
        if phash in seen:
            continue
        seen.add(phash)
        unique.append(params)
    return unique


def _perturb_params(
    params: Dict[str, Any],
    spaces: List[Dict[str, Any]],
    rng: random.Random,
) -> Optional[Dict[str, Any]]:
    candidates = []
    for space in spaces:
        key = space["key"]
        if key not in params:
            continue
        values = space.get("values") or []
        range_cfg = space.get("range") or {}
        param_type = space.get("type", "float")
        if values:
            candidates.append((space, "values"))
        elif param_type in {"int", "float"} and range_cfg.get("min") is not None and range_cfg.get("max") is not None:
            candidates.append((space, "range"))
    if not candidates:
        return None
    for _ in range(20):
        space, mode = rng.choice(candidates)
        key = space["key"]
        current = params.get(key)
        if mode == "values":
            values = list(space.get("values") or [])
            if len(values) < 2:
                continue
            choices = [v for v in values if _hashable_value(v) != _hashable_value(current)]
            if not choices:
                continue
            new_val = rng.choice(choices)
        else:
            range_cfg = space.get("range") or {}
            rmin = float(range_cfg.get("min"))
            rmax = float(range_cfg.get("max"))
            if rmax <= rmin:
                continue
            step = range_cfg.get("step")
            if step is not None:
                step = float(step)
                delta = step if rng.random() < 0.5 else -step
                new_val = float(current) + delta if current is not None else rmin
            else:
                span = rmax - rmin
                delta = span * 0.05
                new_val = float(current) + (delta if rng.random() < 0.5 else -delta)
            new_val = min(max(new_val, rmin), rmax)
            if space.get("type") == "int":
                new_val = int(round(new_val))
        if _hashable_value(new_val) == _hashable_value(current):
            continue
        updated = dict(params)
        updated[key] = new_val
        return updated
    return None


def _parse_date(value: Any) -> pd.Timestamp:
    if isinstance(value, pd.Timestamp):
        return value.normalize()
    if isinstance(value, str):
        return pd.Timestamp(value).normalize()
    raise ValueError(f"Invalid date value: {value}")


def _trading_days(start: pd.Timestamp, end: pd.Timestamp) -> List[pd.Timestamp]:
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    return list(pd.bdate_range(start=start, end=end))


def _resolve_fallback_window(
    cfg: Dict[str, Any],
    total_days: int,
    train_days: int,
    test_days: int,
    purge_days: int,
    embargo_days: int,
) -> Tuple[int, int, int, bool]:
    fallback = cfg.get("fallback_short_window") or {}
    enabled = bool(fallback.get("enabled", True))
    if not enabled:
        return 0, 0, 0, False
    min_required = fallback.get("min_required_days")
    if min_required is None:
        min_required = train_days + test_days + purge_days + embargo_days
    try:
        if total_days >= int(min_required):
            return 0, 0, 0, False
    except (TypeError, ValueError):
        pass
    short_train = int(fallback.get("train_days", 30))
    short_test = int(fallback.get("test_days", 10))
    short_step = int(fallback.get("step_days", 10))
    return short_train, short_test, short_step, True


def _generate_splits(
    days: List[pd.Timestamp],
    mode: str,
    train_days: int,
    test_days: int,
    step_days: int,
    purge_days: int,
    embargo_days: int,
    train_min_days: Optional[int] = None,
    train_max_days: Optional[int] = None,
    max_walks: Optional[int] = None,
) -> List[WalkSplit]:
    if not days:
        return []
    mode = (mode or "rolling").lower()
    train_min_days = int(train_min_days or train_days)
    train_max_days = int(train_max_days or train_days)
    train_max_days = max(train_max_days, train_min_days)
    total_days = len(days)
    splits: List[WalkSplit] = []
    test_start_idx = train_min_days + purge_days + embargo_days
    walk_id = 0
    while test_start_idx + test_days <= total_days:
        train_end_raw_idx = test_start_idx - 1 - embargo_days
        train_end_idx = train_end_raw_idx - purge_days
        if train_end_idx < 0:
            break
        if mode == "expanding":
            train_start_idx = 0
        elif mode == "hybrid":
            train_start_idx = max(0, train_end_idx - train_max_days + 1)
        else:
            train_start_idx = train_end_idx - train_days + 1
        if train_start_idx < 0:
            if mode == "expanding":
                train_start_idx = 0
            else:
                break
        if train_end_idx - train_start_idx + 1 < train_min_days:
            test_start_idx += step_days
            continue
        test_end_idx = test_start_idx + test_days - 1
        split = WalkSplit(
            walk_id=walk_id,
            train_start=days[train_start_idx].strftime("%Y-%m-%d"),
            train_end=days[train_end_idx].strftime("%Y-%m-%d"),
            test_start=days[test_start_idx].strftime("%Y-%m-%d"),
            test_end=days[test_end_idx].strftime("%Y-%m-%d"),
            train_end_raw=days[train_end_raw_idx].strftime("%Y-%m-%d"),
        )
        splits.append(split)
        walk_id += 1
        if max_walks is not None and len(splits) >= int(max_walks):
            break
        test_start_idx += step_days
    return splits


def _hashable_value(value: Any) -> Any:
    if isinstance(value, (type(None), bool, int, float, str)):
        return value
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except TypeError:
        return str(value)


def _param_distance(
    params_a: Dict[str, Any],
    params_b: Dict[str, Any],
    spaces: List[Dict[str, Any]],
) -> float:
    if not spaces:
        return 0.0
    distances = []
    for space in spaces:
        key = space["key"]
        val_a = params_a.get(key)
        val_b = params_b.get(key)
        if val_a is None or val_b is None:
            distances.append(0.0)
            continue
        if val_a == val_b:
            distances.append(0.0)
            continue
        param_type = space.get("type", "float")
        range_cfg = space.get("range") or {}
        values = space.get("values") or []
        if param_type in {"int", "float"} and isinstance(val_a, (int, float)) and isinstance(val_b, (int, float)):
            if range_cfg.get("min") is not None and range_cfg.get("max") is not None:
                span = float(range_cfg["max"]) - float(range_cfg["min"])
            elif values and all(isinstance(v, (int, float)) for v in values):
                span = float(max(values)) - float(min(values))
            else:
                span = 0.0
            if span > 0:
                distances.append(abs(float(val_a) - float(val_b)) / span)
            else:
                distances.append(1.0)
            continue
        distances.append(0.0 if _hashable_value(val_a) == _hashable_value(val_b) else 1.0)
    if not distances:
        return 0.0
    return float(sum(distances) / len(distances))


def _equity_returns(equity: pd.DataFrame) -> pd.Series:
    if equity.empty or "equity" not in equity.columns:
        return pd.Series(dtype=float)
    series = pd.to_numeric(equity["equity"], errors="coerce")
    if "date" in equity.columns:
        dates = pd.to_datetime(equity["date"], errors="coerce")
        mask = ~dates.isna() & ~series.isna()
        series = pd.Series(series[mask].values, index=dates[mask])
    else:
        series = series.dropna()
        if not isinstance(equity.index, pd.DatetimeIndex):
            idx = pd.to_datetime(equity.index, errors="coerce")
            if not idx.isna().all():
                series.index = idx
    return series.pct_change().dropna()


def _align_returns(returns_map: Dict[str, pd.Series]) -> pd.DataFrame:
    if not returns_map:
        return pd.DataFrame()
    df = pd.DataFrame(returns_map)
    return df.fillna(0.0)


def _avg_corr(target: pd.Series, others: Iterable[pd.Series]) -> Optional[float]:
    corrs = []
    for other in others:
        if target.empty or other.empty:
            continue
        aligned = _align_returns({"a": target, "b": other})
        if aligned.empty:
            continue
        corr = aligned["a"].corr(aligned["b"])
        if corr is None or not math.isfinite(corr):
            continue
        corrs.append(float(corr))
    if not corrs:
        return None
    return float(sum(corrs) / len(corrs))


def _select_ensemble(
    candidates: List[Dict[str, Any]],
    spaces: List[Dict[str, Any]],
    returns_by_hash: Dict[str, pd.Series],
    ensemble_size: int,
    min_param_distance: float,
    corr_threshold: float,
    corr_threshold_step: float,
    corr_threshold_max: float,
) -> List[Dict[str, Any]]:
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda r: float(r.get("score", -1e12)), reverse=True)
    selected = [ordered[0]]
    if ensemble_size <= 1:
        return selected
    thresholds = []
    current = corr_threshold
    while current <= corr_threshold_max + 1e-9:
        thresholds.append(round(current, 4))
        current += corr_threshold_step
    remaining = [c for c in ordered[1:]]
    for threshold in thresholds:
        if len(selected) >= ensemble_size:
            break
        for cand in list(remaining):
            if len(selected) >= ensemble_size:
                break
            min_dist = min(
                _param_distance(cand["params"], sel["params"], spaces)
                for sel in selected
            )
            if min_dist < min_param_distance:
                continue
            target_ret = returns_by_hash.get(cand["param_hash"], pd.Series(dtype=float))
            other_returns = [returns_by_hash.get(s["param_hash"], pd.Series(dtype=float)) for s in selected]
            avg_corr = _avg_corr(target_ret, other_returns)
            if avg_corr is not None and avg_corr >= threshold:
                continue
            selected.append(cand)
            remaining.remove(cand)
        if not remaining:
            break
    return selected


def _weights_for_ensemble(
    selected: List[Dict[str, Any]],
    weighting: str,
    max_weight_mult: float,
) -> List[float]:
    n = len(selected)
    if n == 0:
        return []
    weighting = (weighting or "equal").lower()
    if weighting == "score_weighted":
        scores = [float(r.get("score", 0.0) or 0.0) for r in selected]
        total = sum(max(s, 0.0) for s in scores)
        if total <= 0:
            return [1.0 / n] * n
        weights = [max(s, 0.0) / total for s in scores]
        cap = (max_weight_mult or 2.0) / n
        weights = [min(w, cap) for w in weights]
        total = sum(weights)
        if total <= 0:
            return [1.0 / n] * n
        return [w / total for w in weights]
    return [1.0 / n] * n


def _filter_candidates(
    records: List[Dict[str, Any]],
    min_trades: Optional[int],
    max_drawdown_pct: Optional[float],
    max_turnover_pct: Optional[float],
) -> List[Dict[str, Any]]:
    filtered = []
    for record in records:
        summary = record.get("summary") or {}
        if min_trades is not None:
            try:
                if int(summary.get("total_trades", 0)) < int(min_trades):
                    continue
            except (TypeError, ValueError):
                continue
        if max_drawdown_pct is not None:
            try:
                dd = float(summary.get("max_drawdown_pct"))
            except (TypeError, ValueError):
                continue
            if abs(dd) > float(max_drawdown_pct):
                continue
        if max_turnover_pct is not None:
            try:
                turnover = float(summary.get("turnover_pct"))
            except (TypeError, ValueError):
                continue
            if turnover > float(max_turnover_pct):
                continue
        filtered.append(record)
    return filtered


def _stitch_equity(
    segments: List[pd.DataFrame],
    start_equity: float,
) -> pd.DataFrame:
    if not segments:
        return pd.DataFrame()
    current_equity = float(start_equity)
    rows = []
    for segment in segments:
        returns = _equity_returns(segment)
        for date, ret in returns.items():
            current_equity *= 1.0 + float(ret)
            rows.append({"date": date, "equity": current_equity})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df.set_index("date").sort_index()
    return df


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _walk_is_complete(walk_dir: str) -> bool:
    marker = os.path.join(walk_dir, "walk_complete.json")
    if os.path.exists(marker):
        return True
    train_summary = os.path.join(walk_dir, "train", "summary.json")
    test_single = os.path.join(walk_dir, "test", "test_summary_single.json")
    test_ensemble = os.path.join(walk_dir, "test", "test_summary.json")
    return os.path.exists(train_summary) and (os.path.exists(test_single) or os.path.exists(test_ensemble))


def _run_walk_forward(config_path: str) -> None:
    search_cfg = load_search_config(config_path)
    wf_cfg = search_cfg.get("walk_forward") or {}
    if not wf_cfg:
        raise ValueError("grid-search config missing walk_forward block")

    base_config_path = resolve_path_from_config(config_path, search_cfg["base_config"])
    if not base_config_path or not os.path.exists(base_config_path):
        raise FileNotFoundError(f"Base config not found: {search_cfg['base_config']}")
    base_config = load_yaml(base_config_path)

    registry_path = resolve_path_from_config(config_path, search_cfg.get("registry"))
    registry = load_registry(registry_path)
    if not registry:
        registry = generate_registry()
    overrides_path = resolve_path_from_config(config_path, search_cfg.get("registry_overrides"))
    overrides = load_registry(overrides_path)
    if overrides:
        registry = merge_registry(registry, overrides)

    metrics_path = resolve_path_from_config(config_path, search_cfg.get("metrics_registry"))
    metrics_registry = load_metrics_registry(metrics_path)
    objectives = search_cfg.get("objectives") or [
        {"metric": "total_return_pct", "direction": "maximize", "weight": 0.5},
        {"metric": "sharpe_ratio", "direction": "maximize", "weight": 0.5},
    ]

    spaces = build_param_space(search_cfg.get("params", {}), registry)

    backtest_cfg = base_config.get("backtest", {}) or {}
    start_date = _parse_date(backtest_cfg.get("start_date") or backtest_cfg.get("start"))
    end_date_value = backtest_cfg.get("end_date") or backtest_cfg.get("end")
    if end_date_value:
        end_date = _parse_date(end_date_value)
    else:
        end_date = pd.Timestamp.utcnow().normalize()

    days = _trading_days(start_date, end_date)
    if not days:
        raise ValueError("No trading days available for requested date range.")

    mode = wf_cfg.get("mode", "rolling")
    train_days = int(wf_cfg.get("train_days", 252))
    test_days = int(wf_cfg.get("test_days", 21))
    step_days = int(wf_cfg.get("step_days", test_days))
    purge_days = int(wf_cfg.get("purge_days", 20))
    embargo_days = int(wf_cfg.get("embargo_days", 1))
    max_walks = wf_cfg.get("max_walks")
    train_min_days = wf_cfg.get("train_min_days")
    train_max_days = wf_cfg.get("train_max_days")

    short_train, short_test, short_step, used_fallback = _resolve_fallback_window(
        wf_cfg, len(days), train_days, test_days, purge_days, embargo_days
    )
    if used_fallback:
        train_days = short_train
        test_days = short_test
        step_days = short_step
        logger.warning(
            "Falling back to short WFV window train=%s test=%s step=%s due to limited history.",
            train_days,
            test_days,
            step_days,
        )

    splits = _generate_splits(
        days=days,
        mode=mode,
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
        purge_days=purge_days,
        embargo_days=embargo_days,
        train_min_days=train_min_days,
        train_max_days=train_max_days,
        max_walks=max_walks,
    )
    if not splits:
        raise ValueError("No valid walk-forward splits found for the requested window.")

    output_cfg = search_cfg.get("output", {}) or {}
    base_results_dir = os.path.abspath(output_cfg.get("results_dir", "results/grid_search"))
    run_id = wf_cfg.get("run_id")
    if not run_id:
        run_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        run_id = f"{run_id}_{mode}{train_days}_test{test_days}_step{step_days}"

    run_dir = os.path.join(base_results_dir, "wfv", run_id)
    _ensure_dir(run_dir)

    splits_df = pd.DataFrame(
        [
            {
                "walk_id": split.walk_id,
                "train_start": split.train_start,
                "train_end": split.train_end,
                "train_end_raw": split.train_end_raw,
                "test_start": split.test_start,
                "test_end": split.test_end,
            }
            for split in splits
        ]
    )
    splits_df.to_csv(os.path.join(run_dir, "splits.csv"), index=False)

    run_meta = {
        "mode": mode,
        "train_days": train_days,
        "test_days": test_days,
        "step_days": step_days,
        "purge_days": purge_days,
        "embargo_days": embargo_days,
        "walks": len(splits),
        "fallback_used": used_fallback,
    }
    _write_json(os.path.join(run_dir, "run_meta.json"), run_meta)

    selection_cfg = wf_cfg.get("selection") or {}
    candidate_pool = int(selection_cfg.get("candidate_pool_size", 50))
    ensemble_size = int(selection_cfg.get("ensemble_size", 5))
    min_param_distance = float(selection_cfg.get("min_param_distance", 0.10))
    corr_threshold = float(selection_cfg.get("corr_threshold", 0.80))
    corr_threshold_step = float(selection_cfg.get("corr_threshold_step", 0.05))
    corr_threshold_max = float(selection_cfg.get("corr_threshold_max", 0.90))
    weighting = selection_cfg.get("weighting", "equal")
    max_weight_mult = float(selection_cfg.get("max_weight_mult", 2.0))

    constraints = wf_cfg.get("constraints") or {}
    min_trades = constraints.get("min_trades")
    max_turnover_pct = constraints.get("max_turnover_pct")
    drawdown_cap_pct = constraints.get("drawdown_cap_pct")
    if drawdown_cap_pct is None:
        drawdown_cap_pct = search_cfg.get("search", {}).get("drawdown_cap_pct")

    warm_cfg = wf_cfg.get("warm_start") or {}
    warm_enabled = bool(warm_cfg.get("enabled", False))
    seed_top_k = int(warm_cfg.get("seed_top_k", 20))
    perturbations_per_seed = int(warm_cfg.get("perturbations_per_seed", 2))
    exploration_pct = warm_cfg.get("exploration_pct")
    seed_value = (search_cfg.get("search", {}) or {}).get("seed", 0)
    try:
        seed_value = int(seed_value)
    except (TypeError, ValueError):
        seed_value = 0
    seed_rng = random.Random(seed_value)
    global_bank_max = int(warm_cfg.get("global_bank_max", 100))
    global_bank: List[Dict[str, Any]] = []

    stitched_single_segments: List[pd.DataFrame] = []
    stitched_ensemble_segments: List[pd.DataFrame] = []
    stitched_single_trades: List[pd.DataFrame] = []
    stitched_ensemble_trades: List[pd.DataFrame] = []
    selected_params_by_walk: List[Dict[str, Any]] = []
    selection_distances: List[float] = []
    skip_completed = bool(wf_cfg.get("skip_completed_walks", True))

    for split in splits:
        walk_dir = os.path.join(run_dir, f"walk_{split.walk_id:03d}")
        train_dir = os.path.join(walk_dir, "train")
        test_dir = os.path.join(walk_dir, "test")
        if skip_completed and _walk_is_complete(walk_dir):
            logger.info("Skipping completed walk %s.", split.walk_id)
            continue
        _ensure_dir(train_dir)
        _ensure_dir(test_dir)
        logger.info(
            "------------------------ Simulating walk %s -----------------------------",
            split.walk_id,
        )

        train_config = json.loads(json.dumps(base_config))
        train_config.setdefault("backtest", {})["start_date"] = split.train_start
        train_config.setdefault("backtest", {})["end_date"] = split.train_end

        split_meta = {
            "walk_id": split.walk_id,
            "train_start": split.train_start,
            "train_end": split.train_end,
            "train_end_raw": split.train_end_raw,
            "test_start": split.test_start,
            "test_end": split.test_end,
            "purge_days": purge_days,
            "embargo_days": embargo_days,
        }
        _write_json(os.path.join(train_dir, "run_meta.json"), {**split_meta, "split_type": "train"})
        _write_json(os.path.join(test_dir, "run_meta.json"), {**split_meta, "split_type": "test"})

        train_output_cfg = dict(output_cfg or {})
        train_output_cfg["results_dir"] = train_dir
        train_output_cfg["top_n"] = max(int(train_output_cfg.get("top_n", 20)), candidate_pool)

        train_search_cfg = dict(search_cfg.get("search") or {})
        overrides = wf_cfg.get("search_overrides") or {}
        if overrides:
            train_search_cfg = _merge_dict(train_search_cfg, overrides)
        if exploration_pct is not None:
            try:
                train_search_cfg["random_pct"] = float(exploration_pct)
            except (TypeError, ValueError):
                pass
        train_search_cfg["resume"] = bool(wf_cfg.get("resume_train", False))
        prefetch_enabled = bool(train_search_cfg.get("prefetch_data", True))

        seed_params: List[Dict[str, Any]] = []
        if warm_enabled and split.walk_id > 0:
            prev_walk_dir = os.path.join(run_dir, f"walk_{split.walk_id - 1:03d}", "train")
            prev_top_path = os.path.join(prev_walk_dir, "top_results.json")
            if os.path.exists(prev_top_path):
                with open(prev_top_path, "r", encoding="utf-8") as handle:
                    prev_records = json.load(handle) or []
                prev_records = prev_records[:seed_top_k]
                for record in prev_records:
                    params = record.get("params")
                    if isinstance(params, dict):
                        seed_params.append(params)
            if global_bank:
                for record in global_bank[:seed_top_k]:
                    params = record.get("params")
                    if isinstance(params, dict):
                        seed_params.append(params)
            for params in list(seed_params):
                for _ in range(perturbations_per_seed):
                    perturbed = _perturb_params(params, spaces, seed_rng)
                    if perturbed:
                        seed_params.append(perturbed)
            seed_params = _dedupe_params(seed_params)
            train_search_cfg["seed_params"] = seed_params

        run_hybrid_search(
            base_config=train_config,
            base_config_path=base_config_path,
            spaces=spaces,
            objectives=objectives,
            metrics_registry=metrics_registry,
            search_cfg=train_search_cfg,
            output_cfg=train_output_cfg,
        )
        analyze_and_write(
            base_config=train_config,
            spaces=spaces,
            journal_path=os.path.join(train_dir, "journal.jsonl"),
            results_dir=train_dir,
            top_n=int(train_output_cfg.get("top_n", 20)),
        )

        top_results_path = os.path.join(train_dir, "top_results.json")
        if not os.path.exists(top_results_path):
            logger.warning("No top_results.json for walk %s; skipping.", split.walk_id)
            continue
        with open(top_results_path, "r", encoding="utf-8") as handle:
            top_records = json.load(handle) or []
        top_records = top_records[:candidate_pool]
        top_records = _filter_candidates(top_records, min_trades, drawdown_cap_pct, max_turnover_pct)
        if not top_records:
            logger.warning("No viable candidates for walk %s; skipping.", split.walk_id)
            continue

        returns_cache: Dict[str, pd.Series] = {}
        path_map = {space["key"]: space["path"] for space in spaces}
        for record in top_records:
            record["param_hash"] = _record_key(record)
            cache_key = _cache_key(record["param_hash"])
            cached = _load_cached_returns(train_dir, cache_key)
            if cached is not None:
                returns_cache[record["param_hash"]] = cached
                continue
            params = record.get("params") or {}
            candidate_config = json.loads(json.dumps(train_config))
            for key, value in params.items():
                path = path_map.get(key)
                if path:
                    apply_param(candidate_config, path, value)
            equity, _, _ = run_backtest(
                candidate_config,
                base_config_path,
                prefetch_data=prefetch_enabled,
            )
            returns = _equity_returns(equity)
            returns_cache[record.get("param_hash")] = returns
            _store_cached_returns(train_dir, cache_key, returns)

        selected = _select_ensemble(
            candidates=top_records,
            spaces=spaces,
            returns_by_hash=returns_cache,
            ensemble_size=ensemble_size,
            min_param_distance=min_param_distance,
            corr_threshold=corr_threshold,
            corr_threshold_step=corr_threshold_step,
            corr_threshold_max=corr_threshold_max,
        )
        if not selected:
            logger.warning("Selection empty for walk %s; skipping.", split.walk_id)
            continue

        weights = _weights_for_ensemble(selected, weighting, max_weight_mult)
        pairwise_distances = []
        pairwise_corrs = []
        for i in range(len(selected)):
            for j in range(i + 1, len(selected)):
                pairwise_distances.append(
                    _param_distance(selected[i]["params"], selected[j]["params"], spaces)
                )
                corr = _avg_corr(
                    returns_cache.get(selected[i]["param_hash"], pd.Series(dtype=float)),
                    [returns_cache.get(selected[j]["param_hash"], pd.Series(dtype=float))],
                )
                if corr is not None:
                    pairwise_corrs.append(corr)

        selection_info = {
            "selection_method": "corr_distance",
            "candidate_pool": candidate_pool,
            "ensemble_size": len(selected),
            "weights": weights,
            "constraints": {
                "min_trades": min_trades,
                "drawdown_cap_pct": drawdown_cap_pct,
                "max_turnover_pct": max_turnover_pct,
            },
            "diversity": {
                "min_param_distance": min_param_distance,
                "corr_threshold": corr_threshold,
                "corr_threshold_step": corr_threshold_step,
                "corr_threshold_max": corr_threshold_max,
                "min_pairwise_param_distance": min(pairwise_distances) if pairwise_distances else None,
                "avg_pairwise_param_distance": float(sum(pairwise_distances) / len(pairwise_distances))
                if pairwise_distances
                else None,
                "avg_pairwise_return_corr": float(sum(pairwise_corrs) / len(pairwise_corrs))
                if pairwise_corrs
                else None,
            },
            "members": [],
        }

        for member, weight in zip(selected, weights):
            selection_info["members"].append(
                {
                    "param_hash": member.get("param_hash"),
                    "params": member.get("params"),
                    "train_score": member.get("score"),
                    "train_metrics": member.get("metrics"),
                    "weight": weight,
                }
            )

        _write_json(os.path.join(walk_dir, "selected.json"), selection_info)

        test_config = json.loads(json.dumps(base_config))
        test_config.setdefault("backtest", {})["start_date"] = split.test_start
        test_config.setdefault("backtest", {})["end_date"] = split.test_end

        member_equities: List[pd.DataFrame] = []
        member_trades: List[pd.DataFrame] = []
        member_summaries: List[Dict[str, Any]] = []
        for idx, member in enumerate(selected):
            params = member.get("params") or {}
            config = json.loads(json.dumps(test_config))
            for key, value in params.items():
                path = path_map.get(key)
                if path:
                    apply_param(config, path, value)
            equity, trades, summary = run_backtest(
                config,
                base_config_path,
                prefetch_data=prefetch_enabled,
            )
            member_equities.append(equity)
            member_trades.append(trades)
            member_summaries.append(summary)
            member["test_summary"] = summary

        # Single-best outputs (member 0)
        single_equity = member_equities[0] if member_equities else pd.DataFrame()
        single_trades = member_trades[0] if member_trades else pd.DataFrame()
        if not single_equity.empty:
            single_equity.to_csv(os.path.join(test_dir, "test_equity_single.csv"))
        if not single_trades.empty:
            single_trades.to_csv(os.path.join(test_dir, "test_trades_single.csv"), index=False)
        if member_summaries:
            _write_json(os.path.join(test_dir, "test_summary_single.json"), member_summaries[0])

        stitched_single_segments.append(single_equity)
        if not single_trades.empty:
            single_trades = single_trades.copy()
            single_trades["walk_id"] = split.walk_id
            stitched_single_trades.append(single_trades)

        # Ensemble outputs
        weights = weights or [1.0 / len(member_equities)] * len(member_equities)
        returns_map = {
            f"m_{i}": _equity_returns(eq)
            for i, eq in enumerate(member_equities)
        }
        returns_df = _align_returns(returns_map)
        if not returns_df.empty:
            weighted_returns = returns_df.mul(weights, axis=1).sum(axis=1)
            ensemble_equity = pd.DataFrame(
                {"equity": (1.0 + weighted_returns).cumprod() * float(backtest_cfg.get("initial_capital", 100000.0))}
            )
            ensemble_equity.index.name = "date"
        else:
            ensemble_equity = pd.DataFrame()

        weighted_trades_list = []
        for idx, (trades, weight) in enumerate(zip(member_trades, weights)):
            if trades.empty:
                continue
            trades = trades.copy()
            trades["member_id"] = idx
            trades["weight"] = weight
            if "pnl" in trades.columns:
                trades["pnl_weighted"] = trades["pnl"] * weight
            weighted_trades_list.append(trades)
        ensemble_trades = (
            pd.concat(weighted_trades_list, ignore_index=True) if weighted_trades_list else pd.DataFrame()
        )

        if not ensemble_equity.empty:
            ensemble_equity.to_csv(os.path.join(test_dir, "test_equity.csv"))
        if not ensemble_trades.empty:
            ensemble_trades.to_csv(os.path.join(test_dir, "test_trades.csv"), index=False)
        if not ensemble_equity.empty:
            summary = equity_metrics(ensemble_equity, ensemble_trades)
            _write_json(os.path.join(test_dir, "test_summary.json"), summary)

        for idx, summary in enumerate(member_summaries):
            selection_info["members"][idx]["test_summary"] = summary
        _write_json(os.path.join(walk_dir, "selected.json"), selection_info)

        stitched_ensemble_segments.append(ensemble_equity)
        if not ensemble_trades.empty:
            ensemble_trades = ensemble_trades.copy()
            ensemble_trades["walk_id"] = split.walk_id
            stitched_ensemble_trades.append(ensemble_trades)

        if selected_params_by_walk:
            distance = _param_distance(selected_params_by_walk[-1], selected[0]["params"], spaces)
            selection_distances.append(distance)
        selected_params_by_walk.append(selected[0]["params"])

        if warm_enabled:
            global_bank.extend(top_records)
            deduped = {}
            for record in global_bank:
                key = record.get("param_hash") or _record_key(record)
                score = record.get("score")
                try:
                    score_val = float(score)
                except (TypeError, ValueError):
                    score_val = -1e12
                existing = deduped.get(key)
                if existing is None or score_val > existing["score"]:
                    deduped[key] = {"record": record, "score": score_val}
            ranked = sorted(deduped.values(), key=lambda item: item["score"], reverse=True)
            global_bank = [item["record"] for item in ranked[:global_bank_max]]

        _write_json(
            os.path.join(walk_dir, "walk_complete.json"),
            {
                "walk_id": split.walk_id,
                "train_start": split.train_start,
                "train_end": split.train_end,
                "test_start": split.test_start,
                "test_end": split.test_end,
                "completed_at": pd.Timestamp.utcnow().isoformat(),
            },
        )

    stitched_single = _stitch_equity(stitched_single_segments, float(backtest_cfg.get("initial_capital", 100000.0)))
    stitched_ensemble = _stitch_equity(stitched_ensemble_segments, float(backtest_cfg.get("initial_capital", 100000.0)))

    if not stitched_single.empty:
        stitched_single.to_csv(os.path.join(run_dir, "stitched_oos_equity_single.csv"))
        summary_single = equity_metrics(stitched_single, pd.DataFrame())
        _write_json(os.path.join(run_dir, "stitched_oos_summary_single.json"), summary_single)
    if not stitched_ensemble.empty:
        stitched_ensemble.to_csv(os.path.join(run_dir, "stitched_oos_equity.csv"))
        summary_ensemble = equity_metrics(stitched_ensemble, pd.DataFrame())
        _write_json(os.path.join(run_dir, "stitched_oos_summary.json"), summary_ensemble)

    if stitched_single_trades:
        pd.concat(stitched_single_trades, ignore_index=True).to_csv(
            os.path.join(run_dir, "stitched_oos_trades_single.csv"), index=False
        )
    if stitched_ensemble_trades:
        pd.concat(stitched_ensemble_trades, ignore_index=True).to_csv(
            os.path.join(run_dir, "stitched_oos_trades.csv"), index=False
        )

    stability = {
        "walks": len(selected_params_by_walk),
        "param_distance_avg": float(sum(selection_distances) / len(selection_distances))
        if selection_distances
        else 0.0,
        "param_distance_median": float(pd.Series(selection_distances).median())
        if selection_distances
        else 0.0,
        "param_distance_p90": float(pd.Series(selection_distances).quantile(0.9))
        if selection_distances
        else 0.0,
        "param_mode_share": {},
    }
    if selected_params_by_walk:
        keys = sorted({k for params in selected_params_by_walk for k in params.keys()})
        for key in keys:
            vals = [_hashable_value(params.get(key)) for params in selected_params_by_walk]
            counts = pd.Series(vals).value_counts(normalize=True)
            if not counts.empty:
                stability["param_mode_share"][key] = float(counts.iloc[0])
    _write_json(os.path.join(run_dir, "stability.json"), stability)

    logger.info("Walk-forward complete. Results in %s", run_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward grid/optuna search")
    parser.add_argument("--config", type=str, required=True, help="Path to grid-search config YAML")
    args = parser.parse_args()
    _run_walk_forward(args.config)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    main()
