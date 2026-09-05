from __future__ import annotations

from fastapi import APIRouter, HTTPException

from trade_journal_viz.server.app.core.registry import get_run_path, list_runs
from trade_journal_viz.server.app.settings import SETTINGS

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("")
def runs_list():
    runs = list_runs(SETTINGS.runs_root)
    return [run.to_dict() for run in runs]


@router.get("/{run_id}/metadata")
def run_metadata(run_id: str):
    runs = {run.run_id: run for run in list_runs(SETTINGS.runs_root)}
    if run_id not in runs:
        raise HTTPException(status_code=404, detail="Run not found")
    return runs[run_id].to_dict()


@router.get("/{run_id}/files")
def run_files(run_id: str):
    path = get_run_path(SETTINGS.runs_root, run_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    files = [p.name for p in path.iterdir() if p.is_file()]
    return {"run_id": run_id, "files": sorted(files)}
