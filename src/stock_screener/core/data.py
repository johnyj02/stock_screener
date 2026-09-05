"""
Core DataProvider for Stock Screener.
Handles batch fetching of OHLCV data using yfinance.
"""
import ast
import glob
import logging
import os
import re
import math
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import yfinance as yf
from stock_screener.core.cache import CacheManager
from stock_screener.core.indicators import add_common_indicators

logger = logging.getLogger(__name__)


class BaseDataProvider(ABC):
    def __init__(self, compute_indicators: bool = True, read_only_cache: bool = False):
        self.compute_indicators = compute_indicators
        self.read_only_cache = read_only_cache

    @abstractmethod
    def fetch_batch_data(
        self,
        tickers: List[str],
        period: Optional[str] = "1y",
        interval: str = "1d",
        force_update: bool = False,
        as_of_date: Optional[pd.Timestamp] = None,
    ) -> Dict[str, pd.DataFrame]:
        raise NotImplementedError

    def prefetch_signature(self) -> Optional[Dict[str, Any]]:
        return None

    def _to_utc_naive(self, ts: Optional[pd.Timestamp]) -> Optional[pd.Timestamp]:
        if ts is None:
            return None
        if not isinstance(ts, pd.Timestamp):
            ts = pd.Timestamp(ts)
        if ts.tz is not None:
            ts = ts.tz_convert("UTC").tz_localize(None)
        return ts

    def _period_required_start(self, period: Optional[str], now: pd.Timestamp) -> Optional[pd.Timestamp]:
        if not period:
            return None
        period_lower = period.lower()
        if period_lower in {"max", "none"}:
            return None
        if period_lower == "ytd":
            return pd.Timestamp(year=now.year, month=1, day=1)

        match = re.match(r"^(\d+)(d|wk|w|mo|y)$", period_lower)
        if not match:
            return None

        qty = int(match.group(1))
        unit = match.group(2)
        if unit == "d":
            return now - pd.Timedelta(days=qty)
        if unit in {"w", "wk"}:
            return now - pd.Timedelta(weeks=qty)
        if unit == "mo":
            return now - pd.DateOffset(months=qty)
        if unit == "y":
            return now - pd.DateOffset(years=qty)
        return None

    def _interval_to_timedelta(self, interval: str) -> Optional[pd.Timedelta]:
        interval_lower = interval.lower()
        match = re.match(r"^(\d+)(m|h|d|wk|w|mo)$", interval_lower)
        if not match:
            return None
        qty = int(match.group(1))
        unit = match.group(2)
        if unit == "m":
            return pd.Timedelta(minutes=qty)
        if unit == "h":
            return pd.Timedelta(hours=qty)
        if unit == "d":
            return pd.Timedelta(days=qty)
        if unit in {"w", "wk"}:
            return pd.Timedelta(weeks=qty)
        if unit == "mo":
            return pd.Timedelta(days=30 * qty)
        return None


