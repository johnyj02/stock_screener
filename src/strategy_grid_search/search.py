import copy
import contextlib
import faulthandler
import logging
import hashlib
import json
import math
import os
import signal
import time
import threading
import glob
import uuid
from decimal import Decimal, ROUND_HALF_UP
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from concurrent.futures.process import BrokenProcessPool
from typing import Any, Dict, Iterable, List, Optional, Tuple, IO, Generator, Callable

import pandas as pd

from .metrics import compute_score
from .registry import apply_param
from .runner import run_backtest, prefetch_cache

logger = logging.getLogger(__name__)
_WORKER_LOG: Optional[IO[str]] = None
_WORKER_STATE_PATH: Optional[str] = None
_WORKER_CURRENT: Dict[str, Any] = {}
_WORKER_SESSION_ID: Optional[str] = None


class _TrialTimeout(Exception):
    pass


@contextlib.contextmanager
def _timeout_after(seconds: Optional[float]) -> Generator[None, None, None]:
    if not seconds or seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous_handler = signal.getsignal(signal.SIGALRM)

    def _handler(_signum, _frame) -> None:
        raise _TrialTimeout(f"Timeout after {seconds}s")

    signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, float(seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)

try:
    import optuna  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    optuna = None


def _hash_params(params: Dict[str, Any]) -> str:
    payload = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _worker_init(crash_dir: str, session_id: Optional[str]) -> None:
    global _WORKER_LOG, _WORKER_STATE_PATH, _WORKER_SESSION_ID
    _WORKER_SESSION_ID = session_id
    if not crash_dir:
        return
    os.makedirs(crash_dir, exist_ok=True)
    log_path = os.path.join(crash_dir, f"worker_{os.getpid()}.log")
    _WORKER_STATE_PATH = os.path.join(crash_dir, f"worker_{os.getpid()}_current.json")
    _WORKER_LOG = open(log_path, "a", buffering=1, encoding="utf-8")
    _WORKER_LOG.write(f"[worker] started pid={os.getpid()}\n")
    _write_worker_state({"event": "started", "pid": os.getpid(), "ts": time.time()})
    try:
        faulthandler.enable(file=_WORKER_LOG, all_threads=True)
        for sig in (signal.SIGSEGV, signal.SIGBUS, signal.SIGABRT):
            try:
                faulthandler.register(sig, file=_WORKER_LOG, all_threads=True)
            except Exception:
                pass
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _worker_signal_handler)
            except Exception:
                pass
    except Exception:
        pass


def _log_worker_error(message: str) -> None:
    if _WORKER_LOG is None:
        return
    try:
        _WORKER_LOG.write(f"[worker] {message}\n")
    except Exception:
        pass


def _write_worker_state(state: Dict[str, Any]) -> None:
    if not _WORKER_STATE_PATH:
        return
    try:
        with open(_WORKER_STATE_PATH, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, default=str)
    except Exception:
        pass


def _update_worker_state(event: str, payload: Dict[str, Any]) -> None:
    _WORKER_CURRENT.update(payload)
    state = dict(_WORKER_CURRENT)
    state["event"] = event
    state["pid"] = os.getpid()
    state["ts"] = time.time()
    if _WORKER_SESSION_ID is not None:
        state["session_id"] = _WORKER_SESSION_ID
    _write_worker_state(state)
    _log_worker_error(f"{event}: {state}")


def _worker_signal_handler(signum: int, _frame) -> None:
    _log_worker_error(f"signal received: {signum}")
    _update_worker_state("signal", {"signal": signum})


def _make_stage_callback(
    phase: str, trial_id: Optional[int], param_hash: Optional[str]
) -> Callable[[str, Optional[Dict[str, Any]]], None]:
    def _callback(stage: str, payload: Optional[Dict[str, Any]] = None) -> None:
        data = {
            "phase": phase,
            "trial_id": trial_id,
            "param_hash": param_hash,
            "stage": stage,
        }
        if payload:
            data.update(payload)
        _update_worker_state("stage", data)

    return _callback


def _collect_worker_states(
    crash_dir: str,
    session_id: Optional[str],
    limit: int = 5,
) -> List[Dict[str, Any]]:
    if not crash_dir or not os.path.isdir(crash_dir):
        return []
    states: List[Dict[str, Any]] = []
    for path in glob.glob(os.path.join(crash_dir, "worker_*_current.json")):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                state = json.load(handle)
        except Exception:
            continue
        if session_id is not None and state.get("session_id") != session_id:
            continue
        states.append(state)
    states.sort(key=lambda item: float(item.get("ts", 0)), reverse=True)
    trimmed = []
    for state in states[:limit]:
        trimmed.append(
            {
                "pid": state.get("pid"),
                "phase": state.get("phase"),
                "trial_id": state.get("trial_id"),
                "param_hash": state.get("param_hash"),
                "stage": state.get("stage"),
                "event": state.get("event"),
                "ts": state.get("ts"),
            }
        )
    return trimmed


def _load_worker_states(crash_dir: str, session_id: Optional[str]) -> List[Dict[str, Any]]:
    if not crash_dir or not os.path.isdir(crash_dir):
        return []
    states: List[Dict[str, Any]] = []
    for path in glob.glob(os.path.join(crash_dir, "worker_*_current.json")):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                state = json.load(handle)
        except Exception:
            continue
        if session_id is not None and state.get("session_id") != session_id:
            continue
        states.append(state)
    return states


def _find_stalled_workers(
    crash_dir: str,
    timeout_s: Optional[float],
    session_id: Optional[str],
) -> List[Dict[str, Any]]:
    if timeout_s is None or timeout_s <= 0:
        return []
    now = time.time()
    stalled = []
    for state in _load_worker_states(crash_dir, session_id):
        ts = state.get("ts")
        if ts is None:
            continue
        try:
            age = now - float(ts)
        except (TypeError, ValueError):
            continue
        if age < timeout_s:
            continue
        if state.get("event") in {"trial_done", "quick_eval_done"}:
            continue
        stalled.append(
            {
                "pid": state.get("pid"),
                "phase": state.get("phase"),
                "trial_id": state.get("trial_id"),
                "param_hash": state.get("param_hash"),
                "stage": state.get("stage"),
                "event": state.get("event"),
                "ts": state.get("ts"),
            }
        )
    return stalled


