from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trade_journal_viz.server.app.core.dataset import RunDataset


class DatasetCache:
    def __init__(self, max_runs: int = 5) -> None:
        self.max_runs = max_runs
        self._items: OrderedDict[str, RunDataset] = OrderedDict()

    def get(self, run_id: str) -> RunDataset | None:
        dataset = self._items.get(run_id)
        if dataset is None:
            return None
        self._items.move_to_end(run_id)
        return dataset

    def set(self, run_id: str, dataset: RunDataset) -> None:
        if run_id in self._items:
            self._items.move_to_end(run_id)
        self._items[run_id] = dataset
        if len(self._items) > self.max_runs:
            self._items.popitem(last=False)