class DataProvider(BaseDataProvider):
    def __init__(
        self,
        cache_dir: str = ".cache",
        compute_indicators: bool = True,
        read_only_cache: bool = False,
        allow_partial: bool = True,
    ):
        super().__init__(compute_indicators=compute_indicators, read_only_cache=read_only_cache)
        self.cache = CacheManager(cache_dir=cache_dir)
        self.allow_partial = allow_partial

    def _normalize_single_ticker_frame(self, df: pd.DataFrame, ticker: str) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        if isinstance(df.columns, pd.MultiIndex):
            if ticker in df.columns.get_level_values(0):
                df = df[ticker].copy()
            elif ticker in df.columns.get_level_values(-1):
                df = df.xs(ticker, level=-1, axis=1).copy()
        if "Close" not in df.columns:
            new_cols = []
            changed = False
            for col in df.columns:
                if isinstance(col, str):
                    try:
                        parsed = ast.literal_eval(col)
                    except Exception:
                        parsed = None
                    if isinstance(parsed, tuple) and len(parsed) == 2:
                        if parsed[0] == ticker:
                            new_cols.append(parsed[1])
                            changed = True
                            continue
                        if parsed[1] == ticker:
                            new_cols.append(parsed[0])
                            changed = True
                            continue
                new_cols.append(col)
            if changed:
                df = df.copy()
                df.columns = new_cols
        return df.loc[:, ~df.columns.duplicated()]

    def _resolve_download_params(
        self,
        period: Optional[str],
        interval: str,
        end: Optional[pd.Timestamp] = None,
    ) -> Tuple[Optional[str], Optional[pd.Timestamp], Optional[pd.Timestamp], str]:
        interval_lower = interval.lower()
        period_key = period if period is not None else "none"

        period_lower = period.lower() if isinstance(period, str) else None
        if period_lower == "max":
            end = end or pd.Timestamp.utcnow().tz_localize(None)

            if interval_lower == "1m":
                days = 8
            elif interval_lower in {"2m", "5m", "15m", "30m", "90m"}:
                days = 60
            elif interval_lower in {"60m", "1h"} or interval_lower.endswith("h"):
                days = 730
            else:
                 return period, None, None, period_key
            
            safe_days = max(days - 1, 1)
            start = end - pd.Timedelta(days=safe_days)
            return None, start, end, f"max_{safe_days}d"

        return period, None, None, period_key

    def fetch_batch_data(
        self,
        tickers: List[str],
        period: Optional[str] = "1y",
        interval: str = "1d",
        force_update: bool = False,
        as_of_date: Optional[pd.Timestamp] = None,
    ) -> Dict[str, pd.DataFrame]:
        """
        Fetch historical data for a list of tickers with smart caching.
        
        Args:
            tickers: List of ticker symbols.
            period: Data period (e.g. "1y", "max").
            interval: Data interval (e.g. "1d", "15m").
            force_update: If True, bypass cache and download fresh/full.
            as_of_date: Optional timestamp to treat as "current time" for cache freshness.
            
        Returns:
            Dictionary mapping ticker symbol to its DataFrame.
        """
        if not tickers:
            return {}

        if self.read_only_cache:
            results: Dict[str, pd.DataFrame] = {}
            missing: List[str] = []
            metadata = self.cache.get_metadata(tickers, interval)
            current_time = self._to_utc_naive(as_of_date) or pd.Timestamp.utcnow().tz_localize(None)
            required_start = self._period_required_start(period, current_time)

            for ticker in tickers:
                meta = metadata.get(ticker)
                if meta is None:
                    missing.append(ticker)
                    continue
                start_date = self._to_utc_naive(meta[0])
                last_date = self._to_utc_naive(meta[1])
                cached_df = self.cache.get([ticker], interval).get(ticker)
                if cached_df is None or cached_df.empty or last_date is None:
                    missing.append(ticker)
                    continue
                if current_time is not None and last_date < current_time:
                    missing.append(ticker)
                    continue
                if required_start is not None and (start_date is None or start_date > required_start):
                    missing.append(ticker)
                    continue
                results[ticker] = self._normalize_single_ticker_frame(cached_df, ticker)

            if missing and not self.allow_partial:
                raise ValueError(f"Read-only cache missing tickers: {', '.join(sorted(set(missing)))}")
            if missing and self.allow_partial:
                logger.warning(
                    "Read-only cache missing data for symbols: %s",
                    ", ".join(sorted(set(missing))),
                )
            if self.compute_indicators:
                for ticker, df in list(results.items()):
                    results[ticker] = add_common_indicators(df)
            return results

        results = {}
        to_download_full = []
        to_download_partial = {} # ticker -> start_date

        metadata = self.cache.get_metadata(tickers, interval)
        current_time = self._to_utc_naive(as_of_date) or pd.Timestamp.utcnow().tz_localize(None)
        interval_delta = self._interval_to_timedelta(interval)
        required_start = self._period_required_start(period, current_time)

        # Determine strategy for each ticker
        for ticker in tickers:
            if force_update:
                to_download_full.append(ticker)
                continue

            meta = metadata.get(ticker)
            if meta is None:
                to_download_full.append(ticker)
                continue

            start_date = self._to_utc_naive(meta[0])
            last_date = self._to_utc_naive(meta[1])
            if last_date is None: 
                to_download_full.append(ticker)
                continue

            if last_date is not None and current_time is not None and last_date >= current_time:
                cached_df = self.cache.get([ticker], interval).get(ticker)
                if cached_df is not None:
                    results[ticker] = self._normalize_single_ticker_frame(cached_df, ticker)
                    continue
                to_download_full.append(ticker)
                continue

            if required_start is not None and (start_date is None or start_date > required_start):
                to_download_full.append(ticker)
                continue

            if interval_delta is not None and (current_time - last_date) <= interval_delta:
                cached_df = self.cache.get([ticker], interval).get(ticker)
                if cached_df is not None:
                    results[ticker] = self._normalize_single_ticker_frame(cached_df, ticker)
                continue

            # Check for gap safety
            # If we need 'max' or a long period, we should ensure our cache covers it.
            # But typically we Just Want Latest Data appended.

            # Logic: We request data starting from last_date.
            # Note: yfinance start is inclusive. So we might get the last candle again.
            # CacheManager.update handles deduplication.
            
            # GAP CHECK: If the last cached data is too old compared to 'now', and the interval is small, 
            # we might be missing a chunk if we don't fetch carefully. 
            # yfinance limits: 
            # - 1m: 7 days
            # - 15m: 60 days
            # If our cache is older than these limits, we CANNOT fetch the missing bridge. 
            # We must force full refresh.
            
            age_days = (current_time - last_date).days
            interval_lower = interval.lower()
            interval_match = re.match(r"^(\d+)(m|h|d|wk|w|mo)$", interval_lower)
            interval_unit = interval_match.group(2) if interval_match else None
            interval_qty = int(interval_match.group(1)) if interval_match else None

            is_gap_risk = False
            if interval_unit == "m":
                if interval_qty == 1 and age_days > 7:
                    is_gap_risk = True
                elif age_days > 59:
                    is_gap_risk = True
            elif interval_unit == "h":
                if age_days > 729:
                    is_gap_risk = True
            
            if is_gap_risk:
                logger.warning(f"Data gap detected for {ticker} ({interval}). Last cached: {last_date}. Forcing full refresh.")
                to_download_full.append(ticker)
            else:
                to_download_partial[ticker] = last_date

        # 1. Full Downloads
        if to_download_full:
            logger.info(f"Downloading full history for {len(to_download_full)} tickers ({interval})...")
            # Batch download for efficiency if possible, or loop if we want to be safe.
            # yfinance batch is good.
            # Using period defaulting to what was requested or max if None
             
            dl_period, dl_start, dl_end, _ = self._resolve_download_params(period, interval, current_time)
            download_kwargs = {
                "tickers": to_download_full,
                "interval": interval,
                "group_by": "ticker",
                "threads": True,
                "progress": False,
            }
            if dl_start is not None or dl_end is not None:
                download_kwargs["start"] = dl_start
                if dl_end is not None:
                    download_kwargs["end"] = dl_end
            else:
                download_kwargs["period"] = dl_period if dl_period else "max"
            try:
                # yfinance batch download
                data = yf.download(**download_kwargs)
                
                # Parse Result
                if len(to_download_full) == 1:
                    t = to_download_full[0]
                    if not data.empty:
                        data = self._normalize_single_ticker_frame(data, t)
                        if not data.empty:
                            self.cache.save({t: data}, interval)
                            results[t] = data
                else:
                    if isinstance(data.columns, pd.MultiIndex):
                        for t in to_download_full:
                            if t in data.columns.get_level_values(0):
                                t_df = data[t].copy()
                            elif t in data.columns.get_level_values(1):
                                t_df = data.xs(t, level=1, axis=1).copy()
                            else:
                                continue
                            t_df = self._normalize_single_ticker_frame(t_df, t)
                            t_df.dropna(how='all', inplace=True)
                            if not t_df.empty:
                                self.cache.save({t: t_df}, interval)
                                results[t] = t_df
                    else:
                        for t in to_download_full:
                            logger.warning(f"Unexpected data shape for {t}; skipping cache save.")
            except Exception as e:
                logger.error(f"Full download failed: {e}")

        # 2. Partial Updates
        if to_download_partial:
            logger.info(f"Updating {len(to_download_partial)} tickers ({interval})...")
            
            # For partials, we might have different start dates. 
            # Grouping by start date could optimize, but per-ticker fetch is safer for correctness initially.
            # Or we can just fetch '1mo' for everyone if the gap is small? 
            # Let's iterate for safety and correctness.
            
            for t, start_dt in to_download_partial.items():
                try:
                    # Fetch from start_dt
                    download_kwargs = {
                        "tickers": t,
                        "start": start_dt,
                        "interval": interval,
                        "progress": False,
                        "threads": False,
                    }
                    if as_of_date is not None:
                        download_kwargs["end"] = current_time
                    new_df = yf.download(**download_kwargs)
                    if not new_df.empty:
                        success = self.cache.update(t, new_df, interval)
                        if not success:
                            # If update logic failed (e.g. detected weird gap), force full next time or now?
                            # For now, current run gets what it gets, next time valid_cache check will fail?
                            # Actually CacheManager.update returns True usually.
                            pass
                            
                    # Load full updated cache into results
                    # (Or should we return just the new bits? Engine usually expects full history for indicators)
                    # We return full history from cache.
                    
                    full_df = self.cache.get([t], interval).get(t)
                    if full_df is not None:
                        results[t] = self._normalize_single_ticker_frame(full_df, t)
                        
                except Exception as e:
                    logger.error(f"Update failed for {t}: {e}")
                    # Try to return cached at least
                    full_df = self.cache.get([t], interval).get(t)
                    if full_df is not None:
                        results[t] = self._normalize_single_ticker_frame(full_df, t)

        # 3. Load Cached Data for tickers we didn't touch? 
        # (The loop above handles everyone: either full DL, or partial update + load)
        
        for ticker, df in list(results.items()):
            results[ticker] = self._normalize_single_ticker_frame(df, ticker)
        if self.compute_indicators:
            for ticker, df in list(results.items()):
                results[ticker] = add_common_indicators(df)
        return results

    def prefetch_signature(self) -> Optional[Dict[str, Any]]:
        return None


