from pathlib import Path

import pandas as pd

from strategy_backtester.config import resolve_tickers
from strategy_grid_search.config import resolve_path_from_config
from trade_journal_viz.server.app.core.cache import DatasetCache
from trade_journal_viz.server.app.core.dataset import RunDataset


def test_resolve_tickers_combines_inline_and_csv_values(tmp_path: Path):
    pd.DataFrame({"Symbol": ["MSFT", "AAPL"]}).to_csv(
        tmp_path / "tickers.csv",
        index=False,
    )

    tickers = resolve_tickers(
        str(tmp_path / "backtest.yaml"),
        {"tickers": "AAPL, NVDA", "tickers_file": "tickers.csv"},
    )

    assert tickers == ["AAPL", "NVDA", "MSFT"]


def test_search_path_falls_back_to_repo_root(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "configs").mkdir()
    target = repo / "data" / "base.yaml"
    target.parent.mkdir()
    target.touch()

    resolved = resolve_path_from_config(
        str(repo / "configs" / "search.yaml"),
        "data/base.yaml",
    )

    assert resolved == str(target)


def test_dataset_cache_evicts_the_least_recently_used_run(tmp_path: Path):
    cache = DatasetCache(max_runs=2)
    first = RunDataset("first", tmp_path)
    second = RunDataset("second", tmp_path)
    third = RunDataset("third", tmp_path)

    cache.set("first", first)
    cache.set("second", second)
    assert cache.get("first") is first
    cache.set("third", third)

    assert cache.get("second") is None
    assert cache.get("first") is first
    assert cache.get("third") is third
