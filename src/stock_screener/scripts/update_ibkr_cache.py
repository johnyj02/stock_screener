import argparse
import logging
from pathlib import Path
from typing import Iterable, List, Optional

import pandas as pd

from stock_screener.core.data import IBKRDataProvider, build_data_provider


logger = logging.getLogger(__name__)


def _load_tickers(path: Path) -> List[str]:
    try:
        df = pd.read_csv(path)
    except Exception as exc:
        logger.error("Failed to load tickers from %s: %s", path, exc)
        return []
    for col in ("Symbol", "symbol", "Ticker", "ticker"):
        if col in df.columns:
            series = df[col]
            break
    else:
        series = df.iloc[:, 0] if not df.empty else []
    tickers = []
    for value in series:
        if pd.isna(value):
            continue
        item = str(value).strip()
        if item:
            tickers.append(item)
    return tickers


def _unique(items: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _merge_and_save(
    provider: IBKRDataProvider,
    ticker: str,
    interval: str,
    existing: Optional[pd.DataFrame],
    additions: List[pd.DataFrame],
) -> None:
    frames = []
    if existing is not None and not existing.empty:
        frames.append(existing)
    frames.extend([df for df in additions if df is not None and not df.empty])
    if not frames:
        logger.warning("No data available for %s (%s).", ticker, interval)
        return
    combined = pd.concat(frames).sort_index()
    combined = combined.loc[~combined.index.duplicated(keep="last")]
    provider.cache.save({ticker: combined}, interval)


def _update_ticker(
    provider: IBKRDataProvider,
    ticker: str,
    interval: str,
    desired_start: Optional[pd.Timestamp],
    desired_end: pd.Timestamp,
    interval_delta: Optional[pd.Timedelta],
) -> Optional[dict]:
    existing = provider.cache.get([ticker], interval).get(ticker)
    if existing is None or existing.empty:
        logger.info("No cache for %s (%s). Fetching full window.", ticker, interval)
        if desired_start is not None:
            logger.info(
                "Downloading %s (%s): %s -> %s",
                ticker,
                interval,
                desired_start,
                desired_end,
            )
        df = provider._fetch_history(ticker, interval, desired_start, desired_end)
        _merge_and_save(provider, ticker, interval, None, [df])
        return {
            "ticker": ticker,
            "before_start": None,
            "before_end": None,
            "after_start": provider._to_utc_naive(df.index.min()) if not df.empty else None,
            "after_end": provider._to_utc_naive(df.index.max()) if not df.empty else None,
        }

    existing_start = provider._to_utc_naive(existing.index.min())
    existing_end = provider._to_utc_naive(existing.index.max())
    logger.info(
        "Cache range for %s (%s): %s -> %s",
        ticker,
        interval,
        existing_start,
        existing_end,
    )

    additions: List[pd.DataFrame] = []
    if desired_start is not None and existing_start is not None and existing_start > desired_start:
        logger.info(
            "Fetching missing head for %s (%s): %s -> %s",
            ticker,
            interval,
            desired_start,
            existing_start,
        )
        logger.info(
            "Downloading %s (%s): %s -> %s",
            ticker,
            interval,
            desired_start,
            existing_start,
        )
        additions.append(provider._fetch_history(ticker, interval, desired_start, existing_start))

    tail_cutoff = desired_end
    if interval_delta is not None:
        tail_cutoff = desired_end - interval_delta
    if existing_end is None or existing_end < tail_cutoff:
        logger.info(
            "Fetching missing tail for %s (%s): %s -> %s",
            ticker,
            interval,
            existing_end,
            desired_end,
        )
        logger.info(
            "Downloading %s (%s): %s -> %s",
            ticker,
            interval,
            existing_end,
            desired_end,
        )
        additions.append(provider._fetch_history(ticker, interval, existing_end, desired_end))

    if additions:
        _merge_and_save(provider, ticker, interval, existing, additions)
    else:
        logger.info("Cache already up to date for %s (%s).", ticker, interval)

    refreshed = provider.cache.get([ticker], interval).get(ticker)
    after_start = provider._to_utc_naive(refreshed.index.min()) if refreshed is not None and not refreshed.empty else None
    after_end = provider._to_utc_naive(refreshed.index.max()) if refreshed is not None and not refreshed.empty else None
    return {
        "ticker": ticker,
        "before_start": existing_start,
        "before_end": existing_end,
        "after_start": after_start,
        "after_end": after_end,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update IBKR cache for a list of tickers.")
    parser.add_argument(
        "--tickers-file",
        action="append",
        default=[],
        help="CSV file containing a Symbol column (can be provided multiple times).",
    )
    parser.add_argument("--tickers", default="", help="Comma-separated list of tickers to include.")
    parser.add_argument("--interval", default="1m", help="Bar interval to fetch (default: 1m).")
    parser.add_argument(
        "--max-history-days",
        type=int,
        default=1825,
        help="Max history window (days) to backfill for the interval.",
    )
    parser.add_argument("--host", default="172.31.112.1")
    parser.add_argument("--port", type=int, default=7497)
    parser.add_argument("--client-id", type=int, default=1)
    parser.add_argument("--allow-partial", action="store_true", default=True)
    parser.add_argument("--what-to-show", default="TRADES")
    parser.add_argument("--use-rth", action="store_true", default=False)
    parser.add_argument("--max-requests-per-min", type=int, default=6)
    parser.add_argument("--min-sleep-seconds", type=float, default=10.0)
    parser.add_argument("--auto-futures-months-ahead", type=int, default=2)
    parser.add_argument("--futures-contract-mode", default="auto_roll")
    parser.add_argument("--cache-dir", default=".cache_ibkr")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    repo_root = Path(__file__).resolve().parents[3]
    default_files = [
        repo_root / "data" / "futures_only.csv",
        repo_root / "data" / "sp500_top50.csv",
    ]
    use_defaults = not args.tickers_file and not args.tickers
    ticker_files = default_files if use_defaults else [Path(p) for p in args.tickers_file]
    tickers: List[str] = []
    for path in ticker_files:
        tickers.extend(_load_tickers(path))
    if args.tickers:
        tickers.extend([t.strip() for t in args.tickers.split(",") if t.strip()])
    tickers = _unique(tickers)
    if not tickers:
        logger.error("No tickers found.")
        return 1

    data_source = {
        "type": "ibkr",
        "ibkr": {
            "host": args.host,
            "port": args.port,
            "client_id": args.client_id,
            "cache_dir": args.cache_dir,
            "allow_partial": args.allow_partial,
            "what_to_show": args.what_to_show,
            "use_rth": args.use_rth,
            "max_requests_per_min": args.max_requests_per_min,
            "min_sleep_seconds": args.min_sleep_seconds,
            "auto_futures_months_ahead": args.auto_futures_months_ahead,
            "futures_contract_mode": args.futures_contract_mode,
            "max_history_days_by_interval": {args.interval: args.max_history_days},
        },
    }

    provider = build_data_provider(data_source, compute_indicators=False)
    if not isinstance(provider, IBKRDataProvider):
        logger.error("Expected IBKR data provider but got %s", type(provider).__name__)
        return 1

    interval = args.interval
    now = pd.Timestamp.utcnow().tz_localize(None)
    desired_start = now - pd.Timedelta(days=args.max_history_days)
    interval_delta = provider._interval_to_timedelta(interval)

    logger.info("Updating IBKR cache for %s tickers (%s).", len(tickers), interval)
    logger.info("Target history window: %s -> %s", desired_start, now)

    failures = 0
    failed_tickers: List[str] = []
    ranges = []
    for ticker in tickers:
        try:
            record = _update_ticker(provider, ticker, interval, desired_start, now, interval_delta)
            if record:
                ranges.append(record)
        except Exception:
            failures += 1
            failed_tickers.append(ticker)
            logger.exception("Failed to update %s (%s).", ticker, interval)

    if ranges:
        header = f"{'Ticker':<12} {'Before Start':<20} {'Before End':<20} {'After Start':<20} {'After End':<20}"
        logger.info("Cache ranges (before -> after):")
        logger.info(header)
        logger.info("-" * len(header))
        for row in ranges:
            before_start = row["before_start"].strftime("%Y-%m-%d") if row["before_start"] else "-"
            before_end = row["before_end"].strftime("%Y-%m-%d") if row["before_end"] else "-"
            after_start = row["after_start"].strftime("%Y-%m-%d") if row["after_start"] else "-"
            after_end = row["after_end"].strftime("%Y-%m-%d") if row["after_end"] else "-"
            logger.info(
                f"{row['ticker']:<12} {before_start:<20} {before_end:<20} {after_start:<20} {after_end:<20}"
            )

    if failures:
        logger.warning("Completed with %s failures.", failures)
        if failed_tickers:
            logger.warning("Failed tickers: %s", ", ".join(sorted(set(failed_tickers))))
        return 1
    logger.info("Completed cache update successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