def _interval_to_pandas_freq(interval: str) -> Optional[str]:
    match = re.match(r"^(\d+)(m|h|d|wk|w|mo)$", interval.lower())
    if not match:
        return None
    qty = int(match.group(1))
    unit = match.group(2)
    if unit == "m":
        return f"{qty}min"
    if unit == "h":
        return f"{qty}H"
    if unit == "d":
        return f"{qty}D"
    if unit in {"w", "wk"}:
        return f"{qty}W"
    if unit == "mo":
        return f"{qty}M"
    return None


def _resolve_path(config_path: Optional[str], target: str) -> str:
    if os.path.isabs(target):
        return target
    base_dir = os.path.dirname(os.path.abspath(config_path or os.getcwd()))
    return os.path.abspath(os.path.join(base_dir, target))


def _find_column(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    lower_map = {str(col).lower(): col for col in df.columns}
    for cand in candidates:
        col = lower_map.get(cand.lower())
        if col is not None:
            return col
    return None


class StaticFileDataProvider(BaseDataProvider):
    def __init__(
        self,
        files: List[Dict[str, Any]],
        file_interval: Optional[str] = None,
        resample_to: Optional[str] = None,
        timezone: str = "America/Denver",
        timestamp_col: Optional[str] = None,
        timestamp_format: Optional[str] = None,
        compute_indicators: bool = True,
        default_ticker: Optional[str] = None,
    ):
        super().__init__(compute_indicators=compute_indicators)
        self.files = files
        self.file_interval = file_interval
        self.resample_to = resample_to
        self.timezone = timezone
        self.timestamp_col = timestamp_col
        self.timestamp_format = timestamp_format
        self.default_ticker = default_ticker
        self._data_cache: Dict[str, pd.DataFrame] = {}
        self._loaded_paths: set[str] = set()
        self._declared_symbols: set[str] = set(
            str(entry.get("ticker")) for entry in files if entry.get("ticker")
        )
        if default_ticker:
            self._declared_symbols.add(str(default_ticker))

    def _parse_timestamps(self, series: pd.Series) -> pd.Series:
        fmt = self.timestamp_format
        if fmt is None:
            sample = series.dropna().astype(str).head(1)
            if not sample.empty and re.match(r"^\\d{8}\\s+\\d{2}:\\d{2}:\\d{2}$", sample.iloc[0]):
                fmt = "%Y%m%d  %H:%M:%S"
        return pd.to_datetime(series, format=fmt, errors="coerce")

    def _normalize_ohlcv(self, df: pd.DataFrame) -> pd.DataFrame:
        drop_cols = [col for col in df.columns if str(col).lower().startswith("unnamed")]
        if drop_cols:
            df = df.drop(columns=drop_cols)
        col_map: Dict[str, str] = {}
        for col in df.columns:
            key = str(col).strip().lower()
            if key == "open":
                col_map[col] = "Open"
            elif key == "high":
                col_map[col] = "High"
            elif key == "low":
                col_map[col] = "Low"
            elif key == "close":
                col_map[col] = "Close"
            elif key == "volume":
                col_map[col] = "Volume"
        if col_map:
            df = df.rename(columns=col_map)
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        keep = [col for col in ["Open", "High", "Low", "Close", "Volume"] if col in df.columns]
        return df[keep]

    def _apply_timezone(self, index: pd.DatetimeIndex) -> pd.DatetimeIndex:
        if index.tz is None:
            index = index.tz_localize(self.timezone, nonexistent="shift_forward", ambiguous="infer")
        else:
            index = index.tz_convert(self.timezone)
        return index

    def _resample_if_needed(self, df: pd.DataFrame, interval: Optional[str]) -> pd.DataFrame:
        target = self.resample_to or interval
        if not target:
            return df
        source = self.file_interval or pd.infer_freq(df.index)
        if source and source == target:
            return df
        freq = _interval_to_pandas_freq(target)
        if not freq:
            return df
        agg = {
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        }
        df = df.resample(freq, label="left", closed="left").agg(agg)
        return df.dropna(subset=["Open", "High", "Low", "Close"])

    def _prepare_frame(self, df: pd.DataFrame, interval: Optional[str]) -> pd.DataFrame:
        df = self._normalize_ohlcv(df)
        df = df.dropna(subset=["Open", "High", "Low", "Close"]).copy()
        df.index = self._apply_timezone(df.index)
        df = self._resample_if_needed(df, interval)
        df = df.sort_index()
        df = df.loc[~df.index.duplicated(keep="last")]
        df.index = df.index.tz_convert("UTC").tz_localize(None)
        if self.compute_indicators:
            df = add_common_indicators(df)
        return df

    def _load_file(self, path: str, interval: Optional[str]) -> None:
        if path in self._loaded_paths:
            return
        df = pd.read_csv(path)
        ts_col = self.timestamp_col or _find_column(df, ["date", "datetime", "timestamp"])
        if ts_col is None:
            raise ValueError(f"Static file missing timestamp column: {path}")
        ticker_col = _find_column(df, ["ticker", "symbol"])
        timestamps = self._parse_timestamps(df[ts_col])
        df = df.loc[timestamps.notna()].copy()
        df.index = pd.DatetimeIndex(timestamps[timestamps.notna()])
        df = df.drop(columns=[ts_col])
        if ticker_col:
            df[ticker_col] = df[ticker_col].astype(str).str.strip()
            for ticker, sub in df.groupby(ticker_col):
                sub = sub.drop(columns=[ticker_col])
                prepared = self._prepare_frame(sub, interval)
                if not prepared.empty:
                    self._data_cache[ticker] = prepared
        else:
            prepared = self._prepare_frame(df, interval)
            if not prepared.empty:
                ticker = None
                for entry in self.files:
                    if os.path.abspath(entry["path"]) == os.path.abspath(path):
                        ticker = entry.get("ticker")
                        break
                if not ticker:
                    ticker = self.default_ticker
                if not ticker:
                    raise ValueError(f"Ticker not provided for static file: {path}")
                self._data_cache[str(ticker)] = prepared
        self._loaded_paths.add(path)

    def fetch_batch_data(
        self,
        tickers: List[str],
        period: Optional[str] = "1y",
        interval: str = "1d",
        force_update: bool = False,
        as_of_date: Optional[pd.Timestamp] = None,
    ) -> Dict[str, pd.DataFrame]:
        if not tickers:
            return {}
        for entry in self.files:
            self._load_file(entry["path"], interval)
        missing = [t for t in tickers if t not in self._data_cache]
        if missing:
            raise ValueError(f"Static data missing tickers: {', '.join(missing)}")
        results = {t: self._data_cache[t].copy(deep=True) for t in tickers}
        if as_of_date is not None:
            end_ts = pd.Timestamp(as_of_date).tz_localize(None)
            for ticker, df in results.items():
                results[ticker] = df.loc[df.index <= end_ts]
        return results

    def prefetch_signature(self) -> Optional[Dict[str, Any]]:
        return {
            "type": "static",
            "files": [entry["path"] for entry in self.files],
            "file_interval": self.file_interval,
            "resample_to": self.resample_to,
            "timezone": self.timezone,
        }

    def declared_symbols(self) -> set[str]:
        return set(self._declared_symbols)


class IBKRDataProvider(BaseDataProvider):
    _BAR_SIZE_MAP = {
        "1m": "1 min",
        "2m": "2 mins",
        "5m": "5 mins",
        "15m": "15 mins",
        "30m": "30 mins",
        "60m": "1 hour",
        "1h": "1 hour",
        "2h": "2 hours",
        "4h": "4 hours",
        "1d": "1 day",
        "1wk": "1 week",
        "1w": "1 week",
        "1mo": "1 month",
    }

    _DEFAULT_CHUNK_DAYS = {
        "1m": 30,
        "2m": 60,
        "5m": 1825,
        "15m": 180,
        "30m": 365,
        "60m": 730,
        "1h": 730,
        "2h": 730,
        "4h": 730,
        "1d": 3650,
        "1wk": 3650,
        "1w": 3650,
        "1mo": 3650,
    }

    _DEFAULT_HISTORY_DAYS = {
        "1m": 30,
        "2m": 60,
        "5m": 1825,
        "15m": 180,
        "30m": 365,
        "60m": 730,
        "1h": 730,
        "2h": 730,
        "4h": 730,
        "1d": 3650,
        "1wk": 3650,
        "1w": 3650,
        "1mo": 3650,
    }
    _CONTFUT_BUFFER_DAYS = 0
    _FUTURE_MONTHS = {
        "ES": [3, 6, 9, 12],
        "MES": [3, 6, 9, 12],
        "NQ": [3, 6, 9, 12],
        "MNQ": [3, 6, 9, 12],
        "GC": [2, 4, 6, 8, 10, 12],
        "SI": [3, 5, 7, 9, 12],
        "SIL": [3, 5, 7, 9, 12],
        "DX": [3, 6, 9, 12],
    }
    _FUTURE_MONTH_CODES = {
        1: "F",
        2: "G",
        3: "H",
        4: "J",
        5: "K",
        6: "M",
        7: "N",
        8: "Q",
        9: "U",
        10: "V",
        11: "X",
        12: "Z",
    }

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 1,
        cache_dir: str = ".cache_ibkr",
        allow_partial: bool = True,
        read_only_cache: bool = False,
        max_requests_per_min: int = 6,
        min_sleep_seconds: float = 10.0,
        connect_timeout: int = 10,
        contracts: Optional[Dict[str, Dict[str, Any]]] = None,
        what_to_show: str = "TRADES",
        use_rth: bool = False,
        include_expired: bool = True,
        default_stock_exchange: str = "SMART",
        default_futures_exchange: str = "CME",
        default_currency: str = "USD",
        auto_futures_months_ahead: int = 2,
        request_timeout: float = 300.0,
        max_history_days: Optional[int] = None,
        max_history_days_by_interval: Optional[Dict[str, int]] = None,
        futures_use_end_datetime: bool = False,
        futures_contract_mode: str = "auto_roll",
        compute_indicators: bool = True,
    ):
        super().__init__(compute_indicators=compute_indicators, read_only_cache=read_only_cache)
        self.host = host
        self.port = port
        self.client_id = client_id
        self.cache = CacheManager(cache_dir=cache_dir)
        self.allow_partial = allow_partial
        self.min_sleep_seconds = max(10.0, min_sleep_seconds)
        effective_limit = max(1, int(60 / self.min_sleep_seconds))
        self.max_requests_per_min = min(max_requests_per_min, effective_limit)
        self.connect_timeout = connect_timeout
        self.contracts = {str(k): dict(v) for k, v in (contracts or {}).items()}
        self.what_to_show = what_to_show
        self.use_rth = use_rth
        self.include_expired = include_expired
        self.default_stock_exchange = default_stock_exchange
        self.default_futures_exchange = default_futures_exchange
        self.default_currency = default_currency
        self.auto_futures_months_ahead = auto_futures_months_ahead
        self.request_timeout = request_timeout
        self.max_history_days = max_history_days
        self.max_history_days_by_interval = max_history_days_by_interval or {}
        self.futures_contract_mode = str(futures_contract_mode or "auto_roll").lower()
        if self.futures_contract_mode not in {"contfut", "auto_roll"}:
            self.futures_contract_mode = "contfut"
        self.futures_use_end_datetime = futures_use_end_datetime or self.futures_contract_mode == "auto_roll"
        self._ib = None
        self._contract_cache: Dict[str, Any] = {}
        self._window_start: Optional[float] = None
        self._request_count = 0
        self._last_request_ts: Optional[float] = None

    def _connect(self):
        if self._ib is not None and self._ib.isConnected():
            return self._ib
        try:
            from ib_insync import IB
        except ImportError as exc:
            raise ImportError("ib_insync is required for IBKR data_source. pip install ib_insync") from exc
        ib = IB()
        ib.connect(self.host, self.port, clientId=self.client_id, timeout=self.connect_timeout)
        self._ib = ib
        return ib

    def _rate_limit(self) -> None:
        now = time.time()
        if self._window_start is None or now - self._window_start >= 60:
            self._window_start = now
            self._request_count = 0
        if self.max_requests_per_min > 0 and self._request_count >= self.max_requests_per_min:
            sleep_for = max(0.0, 60 - (now - self._window_start))
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._window_start = time.time()
            self._request_count = 0
        if self._last_request_ts is not None and self.min_sleep_seconds > 0:
            delta = now - self._last_request_ts
            if delta < self.min_sleep_seconds:
                time.sleep(self.min_sleep_seconds - delta)
        self._last_request_ts = time.time()
        self._request_count += 1

    def _bar_size(self, interval: str) -> str:
        bar_size = self._BAR_SIZE_MAP.get(interval.lower())
        if not bar_size:
            raise ValueError(f"Unsupported IBKR interval: {interval}")
        return bar_size

    def _chunk_days(self, interval: str) -> int:
        return self._DEFAULT_CHUNK_DAYS.get(interval.lower(), 365)

    def _history_days(self, interval: str) -> int:
        interval_key = interval.lower()
        if interval_key in self.max_history_days_by_interval:
            return int(self.max_history_days_by_interval[interval_key])
        if self.max_history_days is not None:
            return int(self.max_history_days)
        return self._DEFAULT_HISTORY_DAYS.get(interval_key, 365)

    def _is_intraday(self, interval: str) -> bool:
        match = re.match(r"^(\d+)(m|h|d|wk|w|mo)$", interval.lower())
        if not match:
            return False
        unit = match.group(2)
        return unit in {"m", "h"}

    def _base_interval_for(self, interval: str) -> str:
        interval_lower = interval.lower()
        match = re.match(r"^(\d+)(m|h|d|wk|w|mo)$", interval_lower)
        if not match:
            return interval_lower
        unit = match.group(2)
        if unit in {"m", "h"}:
            return "1m"
        if unit in {"d", "wk", "w", "mo"}:
            return "1d"
        return interval_lower

    def _resolve_future_expiry(self, end_dt: Optional[pd.Timestamp]) -> str:
        anchor = self._to_utc_naive(end_dt) or pd.Timestamp.utcnow().tz_localize(None)
        expiry = anchor + pd.DateOffset(months=self.auto_futures_months_ahead)
        return expiry.strftime("%Y%m")

    def _resolve_future_expiry_for_symbol(self, base_symbol: str, end_dt: Optional[pd.Timestamp]) -> str:
        anchor = self._to_utc_naive(end_dt) or pd.Timestamp.utcnow().tz_localize(None)
        target = anchor + pd.DateOffset(months=self.auto_futures_months_ahead)
        months = self._FUTURE_MONTHS.get(base_symbol, list(range(1, 13)))
        year = target.year
        month = target.month
        for candidate in months:
            if candidate >= month:
                return f"{year}{candidate:02d}"
        return f"{year + 1}{months[0]:02d}"

    def _future_local_symbol(self, base_symbol: str, expiry: str) -> Optional[str]:
        if not expiry or len(expiry) < 6:
            return None
        try:
            year = int(expiry[:4])
            month = int(expiry[4:6])
        except ValueError:
            return None
        code = self._FUTURE_MONTH_CODES.get(month)
        if not code:
            return None
        year_digit = str(year % 10)
        return f"{base_symbol}{code}{year_digit}"

    def _extract_expiry(self, entry: Optional[Dict[str, Any]]) -> Optional[str]:
        if not entry:
            return None
        return entry.get("last_trade_date_or_contract_month") or entry.get("lastTradeDateOrContractMonth")

    def _entry_sec_type(self, entry: Optional[Dict[str, Any]]) -> str:
        if not entry:
            return ""
        return str(entry.get("sec_type") or entry.get("secType") or "").upper()

    def _contract_from_entry(self, entry: Dict[str, Any]) -> Any:
        from ib_insync import Contract
        contract = Contract()
        for key, value in entry.items():
            if value is None:
                continue
            if key in {"sec_type", "secType"}:
                setattr(contract, "secType", str(value).upper())
                continue
            if key in {"last_trade_date_or_contract_month", "lastTradeDateOrContractMonth"}:
                setattr(contract, "lastTradeDateOrContractMonth", value)
                continue
            if key in {"local_symbol", "localSymbol"}:
                setattr(contract, "localSymbol", value)
                continue
            setattr(contract, key, value)
        if not getattr(contract, "currency", None):
            contract.currency = self.default_currency
        if not getattr(contract, "exchange", None):
            contract.exchange = self.default_stock_exchange
        contract.includeExpired = bool(entry.get("include_expired", self.include_expired))
        return contract

    def _build_contract(self, ticker: str, end_dt: Optional[pd.Timestamp]) -> Any:
        from ib_insync import Stock, Forex, ContFuture
        entry = self.contracts.get(ticker)
        if entry:
            entry = dict(entry)
            sec_type = self._entry_sec_type(entry)
            expiry = self._extract_expiry(entry)
            if expiry and not sec_type:
                entry["sec_type"] = "FUT"
                sec_type = "FUT"
            if "symbol" not in entry:
                base_symbol = ticker.replace("=F", "").replace("=X", "")
                symbol_overrides = {
                    "BTC": "MBT",
                    "SI": "SIL",
                }
                entry["symbol"] = symbol_overrides.get(base_symbol, base_symbol)
            if ticker.endswith("=F"):
                base_symbol = ticker.replace("=F", "")
                exchange_overrides = {
                    "GC": "COMEX",
                    "SI": "COMEX",
                    "DX": "NYBOT",
                }
                trading_class_overrides = {
                    "ES": "ES",
                    "MES": "MES",
                    "NQ": "NQ",
                    "MNQ": "MNQ",
                    "GC": "GC",
                    "SI": "SIL",
                    "SIL": "SIL",
                    "BTC": "MBT",
                    "DX": "DX",
                }
                if not sec_type:
                    entry.setdefault("sec_type", "CONTFUT")
                    sec_type = self._entry_sec_type(entry)
                if sec_type == "CONTFUT" and self.futures_contract_mode == "auto_roll":
                    entry["sec_type"] = "FUT"
                    sec_type = "FUT"
                entry.setdefault("exchange", exchange_overrides.get(base_symbol, self.default_futures_exchange))
                entry.setdefault("currency", self.default_currency)
                entry.setdefault("tradingClass", trading_class_overrides.get(base_symbol))
                if sec_type == "FUT" and self._extract_expiry(entry) is None:
                    entry["last_trade_date_or_contract_month"] = self._resolve_future_expiry_for_symbol(
                        base_symbol,
                        end_dt,
                    )
            return self._contract_from_entry(entry)

        fx_match = re.match(r"^([A-Z]{3})([A-Z]{3})(=X)?$", ticker)
        if fx_match:
            pair = f"{fx_match.group(1)}{fx_match.group(2)}"
            return Forex(pair)

        if ticker.endswith("=F"):
            base_symbol = ticker.replace("=F", "")
            symbol_overrides = {
                "BTC": "MBT",
                "SI": "SIL",
            }
            exchange_overrides = {
                "GC": "COMEX",
                "SI": "COMEX",
                "DX": "NYBOT",
            }
            trading_class_overrides = {
                "ES": "ES",
                "MES": "MES",
                "NQ": "NQ",
                "MNQ": "MNQ",
                "GC": "GC",
                "SI": "SIL",
                "SIL": "SIL",
                "BTC": "MBT",
                "DX": "DX",
            }
            symbol = symbol_overrides.get(base_symbol, base_symbol)
            exchange = exchange_overrides.get(base_symbol, self.default_futures_exchange)
            if self.futures_contract_mode == "auto_roll":
                from ib_insync import Future
                expiry = self._resolve_future_expiry_for_symbol(base_symbol, end_dt)
                contract = Future(
                    symbol=symbol,
                    lastTradeDateOrContractMonth=expiry,
                    exchange=exchange,
                    currency=self.default_currency,
                )
            else:
                contract = ContFuture(
                    symbol=symbol,
                    exchange=exchange,
                    currency=self.default_currency,
                )
            trading_class = trading_class_overrides.get(base_symbol)
            if trading_class:
                contract.tradingClass = trading_class
            contract.includeExpired = bool(self.include_expired)
            return contract

        contract = Stock(ticker, exchange=self.default_stock_exchange, currency=self.default_currency)
        contract.includeExpired = bool(self.include_expired)
        return contract

    def _get_contract(self, ticker: str, end_dt: Optional[pd.Timestamp]) -> Optional[Any]:
        entry = self.contracts.get(ticker)
        expiry_key = None
        if ticker.endswith("=F") and self._entry_sec_type(entry) == "FUT":
            base_symbol = ticker.replace("=F", "")
            expiry_key = self._extract_expiry(entry) or self._resolve_future_expiry_for_symbol(
                base_symbol,
                end_dt,
            )
        cache_key = f"{ticker}:{expiry_key}" if expiry_key else ticker
        if cache_key in self._contract_cache:
            return self._contract_cache[cache_key]
        ib = self._connect()
        contract = self._build_contract(ticker, end_dt)
        try:
            qualified = ib.qualifyContracts(contract)
        except Exception as exc:
            logger.error("IBKR contract qualification failed for %s: %s", ticker, exc)
            return None
        if not qualified:
            logger.error("IBKR contract qualification returned no result for %s", ticker)
            return None
        contract = qualified[0]
        self._contract_cache[cache_key] = contract
        return contract

    def _future_contract_for_expiry(self, ticker: str, expiry: str) -> Optional[Any]:
        entry = self.contracts.get(ticker)
        base_symbol = ticker.replace("=F", "")
        symbol_overrides = {
            "BTC": "MBT",
            "SI": "SIL",
        }
        symbol = symbol_overrides.get(base_symbol, base_symbol)
        local_symbol = self._future_local_symbol(symbol, expiry)
        if entry:
            entry = dict(entry)
            entry["sec_type"] = "FUT"
            entry["last_trade_date_or_contract_month"] = expiry
            if "symbol" not in entry:
                entry["symbol"] = symbol
            if local_symbol and "localSymbol" not in entry and "local_symbol" not in entry:
                entry["localSymbol"] = local_symbol
            exchange_overrides = {
                "GC": "COMEX",
                "SI": "COMEX",
                "DX": "NYBOT",
            }
            trading_class_overrides = {
                "ES": "ES",
                "MES": "MES",
                "NQ": "NQ",
                "MNQ": "MNQ",
                "GC": "GC",
                "SI": "SIL",
                "SIL": "SIL",
                "BTC": "MBT",
                "DX": "DX",
            }
            entry.setdefault("exchange", exchange_overrides.get(base_symbol, self.default_futures_exchange))
            entry.setdefault("currency", self.default_currency)
            entry.setdefault("tradingClass", trading_class_overrides.get(base_symbol))
            contract = self._contract_from_entry(entry)
        else:
            from ib_insync import Future
            exchange_overrides = {
                "GC": "COMEX",
                "SI": "COMEX",
                "DX": "NYBOT",
            }
            trading_class_overrides = {
                "ES": "ES",
                "MES": "MES",
                "NQ": "NQ",
                "MNQ": "MNQ",
                "GC": "GC",
                "SI": "SIL",
                "SIL": "SIL",
                "BTC": "MBT",
                "DX": "DX",
            }
            exchange = exchange_overrides.get(base_symbol, self.default_futures_exchange)
            contract = Future(
                symbol,
                lastTradeDateOrContractMonth=expiry,
                exchange=exchange,
                currency=self.default_currency,
            )
            trading_class = trading_class_overrides.get(base_symbol)
            if trading_class:
                contract.tradingClass = trading_class
            if local_symbol:
                contract.localSymbol = local_symbol
            contract.includeExpired = bool(self.include_expired)
        cache_key = f"{ticker}:{expiry}"
        if cache_key in self._contract_cache:
            return self._contract_cache[cache_key]
        ib = self._connect()
        try:
            qualified = ib.qualifyContracts(contract)
        except Exception as exc:
            logger.error("IBKR contract qualification failed for %s: %s", ticker, exc)
            return None
        if not qualified and local_symbol:
            contract.localSymbol = local_symbol
            try:
                qualified = ib.qualifyContracts(contract)
            except Exception as exc:
                logger.error("IBKR contract qualification failed for %s (localSymbol=%s): %s", ticker, local_symbol, exc)
                return None
        if not qualified:
            logger.error("IBKR contract qualification returned no result for %s", ticker)
            return None
        contract = qualified[0]
        self._contract_cache[cache_key] = contract
        return contract

    def _iter_future_segments(
        self,
        start: pd.Timestamp,
        end: pd.Timestamp,
        base_symbol: str,
    ) -> Iterable[Tuple[pd.Timestamp, pd.Timestamp, str]]:
        current = start
        while current <= end:
            expiry = self._resolve_future_expiry_for_symbol(base_symbol, current)
            year = int(expiry[:4])
            month = int(expiry[4:6])
            month_end = pd.Timestamp(year, month, 1) + pd.offsets.MonthEnd(0)
            seg_end = month_end + pd.Timedelta(hours=23, minutes=59, seconds=59)
            if seg_end > end:
                seg_end = end
            yield current, seg_end, expiry
            current = seg_end + pd.Timedelta(seconds=1)

    def _bars_to_df(self, bars: List[Any]) -> pd.DataFrame:
        if not bars:
            return pd.DataFrame()
        from ib_insync import util
        df = util.df(bars)
        if df.empty:
            return df
        df = df.rename(
            columns={
                "date": "date",
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume",
            }
        )
        if "date" not in df.columns:
            return pd.DataFrame()
        df["date"] = pd.to_datetime(df["date"], utc=True, errors="coerce")
        df = df.loc[df["date"].notna()].copy()
        df.set_index("date", inplace=True)
        df.index = df.index.tz_convert("UTC").tz_localize(None)
        keep = [col for col in ["Open", "High", "Low", "Close", "Volume"] if col in df.columns]
        df = df[keep]
        for col in keep:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        return df

    def _resample_ohlcv(self, df: pd.DataFrame, interval: str) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        freq = _interval_to_pandas_freq(interval)
        if not freq:
            return df
        agg = {
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        }
        available = {col: agg[col] for col in agg if col in df.columns}
        if not available:
            return df
        tz = "America/New_York"
        session_offset = pd.Timedelta(hours=9, minutes=30)
        index = df.index
        if index.tz is None:
            index = index.tz_localize("UTC")
        else:
            index = index.tz_convert("UTC")
        local = df.copy()
        local.index = index.tz_convert(tz)
        local = local.sort_index()
        local.index = local.index - session_offset
        resampled = local.resample(freq, label="left", closed="left").agg(available)
        resampled.index = resampled.index + session_offset
        resampled = resampled.dropna(subset=["Open", "High", "Low", "Close"])
        resampled.index = resampled.index.tz_convert("UTC").tz_localize(None)
        return resampled

    def _fetch_range(
        self,
        contract: Any,
        start: pd.Timestamp,
        end: pd.Timestamp,
        interval: str,
    ) -> pd.DataFrame:
        bar_size = self._bar_size(interval)
        chunk_days = self._chunk_days(interval)
        ib = self._connect()
        sec_type = str(getattr(contract, "secType", "")).upper()
        ignore_end_datetime = sec_type == "CONTFUT" or (sec_type == "FUT" and not self.futures_use_end_datetime)
        current_end = end
        frames: List[pd.DataFrame] = []
        interval_delta = self._interval_to_timedelta(interval) or pd.Timedelta(minutes=1)

        if ignore_end_datetime:
            history_cap = self._history_days(interval)
            if start is None:
                duration_days = history_cap
            else:
                duration_days = max(1, (end - start).days + 1 + self._CONTFUT_BUFFER_DAYS)
                if duration_days > history_cap:
                    symbol = getattr(contract, "symbol", "CONTFUT")
                    logger.warning(
                        "IBKR CONTFUT request capped to last %s days for %s (%s); "
                        "requested window exceeds available history.",
                        history_cap,
                        symbol,
                        interval,
                    )
                    duration_days = history_cap
            if duration_days > 365:
                duration_years = max(1, int(math.ceil(duration_days / 365)))
                duration_str = f"{duration_years} Y"
            else:
                duration_str = f"{duration_days} D"
            self._rate_limit()
            try:
                bars = ib.reqHistoricalData(
                    contract,
                    endDateTime="",
                    durationStr=duration_str,
                    barSizeSetting=bar_size,
                    whatToShow=self.what_to_show,
                    useRTH=self.use_rth,
                    formatDate=2,
                    timeout=self.request_timeout,
                )
            except Exception as exc:
                logger.error("IBKR historical request failed: %s", exc)
                return pd.DataFrame()
            df = self._bars_to_df(bars)
            if df.empty:
                return df
            df = df.loc[(df.index >= start) & (df.index <= end)]
            return df

        while current_end > start:
            days = (current_end - start).days + 1
            max_days = 365 if self._is_intraday(interval) else None
            duration_days = max(1, min(chunk_days, days, max_days or days))
            if duration_days > 365:
                duration_years = max(1, int(math.ceil(duration_days / 365)))
                duration_str = f"{duration_years} Y"
            else:
                duration_str = f"{duration_days} D"
            self._rate_limit()
            end_dt = current_end
            if end_dt.tzinfo is None:
                end_dt = end_dt.tz_localize("UTC")
            try:
                bars = ib.reqHistoricalData(
                    contract,
                    endDateTime=end_dt,
                    durationStr=duration_str,
                    barSizeSetting=bar_size,
                    whatToShow=self.what_to_show,
                    useRTH=self.use_rth,
                    formatDate=2,
                    timeout=self.request_timeout,
                )
            except Exception as exc:
                logger.error("IBKR historical request failed: %s", exc)
                break
            df = self._bars_to_df(bars)
            if df.empty:
                break
            frames.append(df)
            earliest = df.index.min()
            if earliest <= start:
                break
            next_end = earliest - interval_delta
            if next_end >= current_end:
                break
            current_end = next_end

        if not frames:
            return pd.DataFrame()
        combined = pd.concat(frames).sort_index()
        combined = combined.loc[~combined.index.duplicated(keep="last")]
        combined = combined.loc[(combined.index >= start) & (combined.index <= end)]
        return combined

    def _fetch_history(
        self,
        ticker: str,
        interval: str,
        start: Optional[pd.Timestamp],
        end: pd.Timestamp,
    ) -> pd.DataFrame:
        if ticker.endswith("=F") and self.futures_contract_mode == "auto_roll":
            if start is None:
                start = end - pd.Timedelta(days=self._history_days(interval))
                logger.info(
                    "IBKR history limited to last %s days for %s (%s).",
                    self._history_days(interval),
                    ticker,
                    interval,
                )
            base_symbol = ticker.replace("=F", "")
            frames: List[pd.DataFrame] = []
            for seg_start, seg_end, expiry in self._iter_future_segments(start, end, base_symbol):
                contract = self._future_contract_for_expiry(ticker, expiry)
                if contract is None:
                    continue
                df = self._fetch_range(contract, seg_start, seg_end, interval)
                if not df.empty:
                    frames.append(df)
            if not frames:
                return pd.DataFrame()
            combined = pd.concat(frames).sort_index()
            combined = combined.loc[~combined.index.duplicated(keep="last")]
            combined = combined.loc[(combined.index >= start) & (combined.index <= end)]
            return combined
        contract = self._get_contract(ticker, end)
        if contract is None:
            return pd.DataFrame()
        if start is None:
            start = end - pd.Timedelta(days=self._history_days(interval))
            logger.info(
                "IBKR history limited to last %s days for %s (%s).",
                self._history_days(interval),
                ticker,
                interval,
            )
        return self._fetch_range(contract, start, end, interval)

    def _fetch_batch_ohlcv(
        self,
        tickers: List[str],
        period: Optional[str] = "1y",
        interval: str = "1d",
        force_update: bool = False,
        as_of_date: Optional[pd.Timestamp] = None,
    ) -> Dict[str, pd.DataFrame]:
        if not tickers:
            return {}

        if self.read_only_cache:
            results: Dict[str, pd.DataFrame] = {}
            missing: List[str] = []
            metadata = self.cache.get_metadata(tickers, interval)
            current_time = self._to_utc_naive(as_of_date) or pd.Timestamp.utcnow().tz_localize(None)
            required_start = self._period_required_start(period, current_time)

            for ticker in tickers:
                meta = metadata.get(ticker)
                if meta is None:
                    missing.append(ticker)
                    continue
                start_date = self._to_utc_naive(meta[0])
                last_date = self._to_utc_naive(meta[1])
                cached_df = self.cache.get([ticker], interval).get(ticker)
                if cached_df is None or cached_df.empty or last_date is None:
                    missing.append(ticker)
                    continue
                if current_time is not None and last_date < current_time:
                    missing.append(ticker)
                    continue
                if required_start is not None and (start_date is None or start_date > required_start):
                    missing.append(ticker)
                    continue
                results[ticker] = cached_df

            if missing and not self.allow_partial:
                raise ValueError(f"IBKR read-only cache missing tickers: {', '.join(sorted(set(missing)))}")
            if missing and self.allow_partial:
                logger.warning(
                    "IBKR read-only cache missing data for symbols: %s",
                    ", ".join(sorted(set(missing))),
                )
            return results

        results: Dict[str, pd.DataFrame] = {}
        missing: List[str] = []
        to_download_full: List[str] = []
        to_download_partial: Dict[str, pd.Timestamp] = {}

        metadata = self.cache.get_metadata(tickers, interval)
        current_time = self._to_utc_naive(as_of_date) or pd.Timestamp.utcnow().tz_localize(None)
        interval_delta = self._interval_to_timedelta(interval)
        required_start = self._period_required_start(period, current_time)

        for ticker in tickers:
            if force_update:
                to_download_full.append(ticker)
                continue

            meta = metadata.get(ticker)
            if meta is None:
                to_download_full.append(ticker)
                continue

            start_date = self._to_utc_naive(meta[0])
            last_date = self._to_utc_naive(meta[1])
            if last_date is None:
                to_download_full.append(ticker)
                continue
            logger.info(
                "Cache range for %s (%s): %s -> %s",
                ticker,
                interval,
                start_date,
                last_date,
            )

            if last_date is not None and current_time is not None and last_date >= current_time:
                cached_df = self.cache.get([ticker], interval).get(ticker)
                if cached_df is not None:
                    results[ticker] = cached_df
                    continue
                to_download_full.append(ticker)
                continue

            if required_start is not None and (start_date is None or start_date > required_start):
                to_download_full.append(ticker)
                continue

            if interval_delta is not None and (current_time - last_date) <= interval_delta:
                cached_df = self.cache.get([ticker], interval).get(ticker)
                if cached_df is not None:
                    results[ticker] = cached_df
                continue

            to_download_partial[ticker] = last_date

        for ticker in to_download_full:
            df = self._fetch_history(ticker, interval, required_start, current_time)
            if df.empty:
                missing.append(ticker)
                continue
            self.cache.save({ticker: df}, interval)
            results[ticker] = df

        for ticker, start_dt in to_download_partial.items():
            df = self._fetch_history(ticker, interval, start_dt, current_time)
            if df.empty:
                cached_df = self.cache.get([ticker], interval).get(ticker)
                if cached_df is not None:
                    results[ticker] = cached_df
                else:
                    missing.append(ticker)
                continue
            self.cache.update(ticker, df, interval)
            full_df = self.cache.get([ticker], interval).get(ticker)
            if full_df is not None:
                results[ticker] = full_df

        if missing and not self.allow_partial:
            raise ValueError(f"IBKR data missing tickers: {', '.join(sorted(set(missing)))}")
        if missing and self.allow_partial:
            logger.warning("IBKR missing data for symbols: %s", ", ".join(sorted(set(missing))))

        return results

    def fetch_batch_data(
        self,
        tickers: List[str],
        period: Optional[str] = "1y",
        interval: str = "1d",
        force_update: bool = False,
        as_of_date: Optional[pd.Timestamp] = None,
    ) -> Dict[str, pd.DataFrame]:
        if not tickers:
            return {}

        base_interval = self._base_interval_for(interval)
        if base_interval != interval:
            logger.info(
                "IBKR using base interval %s for %s; resampling after cache update.",
                base_interval,
                interval,
            )
            base_results = self._fetch_batch_ohlcv(
                tickers=tickers,
                period=period,
                interval=base_interval,
                force_update=force_update,
                as_of_date=as_of_date,
            )
            current_time = self._to_utc_naive(as_of_date) or pd.Timestamp.utcnow().tz_localize(None)
            required_start = self._period_required_start(period, current_time)
            results: Dict[str, pd.DataFrame] = {}
            for ticker, df in base_results.items():
                resampled = self._resample_ohlcv(df, interval)
                if required_start is not None:
                    resampled = resampled.loc[resampled.index >= required_start]
                if current_time is not None:
                    resampled = resampled.loc[resampled.index <= current_time]
                if resampled.empty:
                    continue
                results[ticker] = resampled
                # Drop 1m data as soon as we resample to reduce memory usage
                base_results[ticker] = pd.DataFrame()
            base_results.clear()
            if self.compute_indicators:
                for ticker, df in list(results.items()):
                    results[ticker] = add_common_indicators(df)
            return results

        results = self._fetch_batch_ohlcv(
            tickers=tickers,
            period=period,
            interval=interval,
            force_update=force_update,
            as_of_date=as_of_date,
        )
        if self.compute_indicators:
            for ticker, df in list(results.items()):
                results[ticker] = add_common_indicators(df)
        return results

    def prefetch_signature(self) -> Optional[Dict[str, Any]]:
        return {
            "type": "ibkr",
            "host": self.host,
            "port": self.port,
            "client_id": self.client_id,
            "what_to_show": self.what_to_show,
            "use_rth": self.use_rth,
            "include_expired": self.include_expired,
            "cache_dir": str(self.cache.cache_dir),
        }


