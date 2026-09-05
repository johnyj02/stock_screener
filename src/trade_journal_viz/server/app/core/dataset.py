from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from trade_journal_viz.server.app.core.cache import DatasetCache
from trade_journal_viz.server.app.schemas.columns import (
    DATE_COLUMNS,
    MIN_REQUIRED_FILES,
    REQUIRED_COLUMNS,
    TABLE_FILES,
)


class DatasetError(RuntimeError):
    pass


@dataclass
class RunDataset:
    run_id: str
    run_path: Path
    tables: Dict[str, pd.DataFrame] = field(default_factory=dict)

    def load(self) -> "RunDataset":
        missing_required = [
            filename for filename in MIN_REQUIRED_FILES if not (self.run_path / filename).exists()
        ]
        if missing_required:
            raise DatasetError(f"Missing required files: {', '.join(missing_required)}")

        for table_name, filename in TABLE_FILES.items():
            path = self.run_path / filename
            if not path.exists():
                self.tables[table_name] = self._empty_table(table_name)
                continue
            df = pd.read_csv(path)
            df = self._parse_dates(table_name, df)
            self._validate_columns(table_name, df)
            self.tables[table_name] = df
        return self

    def _parse_dates(self, table_name: str, df: pd.DataFrame) -> pd.DataFrame:
        date_cols = DATE_COLUMNS.get(table_name)
        if not date_cols:
            return df
        for col in date_cols:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        return df

    def _validate_columns(self, table_name: str, df: pd.DataFrame) -> None:
        required = REQUIRED_COLUMNS.get(table_name)
        if not required:
            return
        missing = required.difference(df.columns)
        if missing:
            raise DatasetError(
                f"Table '{table_name}' is missing columns: {', '.join(sorted(missing))}"
            )

    def _empty_table(self, table_name: str) -> pd.DataFrame:
        required = REQUIRED_COLUMNS.get(table_name, set())
        return pd.DataFrame(columns=sorted(required))

    def table(self, table_name: str) -> pd.DataFrame:
        if table_name not in self.tables:
            raise DatasetError(f"Table '{table_name}' not loaded")
        return self.tables[table_name]


class DatasetProvider:
    def __init__(self, runs_root: Path, cache: Optional[DatasetCache] = None) -> None:
        self.runs_root = runs_root
        self.cache = cache

    def get(self, run_id: str) -> RunDataset:
        if self.cache:
            cached = self.cache.get(run_id)
            if cached:
                return cached
        run_path = self.runs_root / run_id
        dataset = RunDataset(run_id=run_id, run_path=run_path).load()
        if self.cache:
            self.cache.set(run_id, dataset)
        return dataset