def _log_worker_stall(crash_dir: str, phase: str, timeout_s: float, stalled: List[Dict[str, Any]]) -> None:
    if stalled:
        logger.warning(
            "Detected stalled worker(s) during %s (>%ss since last update): %s",
            phase,
            timeout_s,
            stalled,
        )
    else:
        logger.warning(
            "Detected stalled worker(s) during %s (>%ss since last update).",
            phase,
            timeout_s,
        )


def _log_pool_crash(
    crash_dir: str,
    phase: str,
    attempt: int,
    max_attempts: int,
    session_id: Optional[str],
) -> None:
    states = _collect_worker_states(crash_dir, session_id)
    if states:
        logger.warning(
            "Process pool crashed during %s (restart %s/%s). Last worker states: %s",
            phase,
            attempt,
            max_attempts,
            states,
        )
    else:
        logger.warning(
            "Process pool crashed during %s (restart %s/%s). No worker state files found.",
            phase,
            attempt,
            max_attempts,
        )


def _normalize_seed_params(seed_params: Iterable[Any], spaces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not seed_params:
        return []
    allowed = {space["key"] for space in spaces}
    normalized = []
    for entry in seed_params:
        if not isinstance(entry, dict):
            continue
        params = {k: v for k, v in entry.items() if k in allowed}
        if params:
            normalized.append(params)
    return normalized


def _truncate_config_for_quick_eval(config: Dict[str, Any], quick_cfg: Dict[str, Any]) -> Dict[str, Any]:
    backtest_cfg = config.get("backtest", {}) or {}
    start_date = backtest_cfg.get("start_date") or backtest_cfg.get("start")
    end_date = backtest_cfg.get("end_date") or backtest_cfg.get("end")
    if not start_date:
        return config
    try:
        start = pd.Timestamp(start_date).normalize()
        end = pd.Timestamp(end_date).normalize() if end_date else pd.Timestamp.utcnow().normalize()
    except Exception:
        return config
    days = list(pd.bdate_range(start=start, end=end))
    if len(days) < 2:
        return config
    quick_days = quick_cfg.get("train_days")
    if quick_days is not None:
        try:
            quick_days = int(quick_days)
        except (TypeError, ValueError):
            quick_days = None
    if quick_days is not None and quick_days > 1:
        idx = min(len(days) - 1, max(1, quick_days - 1))
    else:
        fraction = quick_cfg.get("train_fraction", 0.3)
        try:
            fraction = float(fraction)
        except (TypeError, ValueError):
            fraction = 0.3
        idx = max(1, int(len(days) * fraction) - 1)
    backtest_cfg = dict(backtest_cfg)
    backtest_cfg["end_date"] = days[idx].strftime("%Y-%m-%d")
    config = copy.deepcopy(config)
    config["backtest"] = backtest_cfg
    return config


def _quick_eval_score(
    base_config: Dict[str, Any],
    config_path: str,
    params: Dict[str, Any],
    spaces: List[Dict[str, Any]],
    objectives: List[Dict[str, Any]],
    metrics_registry: Dict[str, Any],
    quick_cfg: Dict[str, Any],
    prefetch_data: bool,
    read_only_cache: bool,
    timeout_s: Optional[float],
) -> Optional[float]:
    param_hash = _hash_params(params)
    stage_callback = _make_stage_callback("quick_eval", None, param_hash)
    _update_worker_state(
        "quick_eval_start",
        {"phase": "quick_eval", "param_hash": param_hash},
    )
    config = _apply_params_to_config(base_config, params, spaces)
    config = _truncate_config_for_quick_eval(config, quick_cfg)
    try:
        with _timeout_after(timeout_s):
            _, _, summary = run_backtest(
                config=config,
                config_path=config_path,
                run_dir=None,
                save_outputs=False,
                prefetch_data=prefetch_data,
                read_only_cache=read_only_cache,
                stage_callback=stage_callback,
            )
    except _TrialTimeout as exc:
        last_stage = _WORKER_CURRENT.get("stage")
        _log_worker_error(f"Timeout during quick-eval: {exc} (stage={last_stage})")
        _update_worker_state(
            "quick_eval_timeout",
            {
                "phase": "quick_eval",
                "param_hash": param_hash,
                "error": str(exc),
                "stage": last_stage,
            },
        )
        return None
    except MemoryError as exc:
        _log_worker_error(f"MemoryError during quick-eval: {exc}")
        _update_worker_state(
            "quick_eval_error",
            {"phase": "quick_eval", "param_hash": param_hash, "error": f"MemoryError: {exc}"},
        )
        return None
    except Exception as exc:
        _log_worker_error(f"Exception during quick-eval: {exc}")
        _update_worker_state(
            "quick_eval_error",
            {"phase": "quick_eval", "param_hash": param_hash, "error": str(exc)},
        )
        return None
    score, _ = compute_score(summary, objectives, metrics_registry)
    _update_worker_state(
        "quick_eval_done",
        {"phase": "quick_eval", "param_hash": param_hash, "score": score},
    )
    return score


def _drawdown_exceeds_cap(summary: Dict[str, Any], cap_pct: float | None) -> tuple[bool, float | None]:
    if cap_pct is None:
        return False, None
    try:
        cap = float(cap_pct)
    except (TypeError, ValueError):
        return False, None
    if cap <= 0:
        return False, None
    try:
        drawdown = float(summary.get("max_drawdown_pct"))
    except (TypeError, ValueError):
        return False, None
    if not math.isfinite(drawdown):
        return False, None
    return abs(drawdown) > cap, drawdown


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _normalize_storage(storage: Any) -> Optional[str]:
    if not storage:
        return None
    if isinstance(storage, str):
        storage = storage.strip()
        if not storage:
            return None
        if storage in {":memory:", "sqlite:///:memory:"}:
            return "sqlite:///:memory:"
        if "://" in storage:
            return storage
        path = storage
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return f"sqlite:///{path}"
    return None


def _build_optuna_storage(opt_cfg: Dict[str, Any]) -> Any:
    storage_url = _normalize_storage(opt_cfg.get("storage"))
    if storage_url is None:
        return None
    if not storage_url.startswith("sqlite://"):
        return storage_url
    try:
        timeout = float(opt_cfg.get("sqlite_timeout", 60))
    except (TypeError, ValueError):
        timeout = 60.0
    engine_kwargs = {"connect_args": {"timeout": timeout}}
    storage = optuna.storages.RDBStorage(storage_url, engine_kwargs=engine_kwargs)
    journal_mode = opt_cfg.get("sqlite_journal_mode", "wal")
    synchronous = opt_cfg.get("sqlite_synchronous", "normal")
    try:
        with storage.engine.connect() as connection:
            if journal_mode:
                connection.exec_driver_sql(f"PRAGMA journal_mode={journal_mode}")
            if synchronous:
                connection.exec_driver_sql(f"PRAGMA synchronous={synchronous}")
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("Failed to set SQLite pragmas for Optuna storage: %s", exc)
    return storage


def _load_journal(journal_path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(journal_path):
        return []
    records = []
    with open(journal_path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                continue
    return records


def _append_journal(journal_path: str, record: Dict[str, Any]) -> None:
    with open(journal_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=str) + "\n")


def _write_results_csv(records: List[Dict[str, Any]], path: str) -> None:
    if not records:
        return
    import csv

    fieldnames = sorted({k for r in records for k in r.keys()})
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in records:
            writer.writerow(row)


def _range_values(range_cfg: Dict[str, Any]) -> List[Any]:
    if not range_cfg:
        return []
    if "values" in range_cfg:
        return list(range_cfg.get("values") or [])
    start = range_cfg.get("min")
    stop = range_cfg.get("max")
    step = range_cfg.get("step")
    if start is None or stop is None or step is None:
        return []
    values = []
    current = start
    while current <= stop + 1e-12:
        values.append(current)
        current = current + step
    return values


def _is_simple_optuna_choice(value: Any) -> bool:
    return isinstance(value, (type(None), bool, int, float, str))


def _encode_optuna_choice(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except TypeError:
        return str(value)


def _quantize_float_to_step(value: float, low: float, high: float, step: float) -> float:
    try:
        val = Decimal(str(value))
        low_d = Decimal(str(low))
        high_d = Decimal(str(high))
        step_d = Decimal(str(step))
    except Exception:
        return float(min(max(value, low), high))
    if step_d > 0:
        steps = (val - low_d) / step_d
        steps = steps.to_integral_value(rounding=ROUND_HALF_UP)
        val = low_d + steps * step_d
    if val < low_d:
        val = low_d
    if val > high_d:
        val = high_d
    places = max(-step_d.as_tuple().exponent, 0)
    if places:
        quant = Decimal(1).scaleb(-places)
        val = val.quantize(quant)
    return float(val)


def _coerce_int_to_step(value: int, low: int, high: int, step: int) -> int:
    if step <= 0:
        return int(min(max(value, low), high))
    steps = int(round((value - low) / step))
    value = low + steps * step
    if value < low:
        value = low
    if value > high:
        value = high
    return int(value)


def _build_optuna_distributions(spaces: List[Dict[str, Any]]) -> Dict[str, Any]:
    if optuna is None:
        return {}
    distributions: Dict[str, Any] = {}
    for space in spaces:
        key = space["key"]
        values = space.get("values")
        optuna_values = None
        optuna_decode = None
        ptype = space.get("type", "float")
        range_cfg = space.get("range") or {}
        if values:
            if any(not _is_simple_optuna_choice(v) for v in values):
                optuna_values = [_encode_optuna_choice(v) for v in values]
                optuna_decode = dict(zip(optuna_values, values))
            else:
                optuna_values = list(values)
            space["optuna_values"] = optuna_values
            space["optuna_decode"] = optuna_decode
            distributions[key] = optuna.distributions.CategoricalDistribution(optuna_values)
            continue
        if ptype == "bool":
            distributions[key] = optuna.distributions.CategoricalDistribution([True, False])
            continue
        rmin = range_cfg.get("min")
        rmax = range_cfg.get("max")
        step = range_cfg.get("step")
        if rmin is None or rmax is None:
            logger.warning("Optuna distribution skipped for %s: missing range.", key)
            continue
        if ptype == "int":
            distributions[key] = optuna.distributions.IntDistribution(
                int(rmin),
                int(rmax),
                step=int(step) if step else 1,
            )
        else:
            distributions[key] = optuna.distributions.FloatDistribution(
                float(rmin),
                float(rmax),
                step=float(step) if step else None,
            )
    return distributions


def _prepare_optuna_params(
    params: Dict[str, Any],
    distributions: Dict[str, Any],
    spaces: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if set(params.keys()) != set(distributions.keys()):
        return None
    prepared: Dict[str, Any] = {}
    for key, dist in distributions.items():
        value = params.get(key)
        space = spaces.get(key, {})
        if space.get("optuna_decode") is not None:
            value = _encode_optuna_choice(value)
        if isinstance(dist, optuna.distributions.IntDistribution):
            try:
                intval = int(value)
            except (TypeError, ValueError):
                return None
            prepared[key] = _coerce_int_to_step(intval, dist.low, dist.high, dist.step or 1)
        elif isinstance(dist, optuna.distributions.FloatDistribution):
            try:
                fval = float(value)
            except (TypeError, ValueError):
                return None
            if dist.step is not None:
                prepared[key] = _quantize_float_to_step(fval, dist.low, dist.high, dist.step)
            else:
                prepared[key] = float(min(max(fval, dist.low), dist.high))
        else:
            prepared[key] = value
        if hasattr(dist, "choices") and prepared[key] not in dist.choices:
            return None
    return prepared


def _inject_random_trials(
    study: Any,
    records: List[Dict[str, Any]],
    distributions: Dict[str, Any],
    spaces: List[Dict[str, Any]],
) -> int:
    if not records or not distributions:
        return 0
    existing = {_hash_params(t.params) for t in study.trials if t.params}
    space_map = {space["key"]: space for space in spaces}
    added = 0
    for record in records:
        if record.get("phase") not in {"random", "seed"} or record.get("status") != "ok":
            continue
        params = record.get("params") or {}
        prepared = _prepare_optuna_params(params, distributions, space_map)
        if prepared is None:
            continue
        key = _hash_params(prepared)
        if key in existing:
            continue
        score = record.get("score")
        if score is None:
            continue
        try:
            value = float(score)
        except (TypeError, ValueError):
            continue
        trial = optuna.trial.create_trial(
            params=prepared,
            distributions=distributions,
            value=value,
            state=optuna.trial.TrialState.COMPLETE,
        )
        study.add_trial(trial)
        existing.add(key)
        added += 1
    return added


def build_param_space(params_cfg: Dict[str, Any], registry: Dict[str, Any]) -> List[Dict[str, Any]]:
    spaces: List[Dict[str, Any]] = []
    for key, spec in (params_cfg or {}).items():
        if spec is None:
            spec = {}
        if isinstance(spec, list):
            spec = {"values": spec}
        enabled = bool(spec.get("enabled", True))
        if not enabled:
            continue
        reg_entry = registry.get(key, {})
        path = spec.get("path") or reg_entry.get("path")
        if not path:
            raise ValueError(f"Param '{key}' is missing a registry path")
        param_type = spec.get("type") or reg_entry.get("type") or "float"
        values = spec.get("values")
        range_cfg = spec.get("range")
        if values is None and range_cfg is None and reg_entry.get("range") is not None:
            range_cfg = reg_entry.get("range")
        spaces.append(
            {
                "key": key,
                "path": path,
                "type": param_type,
                "values": values,
                "range": range_cfg,
            }
        )
    if not spaces:
        raise ValueError("No tunable params found")
    return spaces


def _sample_random_value(space: Dict[str, Any], rng) -> Any:
    values = space.get("values")
    if values:
        return rng.choice(list(values))
    range_cfg = space.get("range") or {}
    param_type = space.get("type", "float")
    rmin = range_cfg.get("min")
    rmax = range_cfg.get("max")
    if rmin is None or rmax is None:
        raise ValueError(f"Param {space['key']} missing range values")
    if param_type == "int":
        return rng.randint(int(rmin), int(rmax))
    if param_type == "bool":
        return rng.choice([True, False])
    step = range_cfg.get("step")
    if step:
        values = _range_values({"min": rmin, "max": rmax, "step": step})
        return rng.choice(values)
    return rng.uniform(float(rmin), float(rmax))


def _generate_random_params(
    spaces: List[Dict[str, Any]],
    n: int,
    rng,
    used_hashes: set,
    max_attempts: int = 10000,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    attempts = 0
    while len(results) < n and attempts < max_attempts:
        attempts += 1
        params = {space["key"]: _sample_random_value(space, rng) for space in spaces}
        phash = _hash_params(params)
        if phash in used_hashes:
            continue
        used_hashes.add(phash)
        results.append(params)
    if len(results) < n:
        raise RuntimeError("Could not generate enough unique random parameter sets.")
    return results


def _apply_params_to_config(
    base_config: Dict[str, Any],
    params: Dict[str, Any],
    spaces: List[Dict[str, Any]],
) -> Dict[str, Any]:
    config = copy.deepcopy(base_config)
    path_map = {space["key"]: space["path"] for space in spaces}
    for key, value in params.items():
        path = path_map.get(key)
        if not path:
            raise ValueError(f"Missing registry path for param {key}")
        apply_param(config, path, value)
    return config


def _run_trial(
    trial_id: int,
    phase: str,
    base_config: Dict[str, Any],
    config_path: str,
    params: Dict[str, Any],
    spaces: List[Dict[str, Any]],
    objectives: List[Dict[str, Any]],
    metrics_registry: Dict[str, Any],
    save_outputs: bool,
    run_dir: Optional[str],
    drawdown_cap_pct: float | None,
    prefetch_data: bool,
    read_only_cache: bool,
    timeout_s: Optional[float],
) -> Dict[str, Any]:
    started = time.time()
    param_hash = _hash_params(params)
    stage_callback = _make_stage_callback(phase, trial_id, param_hash)
    record: Dict[str, Any] = {
        "trial_id": trial_id,
        "phase": phase,
        "params": params,
        "param_hash": param_hash,
        "status": "ok",
    }
    _update_worker_state(
        "trial_start",
        {"phase": phase, "trial_id": trial_id, "param_hash": param_hash},
    )
    try:
        config = _apply_params_to_config(base_config, params, spaces)
        with _timeout_after(timeout_s):
            _, _, summary = run_backtest(
                config=config,
                config_path=config_path,
                run_dir=run_dir,
                save_outputs=save_outputs,
                prefetch_data=prefetch_data,
                read_only_cache=read_only_cache,
                stage_callback=stage_callback,
            )
        score, metric_values = compute_score(summary, objectives, metrics_registry)
        pruned, drawdown = _drawdown_exceeds_cap(summary, drawdown_cap_pct)
        if pruned:
            record.update(
                {
                    "status": "pruned",
                    "error": f"max_drawdown_pct {drawdown:.4f} exceeds cap {float(drawdown_cap_pct):.2f}",
                    "metrics": metric_values,
                    "summary": summary,
                }
            )
        else:
            record.update(
                {
                    "score": score,
                    "metrics": metric_values,
                    "summary": summary,
                }
            )
    except _TrialTimeout as exc:
        last_stage = _WORKER_CURRENT.get("stage")
        _log_worker_error(f"Timeout in trial {trial_id}: {exc} (stage={last_stage})")
        record.update(
            {"status": "failed", "error": f"Timeout: {exc} (stage={last_stage})"}
        )
    except MemoryError as exc:
        _log_worker_error(f"MemoryError in trial {trial_id}: {exc}")
        record.update({"status": "failed", "error": f"MemoryError: {exc}"})
    except Exception as exc:
        record.update({"status": "failed", "error": str(exc)})
        _log_worker_error(f"Exception in trial {trial_id}: {exc}")
    record["duration_s"] = round(time.time() - started, 4)
    _update_worker_state(
        "trial_done",
        {
            "phase": phase,
            "trial_id": trial_id,
            "param_hash": record.get("param_hash"),
            "status": record.get("status"),
        },
    )
    return record


def run_hybrid_search(
    base_config: Dict[str, Any],
    base_config_path: str,
    spaces: List[Dict[str, Any]],
    objectives: List[Dict[str, Any]],
    metrics_registry: Dict[str, Any],
    search_cfg: Dict[str, Any],
    output_cfg: Dict[str, Any],
) -> str:
    results_dir = os.path.abspath(output_cfg.get("results_dir", "results/grid_search"))
    _ensure_dir(results_dir)
    journal_path = os.path.join(results_dir, "journal.jsonl")
    results_csv = os.path.join(results_dir, "results.csv")
    crash_dir = os.path.join(results_dir, "crash_logs")
    session_id = uuid.uuid4().hex

    records = _load_journal(journal_path) if search_cfg.get("resume", True) else []
    used_hashes = {r.get("param_hash") for r in records if r.get("param_hash")}
    completed_seed = sum(
        1
        for r in records
        if r.get("phase") == "seed" and r.get("status") in {"ok", "pruned"}
    )
    completed_random = sum(
        1
        for r in records
        if r.get("phase") == "random" and r.get("status") in {"ok", "pruned"}
    )
    completed_optuna = sum(
        1
        for r in records
        if r.get("phase") == "optuna" and r.get("status") in {"ok", "pruned"}
    )

    trial_id = max([r.get("trial_id", 0) for r in records], default=0) + 1
    save_runs = bool(output_cfg.get("save_runs", False))
    rng = __import__("random").Random(search_cfg.get("seed", 0))

    drawdown_cap_pct = search_cfg.get("drawdown_cap_pct")
    prefetch_enabled = bool(search_cfg.get("prefetch_data", True))
    worker_prefetch = False
    worker_read_only_cache = False
    if prefetch_enabled:
        try:
            prefetched = prefetch_cache(base_config, base_config_path, prefetch_data=True)
        except Exception as exc:
            logger.warning("Prefetch failed; workers will download as needed: %s", exc)
            prefetched = False
        worker_read_only_cache = bool(prefetched)
        if worker_read_only_cache:
            logger.info("Prefetch complete; workers will use read-only cache.")
            worker_prefetch = True
    try:
        trial_timeout_s = float(search_cfg.get("trial_timeout_s", 420))
    except (TypeError, ValueError):
        trial_timeout_s = 420.0
    if trial_timeout_s <= 0:
        trial_timeout_s = None
    try:
        worker_stall_timeout_s = float(search_cfg.get("worker_stall_timeout_s", 600))
    except (TypeError, ValueError):
        worker_stall_timeout_s = 600.0
    if worker_stall_timeout_s <= 0:
        worker_stall_timeout_s = None
    try:
        worker_stall_check_s = float(search_cfg.get("worker_stall_check_s", 30))
    except (TypeError, ValueError):
        worker_stall_check_s = 30.0
    if worker_stall_check_s <= 0:
        worker_stall_check_s = 30.0
    worker_stall_kill = bool(search_cfg.get("worker_stall_kill", True))
    try:
        pool_restart_max = int(search_cfg.get("pool_restart_max", 2))
    except (TypeError, ValueError):
        pool_restart_max = 2
    pool_restart_max = max(pool_restart_max, 0)
    try:
        pool_restart_delay_s = float(search_cfg.get("pool_restart_delay_s", 2))
    except (TypeError, ValueError):
        pool_restart_delay_s = 2.0
    if pool_restart_delay_s < 0:
        pool_restart_delay_s = 0.0

    opt_cfg = search_cfg.get("optuna", {}) or {}
    optuna_enabled = bool(opt_cfg.get("enabled", True)) and optuna is not None

    seed_params = _normalize_seed_params(search_cfg.get("seed_params") or [], spaces)
    seed_max = search_cfg.get("seed_max")
    if seed_max is not None:
        try:
            seed_params = seed_params[: int(seed_max)]
        except (TypeError, ValueError):
            pass
    seed_params = [p for p in seed_params if _hash_params(p) not in used_hashes]
    reserved_hashes = {_hash_params(params) for params in seed_params}

    if seed_params:
        seed_workers = int(search_cfg.get("seed_parallel_workers", search_cfg.get("parallel_workers", 1)))
        seed_jobs: List[Tuple[int, Dict[str, Any], Optional[str]]] = []
        for params in seed_params:
            run_dir = None
            if save_runs:
                run_dir = os.path.join(results_dir, f"trial_{trial_id}")
            seed_jobs.append((trial_id, params, run_dir))
            trial_id += 1

        remaining_jobs = list(seed_jobs)
        completed_ids: set[int] = set()
        restart_count = 0
        while remaining_jobs:
            broken = False
            with ProcessPoolExecutor(
                max_workers=seed_workers,
                initializer=_worker_init,
                initargs=(crash_dir, session_id),
            ) as executor:
                future_map: Dict[Any, int] = {}
                for job_trial_id, params, run_dir in remaining_jobs:
                    future = executor.submit(
                        _run_trial,
                        job_trial_id,
                        "seed",
                        base_config,
                        base_config_path,
                        params,
                        spaces,
                        objectives,
                        metrics_registry,
                        save_runs,
                        run_dir,
                        drawdown_cap_pct,
                        worker_prefetch,
                        worker_read_only_cache,
                        trial_timeout_s,
                    )
                    future_map[future] = job_trial_id
                pending = set(future_map.keys())
                while pending:
                    done, pending = wait(pending, timeout=worker_stall_check_s, return_when=FIRST_COMPLETED)
                    if not done:
                        stalled = _find_stalled_workers(crash_dir, worker_stall_timeout_s, session_id)
                        if stalled:
                            _log_worker_stall(crash_dir, "seed", worker_stall_timeout_s or 0, stalled)
                            if worker_stall_kill:
                                for state in stalled:
                                    pid = state.get("pid")
                                    if pid:
                                        try:
                                            os.kill(int(pid), signal.SIGKILL)
                                        except Exception:
                                            pass
                            restart_count += 1
                            broken = True
                            break
                        continue
                    for future in done:
                        try:
                            record = future.result()
                        except BrokenProcessPool:
                            restart_count += 1
                            _log_pool_crash(crash_dir, "seed", restart_count, pool_restart_max, session_id)
                            broken = True
                            pending = set()
                            break
                        completed_ids.add(int(record.get("trial_id", -1)))
                        records.append(record)
                        used_hashes.add(record.get("param_hash"))
                        _append_journal(journal_path, record)
                        _write_results_csv(records, results_csv)
            remaining_jobs = [job for job in remaining_jobs if job[0] not in completed_ids]
            if not broken:
                break
            if restart_count > pool_restart_max:
                logger.warning(
                    "Process pool crashed too many times during seed trials; falling back to serial. "
                    "Check %s for worker crash logs.",
                    crash_dir,
                )
                break
            if pool_restart_delay_s:
                time.sleep(pool_restart_delay_s)
        if remaining_jobs:
            for job_trial_id, params, run_dir in remaining_jobs:
                if job_trial_id in completed_ids:
                    continue
                record = _run_trial(
                    job_trial_id,
                    "seed",
                    base_config,
                    base_config_path,
                    params,
                    spaces,
                    objectives,
                    metrics_registry,
                    save_runs,
                    run_dir,
                    drawdown_cap_pct,
                    worker_prefetch,
                    worker_read_only_cache,
                    trial_timeout_s,
                )
                records.append(record)
                used_hashes.add(record.get("param_hash"))
                _append_journal(journal_path, record)
                _write_results_csv(records, results_csv)
        completed_seed = sum(
            1
            for r in records
            if r.get("phase") == "seed" and r.get("status") in {"ok", "pruned"}
        )

    total_trials = int(search_cfg.get("total_trials", 0))
    random_pct = float(search_cfg.get("random_pct", 0.0))
    total_trials = max(0, total_trials - completed_seed)
    random_trials = int(round(total_trials * random_pct))
    optuna_trials = max(0, total_trials - random_trials)

    random_remaining = max(0, random_trials - completed_random)
    optuna_remaining = max(0, optuna_trials - completed_optuna) if optuna_enabled else 0

    try:
        progress_every = int(search_cfg.get("progress_every", 20))
    except (TypeError, ValueError):
        progress_every = 20
    total_target = random_remaining + optuna_remaining
    progress_state = {"done": 0}
    progress_lock = threading.Lock()

    def _mark_progress(count: int = 1) -> None:
        if progress_every <= 0 or total_target <= 0:
            return
        with progress_lock:
            progress_state["done"] += count
            done = progress_state["done"]
        if done % progress_every == 0 or done >= total_target:
            logger.info("---- Progress: %s/%s trials completed ----", done, total_target)

    quick_cfg = search_cfg.get("quick_eval", {}) or {}
    quick_enabled = bool(quick_cfg.get("enabled", False))
    quick_keep_pct = float(quick_cfg.get("keep_pct", 0.3))
    quick_min_score = quick_cfg.get("min_score")
    record_quick_pruned = bool(quick_cfg.get("record_pruned", False))
    quick_timeout_s = quick_cfg.get("timeout_s", trial_timeout_s)
    try:
        quick_timeout_s = float(quick_timeout_s) if quick_timeout_s is not None else None
    except (TypeError, ValueError):
        quick_timeout_s = trial_timeout_s
    if quick_timeout_s is not None and quick_timeout_s <= 0:
        quick_timeout_s = None

    if random_remaining:
        param_sets = _generate_random_params(spaces, random_remaining, rng, set(used_hashes) | reserved_hashes)
        workers = int(search_cfg.get("parallel_workers", 1))

        if quick_enabled:
            quick_scores: List[Tuple[float, Dict[str, Any]]] = []
            quick_items = [(_hash_params(params), params) for params in param_sets]
            remaining = list(quick_items)
            completed: set[str] = set()
            restart_count = 0
            while remaining:
                broken = False
                with ProcessPoolExecutor(
                    max_workers=workers,
                    initializer=_worker_init,
                    initargs=(crash_dir, session_id),
                ) as executor:
                    future_map: Dict[Any, Tuple[str, Dict[str, Any]]] = {}
                    for phash, params in remaining:
                        future = executor.submit(
                            _quick_eval_score,
                            base_config,
                            base_config_path,
                            params,
                            spaces,
                            objectives,
                            metrics_registry,
                            quick_cfg,
                            worker_prefetch,
                            worker_read_only_cache,
                            quick_timeout_s,
                        )
                        future_map[future] = (phash, params)
                    pending = set(future_map.keys())
                    while pending:
                        done, pending = wait(pending, timeout=worker_stall_check_s, return_when=FIRST_COMPLETED)
                        if not done:
                            stalled = _find_stalled_workers(crash_dir, worker_stall_timeout_s, session_id)
                            if stalled:
                                _log_worker_stall(crash_dir, "quick-eval", worker_stall_timeout_s or 0, stalled)
                                if worker_stall_kill:
                                    for state in stalled:
                                        pid = state.get("pid")
                                        if pid:
                                            try:
                                                os.kill(int(pid), signal.SIGKILL)
                                            except Exception:
                                                pass
                                restart_count += 1
                                broken = True
                                break
                            continue
                        for future in done:
                            phash, params = future_map[future]
                            try:
                                score = future.result()
                            except BrokenProcessPool:
                                restart_count += 1
                                _log_pool_crash(crash_dir, "quick-eval", restart_count, pool_restart_max, session_id)
                                broken = True
                                pending = set()
                                break
                            except Exception:
                                score = None
                            completed.add(phash)
                            _mark_progress(1)
                            if score is None:
                                continue
                            quick_scores.append((float(score), params))
                remaining = [(phash, params) for phash, params in remaining if phash not in completed]
                if not broken:
                    break
                if restart_count > pool_restart_max:
                    logger.warning(
                        "Process pool crashed too many times during quick-eval; retrying remaining trials serially. "
                        "Check %s for worker crash logs.",
                        crash_dir,
                    )
                    break
                if pool_restart_delay_s:
                    time.sleep(pool_restart_delay_s)
            if remaining:
                for _phash, params in remaining:
                    score = _quick_eval_score(
                        base_config,
                        base_config_path,
                        params,
                        spaces,
                        objectives,
                        metrics_registry,
                        quick_cfg,
                        worker_prefetch,
                        worker_read_only_cache,
                        quick_timeout_s,
                    )
                    _mark_progress(1)
                    if score is None:
                        continue
                    quick_scores.append((float(score), params))

            quick_scores.sort(key=lambda item: item[0], reverse=True)
            keep_count = max(1, int(len(quick_scores) * max(min(quick_keep_pct, 1.0), 0.0)))
            param_sets = [params for score, params in quick_scores[:keep_count]]
            if quick_min_score is not None:
                try:
                    min_score = float(quick_min_score)
                except (TypeError, ValueError):
                    min_score = None
                if min_score is not None:
                    param_sets = [
                        params for score, params in quick_scores[:keep_count] if score >= min_score
                    ]
            if not param_sets:
                logger.warning("Quick-eval pruned all random trials; skipping random phase.")
            if record_quick_pruned and quick_scores:
                kept_hashes = {_hash_params(params) for params in param_sets}
                for score, params in quick_scores:
                    phash = _hash_params(params)
                    if phash in kept_hashes:
                        continue
                    record = {
                        "trial_id": trial_id,
                        "phase": "quick_eval",
                        "status": "pruned",
                        "params": params,
                        "param_hash": phash,
                        "score": score,
                        "error": "quick-eval filtered",
                    }
                    trial_id += 1
                    records.append(record)
                    used_hashes.add(phash)
                    _append_journal(journal_path, record)
                _write_results_csv(records, results_csv)
        else:
            random_jobs: List[Tuple[int, Dict[str, Any], Optional[str]]] = []
            for params in param_sets:
                run_dir = None
                if save_runs:
                    run_dir = os.path.join(results_dir, f"trial_{trial_id}")
                random_jobs.append((trial_id, params, run_dir))
                trial_id += 1

            remaining_jobs = list(random_jobs)
            completed_ids: set[int] = set()
            restart_count = 0
            while remaining_jobs:
                broken = False
                with ProcessPoolExecutor(
                    max_workers=workers,
                    initializer=_worker_init,
                    initargs=(crash_dir, session_id),
                ) as executor:
                    future_map: Dict[Any, int] = {}
                    for job_trial_id, params, run_dir in remaining_jobs:
                        future = executor.submit(
                            _run_trial,
                            job_trial_id,
                            "random",
                            base_config,
                            base_config_path,
                            params,
                            spaces,
                            objectives,
                            metrics_registry,
                            save_runs,
                            run_dir,
                            drawdown_cap_pct,
                            worker_prefetch,
                            worker_read_only_cache,
                            trial_timeout_s,
                        )
                        future_map[future] = job_trial_id
                    pending = set(future_map.keys())
                    while pending:
                        done, pending = wait(pending, timeout=worker_stall_check_s, return_when=FIRST_COMPLETED)
                        if not done:
                            stalled = _find_stalled_workers(crash_dir, worker_stall_timeout_s, session_id)
                            if stalled:
                                _log_worker_stall(crash_dir, "random", worker_stall_timeout_s or 0, stalled)
                                if worker_stall_kill:
                                    for state in stalled:
                                        pid = state.get("pid")
                                        if pid:
                                            try:
                                                os.kill(int(pid), signal.SIGKILL)
                                            except Exception:
                                                pass
                                restart_count += 1
                                broken = True
                                break
                            continue
                        for future in done:
                            try:
                                record = future.result()
                            except BrokenProcessPool:
                                restart_count += 1
                                _log_pool_crash(crash_dir, "random", restart_count, pool_restart_max, session_id)
                                broken = True
                                pending = set()
                                break
                            completed_ids.add(int(record.get("trial_id", -1)))
                            records.append(record)
                            used_hashes.add(record.get("param_hash"))
                            _append_journal(journal_path, record)
                            _write_results_csv(records, results_csv)
                            if not quick_enabled:
                                _mark_progress(1)
                remaining_jobs = [job for job in remaining_jobs if job[0] not in completed_ids]
                if not broken:
                    break
                if restart_count > pool_restart_max:
                    logger.warning(
                        "Process pool crashed too many times during random trials; retrying remaining trials serially. "
                        "Check %s for worker crash logs.",
                        crash_dir,
                    )
                    break
                if pool_restart_delay_s:
                    time.sleep(pool_restart_delay_s)
            if remaining_jobs:
                for job_trial_id, params, run_dir in remaining_jobs:
                    if job_trial_id in completed_ids:
                        continue
                    record = _run_trial(
                        job_trial_id,
                        "random",
                        base_config,
                        base_config_path,
                        params,
                        spaces,
                        objectives,
                        metrics_registry,
                        save_runs,
                        run_dir,
                        drawdown_cap_pct,
                        worker_prefetch,
                        worker_read_only_cache,
                        trial_timeout_s,
                    )
                    records.append(record)
                    used_hashes.add(record.get("param_hash"))
                    _append_journal(journal_path, record)
                    _write_results_csv(records, results_csv)
                    if not quick_enabled:
                        _mark_progress(1)

    if optuna_remaining <= 0:
        return results_dir

    if not optuna_enabled:
        return results_dir

    sampler_name = opt_cfg.get("sampler", "tpe")
    storage = _build_optuna_storage(opt_cfg)
    n_jobs = int(opt_cfg.get("n_jobs", 1))
    if sampler_name == "tpe":
        try:
            n_startup = int(opt_cfg.get("n_startup_trials", 10))
        except (TypeError, ValueError):
            n_startup = 10
        sampler = optuna.samplers.TPESampler(seed=search_cfg.get("seed", 0), n_startup_trials=n_startup)
    else:
        sampler = optuna.samplers.RandomSampler(seed=search_cfg.get("seed", 0))
    pruner_cfg = opt_cfg.get("pruner", {}) or {}
    pruner = None
    pruner_type = (pruner_cfg.get("type") or "").lower()
    if pruner_type in {"median", "medianpruner"}:
        pruner = optuna.pruners.MedianPruner(
            n_startup_trials=int(pruner_cfg.get("n_startup_trials", 10)),
            n_warmup_steps=int(pruner_cfg.get("n_warmup_steps", 0)),
            interval_steps=int(pruner_cfg.get("interval_steps", 1)),
        )
    elif pruner_type in {"sha", "successivehalving"}:
        pruner = optuna.pruners.SuccessiveHalvingPruner(
            min_resource=int(pruner_cfg.get("min_resource", 1)),
            reduction_factor=int(pruner_cfg.get("reduction_factor", 3)),
            min_early_stopping_rate=int(pruner_cfg.get("min_early_stopping_rate", 0)),
        )
    elif pruner_type in {"none", "nop"}:
        pruner = optuna.pruners.NopPruner()
    study = optuna.create_study(
        direction="maximize",
        sampler=sampler,
        pruner=pruner,
        storage=storage,
        load_if_exists=bool(storage),
    )
    distributions = _build_optuna_distributions(spaces)
    space_map = {space["key"]: space for space in spaces}
    if seed_params:
        for params in seed_params:
            prepared = _prepare_optuna_params(params, distributions, space_map)
            if prepared is None:
                continue
            if _hash_params(prepared) in used_hashes:
                continue
            study.enqueue_trial(prepared)
    injected = _inject_random_trials(study, records, distributions, spaces)
    if injected:
        logger.info("Injected %s random trials into Optuna study.", injected)
    lock = threading.Lock()
    duplicate_prunes = 0
    duplicate_prune_limit = int(search_cfg.get("duplicate_prune_limit", 10))

    def objective(trial) -> float:
        nonlocal trial_id
        nonlocal duplicate_prunes
        marked = False

        def _mark_once() -> None:
            nonlocal marked
            if marked:
                return
            _mark_progress(1)
            marked = True

        params = {}
        for space in spaces:
            key = space["key"]
            values = space.get("optuna_values") or space.get("values")
            if values:
                choice = trial.suggest_categorical(key, list(values))
                decode = space.get("optuna_decode")
                params[key] = decode.get(choice, choice) if decode else choice
                continue
            range_cfg = space.get("range") or {}
            rmin = range_cfg.get("min")
            rmax = range_cfg.get("max")
            step = range_cfg.get("step")
            ptype = space.get("type", "float")
            if ptype == "int":
                params[key] = trial.suggest_int(key, int(rmin), int(rmax), step=int(step) if step else 1)
            else:
                params[key] = trial.suggest_float(key, float(rmin), float(rmax), step=float(step) if step else None)

        phash = _hash_params(params)
        with lock:
            if phash in used_hashes:
                duplicate_prunes += 1
                if duplicate_prunes >= duplicate_prune_limit:
                    study.stop()
                _mark_once()
                raise optuna.TrialPruned("duplicate params")
            used_hashes.add(phash)
            duplicate_prunes = 0
            local_id = trial_id
            trial_id += 1

        if quick_enabled:
            quick_score = _quick_eval_score(
                base_config,
                base_config_path,
                params,
                spaces,
                objectives,
                metrics_registry,
                quick_cfg,
                worker_prefetch,
                worker_read_only_cache,
                quick_timeout_s,
            )
            if quick_score is None:
                quick_score = -1e12
            trial.report(float(quick_score), step=0)
            if quick_min_score is not None:
                try:
                    min_score = float(quick_min_score)
                except (TypeError, ValueError):
                    min_score = None
                if min_score is not None and float(quick_score) < min_score:
                    if record_quick_pruned:
                        record = {
                            "trial_id": local_id,
                            "phase": "quick_eval",
                            "status": "pruned",
                            "params": params,
                            "param_hash": phash,
                            "score": float(quick_score),
                            "error": "quick-eval below min_score",
                        }
                        with lock:
                            records.append(record)
                            _append_journal(journal_path, record)
                            _write_results_csv(records, results_csv)
                    _mark_once()
                    raise optuna.TrialPruned("quick-eval below min_score")
            if trial.should_prune():
                if record_quick_pruned:
                    record = {
                        "trial_id": local_id,
                        "phase": "quick_eval",
                        "status": "pruned",
                        "params": params,
                        "param_hash": phash,
                        "score": float(quick_score),
                        "error": "quick-eval pruned",
                    }
                    with lock:
                        records.append(record)
                        _append_journal(journal_path, record)
                        _write_results_csv(records, results_csv)
                _mark_once()
                raise optuna.TrialPruned("quick-eval pruned")
        run_dir = None
        if save_runs:
            run_dir = os.path.join(results_dir, f"trial_{local_id}")
        record = _run_trial(
            local_id,
            "optuna",
            base_config,
            base_config_path,
            params,
            spaces,
            objectives,
            metrics_registry,
            save_runs,
            run_dir,
            drawdown_cap_pct,
            worker_prefetch,
            worker_read_only_cache,
            trial_timeout_s,
        )
        with lock:
            records.append(record)
            _append_journal(journal_path, record)
            _write_results_csv(records, results_csv)
        if record.get("status") == "pruned":
            _mark_once()
            raise optuna.TrialPruned(record.get("error") or "drawdown cap")
        _mark_once()
        return float(record.get("score") or -1e12)

    if optuna_remaining > 0:
        study.optimize(objective, n_trials=optuna_remaining, n_jobs=max(1, n_jobs))
    return results_dir
