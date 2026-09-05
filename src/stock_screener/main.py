import sys
import os
import argparse
import pandas as pd
from tabulate import tabulate
import logging
from colorama import init, Fore, Style

# Ensure package is in path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from stock_screener.core.engine import ScreenerEngine

# Constants
DATA_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'data')
DEFAULT_TICKERS_FILE = os.path.join(DATA_DIR, 'sp500.csv')

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def load_tickers(file_path: str) -> list:
    """Load tickers from CSV file."""
    if not os.path.exists(file_path):
        logger.error(f"Ticker file not found: {file_path}")
        return []
        
    try:
        df = pd.read_csv(file_path)
        if 'Symbol' in df.columns:
            return df['Symbol'].tolist()
        else:
            # Assume no header, first column
            return df.iloc[:, 0].tolist()
    except Exception as e:
        logger.error(f"Error reading tickers: {e}")
        return []

def main():
    init() # Colorama init
    
    parser = argparse.ArgumentParser(description="Advanced Stock Screener")
    parser.add_argument('--limit', type=int, default=50, help='Limit number of tickers to screen (default: 50)')
    parser.add_argument('--tickers', type=str, help='Comma separated list of tickers manually')
    parser.add_argument('--period', type=str, default="max", help='Data period to download (e.g. 1y, 6mo, 2y)')
    parser.add_argument('--interval', type=str, default="1d", help='Data interval (e.g. 1d, 1h, 15m)')
    parser.add_argument('--force-update', action='store_true', help='Force refresh of data cache (ignore local files)')
    args = parser.parse_args()
    
    # 1. Get Tickers
    if args.tickers:
        tickers = args.tickers.split(',')
    else:
        logger.info(f"Loading tickers from {DEFAULT_TICKERS_FILE}...")
        tickers = load_tickers(DEFAULT_TICKERS_FILE)
        
    if args.limit and args.limit > 0:
        tickers = tickers[:args.limit]
        
    logger.info(f"Starting screener for {len(tickers)} tickers ({args.period}/{args.interval})...")
    if args.force_update:
        logger.info("Force Update ENABLED: Downloading fresh data.")
    
    # 2. Run Engine
    engine = ScreenerEngine()
    results = engine.run(tickers, period=args.period, interval=args.interval, force_update=args.force_update)
    
    # 3. Output Results
    if not results:
        print(Fore.YELLOW + "\nNo stocks matched any strategy." + Style.RESET_ALL)
        return

    print(Fore.GREEN + f"\nfound {len(results)} matches:\n" + Style.RESET_ALL)
    
    # Format for table
    table_data = []
    for r in results:
        symbol = r['symbol']
        price = f"${r['price']:.2f}"
        matches = ", ".join(r['matches'])
        
        # Extract sentiment
        metrics_by_strategy = r['metrics']
        sentiments = {m.get('sentiment') for m in metrics_by_strategy.values() if m.get('sentiment')}
        if 'BULLISH' in sentiments and 'BEARISH' in sentiments:
            sentiment = 'MIXED'
        elif len(sentiments) == 1:
            sentiment = next(iter(sentiments))
        elif 'WATCH' in sentiments:
            sentiment = 'WATCH'
        else:
            sentiment = 'N/A'
        
        # Determine color for sentiment
        sent_color = Fore.WHITE
        if sentiment == 'BULLISH':
            sent_color = Fore.GREEN
        elif sentiment == 'BEARISH':
            sent_color = Fore.RED
        elif sentiment in {'WATCH', 'MIXED'}:
            sent_color = Fore.YELLOW
            
        sentiment_str = sent_color + sentiment + Style.RESET_ALL
        
        # Extract separated Risk Columns
        stop_losses = []
        triggers = []
        display_metrics = []
        for strat, metrics in metrics_by_strategy.items():
            if 'stop_loss' in metrics:
                stop_losses.append(f"{strat}:{metrics['stop_loss']}")
            if 'breakout_lvl' in metrics:
                triggers.append(f"{strat}:{metrics['breakout_lvl']}")

            for k, v in metrics.items():
                if k not in ['sentiment', 'stop_loss', 'breakout_lvl', 'pattern', 'score']:
                    display_metrics.append(f"{strat}.{k}: {v}")

        stop_loss = ", ".join(stop_losses) if stop_losses else '-'
        trigger = ", ".join(triggers) if triggers else '-'
        
        # Format remaining metrics
        metrics_str = ", ".join(display_metrics)
        
        table_data.append([symbol, price, sentiment_str, matches, stop_loss, trigger, metrics_str])
        
    headers = ["Symbol", "Price", "Sentiment", "Strategies", "Stop Loss", "Trigger", "Key Metrics"]
    
    print(tabulate(table_data, headers=headers, tablefmt="grid"))
    
    # Summary of strategies
    print("\nStrategy Hit Counts:")
    all_hits = [m for r in results for m in r['matches']]
    from collections import Counter
    counts = Counter(all_hits)
    for res, count in counts.most_common():
         print(f"  - {res}: {count}")

if __name__ == "__main__":
    main()
