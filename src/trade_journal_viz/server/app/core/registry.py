from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from trade_journal_viz.server.app.schemas.columns import MIN_REQUIRED_FILES


@dataclass(frozen=True)
class RunInfo:
    run_id: str
    path: Path
    created_at: str
    date_range: Optional[Dict[str, str]]
    files: List[str]

    def to_dict(self) -> Dict[str, object]:
        return {
            "run_id": self.run_id,
            "path": str(self.path),
            "created_at": self.created_at,
            "date_range": self.date_range,
            "files": self.files,
        }


def _read_date_range(equity_path: Path) -> Optional[Dict[str, str]]:
    if not equity_path.exists():
        return None
    try:
        with equity_path.open(newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            date_idx = header.index("date")
            first_row = next(reader, None)
            if not first_row:
                return None
            first_date = first_row[date_idx]
            last_date = first_date
            for row in reader:
                if row:
                    last_date = row[date_idx]
        return {"start": first_date, "end": last_date}
    except Exception:
        return None


def _format_created_at(path: Path) -> str:
    try:
        mtime = path.stat().st_mtime
        return datetime.fromtimestamp(mtime).isoformat(timespec="seconds")
    except Exception:
        return ""


def list_runs(runs_root: Path) -> List[RunInfo]:
    runs: List[RunInfo] = []
    if not runs_root.exists():
        return runs

    seen_paths = set()
    for trades_path in sorted(runs_root.rglob("trades.csv")):
        run_path = trades_path.parent
        if run_path in seen_paths or not run_path.is_dir():
            continue
        files = [p.name for p in run_path.iterdir() if p.is_file()]
        if not MIN_REQUIRED_FILES.issubset(set(files)):
            continue
        seen_paths.add(run_path)
        date_range = _read_date_range(run_path / "equity_books_daily.csv")
        runs.append(
            RunInfo(
                run_id=run_path.name,
                path=run_path,
                created_at=_format_created_at(run_path),
                date_range=date_range,
                files=sorted(files),
            )
        )
    return runs


def get_run_path(runs_root: Path, run_id: str) -> Path:
    return runs_root / run_id
