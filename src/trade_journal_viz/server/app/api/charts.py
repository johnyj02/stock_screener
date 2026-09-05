from __future__ import annotations

from pathlib import Path
from typing import List

import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from trade_journal_viz.server.app.settings import SETTINGS

router = APIRouter(prefix="/runs/{run_id}/charts", tags=["charts"])


def _charts_dir(run_id: str) -> Path:
    return SETTINGS.runs_root / run_id / "charts"


@router.get("")
def list_charts(run_id: str) -> List[dict]:
    charts_path = _charts_dir(run_id)
    manifest_path = SETTINGS.runs_root / run_id / "charts_manifest.csv"
    if not charts_path.exists() or not charts_path.is_dir():
        return []
    items: List[dict] = []
    if manifest_path.exists():
        df = pd.read_csv(manifest_path)
        df = df.where(pd.notnull(df), None)
        items = df.to_dict(orient="records")
    seen = {item.get("filename") for item in items if item.get("filename")}
    for file in sorted(charts_path.iterdir()):
        if not file.is_file():
            continue
        if file.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".svg"}:
            continue
        if file.name in seen:
            continue
        items.append({
            "filename": file.name,
            "size_bytes": file.stat().st_size,
            "chart_type": "file",
        })
    return items


@router.get("/{filename}")
def get_chart(run_id: str, filename: str):
    charts_path = _charts_dir(run_id)
    file_path = (charts_path / filename).resolve()
    if not str(file_path).startswith(str(charts_path.resolve())):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Chart not found")
    return FileResponse(file_path)
