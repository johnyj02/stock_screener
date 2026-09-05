from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _default_runs_root() -> Path:
    here = Path(__file__).resolve()
    try:
        repo_root = here.parents[4]
    except IndexError:
        repo_root = here.parents[2]
    return repo_root / "results"


RUNS_ROOT = Path(os.getenv("TRADE_JOURNAL_RUNS_ROOT", str(_default_runs_root()))).expanduser().resolve()
CACHE_ENABLED = os.getenv("TRADE_JOURNAL_CACHE_ENABLED", "1") == "1"
CACHE_MAX_RUNS = int(os.getenv("TRADE_JOURNAL_CACHE_MAX_RUNS", "5"))


@dataclass(frozen=True)
class ApiSettings:
    runs_root: Path = RUNS_ROOT
    cache_enabled: bool = CACHE_ENABLED
    cache_max_runs: int = CACHE_MAX_RUNS


SETTINGS = ApiSettings()
