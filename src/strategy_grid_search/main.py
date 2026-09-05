import argparse
import logging
import os
from typing import List

from .config import load_search_config, load_yaml, resolve_path_from_config
from .metrics import load_metrics_registry
from .registry import (
    generate_registry,
    load_registry,
    merge_registry,
    registry_keys_for_config,
    save_registry,
)
from .search import build_param_space, run_hybrid_search
from .analysis import analyze_and_write

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
DEFAULT_REGISTRY_PATHS = [
    "param_registry.yaml",
    os.path.join("src", "strategy_grid_search", "param_registry", "param_registry.yaml"),
]


def _extract_strategy_names(config: dict) -> List[str]:
    names = []
    for entry in config.get("strategies", []) or []:
        if isinstance(entry, dict):
            name = entry.get("class") or entry.get("name")
            if name:
                names.append(name)
        elif isinstance(entry, str):
            names.append(entry)
    return names


def _resolve_registry_path(path: str) -> str | None:
    if path and os.path.exists(path):
        return path
    for candidate in DEFAULT_REGISTRY_PATHS:
        if os.path.exists(candidate):
            return candidate
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Strategy grid/random/optuna search")
    parser.add_argument("--config", type=str, help="Path to grid-search config YAML")
    parser.add_argument("--generate-registry", action="store_true", help="Generate full parameter registry")
    parser.add_argument("--registry-out", type=str, default="param_registry.yaml")
    parser.add_argument("--metrics-out", type=str, default=None, help="Write default metrics registry YAML")
    parser.add_argument("--list-params", action="store_true", help="List tunable params")
    parser.add_argument("--base-config", type=str, default=None, help="Filter params to a base backtest config")

    args = parser.parse_args()

    if args.generate_registry:
        registry = generate_registry()
        save_registry(registry, args.registry_out)
        logger.info("Registry written to %s", os.path.abspath(args.registry_out))
        return

    if args.metrics_out:
        metrics = load_metrics_registry(None)
        save_registry(metrics, args.metrics_out)
        logger.info("Metrics registry written to %s", os.path.abspath(args.metrics_out))
        return

    if args.list_params:
        registry_path = _resolve_registry_path(args.registry_out)
        if registry_path:
            registry = load_registry(registry_path)
            if registry_path != args.registry_out:
                logger.info("Using registry at %s", os.path.abspath(registry_path))
        else:
            logger.warning("Registry file not found; generating in-memory registry.")
            registry = generate_registry()
        if args.base_config:
            base_path = os.path.abspath(args.base_config)
            base_cfg = load_yaml(base_path)
            names = _extract_strategy_names(base_cfg)
            keys = registry_keys_for_config(registry, names)
        else:
            keys = sorted(registry.keys())
        for key in keys:
            print(key)
        return

    if not args.config:
        parser.error("--config is required unless using --generate-registry/--list-params/--metrics-out")

    search_cfg = load_search_config(args.config)
    base_config_path = resolve_path_from_config(args.config, search_cfg["base_config"])
    if not base_config_path or not os.path.exists(base_config_path):
        raise FileNotFoundError(f"Base config not found: {search_cfg['base_config']}")
    base_config = load_yaml(base_config_path)

    registry_path = resolve_path_from_config(args.config, search_cfg.get("registry"))
    registry = load_registry(registry_path)
    if not registry:
        registry = generate_registry()
    overrides_path = resolve_path_from_config(args.config, search_cfg.get("registry_overrides"))
    overrides = load_registry(overrides_path)
    if overrides:
        registry = merge_registry(registry, overrides)

    metrics_path = resolve_path_from_config(args.config, search_cfg.get("metrics_registry"))
    metrics_registry = load_metrics_registry(metrics_path)
    objectives = search_cfg.get("objectives") or [
        {"metric": "total_return_pct", "direction": "maximize", "weight": 0.5},
        {"metric": "sharpe_ratio", "direction": "maximize", "weight": 0.5},
    ]

    spaces = build_param_space(search_cfg.get("params", {}), registry)
    results_dir = run_hybrid_search(
        base_config=base_config,
        base_config_path=base_config_path,
        spaces=spaces,
        objectives=objectives,
        metrics_registry=metrics_registry,
        search_cfg=search_cfg.get("search", {}),
        output_cfg=search_cfg.get("output", {}),
    )
    output_cfg = search_cfg.get("output", {}) or {}
    summary = analyze_and_write(
        base_config=base_config,
        spaces=spaces,
        journal_path=os.path.join(results_dir, "journal.jsonl"),
        results_dir=results_dir,
        top_n=int(output_cfg.get("top_n", 20)),
    )
    if summary.get("status") == "empty":
        logger.info("Search complete. No successful trials recorded.")
    else:
        logger.info("Search complete. Results: %s", results_dir)
        logger.info("Best score: %s", summary.get("best_score"))
        logger.info("Best metrics: %s", summary.get("best_metrics"))


if __name__ == "__main__":
    main()
