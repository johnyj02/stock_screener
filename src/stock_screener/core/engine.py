"""
Main execution engine for the stock screener.
"""
import logging
import pandas as pd
from typing import List, Dict, Any
from tqdm import tqdm

from stock_screener.core.data import DataProvider
from stock_screener.core.indicators import add_market_context_columns
from stock_screener.core.loader import StrategyLoader

logger = logging.getLogger(__name__)

class ScreenerEngine:
    def __init__(self):
        self.data_provider = DataProvider()
        self.loader = StrategyLoader()
        self.strategies = self.loader.load_strategies()

    def _market_symbols(self) -> List[str]:
        symbols: List[str] = []
        for strategy in self.strategies:
            market_symbols = getattr(strategy, "market_symbols", None)
            if not market_symbols:
                continue
            for symbol in market_symbols:
                if symbol:
                    symbols.append(symbol)
        return list(dict.fromkeys(symbols))
        
    def run(self, tickers: List[str], period: str = "1y", interval: str = "1d", force_update: bool = False, as_of_date: str = None, data_source: Dict[str, pd.DataFrame] = None) -> List[Dict[str, Any]]:
        """
        Run the screener on a list of tickers.
        
        Args:
            tickers: List of ticker symbols to screen.
            period: Data period to download (default "1y").
            interval: Data interval (default "1d").
            force_update: If True, bypass cache and download fresh.
            as_of_date: Optional date string (YYYY-MM-DD) to simulate screening as of that day.
            data_source: Optional dictionary of pre-loaded DataFrames to use instead of fetching.
        """
        if not self.strategies:
            logger.warning("No strategies loaded! Please add strategy files to src/stock_screener/strategies/")
            return []
            
        # 1. Fetch Data (or use provided source)
        market_symbols = self._market_symbols()
        if data_source:
            fetch_list = list(dict.fromkeys(tickers + market_symbols))
            data_map = {t: data_source[t] for t in fetch_list if t in data_source}
        else:
            logger.info(f"Fetching market data ({period}/{interval})...")
            fetch_list = list(dict.fromkeys(tickers + market_symbols))
            data_map = self.data_provider.fetch_batch_data(fetch_list, period=period, interval=interval, force_update=force_update)
        
        # Filter for as_of_date
        if as_of_date:
            target_date = pd.Timestamp(as_of_date).tz_localize(None)
            filtered_map = {}
            for t, df in data_map.items():
                # Ensure index is naive or handle TZ correctly. 
                # usually yfinance is TZ-aware. modifying target to be safe?
                # Simplest: convert df index to naive for comparison or target to aware.
                # data_map usually has tz-aware GMT index.
                try:
                    if df.index.tz is not None:
                       target_cmp = target_date.tz_localize(df.index.tz)
                    else:
                       target_cmp = target_date
                    
                    # Filter
                    mask = df.index <= target_cmp
                    filtered_df = df[mask]
                    if not filtered_df.empty:
                        filtered_map[t] = filtered_df
                except Exception as e:
                    logger.warning(f"Date filtering failed for {t}: {e}")
            data_map = filtered_map

        add_market_context_columns(data_map)
        
        results = []
        
        # 2. Iterate Tickers
        logger.info(f"Screening {len(data_map)} tickers against {len(self.strategies)} strategies...")
        
        for symbol in tqdm(tickers, desc="Screening"):
            df = data_map.get(symbol)
            if df is None or df.empty:
                continue
                
            matched_strategies = []
            matched_metrics = {}
            
            # Get latest price for display
            try:
                current_price = df['Close'].iloc[-1]
            except Exception:
                current_price = 0.0
            
            # 3. Apply Strategies
            for strategy in self.strategies:
                try:
                    is_match, metrics = strategy.check(df)
                    
                    if is_match:
                        matched_strategies.append(strategy.get_name())
                        if metrics:
                            matched_metrics[strategy.get_name()] = metrics
                            
                except Exception as e:
                    logger.debug(f"Error running {strategy.get_name()} on {symbol}: {e}")
            
            # 4. Collect Result if any matches
            if matched_strategies:
                result = {
                    'symbol': symbol,
                    'price': current_price,
                    'matches': matched_strategies,
                    'metrics': matched_metrics
                }
                results.append(result)
                
        return results
