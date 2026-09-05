import logging
import os
import re
from typing import Any, Dict

import yaml

from strategy_backtester.config import resolve_path


def load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    return data


def load_search_config(path: str) -> Dict[str, Any]:
    cfg = load_yaml(path)
    if "base_config" not in cfg:
        raise ValueError("grid-search config missing 'base_config'")
    if "params" not in cfg:
        raise ValueError("grid-search config missing 'params'")

    search = cfg.get("search", {}) or {}
    output = cfg.get("output", {}) or {}
    trial_name = cfg.get("trial_name")

    search.setdefault("total_trials", 50)
    search.setdefault("random_pct", 0.3)
    search.setdefault("parallel_workers", max(1, os.cpu_count() or 1))
    search.setdefault("seed", 0)
    search.setdefault("resume", True)

    optuna_cfg = search.get("optuna", {}) or {}
    optuna_cfg.setdefault("enabled", True)
    optuna_cfg.setdefault("sampler", "tpe")
    optuna_cfg.setdefault("n_jobs", 1)
    optuna_cfg.setdefault("storage", None)
    search["optuna"] = optuna_cfg
    if trial_name:
        sanitized = _sanitize_trial_name(str(trial_name))
        if sanitized != trial_name:
            logger.warning("Trial name sanitized from '%s' to '%s'", trial_name, sanitized)
        cfg["trial_name"] = sanitized
        results_dir = os.path.join("src", "strategy_grid_search", "results", "grid_search", sanitized)
        output["results_dir"] = results_dir
        optuna_cfg["storage"] = os.path.join(results_dir, "optuna.db")
    else:
        output.setdefault("results_dir", os.path.join("results", "grid_search"))
    output.setdefault("save_runs", False)
    output.setdefault("top_n", 20)
    cfg["search"] = search
    cfg["output"] = output
    return cfg


def _find_repo_root(start_path: str) -> str | None:
    current = os.path.abspath(start_path)
    for _ in range(6):
        if os.path.isdir(os.path.join(current, "src")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


def resolve_path_from_config(config_path: str, target: str | None) -> str | None:
    if not target or os.path.isabs(target):
        return target
    candidate = resolve_path(config_path, target)
    if os.path.exists(candidate):
        return candidate
    root = _find_repo_root(os.path.dirname(os.path.abspath(config_path)))
    alternative = os.path.abspath(os.path.join(root, target)) if root else None
    return alternative if alternative and os.path.exists(alternative) else candidate


logger = logging.getLogger(__name__)

_TRIAL_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize_trial_name(value: str) -> str:
    value = value.strip()
    if not value:
        return "trial"
    cleaned = _TRIAL_NAME_RE.sub("_", value)
    return cleaned.strip("_") or "trial"