def _expand_static_files(entries: List[Any], config_path: Optional[str]) -> List[Dict[str, Any]]:
    expanded: List[Dict[str, Any]] = []
    for entry in entries:
        if isinstance(entry, str):
            entry = {"path": entry}
        if not isinstance(entry, dict):
            continue
        raw_path = entry.get("path")
        if not raw_path:
            continue
        resolved = _resolve_path(config_path, raw_path)
        matches = glob.glob(resolved)
        if not matches:
            raise ValueError(f"Static file path not found: {resolved}")
        ticker = entry.get("ticker")
        if ticker and len(matches) > 1:
            raise ValueError(f"Static file path expanded to multiple files with one ticker: {raw_path}")
        for match in matches:
            expanded.append({"path": match, "ticker": ticker})
    return expanded


def build_data_provider(
    data_source: Optional[Dict[str, Any]],
    config_path: Optional[str] = None,
    tickers: Optional[List[str]] = None,
    cache_dir: str = ".cache",
    compute_indicators: bool = True,
    read_only_cache: bool = False,
) -> BaseDataProvider:
    if not data_source:
        return DataProvider(
            cache_dir=cache_dir,
            compute_indicators=compute_indicators,
            read_only_cache=read_only_cache,
        )
    source_type = str(data_source.get("type", "yfinance")).lower()
    if source_type in {"yfinance", "yf", "yahoo"}:
        return DataProvider(
            cache_dir=cache_dir,
            compute_indicators=compute_indicators,
            read_only_cache=read_only_cache,
        )
    if source_type == "ibkr":
        ibkr_cfg = data_source.get("ibkr", {}) or {}
        return IBKRDataProvider(
            host=ibkr_cfg.get("host", "127.0.0.1"),
            port=int(ibkr_cfg.get("port", 7497)),
            client_id=int(ibkr_cfg.get("client_id", 1)),
            cache_dir=ibkr_cfg.get("cache_dir", ".cache_ibkr"),
            allow_partial=bool(ibkr_cfg.get("allow_partial", True)),
            read_only_cache=read_only_cache,
            max_requests_per_min=int(ibkr_cfg.get("max_requests_per_min", 6)),
            min_sleep_seconds=float(ibkr_cfg.get("min_sleep_seconds", 10.0)),
            connect_timeout=int(ibkr_cfg.get("connect_timeout", 10)),
            contracts=ibkr_cfg.get("contracts"),
            what_to_show=ibkr_cfg.get("what_to_show", "TRADES"),
            use_rth=bool(ibkr_cfg.get("use_rth", False)),
            include_expired=bool(ibkr_cfg.get("include_expired", True)),
            default_stock_exchange=ibkr_cfg.get("default_stock_exchange", "SMART"),
            default_futures_exchange=ibkr_cfg.get("default_futures_exchange", "CME"),
            default_currency=ibkr_cfg.get("default_currency", "USD"),
            auto_futures_months_ahead=int(ibkr_cfg.get("auto_futures_months_ahead", 2)),
            request_timeout=float(ibkr_cfg.get("request_timeout", 300.0)),
            max_history_days=ibkr_cfg.get("max_history_days"),
            max_history_days_by_interval=ibkr_cfg.get("max_history_days_by_interval"),
            futures_use_end_datetime=bool(ibkr_cfg.get("futures_use_end_datetime", False)),
            futures_contract_mode=ibkr_cfg.get("futures_contract_mode", "auto_roll"),
            compute_indicators=compute_indicators,
        )
    if source_type != "static":
        raise ValueError(f"Unsupported data_source type: {source_type}")

    static_cfg = data_source.get("static", {}) or {}
    files = _expand_static_files(static_cfg.get("files") or [], config_path)
    if not files:
        raise ValueError("Static data_source requires at least one file.")
    default_ticker = static_cfg.get("ticker")
    if default_ticker is None and tickers and len(tickers) == 1:
        default_ticker = tickers[0]
    if default_ticker and len(files) > 1:
        if any(entry.get("ticker") is None for entry in files):
            raise ValueError("Static data_source requires per-file ticker when multiple files are provided.")
    provider = StaticFileDataProvider(
        files=files,
        file_interval=static_cfg.get("file_interval"),
        resample_to=static_cfg.get("resample_to"),
        timezone=static_cfg.get("timezone", "America/Denver"),
        timestamp_col=static_cfg.get("timestamp_col"),
        timestamp_format=static_cfg.get("timestamp_format"),
        compute_indicators=compute_indicators,
        default_ticker=default_ticker,
    )
    return provider
