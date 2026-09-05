import pandas as pd
import numpy as np

from stock_screener.core.cache import CacheManager
from stock_screener.core.data import DataProvider


def _sample_df(rows: int = 10, start: str = "2020-01-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=rows, freq="D")
    close = pd.Series(np.linspace(100, 100 + rows - 1, rows), index=idx)
    high = close + 1
    low = close - 1
    open_ = close
    volume = pd.Series(1_000_000, index=idx, dtype="float")
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


def test_cache_manager_save_update_metadata(tmp_path):
    cm = CacheManager(cache_dir=tmp_path)
    df = _sample_df()
    cm.save({"AAA": df}, "1d")

    meta = cm.get_metadata(["AAA"], "1d")
    assert "AAA" in meta
    assert meta["AAA"][0] == df.index[0]
    assert meta["AAA"][1] == df.index[-1]

    extended = _sample_df(start="2020-01-05", rows=10)
    cm.update("AAA", extended, "1d")
    loaded = cm.get(["AAA"], "1d")["AAA"]

    assert loaded.index[0] == df.index[0]
    assert loaded.index[-1] == extended.index[-1]
    assert loaded.index.is_unique


def test_data_provider_uses_cache_and_adds_indicators(monkeypatch, tmp_path):
    calls = {"download": 0}

    def fake_download(*args, **kwargs):
        calls["download"] += 1
        tickers = kwargs.get("tickers") or kwargs.get("ticker") or kwargs.get("symbols")
        # Mimic yfinance: single ticker -> flat columns, multiple -> MultiIndex
        if isinstance(tickers, str) or (isinstance(tickers, list) and len(tickers) == 1):
            return _sample_df()
        frames = []
        keys = []
        for t in tickers:
            frames.append(_sample_df())
            keys.append(t)
        return pd.concat(frames, axis=1, keys=keys)

    import yfinance

    monkeypatch.setattr(yfinance, "download", fake_download)

    dp = DataProvider(cache_dir=tmp_path, compute_indicators=True)
    result = dp.fetch_batch_data(["AAA"], period="1mo", interval="1d", force_update=False)
    assert "AAA" in result
    df = result["AAA"]
    assert "ATR_14" in df.columns
    assert calls["download"] >= 1

    result_cached = dp.fetch_batch_data(["AAA"], period="1mo", interval="1d", force_update=False)
    assert "AAA" in result_cached
    assert calls["download"] <= 2  # allow one refresh but not repeated failures
