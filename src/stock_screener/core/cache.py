import pandas as pd
import logging
from pathlib import Path
from typing import List, Optional, Dict, Tuple

logger = logging.getLogger(__name__)

class CacheManager:
    def __init__(self, cache_dir: str = ".cache"):
        """
        Initialize the Cache Manager.
        
        Args:
            cache_dir: Directory to store cache files.
        """
        self.cache_dir = Path(cache_dir)
        
        # Create cache directory if it doesn't exist
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"Failed to create cache directory: {e}")

    def _get_file_path(self, ticker: str, interval: str) -> Path:
        """Generate file path for a ticker and interval."""
        return self.cache_dir / f"{ticker}_{interval}.parquet"

    def _ensure_str_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Ensure Parquet-friendly column names."""
        if all(isinstance(col, str) for col in df.columns):
            return df
        sanitized = df.copy()
        sanitized.columns = [str(col) for col in sanitized.columns]
        return sanitized

    def _remove_corrupt_cache(self, file_path: Path, ticker: str, error: Exception) -> None:
        try:
            file_path.unlink(missing_ok=True)
        except Exception:
            logger.warning("Failed to remove corrupt cache for %s after error: %s", ticker, error)

    def get_metadata(self, tickers: List[str], interval: str) -> Dict[str, Optional[Tuple[pd.Timestamp, pd.Timestamp]]]:
        """
        Get the start and end dates for cached data of each ticker.
        Returns a dict: {ticker: (start_date, end_date) or None}
        """
        metadata = {}
        for ticker in tickers:
            file_path = self._get_file_path(ticker, interval)
            if not file_path.exists():
                metadata[ticker] = None
                continue
                
            try:
                df = pd.read_parquet(file_path)
                if df.empty:
                    metadata[ticker] = None
                else:
                    metadata[ticker] = (df.index[0], df.index[-1])
            except Exception as e:
                self._remove_corrupt_cache(file_path, ticker, e)
                metadata[ticker] = None
        return metadata

    def get(self, tickers: List[str], interval: str) -> Dict[str, pd.DataFrame]:
        """
        Retrieve data from cache.
        """
        results = {}
        for ticker in tickers:
            file_path = self._get_file_path(ticker, interval)
            if file_path.exists():
                try:
                    df = pd.read_parquet(file_path)
                    results[ticker] = df
                except Exception as e:
                    logger.warning(f"Failed to read cache for {ticker}: {e}")
                    self._remove_corrupt_cache(file_path, ticker, e)
        return results

    def save(self, data_dict: Dict[str, pd.DataFrame], interval: str) -> None:
        """
        Save fresh data to cache (overwrites existing).
        """
        for ticker, df in data_dict.items():
            if df.empty:
                continue
            file_path = self._get_file_path(ticker, interval)
            try:
                self._ensure_str_columns(df).to_parquet(file_path)
            except Exception as e:
                logger.error(f"Failed to save cache for {ticker}: {e}")

    def update(self, ticker: str, new_data: pd.DataFrame, interval: str) -> bool:
        """
        Update cache for a ticker with new data.
        Returns True if successful, False if a full refresh is needed (gap detected).
        """
        file_path = self._get_file_path(ticker, interval)

        # Normalize incoming data to plain OHLCV columns (yfinance can return MultiIndex for single tickers)
        if isinstance(new_data.columns, pd.MultiIndex):
            if ticker in new_data.columns.get_level_values(0):
                new_data = new_data[ticker].copy()
            else:
                new_data.columns = [c[-1] if isinstance(c, tuple) else c for c in new_data.columns]
        new_data = new_data.loc[:, ~new_data.columns.duplicated()]
        
        if not file_path.exists():
            try:
                self._ensure_str_columns(new_data).to_parquet(file_path)
                return True
            except Exception as e:
                logger.error(f"Failed to create new cache for {ticker}: {e}")
                return True # Technically not a gap, just a save error
                
        try:
            cached_df = pd.read_parquet(file_path)
            
            if cached_df.empty:
                self._ensure_str_columns(new_data).to_parquet(file_path)
                return True

            combined = pd.concat([cached_df, new_data])
            combined = combined[~combined.index.duplicated(keep='last')] # Keep new data in case of overlap corrections
            combined.sort_index(inplace=True)

            self._ensure_str_columns(combined).to_parquet(file_path)
            return True
            
        except Exception as e:
            logger.error(f"Failed to update cache for {ticker}: {e}")
            self._remove_corrupt_cache(file_path, ticker, e)
            try:
                self._ensure_str_columns(new_data).to_parquet(file_path)
                return True
            except Exception as write_err:
                logger.error(f"Failed to rewrite cache for {ticker}: {write_err}")
            return False

    def clear(self):
        """Delete all files in cache."""
        for p in self.cache_dir.glob("*.parquet"):
            try:
                p.unlink()
            except Exception:
                pass
