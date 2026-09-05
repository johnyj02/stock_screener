from __future__ import annotations

from trade_journal_viz.server.app.core.cache import DatasetCache
from trade_journal_viz.server.app.core.dataset import DatasetProvider
from trade_journal_viz.server.app.settings import SETTINGS

_cache = DatasetCache(max_runs=SETTINGS.cache_max_runs) if SETTINGS.cache_enabled else None
_provider = DatasetProvider(runs_root=SETTINGS.runs_root, cache=_cache)


def get_provider() -> DatasetProvider:
    return _provider
