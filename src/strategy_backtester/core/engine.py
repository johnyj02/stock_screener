import logging
import math
import os
from typing import List, Dict, Any, Optional, Tuple

import numpy as np
import pandas as pd
from tqdm import tqdm

from stock_screener.core.engine import ScreenerEngine
from stock_screener.core.data import BaseDataProvider, DataProvider
from stock_screener.core.indicators import add_market_context_columns
from stock_screener.core.regime_features import add_intraday_orb_day_type
from stock_screener.core.event_calendar import EventCalendar
from .contracts import DEFAULT_CONTRACT_MULTIPLIERS, contract_multiplier, is_future_symbol
from .metrics import equity_metrics, group_trade_metrics
from .regime import regime_state as get_regime_state, RegimeState
from .regime_models import load_regime_model
from .sizing import portfolio_vol_scale, resolve_stop_loss

logger = logging.getLogger(__name__)

class BacktestEngine:
    def __init__(
        self,
        start_date: str,
        universe: List[str],
        end_date: Optional[str] = None,
        interval: str = "1d",
        strategies: Optional[List[Any]] = None,
        data_provider: Optional[BaseDataProvider] = None,
        use_vectorized: bool = True,
        initial_capital: float = 100000.0,
        trade_size: float = 3000.0,
        max_alloc_pct: float = 0.40,
        risk_per_trade_pct: float = 0.005,
        portfolio_vol_target: float = 0.11,
        portfolio_vol_lookback: int = 63,
        regime_symbol: str = "SPY",
        regime: Optional[Dict[str, Any]] = None,
        hedge_symbol: str = "MES=F",
        allow_risk_off_entries: bool = False,
        risk_off_size_mult: float = 0.5,
        exit_on_risk_off: bool = True,
        allow_short_risk_on: bool = False,
        allow_short_equity: bool = False,
        force_update_cache: bool = False,
        futures_margin_pct: Optional[float] = None,
        max_new_positions_per_day: int = 20,
        liquidity_lookback: int = 20,
        min_avg_dollar_vol: float = 20_000_000.0,
        min_avg_volume_futures: float = 1000.0,
        cooldown_days: int = 5,
        regime_stability_days: int = 4,
        breakeven_r: float = -0.1,
        breakeven_delay_bars: int = 1,
        regime_flip_band_pct: float = 0.0075,
        regime_flip_slope_pct: float = 0.0005,
        commission_per_trade: float = 0.0,
        commission_pct: float = 0.0,
        slippage_bps: float = 0.0,
        min_trade_notional: float = 0.0,
        min_equity_shares: int = 1,
        position_qty_epsilon: float = 1e-6,
        skip_trade_if_notional_lt_min: bool = True,
        equity_qty_rounding: str = "floor_int",
        book_allocator: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize Backtester.
        
        Args:
            start_date: Start date for backtest (YYYY-MM-DD).
            universe: List of tickers to monitor (e.g. SP500).
        """
        self.start_date = pd.Timestamp(start_date).tz_localize(None)
        self.end_date = pd.Timestamp(end_date).tz_localize(None) if end_date else None
        self.interval = interval
        self.universe = universe
        self.screener = ScreenerEngine()
        if strategies is not None:
            self.screener.strategies = strategies
        self.data_provider = data_provider or DataProvider()
        self.screener.data_provider = self.data_provider
        self.use_vectorized = use_vectorized
        self.initial_capital = initial_capital
        self.trade_size = trade_size
        self.max_alloc_pct = max_alloc_pct
        self.risk_per_trade_pct = risk_per_trade_pct
        self.portfolio_vol_target = portfolio_vol_target
        self.portfolio_vol_lookback = portfolio_vol_lookback
        self.regime_symbol = regime_symbol
        self.regime_config = regime or {}
        self.hedge_symbol = hedge_symbol
        self.allow_risk_off_entries = allow_risk_off_entries
        self.risk_off_size_mult = risk_off_size_mult
        self.exit_on_risk_off = exit_on_risk_off
        self.allow_short_risk_on = allow_short_risk_on
        self.allow_short_equity = allow_short_equity
        self.force_update_cache = force_update_cache
        self.futures_margin_pct = futures_margin_pct
        self.max_new_positions_per_day = max_new_positions_per_day
        self.liquidity_lookback = liquidity_lookback
        self.min_avg_dollar_vol = min_avg_dollar_vol
        self.min_avg_volume_futures = min_avg_volume_futures
        self.cooldown_days = cooldown_days
        self.regime_stability_days = regime_stability_days
        self.breakeven_r = breakeven_r
        self.breakeven_delay_bars = breakeven_delay_bars
        self.regime_flip_band_pct = regime_flip_band_pct
        self.regime_flip_slope_pct = regime_flip_slope_pct
        self.commission_per_trade = commission_per_trade
        self.commission_pct = commission_pct
        self.slippage_bps = slippage_bps
        self.min_trade_notional = min_trade_notional
        self.min_equity_shares = min_equity_shares
        self.position_qty_epsilon = position_qty_epsilon
        self.skip_trade_if_notional_lt_min = skip_trade_if_notional_lt_min
        self.equity_qty_rounding = equity_qty_rounding
        self.book_allocator = book_allocator or {}
        self.book_allocator_enabled = bool(self.book_allocator.get("books"))
        self.book_total_risk_pct = float(self.book_allocator.get("total_risk_budget_pct", 0.10))
        self.book_index_floor = float(self.book_allocator.get("book_index_floor", 0.0) or 0.0)
        self.portfolio_drawdown_throttle_pct = float(self.book_allocator.get("portfolio_drawdown_throttle_pct", 0.08))
        self.portfolio_throttle_size_mult = float(self.book_allocator.get("portfolio_throttle_size_mult", 0.5))
        self.portfolio_throttle_disable_adds = bool(self.book_allocator.get("portfolio_throttle_disable_adds", True))
        self.core_book_name = str(self.book_allocator.get("core_book_name", "core"))
        self.convex_book_name = str(self.book_allocator.get("convex_book_name", "convex"))
        self.convex_block_if_core_risk_off = bool(self.book_allocator.get("convex_block_if_core_risk_off", True))
        self.book_recovery_lookback = int(self.book_allocator.get("recovery_lookback_days", 20) or 20)
        self.screen_universe = list(universe)
        self.strategy_map = {s.get_name(): s for s in self.screener.strategies}
        self.full_data: Dict[str, pd.DataFrame] = {}
        self.contract_multipliers = DEFAULT_CONTRACT_MULTIPLIERS.copy()
        self.regime_model_name = None
        self.regime_symbols: List[str] = []
        self.regime_params: Dict[str, Any] = {}
        self.regime_missing_policy: Dict[str, Any] = {}
        self.regime_size_by_default = False
        self.regime_risk_buckets: List[Dict[str, Any]] = []
        self.regime_apply_exposure_caps = False
        self.regime_context_symbols: set[str] = set()
        self._latest_regime_state: Optional[RegimeState] = None
        self.orb_trade_state: Dict[Tuple[str, str, pd.Timestamp], Dict[str, Any]] = {}
        
        # Portfolio State
        self.holdings: Dict[Tuple[str, str], Dict[str, Any]] = {}  # (ticker, strategy) -> holding
        self.trades: List[Dict[str, Any]] = []
        self.equity_curve: List[Dict[str, Any]] = []
        self.cash = initial_capital # Initial Capital
        self.last_exit_dates: Dict[Tuple[str, str], pd.Timestamp] = {}
        self.last_loss_exit_dates: Dict[tuple, pd.Timestamp] = {}
        self.last_stop_exit_dates: Dict[tuple, pd.Timestamp] = {}
        self.recent_signals: Dict[str, Dict[str, pd.Timestamp]] = {}
        self.book_state: Dict[str, Dict[str, Any]] = {}
        self.book_realized_pnl: Dict[str, float] = {}
        self.book_equity_history: Dict[str, List[Dict[str, Any]]] = {}
        self.book_daily_records: List[Dict[str, Any]] = []
        self.book_state_changes: List[Dict[str, Any]] = []
        self.rejection_stats: Dict[pd.Timestamp, Dict[str, Dict[str, int]]] = {}
        self.event_calendar_cache: Dict[str, EventCalendar] = {}
        self.rejection_columns = [
            "signals_seen",
            "accepted",
            "rejected_risk_budget",
            "rejected_max_positions",
            "rejected_liquidity",
            "rejected_regime",
            "rejected_regime_usable",
            "rejected_regime_trend",
            "rejected_regime_vol",
            "rejected_regime_prob",
            "rejected_regime_stability",
            "rejected_regime_flip_avoid",
            "rejected_cooldown",
            "rejected_min_notional",
            "rejected_event_calendar",
            "rejected_other",
        ]
        self.position_id_counter = 1
        self.entry_id_counter = 1
        self.portfolio_peak_equity = initial_capital
        self.portfolio_drawdown_pct = 0.0
        self.portfolio_throttle_active = False
        if self.book_allocator_enabled:
            self._init_book_state()

    def _position_key(self, ticker: str, strategy: Optional[str]) -> Tuple[str, str]:
        return (ticker, strategy or "UNKNOWN")

    def _has_position(self, ticker: str, strategy: Optional[str]) -> bool:
        return self._position_key(ticker, strategy) in self.holdings

    def _init_book_state(self) -> None:
        books = self.book_allocator.get("books", {}) if isinstance(self.book_allocator, dict) else {}
        for name, cfg in (books or {}).items():
            risk_pct = float(cfg.get("risk_budget_pct", 0.0) or 0.0)
            risk_reference = self.initial_capital * self.book_total_risk_pct * risk_pct
            if risk_reference <= 0:
                risk_reference = max(self.initial_capital * self.book_total_risk_pct, 1.0)
            self.book_state[name] = {
                "risk_budget_pct": risk_pct,
                "max_per_trade_risk_pct": float(cfg.get("max_per_trade_risk_pct", 0.0) or 0.0),
                "max_concurrent_risk_pct": float(cfg.get("max_concurrent_risk_pct", risk_pct) or risk_pct),
                "drawdown_reduce_pct": float(cfg.get("drawdown_reduce_pct", 0.0) or 0.0),
                "drawdown_disable_pct": float(cfg.get("drawdown_disable_pct", 0.0) or 0.0),
                "drawdown_reduce_to_pct": float(cfg.get("drawdown_reduce_to_pct", risk_pct) or risk_pct),
                "recovery_lookback_days": int(cfg.get("recovery_lookback_days", self.book_recovery_lookback) or self.book_recovery_lookback),
                "risk_budget_reference": float(risk_reference),
                "disabled": False,
                "throttled": False,
                "index_raw": 1.0,
                "peak_index_raw": 1.0,
                "drawdown_pct_raw": 0.0,
                "index_alloc": 1.0,
                "peak_index_alloc": 1.0,
                "drawdown_pct": 0.0,
            }
            self.book_realized_pnl[name] = 0.0
            self.book_equity_history[name] = []

    def _next_position_id(self) -> int:
        pid = self.position_id_counter
        self.position_id_counter += 1
        return pid

    def _next_entry_id(self) -> int:
        eid = self.entry_id_counter
        self.entry_id_counter += 1
        return eid

    def _allocator_state_label(self, book: Optional[str]) -> Optional[str]:
        if not self.book_allocator_enabled or not book:
            return None
        state = self.book_state.get(book)
        if not state:
            return None
        if state.get("disabled"):
            return "disabled"
        if state.get("throttled"):
            return "throttled"
        return "normal"

    def _book_risk_snapshot(self, book: Optional[str], equity: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
        if not self.book_allocator_enabled or not book or not equity:
            return None, None
        pct = self._book_active_risk_pct(book)
        dollars = equity * self.book_total_risk_pct * pct
        return pct, dollars

    def _rejection_bucket(self, date: pd.Timestamp, book: Optional[str]) -> Dict[str, int]:
        date_key = pd.Timestamp(date).normalize()
        book_key = book or "unassigned"
        book_stats = self.rejection_stats.setdefault(date_key, {})
        if book_key not in book_stats:
            book_stats[book_key] = {key: 0 for key in self.rejection_columns}
        return book_stats[book_key]

    def _record_signal_seen(self, date: pd.Timestamp, book: Optional[str]) -> None:
        bucket = self._rejection_bucket(date, book)
        bucket["signals_seen"] += 1

    def _record_accept(self, date: pd.Timestamp, book: Optional[str]) -> None:
        bucket = self._rejection_bucket(date, book)
        bucket["accepted"] += 1

    def _record_rejection(self, date: pd.Timestamp, book: Optional[str], reason: str) -> None:
        bucket = self._rejection_bucket(date, book)
        key = reason if reason in bucket else "rejected_other"
        bucket[key] += 1

    def _strategy_book(self, strategy: Optional[Any]) -> Optional[str]:
        if strategy is None:
            return None
        book = getattr(strategy, "book", None)
        return book if book else None

    def _book_open_risk(self, book: str, date: pd.Timestamp, data_source: Dict[str, pd.DataFrame]) -> float:
        if not self.book_allocator_enabled or book not in self.book_state:
            return 0.0
        total = 0.0
        for (ticker, _), info in self.holdings.items():
            if info.get("book") != book:
                continue
            qty = info.get("quantity", 0.0)
            if qty <= 0:
                continue
            multiplier = info.get("multiplier", 1.0)
            side = info.get("side", 1)
            stop = info.get("stop_loss")
            entry_price = info.get("entry_price")
            price = None
            if ticker in data_source:
                try:
                    price = data_source[ticker].loc[date]["Close"]
                except Exception:
                    price = None
            if price is None:
                price = entry_price
            if price is None:
                continue
            if stop is None:
                risk_per_unit = info.get("initial_risk")
                if risk_per_unit is None:
                    continue
            else:
                if side == 1:
                    risk_per_unit = max(price - stop, 0.0)
                else:
                    risk_per_unit = max(stop - price, 0.0)
            total += risk_per_unit * qty * multiplier
        return total

    def _book_unrealized_pnl(self, book: str, date: pd.Timestamp, data_source: Dict[str, pd.DataFrame]) -> float:
        total = 0.0
        for (ticker, _), info in self.holdings.items():
            if info.get("book") != book:
                continue
            qty = info.get("quantity", 0.0)
            if qty <= 0:
                continue
            multiplier = info.get("multiplier", 1.0)
            side = info.get("side", 1)
            entry_price = info.get("entry_price")
            if entry_price is None:
                continue
            price = None
            if ticker in data_source:
                try:
                    price = data_source[ticker].loc[date]["Close"]
                except Exception:
                    price = None
            if price is None:
                price = entry_price
            if side == 1:
                total += (price - entry_price) * qty * multiplier
            else:
                total += (entry_price - price) * qty * multiplier
        return total

    def _book_equity(self, book: str, date: pd.Timestamp, data_source: Dict[str, pd.DataFrame]) -> float:
        state = self.book_state.get(book)
        if not state:
            return 0.0
        realized = self.book_realized_pnl.get(book, 0.0)
        unrealized = self._book_unrealized_pnl(book, date, data_source)
        pnl_total = realized + unrealized
        reference = float(state.get("risk_budget_reference", 0.0) or 0.0)
        if reference <= 0:
            return 1.0 + pnl_total
        index = 1.0 + (pnl_total / reference)
        return index

    def _update_book_states(self, date: pd.Timestamp, data_source: Dict[str, pd.DataFrame]) -> None:
        if not self.book_allocator_enabled:
            return
        for book, state in self.book_state.items():
            prev_disabled = bool(state.get("disabled", False))
            prev_throttled = bool(state.get("throttled", False))
            prev_label = self._allocator_state_label(book)
            prev_risk_pct = self._book_active_risk_pct(book)
            index_raw = self._book_equity(book, date, data_source)
            state["index_raw"] = index_raw
            history = self.book_equity_history.get(book, [])
            index_for_alloc = max(index_raw, self.book_index_floor)
            state["index_alloc"] = index_for_alloc
            history.append({"date": date, "index": index_for_alloc})
            self.book_equity_history[book] = history
            peak_raw = state.get("peak_index_raw")
            if peak_raw is None or index_raw > peak_raw:
                peak_raw = index_raw
            state["peak_index_raw"] = peak_raw
            drawdown_raw = (index_raw / peak_raw - 1.0) if peak_raw else 0.0
            state["drawdown_pct_raw"] = drawdown_raw
            peak_alloc = state.get("peak_index_alloc")
            if peak_alloc is None or index_for_alloc > peak_alloc:
                peak_alloc = index_for_alloc
            state["peak_index_alloc"] = peak_alloc
            drawdown_alloc = (index_for_alloc / peak_alloc - 1.0) if peak_alloc else 0.0
            state["drawdown_pct"] = drawdown_alloc

            disabled = state.get("disabled", False)
            disable_thresh = state.get("drawdown_disable_pct", 0.0)
            if disable_thresh > 0 and drawdown_alloc <= -disable_thresh:
                disabled = True
            if disabled:
                lookback = state.get("recovery_lookback_days", self.book_recovery_lookback)
                if lookback > 0 and len(history) >= lookback:
                    window = history[-lookback:]
                    recent_max = max(item["index"] for item in window)
                    if index_for_alloc >= recent_max:
                        disabled = False
                        state["peak_index_alloc"] = index_for_alloc
            state["disabled"] = disabled

            throttle_thresh = state.get("drawdown_reduce_pct", 0.0)
            throttled = False
            if throttle_thresh > 0 and drawdown_alloc <= -throttle_thresh:
                throttled = True
            state["throttled"] = throttled
            new_label = self._allocator_state_label(book)
            new_risk_pct = self._book_active_risk_pct(book)
            if prev_disabled != disabled:
                trigger = "drawdown_disable" if disabled else "drawdown_recover"
                self.book_state_changes.append({
                    "date": date,
                    "book": book,
                    "prev_state": prev_label,
                    "new_state": new_label,
                    "trigger": trigger,
                    "book_drawdown_pct": drawdown_alloc * 100,
                    "portfolio_drawdown_pct": self.portfolio_drawdown_pct * 100,
                    "risk_budget_before": prev_risk_pct,
                    "risk_budget_after": new_risk_pct,
                })
            if prev_throttled != throttled:
                trigger = "drawdown_throttle" if throttled else "drawdown_throttle_recover"
                self.book_state_changes.append({
                    "date": date,
                    "book": book,
                    "prev_state": prev_label,
                    "new_state": new_label,
                    "trigger": trigger,
                    "book_drawdown_pct": drawdown_alloc * 100,
                    "portfolio_drawdown_pct": self.portfolio_drawdown_pct * 100,
                    "risk_budget_before": prev_risk_pct,
                    "risk_budget_after": new_risk_pct,
                })

    def _update_portfolio_state(self, date: pd.Timestamp, data_source: Dict[str, pd.DataFrame]) -> None:
        equity = self._current_equity(date, data_source)
        if equity > self.portfolio_peak_equity:
            self.portfolio_peak_equity = equity
        if self.portfolio_peak_equity:
            self.portfolio_drawdown_pct = (equity / self.portfolio_peak_equity) - 1.0
        else:
            self.portfolio_drawdown_pct = 0.0
        self.portfolio_throttle_active = (
            self.portfolio_drawdown_throttle_pct > 0
            and self.portfolio_drawdown_pct <= -self.portfolio_drawdown_throttle_pct
        )

    def _book_active_risk_pct(self, book: str) -> float:
        state = self.book_state.get(book)
        if not state or state.get("disabled"):
            return 0.0
        if state.get("throttled"):
            return float(state.get("drawdown_reduce_to_pct", state.get("risk_budget_pct", 0.0)) or 0.0)
        return float(state.get("max_concurrent_risk_pct", state.get("risk_budget_pct", 0.0)) or 0.0)

    def _book_budget_dollars(self, book: str, equity: float) -> float:
        pct = self._book_active_risk_pct(book)
        return equity * self.book_total_risk_pct * pct

    def _book_per_trade_cap(self, book: str, equity: float) -> float:
        state = self.book_state.get(book)
        if not state:
            return 0.0
        cap_pct = float(state.get("max_per_trade_risk_pct", 0.0) or 0.0)
        if cap_pct <= 0:
            return 0.0
        return equity * self.book_total_risk_pct * cap_pct

    def _positions_for_ticker(self, ticker: str) -> List[Tuple[str, str]]:
        return [key for key in self.holdings.keys() if key[0] == ticker]

    def _benchmark_symbols(self) -> List[str]:
        symbols = []
        for strategy in self.screener.strategies:
            if not getattr(strategy, "requires_benchmark", False):
                continue
            symbol = getattr(strategy, "benchmark_symbol", None) or self.regime_symbol
            if symbol:
                symbols.append(symbol)
        return list(dict.fromkeys(symbols))

    def _market_symbols_optional(self, strategy: Any) -> bool:
        cfg = getattr(strategy, "regime_gating", None)
        if isinstance(cfg, dict):
            return not bool(cfg.get("enabled", False))
        return False

    def _include_optional_symbol(self, symbol: Optional[str]) -> bool:
        if not symbol:
            return False
        provider = self.data_provider
        if hasattr(provider, "declared_symbols"):
            try:
                declared = provider.declared_symbols()
            except Exception:
                return False
            return symbol in declared
        return True

    def _market_symbols(self, include_optional: bool = True) -> List[str]:
        symbols: List[str] = []
        for strategy in self.screener.strategies:
            market_symbols = getattr(strategy, "market_symbols", None)
            if not market_symbols:
                continue
            is_optional = self._market_symbols_optional(strategy)
            for symbol in market_symbols:
                if symbol:
                    if include_optional or not is_optional or self._include_optional_symbol(symbol):
                        symbols.append(symbol)
        return list(dict.fromkeys(symbols))

    def _resolve_regime_config(self) -> Dict[str, Any]:
        cfg = dict(self.regime_config or {})
        model = (cfg.get("model") or "simple_sma").strip()
        symbols = dict(cfg.get("symbols") or {})
        if not symbols.get("market"):
            symbols["market"] = self.regime_symbol
        symbols.setdefault("vol", "VIXY")
        symbols.setdefault("credit", "HYG")
        symbols.setdefault("rates", "TLT")
        params = dict(cfg.get("params") or {})
        params.setdefault("risk_on_threshold", 0.60)
        params.setdefault("hysteresis_on", 0.65)
        params.setdefault("hysteresis_off", 0.55)
        params.setdefault("hedge_max_pct", 0.50)
        params.setdefault("intraday_mode", "daily_ffill_shift1")
        params.setdefault("size_by_regime_default", False)
        params.setdefault("apply_exposure_caps", False)
        params.setdefault(
            "risk_mult_buckets",
            [
                {"min": 0.8, "mult": 1.0},
                {"min": 0.6, "mult": 0.7},
                {"min": 0.4, "mult": 0.4},
                {"min": 0.0, "mult": 0.0},
            ],
        )
        buckets = params.get("risk_mult_buckets") or []
        try:
            buckets = sorted(buckets, key=lambda item: float(item.get("min", 0.0)), reverse=True)
        except (TypeError, ValueError, AttributeError):
            pass
        params["risk_mult_buckets"] = buckets
        missing_policy = dict(cfg.get("missing_data_policy") or {})
        missing_policy.setdefault("policy", "soft")
        missing_policy.setdefault("max_bad_days_pct", 0.05)
        missing_policy.setdefault("min_components", 2)
        missing_policy.setdefault("warn_limit_per_symbol", 1)
        cfg["model"] = model
        cfg["symbols"] = symbols
        cfg["params"] = params
        cfg["missing_data_policy"] = missing_policy
        return cfg

    def _regime_context_symbols(self, cfg: Dict[str, Any]) -> List[str]:
        model = load_regime_model(cfg.get("model", "simple_sma"))
        symbols = model.required_symbols(cfg)
        return list(dict.fromkeys([s for s in symbols if s]))

    def _regime_risk_mult(self, regime_state: Optional[RegimeState], strategy: Optional[Any]) -> float:
        if regime_state is None:
            return 1.0
        size_by_regime = getattr(strategy, "size_by_regime", None) if strategy is not None else None
        if size_by_regime is None:
            size_by_regime = self.regime_size_by_default
        if not size_by_regime:
            return 1.0
        prob = getattr(regime_state, "risk_on_prob", None)
        try:
            prob = float(prob)
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(prob):
            return 0.0
        for bucket in self.regime_risk_buckets:
            try:
                if prob >= float(bucket.get("min", 0.0)):
                    return float(bucket.get("mult", 0.0))
            except (TypeError, ValueError):
                continue
        return 0.0

    def _regime_exposure_mult(self, regime_state: Optional[RegimeState]) -> float:
        if not self.regime_apply_exposure_caps or regime_state is None:
            return 1.0
        prob = getattr(regime_state, "risk_on_prob", None)
        try:
            prob = float(prob)
        except (TypeError, ValueError):
            prob = 0.0
        if not math.isfinite(prob):
            prob = 0.0
        return 0.5 + (0.5 * max(0.0, min(1.0, prob)))

    def _validate_regime_diagnostics(self, diagnostics: Dict[str, Any], cfg: Dict[str, Any]) -> None:
        if not diagnostics:
            return
        missing_policy = cfg.get("missing_data_policy") or {}
        policy = str(missing_policy.get("policy", "soft")).lower()
        max_bad_days_pct = float(missing_policy.get("max_bad_days_pct", 0.05) or 0.05)
        warn_limit = int(missing_policy.get("warn_limit_per_symbol", 1) or 1)
        market_symbol = diagnostics.get("market_symbol")
        missing_counts = diagnostics.get("missing_counts") or {}
        no_overlap = diagnostics.get("no_overlap") or []
        bad_days_pct = float(diagnostics.get("bad_days_pct", 0.0) or 0.0)
        total_days = int(diagnostics.get("total_days", 0) or 0)

        if no_overlap:
            raise ValueError(f"Regime symbols have no overlap with backtest window: {sorted(set(no_overlap))}")
        if market_symbol and missing_counts.get(market_symbol, 0) > 0:
            raise ValueError(f"Market regime symbol {market_symbol} missing data during backtest window.")
        if bad_days_pct > max_bad_days_pct:
            raise ValueError(
                f"Regime unusable on {bad_days_pct:.1%} of days (max {max_bad_days_pct:.1%})."
            )
        if policy == "hard":
            missing_any = {k: v for k, v in missing_counts.items() if v > 0}
            if missing_any:
                raise ValueError(f"Regime symbols missing data: {missing_any}")

        warned_symbols = []
        if warn_limit > 0:
            for symbol, count in missing_counts.items():
                if not symbol or count <= 0:
                    continue
                if symbol == market_symbol:
                    continue
                logger.warning(
                    "Regime data missing for %s on %s/%s days.",
                    symbol,
                    count,
                    total_days,
                )
                warned_symbols.append(symbol)
            if warned_symbols:
                logger.warning(
                    "Regime data missing for %s symbol(s): %s",
                    len(warned_symbols),
                    ", ".join(sorted(warned_symbols)),
                )
        if bad_days_pct > 0 and policy == "soft":
            logger.warning(
                "Regime unavailable on %s%% of days (policy=soft).",
                round(bad_days_pct * 100, 2),
            )

    def _add_relative_strength_columns(self, full_data: Dict[str, pd.DataFrame]) -> None:
        lookbacks = set()
        benchmarks = set()
        for strategy in self.screener.strategies:
            if not getattr(strategy, "requires_benchmark", False):
                continue
            lookback = int(getattr(strategy, "lookback_days", 126) or 126)
            if lookback > 0:
                lookbacks.add(lookback)
            bench = getattr(strategy, "benchmark_symbol", None) or self.regime_symbol
            if bench:
                benchmarks.add(bench)

        if not lookbacks:
            return
        if len(benchmarks) != 1:
            logger.warning("Relative strength skipped: expected 1 benchmark, got %s.", sorted(benchmarks))
            return

        benchmark_symbol = next(iter(benchmarks))
        bench_df = full_data.get(benchmark_symbol)
        if bench_df is None or "Close" not in bench_df.columns:
            logger.warning("Relative strength skipped: benchmark %s missing.", benchmark_symbol)
            return

        close_map = {}
        for ticker in self.screen_universe:
            df = full_data.get(ticker)
            if df is None or "Close" not in df.columns:
                continue
            close_map[ticker] = df["Close"]
        if not close_map:
            return

        close_df = pd.DataFrame(close_map)
        bench_close = bench_df["Close"].reindex(close_df.index)
        for lookback in sorted(lookbacks):
            rel_ret = close_df / close_df.shift(lookback) - 1.0
            bench_ret = bench_close / bench_close.shift(lookback) - 1.0
            rel = rel_ret.sub(bench_ret, axis=0)
            rank = rel.rank(axis=1, pct=True)
            rel_col = f"RS_REL_{lookback}"
            rank_col = f"RS_RANK_{lookback}"
            for ticker in close_df.columns:
                df = full_data.get(ticker)
                if df is None:
                    continue
                df[rel_col] = rel[ticker]
                df[rank_col] = rank[ticker]

    def _emit_status(self, stage: str, payload: Dict[str, Any] | None = None) -> None:
        callback = getattr(self, "status_callback", None)
        if not callable(callback):
            return
        try:
            callback(stage, payload or {})
        except Exception:
            pass
        
    def run(self):
        """
        Run the backtest simulation.
        """
        self._emit_status("run_start")
        self.recent_signals = {}
        regime_cfg = self._resolve_regime_config()
        regime_model = load_regime_model(regime_cfg.get("model", "simple_sma"))
        self.regime_model_name = regime_cfg.get("model")
        self.regime_symbols = self._regime_context_symbols(regime_cfg)
        self.regime_params = regime_cfg.get("params") or {}
        self.regime_missing_policy = regime_cfg.get("missing_data_policy") or {}
        self.regime_size_by_default = bool(self.regime_params.get("size_by_regime_default", False))
        self.regime_risk_buckets = list(self.regime_params.get("risk_mult_buckets") or [])
        self.regime_apply_exposure_caps = bool(self.regime_params.get("apply_exposure_caps", False))
        # 1. Pre-load Data for Universe
        # Fetching 'max' to ensure we have history for indicators regardless of start date
        extra_symbols = [self.regime_symbol]
        if self._include_optional_symbol(self.hedge_symbol):
            extra_symbols.append(self.hedge_symbol)
        extra_symbols.extend(self._benchmark_symbols())
        extra_symbols.extend(self._market_symbols(include_optional=False))
        extra_symbols.extend(self.regime_symbols)
        context_symbols = set(self.regime_symbols)
        context_symbols.update(self._benchmark_symbols())
        context_symbols.update(self._market_symbols(include_optional=False))
        if self.regime_symbol:
            context_symbols.add(self.regime_symbol)
        if self._include_optional_symbol(self.hedge_symbol):
            context_symbols.add(self.hedge_symbol)
        self.regime_context_symbols = context_symbols
        full_data = self.full_data if self.full_data else None
        if full_data is None:
            self._emit_status("data_fetch_start", {"symbols": len(self.universe)})
            logger.info("Pre-loading data for backtest universe...")
            fetch_universe = list(dict.fromkeys(self.universe + extra_symbols))
            full_data = self.data_provider.fetch_batch_data(
                fetch_universe,
                period="max",
                interval=self.interval,
                force_update=self.force_update_cache,
                as_of_date=self.end_date,
            )
            self._emit_status(
                "data_fetch_done",
                {"symbols": len(full_data) if full_data else 0},
            )
        else:
            logger.info("Using pre-loaded data for backtest universe.")
            self._emit_status(
                "data_fetch_done",
                {"symbols": len(full_data) if full_data else 0, "preloaded": True},
            )
        
        # Filter data to be strictly >= start_date - lookback buffer?
        # Actually, we need history BEFORE start_date for indicators (e.g. SMA200).
        # So we keep full history in memory, but 'as_of_date' in screener handles the hiding of future data.
        
        if not full_data:
            logger.error("No data available for backtest.")
            self._emit_status("data_fetch_empty")
            return

        if not self.full_data:
            self._add_relative_strength_columns(full_data)
            add_market_context_columns(full_data)
            add_intraday_orb_day_type(full_data, self.interval, config=self.regime_config)
            self.full_data = full_data
            self._emit_status("context_columns_done")

        # Determine Date Range from Data (Common Index)
        # We need a master timeline. SPY or AAPL is a good proxy for trading days.
        market_symbol = (regime_cfg.get("symbols") or {}).get("market") or self.regime_symbol
        reference_ticker = market_symbol if market_symbol in full_data else (self.regime_symbol if self.regime_symbol in full_data else list(full_data.keys())[0])
        master_index = full_data[reference_ticker].index
        
        # Filter for dates >= start_date
        # Need to handle TZ awareness
        if master_index.tz is not None:
             start_cmp = self.start_date.tz_localize(master_index.tz)
        else:
             start_cmp = self.start_date
             
        simulation_days = master_index[master_index >= start_cmp]
        if self.end_date is not None:
            if master_index.tz is not None:
                end_cmp = self.end_date.tz_localize(master_index.tz)
            else:
                end_cmp = self.end_date
            simulation_days = simulation_days[simulation_days <= end_cmp]
        
        if self.end_date is not None:
            logger.info(
                "Starting simulation from %s to %s (%s trading days)",
                self.start_date.date(),
                self.end_date.date(),
                len(simulation_days),
            )
        else:
            logger.info("Starting simulation from %s (%s trading days)", self.start_date.date(), len(simulation_days))
        self._emit_status("simulation_window_ready", {"days": len(simulation_days)})

        if market_symbol and market_symbol not in full_data:
            logger.warning("Regime market symbol %s missing from data.", market_symbol)

        self._emit_status("regime_build_start")
        regime_result = regime_model.build(
            full_data,
            master_index,
            interval=self.interval,
            config=regime_cfg,
            start_date=self.start_date,
            end_date=self.end_date,
        )
        self._emit_status("regime_build_done")
        regime_df = regime_result.frame
        self._validate_regime_diagnostics(regime_result.diagnostics, regime_cfg)

        if regime_df is not None and "risk_on" in regime_df.columns:
            risk_on_series = regime_df["risk_on"].astype("boolean")
            usable_series = None
            if "regime_usable" in regime_df.columns:
                usable_series = regime_df["regime_usable"].astype("boolean")
            risk_on_prob_series = None
            if "risk_on_prob" in regime_df.columns:
                risk_on_prob_series = pd.to_numeric(regime_df["risk_on_prob"], errors="coerce")
            trend_state_series = None
            if "trend_state" in regime_df.columns:
                trend_state_series = regime_df["trend_state"].astype("string")
            vol_state_series = None
            if "vol_state" in regime_df.columns:
                vol_state_series = regime_df["vol_state"].astype("string")
            stress_series = None
            if "stress" in regime_df.columns:
                stress_series = pd.to_numeric(regime_df["stress"], errors="coerce")

            for df in full_data.values():
                risk_on = risk_on_series.reindex(df.index).astype("boolean").ffill()
                usable = None
                if usable_series is not None:
                    usable = (
                        usable_series.reindex(df.index)
                        .astype("boolean")
                        .ffill()
                        .fillna(False)
                    )
                    risk_on = risk_on.where(usable, False)
                risk_on = risk_on.astype("boolean").fillna(True)
                df["REGIME_RISK_ON"] = risk_on.to_numpy(dtype=bool)
                if risk_on_prob_series is not None:
                    df["REGIME_RISK_ON_PROB"] = risk_on_prob_series.reindex(df.index).ffill()
                if trend_state_series is not None:
                    df["REGIME_TREND_STATE"] = trend_state_series.reindex(df.index).ffill()
                if vol_state_series is not None:
                    df["REGIME_VOL_STATE"] = vol_state_series.reindex(df.index).ffill()
                if stress_series is not None:
                    df["REGIME_STRESS"] = stress_series.reindex(df.index).ffill()
                if usable is not None:
                    df["REGIME_USABLE"] = usable.to_numpy(dtype=bool)

            # Inject any additional regime columns (e.g., daily labels/scores)
            core_cols = {
                "risk_on",
                "risk_on_prob",
                "trend_state",
                "vol_state",
                "stress",
                "regime_usable",
                "severity",
                "hedge_pct",
                "risk_on_streak",
                "sma200_slope_pct",
                "sma200_distance_pct",
            }
            extra_cols = [c for c in regime_df.columns if c not in core_cols]
            if extra_cols:
                extra_df = regime_df[extra_cols]
                for df in full_data.values():
                    aligned = extra_df.reindex(df.index).ffill()
                    for col in extra_cols:
                        df[col] = aligned[col].to_numpy()

        if self.use_vectorized:
            self._emit_status("signals_build_start")
            signal_cache, stop_cache, exit_cache, score_cache = self._build_signal_cache(full_data)
            self._emit_status("signals_build_done")
            self._emit_status("simulation_start")
            self._run_vectorized(simulation_days, full_data, signal_cache, stop_cache, exit_cache, score_cache, regime_df)
            self._emit_status("simulation_done")
            return

        self._emit_status("simulation_start")
        last_day = simulation_days[-1] if len(simulation_days) else None
        for current_day in tqdm(simulation_days, desc="Simulating"):
            # 1. Update Portfolio Value (Mark to Market)
            current_holdings_list = list({key[0] for key in self.holdings.keys()})
            
            # Identify candidates to scan: Universe + Current Holdings
            # (Holdings might not be in universe if they were removed from index, but we still hold them)
            scan_list = list(set(self.screen_universe + current_holdings_list))
            exclude = {self.hedge_symbol}
            exclude.update({s for s in context_symbols if s not in self.screen_universe})
            scan_list = [s for s in scan_list if s not in exclude]

            regime_state = get_regime_state(current_day, regime_df)
            self._latest_regime_state = regime_state
            self._update_portfolio_state(current_day, full_data)
            self._update_book_states(current_day, full_data)
            
            # 2. Run Screener for this day
            # We pass the full_data as 'data_source' so it doesn't fetch.
            # Screener will interpret 'as_of_date' to slice this data.
            # Optimization: Screener logic slices DF every time. Efficient enough for daily resolution.
            
            # Format date as string YYYY-MM-DD for screener
            as_of_str = str(current_day.date())
            
            screen_results = self.screener.run(
                tickers=scan_list, 
                as_of_date=as_of_str, 
                data_source=full_data,
                force_update=False # never force update inside backtest loop
            )
            
            # 3. Process Signals
            self._process_signals(current_day, screen_results, full_data, regime_state)

            # 3b. Hedge update based on post-trade exposure
            self._update_hedge(current_day, full_data, regime_state)

            if last_day is not None and current_day == last_day:
                self._liquidate_all_positions(current_day, full_data, reason="End of Backtest")

            # 4. Record Equity
            equity_entry = self._record_equity(current_day, full_data)
            if equity_entry is not None:
                self._record_book_daily(
                    current_day,
                    full_data,
                    regime_state,
                    equity_entry.get("equity", 0.0),
                    equity_entry.get("vol_scale", 0.0),
                )
        self._emit_status("simulation_done")

    def _build_signal_cache(self, full_data: Dict[str, pd.DataFrame]):
        strategies = self.screener.strategies
        signal_cache = {}
        stop_cache = {}
        exit_cache = {}
        score_cache = {}

        logger.info("Precomputing vectorized signals...")
        for strategy in strategies:
            if getattr(strategy, "debug_counters", False) and hasattr(strategy, "reset_debug_counts"):
                strategy.reset_debug_counts()
        for ticker in tqdm(self.screen_universe, desc="Signals"):
            if ticker not in full_data:
                continue
            df = full_data[ticker]
            strat_signals = {}
            strat_stops = {}
            strat_exits = {}
            strat_scores = {}

            for strategy in strategies:
                name = strategy.get_name()
                try:
                    signal = strategy.signal(df)
                except Exception as e:
                    logger.warning(f"Signal build failed for {name} on {ticker}: {e}")
                    continue

                if signal is None:
                    continue
                signal = signal.reindex(df.index).fillna(False).astype(bool)
                strat_signals[name] = signal

                try:
                    stop_series = strategy.stop_loss_series(df)
                except Exception:
                    stop_series = None
                if stop_series is not None:
                    stop_series = stop_series.reindex(df.index)
                strat_stops[name] = stop_series

                try:
                    exit_series = strategy.exit_signal(df)
                except Exception:
                    exit_series = None
                if exit_series is not None:
                    exit_series = exit_series.reindex(df.index).fillna(False).astype(bool)
                strat_exits[name] = exit_series

                try:
                    score_series = strategy.score_series(df)
                except Exception:
                    score_series = None
                if score_series is not None:
                    score_series = score_series.reindex(df.index).fillna(0.0)
                strat_scores[name] = score_series

            signal_cache[ticker] = strat_signals
            stop_cache[ticker] = strat_stops
            exit_cache[ticker] = strat_exits
            score_cache[ticker] = strat_scores

        for strategy in strategies:
            if getattr(strategy, "debug_counters", False) and hasattr(strategy, "log_debug_summary"):
                strategy.log_debug_summary()

        return signal_cache, stop_cache, exit_cache, score_cache

    def _run_vectorized(self, simulation_days, data_source, signal_cache, stop_cache, exit_cache, score_cache, regime_df):
        strategies = self.screener.strategies
        strategies_by_name = {s.get_name(): s for s in strategies}
        last_day = simulation_days[-1] if len(simulation_days) else None

        for current_day in tqdm(simulation_days, desc="Simulating"):
            current_holdings_list = list({key[0] for key in self.holdings.keys()})
            scan_list = list(set(self.screen_universe + current_holdings_list))
            exclude = {self.hedge_symbol}
            exclude.update({s for s in self.regime_context_symbols if s not in self.screen_universe})
            scan_list = [s for s in scan_list if s not in exclude]

            regime_state = get_regime_state(current_day, regime_df)
            self._latest_regime_state = regime_state
            self._update_portfolio_state(current_day, data_source)
            self._update_book_states(current_day, data_source)

            for ticker in scan_list:
                if ticker not in data_source:
                    continue
                strat_signals = signal_cache.get(ticker, {})
                if strat_signals:
                    self._record_today_signals(ticker, current_day, strat_signals, strategies_by_name)

            # Exit logic with strategy-specific exits, trailing stops, and risk-off handling
            for position_key in list(self.holdings.keys()):
                holding = self.holdings[position_key]
                ticker, strategy_name = position_key
                if holding.get("asset_type") == "hedge":
                    continue
                if ticker not in data_source:
                    continue
                try:
                    row = data_source[ticker].loc[current_day]
                except Exception:
                    continue

                price = row['Close']
                self._update_excursions(holding, row, current_day)
                entry_price = holding['entry_price']
                stop_loss = holding.get('stop_loss')
                take_profit = holding.get('take_profit')
                side = holding.get("side", 1)
                strategy_obj = strategies_by_name.get(strategy_name) if strategy_name else None

                use_regime_exit = self.exit_on_risk_off
                if strategy_obj is not None:
                    use_regime_exit = use_regime_exit and bool(getattr(strategy_obj, "use_regime_exit", True))
                if side == 1 and use_regime_exit and not regime_state.risk_on:
                    self._execute_sell(position_key, current_day, price, "Regime Risk-Off", data_source)
                    continue

                if strategy_obj is not None and bool(getattr(strategy_obj, "use_stop_loss", True)):
                    stop_series = stop_cache.get(ticker, {}).get(strategy_name)
                    if stop_series is not None and current_day in stop_series.index:
                        stop_value = stop_series.loc[current_day]
                        if not pd.isna(stop_value):
                            new_stop = float(stop_value)
                            should_update = stop_loss is None or (side == 1 and new_stop > stop_loss) or (side == -1 and new_stop < stop_loss)
                            if should_update and self._atr_contraction_allows_tighten(holding, data_source[ticker], strategy_obj, current_day):
                                stop_loss = new_stop
                                holding['stop_loss'] = new_stop
                                holding["stop_type"] = "signal"

                use_breakeven = bool(getattr(strategy_obj, "use_breakeven", True)) if strategy_obj is not None else True
                if use_breakeven and bool(getattr(strategy_obj, "use_stop_loss", True) if strategy_obj is not None else True):
                    stop_loss = self._apply_breakeven_stop(holding, price, current_day, data_source, ticker, strategy_obj)

                self._update_trend_confirm(holding, row, data_source[ticker], strategy_obj, current_day)
                stop_loss = self._apply_trailing_stop(holding, row, strategy_obj)

                if stop_loss is not None and bool(getattr(strategy_obj, "use_stop_loss", True) if strategy_obj is not None else True):
                    stop_fill_price, stop_fill_mode = self._stop_fill_price(row, stop_loss, side)
                    if stop_fill_price is not None:
                        holding["stop_level"] = stop_loss
                        holding["stop_fill_price"] = stop_fill_price
                        holding["stop_fill_mode"] = stop_fill_mode
                        self._execute_sell(position_key, current_day, stop_fill_price, "Stop Loss", data_source)
                        continue

                if strategy_obj is not None and bool(getattr(strategy_obj, "use_exit_signal", True)):
                    exit_series = exit_cache.get(ticker, {}).get(strategy_name)
                    if exit_series is not None and current_day in exit_series.index:
                        if bool(exit_series.loc[current_day]):
                            self._execute_sell(position_key, current_day, price, "Exit Signal", data_source)
                            continue

                if self._should_exhaustion_exit(holding, row, data_source[ticker], strategy_obj, current_day):
                    self._execute_sell(position_key, current_day, price, "Exhaustion Exit", data_source)
                    continue

                if self._should_giveback_exit(holding, price, current_day, strategy_obj):
                    self._execute_sell(position_key, current_day, price, "Giveback Exit", data_source)
                    continue

                if self._should_momentum_fail_exit(holding, data_source[ticker], strategy_obj, current_day):
                    self._execute_sell(position_key, current_day, price, "Momentum Fail Exit", data_source)
                    continue

                if self._should_early_failure_exit(holding, row, data_source[ticker], strategy_obj, current_day):
                    self._execute_sell(position_key, current_day, price, "Early Failure Exit", data_source)
                    continue

                if self._should_follow_through_exit(holding, row, data_source[ticker], strategy_obj, current_day):
                    self._execute_sell(position_key, current_day, price, "No Follow-Through Exit", data_source)
                    continue

                if self._should_decay_exit(holding, current_day, strategy_obj):
                    self._execute_sell(position_key, current_day, price, "Decay Exit", data_source)
                    continue

                has_staged_tp = bool(holding.get("take_profit_levels"))
                if has_staged_tp:
                    self._apply_staged_take_profit(ticker, holding, price, current_day, strategy_obj, data_source)
                    if position_key not in self.holdings:
                        continue
                else:
                    self._apply_partial_take_profit(ticker, holding, price, current_day, strategy_obj, data_source)
                    if position_key not in self.holdings:
                        continue

                    use_take_profit = bool(getattr(strategy_obj, "use_take_profit", True)) if strategy_obj is not None else True
                    if take_profit is not None and use_take_profit:
                        if side == 1 and price >= take_profit:
                            self._execute_sell(position_key, current_day, price, "Take Profit", data_source)
                            continue
                        if side == -1 and price <= take_profit:
                            self._execute_sell(position_key, current_day, price, "Take Profit", data_source)
                            continue

                use_fallback_stop = bool(getattr(strategy_obj, "use_fallback_stop", True)) if strategy_obj is not None else True
                use_stop_loss = bool(getattr(strategy_obj, "use_stop_loss", True)) if strategy_obj is not None else True
                if stop_loss is None and use_fallback_stop and use_stop_loss:
                    fallback_level = entry_price * (0.95 if side == 1 else 1.05)
                    stop_fill_price, stop_fill_mode = self._stop_fill_price(row, fallback_level, side)
                    if stop_fill_price is not None:
                        holding["stop_type"] = "fallback"
                        holding["stop_level"] = fallback_level
                        holding["stop_fill_price"] = stop_fill_price
                        holding["stop_fill_mode"] = stop_fill_mode
                        holding["stop_loss"] = fallback_level
                        self._execute_sell(position_key, current_day, stop_fill_price, "Stop Loss", data_source)
                        continue

                if self._time_stop_reached(position_key, current_day):
                    if self._execute_sell(position_key, current_day, price, "Time Stop", data_source):
                        continue

                if position_key in self.holdings:
                    self._maybe_add_on(
                        holding,
                        ticker,
                        current_day,
                        price,
                        data_source,
                        regime_state,
                        strategy_obj,
                    )

            # Entry logic using precomputed signals with scoring
            candidates = []
            for ticker in scan_list:
                if ticker not in data_source:
                    continue

                strat_signals = signal_cache.get(ticker, {})
                # Cooldowns are handled per strategy inside the loop below.

                df = data_source[ticker]
                if not self._passes_liquidity_filter(df, ticker, current_day):
                    for name, strategy in strategies_by_name.items():
                        signal_series = strat_signals.get(name)
                        if signal_series is None or current_day not in signal_series.index:
                            continue
                        if not bool(signal_series.loc[current_day]):
                            continue
                        book = self._strategy_book(strategy) if strategy is not None else None
                        self._record_signal_seen(current_day, book)
                        self._record_rejection(current_day, book, "rejected_liquidity")
                    continue

                if not strat_signals:
                    continue

                best_candidate = None
                for name, strategy in strategies_by_name.items():
                    signal_series = strat_signals.get(name)
                    if signal_series is None or current_day not in signal_series.index:
                        continue
                    if not bool(signal_series.loc[current_day]):
                        continue
                    book = self._strategy_book(strategy) if strategy is not None else None
                    self._record_signal_seen(current_day, book)
                    sentiment = getattr(strategy, "sentiment", None)
                    side = self._entry_side(ticker, sentiment)
                    if side is None:
                        self._record_rejection(current_day, book, "rejected_other")
                        continue
                    if self._has_position(ticker, name):
                        self._record_rejection(current_day, book, "rejected_other")
                        continue
                    if self._in_cooldown(ticker, name, current_day):
                        self._record_rejection(current_day, book, "rejected_cooldown")
                        continue

                    try:
                        price = df.loc[current_day]['Close']
                    except Exception:
                        self._record_rejection(current_day, book, "rejected_other")
                        continue

                    stop_loss = None
                    use_stop_loss = bool(getattr(strategy, "use_stop_loss", True))
                    if use_stop_loss:
                        stop_series = stop_cache.get(ticker, {}).get(name)
                        if stop_series is not None and current_day in stop_series.index:
                            stop_value = stop_series.loc[current_day]
                            if not pd.isna(stop_value):
                                stop_loss = float(stop_value)
                        if stop_loss is None and bool(getattr(strategy, "use_fallback_stop", True)):
                            stop_loss = resolve_stop_loss(
                                current_day,
                                data_source,
                                ticker,
                                price,
                                stop_loss,
                                side,
                            )

                    score_series = score_cache.get(ticker, {}).get(name)
                    score = 0.0
                    if score_series is not None and current_day in score_series.index:
                        score = float(score_series.loc[current_day])
                        if pd.isna(score):
                            score = 0.0

                    hist = df.loc[:current_day]
                    take_profit_levels, take_profit_level_pcts = self._resolve_take_profit_levels(strategy, hist, price)
                    if take_profit_levels:
                        take_profit = take_profit_levels[-1]
                    else:
                        take_profit = self._resolve_take_profit(strategy, hist, price)
                    take_profit_level_pct_mode = None
                    if take_profit_levels:
                        take_profit_level_pct_mode = getattr(strategy, "take_profit_level_pct_mode", "initial")

                    candidate = {
                        "ticker": ticker,
                        "strategy": name,
                        "strategy_obj": strategy,
                        "price": price,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "take_profit_levels": take_profit_levels,
                        "take_profit_level_pcts": take_profit_level_pcts,
                        "take_profit_level_pct_mode": take_profit_level_pct_mode,
                        "side": side,
                        "score": score,
                        "entry_metrics": None,
                    }
                    if strategy is not None:
                        try:
                            is_match, metrics = strategy.check(hist)
                        except Exception:
                            is_match, metrics = False, {}
                        if is_match:
                            candidate["entry_metrics"] = metrics
                        else:
                            candidate["entry_metrics"] = {"score": score}
                    allowed, reason = self._entry_allowed(
                        strategy,
                        side,
                        regime_state,
                        candidate["entry_metrics"],
                        ticker=ticker,
                        date=current_day,
                    )
                    if not allowed:
                        self._record_rejection(current_day, book, reason or "rejected_other")
                        continue
                    orb_allowed, orb_reason = self._orb_entry_allowed(
                        strategy,
                        ticker,
                        side,
                        current_day,
                        data_source,
                    )
                    if not orb_allowed:
                        self._record_rejection(current_day, book, orb_reason or "rejected_other")
                        continue
                    if self._in_stop_cooldown(ticker, strategy, current_day):
                        self._record_rejection(current_day, book, "rejected_cooldown")
                        continue
                    if self._in_loss_cooldown(ticker, strategy, current_day):
                        self._record_rejection(current_day, book, "rejected_cooldown")
                        continue
                    if best_candidate is None or candidate["score"] > best_candidate["score"]:
                        best_candidate = candidate

                if best_candidate:
                    candidates.append(best_candidate)

            candidates.sort(key=lambda c: c["score"], reverse=True)
            slots = self.max_new_positions_per_day
            for candidate in candidates:
                book = self._strategy_book(candidate.get("strategy_obj"))
                if slots <= 0:
                    self._record_rejection(current_day, book, "rejected_max_positions")
                    continue
                amount_usd, amt_reason = self._available_trade_amount(
                    current_day,
                    data_source,
                    candidate["ticker"],
                    candidate["price"],
                    candidate["stop_loss"],
                    regime_state,
                    candidate["side"],
                    candidate.get("strategy_obj"),
                    return_reason=True,
                )
                if amount_usd <= 0:
                    if amt_reason == "min_notional":
                        self._record_rejection(current_day, book, "rejected_min_notional")
                    elif amt_reason == "risk_budget":
                        self._record_rejection(current_day, book, "rejected_risk_budget")
                    else:
                        self._record_rejection(current_day, book, "rejected_other")
                    continue
                executed = self._execute_buy(
                    candidate["ticker"],
                    current_day,
                    candidate["price"],
                    amount_usd,
                    candidate["stop_loss"],
                    candidate["strategy"],
                    candidate["side"],
                    "futures" if is_future_symbol(candidate["ticker"]) else "equity",
                    candidate["take_profit"],
                    candidate.get("entry_metrics"),
                    data_source,
                    take_profit_levels=candidate.get("take_profit_levels"),
                    take_profit_level_pcts=candidate.get("take_profit_level_pcts"),
                    take_profit_level_pct_mode=candidate.get("take_profit_level_pct_mode"),
                )
                if executed:
                    self._record_accept(current_day, book)
                    slots -= 1
                else:
                    self._record_rejection(current_day, book, "rejected_other")

            self._update_hedge(current_day, data_source, regime_state)
            if last_day is not None and current_day == last_day:
                self._liquidate_all_positions(current_day, data_source, reason="End of Backtest")
            equity_entry = self._record_equity(current_day, data_source)
            if equity_entry is not None:
                self._record_book_daily(
                    current_day,
                    data_source,
                    regime_state,
                    equity_entry.get("equity", 0.0),
                    equity_entry.get("vol_scale", 0.0),
                )

    def _process_signals(self, date, results, data_source, regime_state: RegimeState):
        """Evaluate screener results and execute trades."""
        
        # Placeholder Exit Logic: Sell if price drops 5% from entry (Stop Loss) or 10% gain.
        # Real logic should come from Strategy but for MVP backtest structure:
        
        for res in results:
            ticker = res["symbol"]
            matches = res.get("matches", [])
            if matches:
                self._record_today_match_signals(ticker, date, matches)

        for position_key in list(self.holdings.keys()):
            holding = self.holdings[position_key]
            ticker, strategy_name = position_key
            if holding.get("asset_type") == "hedge":
                continue
            if ticker not in data_source:
                continue

            df = data_source[ticker]
            try:
                row = df.loc[date]
                price = row['Close']
            except Exception:
                continue

            self._update_excursions(holding, row, date)
            entry_price = holding['entry_price']
            stop_loss = holding.get('stop_loss')
            take_profit = holding.get('take_profit')
            side = holding.get("side", 1)
            strategy = self.strategy_map.get(strategy_name) if strategy_name else None

            use_regime_exit = self.exit_on_risk_off
            if strategy is not None:
                use_regime_exit = use_regime_exit and bool(getattr(strategy, "use_regime_exit", True))
            if side == 1 and use_regime_exit and not regime_state.risk_on:
                self._execute_sell(position_key, date, price, "Regime Risk-Off", data_source)
                continue

            if strategy and bool(getattr(strategy, "use_stop_loss", True)):
                try:
                    stop_series = strategy.stop_loss_series(df)
                except Exception:
                    stop_series = None
                if stop_series is not None and date in stop_series.index:
                    stop_value = stop_series.loc[date]
                    if not pd.isna(stop_value):
                        new_stop = float(stop_value)
                        should_update = stop_loss is None or (side == 1 and new_stop > stop_loss) or (side == -1 and new_stop < stop_loss)
                        if should_update and self._atr_contraction_allows_tighten(holding, df, strategy, date):
                            stop_loss = new_stop
                            holding['stop_loss'] = new_stop
                            holding["stop_type"] = "signal"

            use_breakeven = bool(getattr(strategy, "use_breakeven", True)) if strategy is not None else True
            if use_breakeven and bool(getattr(strategy, "use_stop_loss", True) if strategy is not None else True):
                stop_loss = self._apply_breakeven_stop(holding, price, date, data_source, ticker, strategy)

            self._update_trend_confirm(holding, row, df, strategy, date)
            stop_loss = self._apply_trailing_stop(holding, row, strategy)

            if stop_loss is not None and bool(getattr(strategy, "use_stop_loss", True) if strategy is not None else True):
                stop_fill_price, stop_fill_mode = self._stop_fill_price(row, stop_loss, side)
                if stop_fill_price is not None:
                    holding["stop_level"] = stop_loss
                    holding["stop_fill_price"] = stop_fill_price
                    holding["stop_fill_mode"] = stop_fill_mode
                    self._execute_sell(position_key, date, stop_fill_price, "Stop Loss", data_source)
                    continue

            if strategy and bool(getattr(strategy, "use_exit_signal", True)):
                try:
                    exit_series = strategy.exit_signal(df)
                except Exception:
                    exit_series = None
                if exit_series is not None and date in exit_series.index:
                    if bool(exit_series.loc[date]):
                        self._execute_sell(position_key, date, price, "Exit Signal", data_source)
                        continue

            if self._should_exhaustion_exit(holding, row, df, strategy, date):
                self._execute_sell(position_key, date, price, "Exhaustion Exit", data_source)
                continue

            if self._should_giveback_exit(holding, price, date, strategy):
                self._execute_sell(position_key, date, price, "Giveback Exit", data_source)
                continue

            if self._should_momentum_fail_exit(holding, df, strategy, date):
                self._execute_sell(position_key, date, price, "Momentum Fail Exit", data_source)
                continue

            if self._should_early_failure_exit(holding, row, df, strategy, date):
                self._execute_sell(position_key, date, price, "Early Failure Exit", data_source)
                continue

            if self._should_follow_through_exit(holding, row, df, strategy, date):
                self._execute_sell(position_key, date, price, "No Follow-Through Exit", data_source)
                continue

            if self._should_decay_exit(holding, date, strategy):
                self._execute_sell(position_key, date, price, "Decay Exit", data_source)
                continue

            has_staged_tp = bool(holding.get("take_profit_levels"))
            if has_staged_tp:
                self._apply_staged_take_profit(ticker, holding, price, date, strategy, data_source)
                if position_key not in self.holdings:
                    continue
            else:
                self._apply_partial_take_profit(ticker, holding, price, date, strategy, data_source)
                if position_key not in self.holdings:
                    continue

                use_take_profit = bool(getattr(strategy, "use_take_profit", True)) if strategy is not None else True
                if take_profit is not None and use_take_profit:
                    if side == 1 and price >= take_profit:
                        self._execute_sell(position_key, date, price, "Take Profit", data_source)
                        continue
                    if side == -1 and price <= take_profit:
                        self._execute_sell(position_key, date, price, "Take Profit", data_source)
                        continue

            use_fallback_stop = bool(getattr(strategy, "use_fallback_stop", True)) if strategy is not None else True
            use_stop_loss = bool(getattr(strategy, "use_stop_loss", True)) if strategy is not None else True
            if stop_loss is None and use_fallback_stop and use_stop_loss:
                fallback_level = entry_price * (0.95 if side == 1 else 1.05)
                stop_fill_price, stop_fill_mode = self._stop_fill_price(row, fallback_level, side)
                if stop_fill_price is not None:
                    holding["stop_type"] = "fallback"
                    holding["stop_level"] = fallback_level
                    holding["stop_fill_price"] = stop_fill_price
                    holding["stop_fill_mode"] = stop_fill_mode
                    holding["stop_loss"] = fallback_level
                    self._execute_sell(position_key, date, stop_fill_price, "Stop Loss", data_source)
                    continue

            if self._time_stop_reached(position_key, date):
                if self._execute_sell(position_key, date, price, "Time Stop", data_source):
                    continue

            if position_key in self.holdings:
                self._maybe_add_on(
                    holding,
                    ticker,
                    date,
                    price,
                    data_source,
                    regime_state,
                    strategy,
                )
                
        # Entry Logic with scoring and liquidity filters
        candidates = []
        for res in results:
            ticker = res['symbol']
            # Cooldowns are handled per strategy inside the loop below.
            if ticker not in data_source:
                continue

            df = data_source[ticker]
            if not self._passes_liquidity_filter(df, ticker, date):
                for strat in res.get('matches', []):
                    strategy_obj = self.strategy_map.get(strat)
                    book = self._strategy_book(strategy_obj) if strategy_obj is not None else None
                    self._record_signal_seen(date, book)
                    self._record_rejection(date, book, "rejected_liquidity")
                continue

            matches = res['matches']  # List of strategy names
            metrics_by_strategy = res.get('metrics', {})

            best_candidate = None
            for strat in matches:
                metrics = metrics_by_strategy.get(strat, {})
                strategy_obj = self.strategy_map.get(strat)
                book = self._strategy_book(strategy_obj) if strategy_obj is not None else None
                self._record_signal_seen(date, book)
                sentiment = metrics.get('sentiment')
                side = self._entry_side(ticker, sentiment)
                if side is None:
                    self._record_rejection(date, book, "rejected_other")
                    continue
                if self._has_position(ticker, strat):
                    self._record_rejection(date, book, "rejected_other")
                    continue
                allowed, reason = self._entry_allowed(strategy_obj, side, regime_state, metrics, ticker=ticker, date=date)
                if not allowed:
                    self._record_rejection(date, book, reason or "rejected_other")
                    continue
                orb_allowed, orb_reason = self._orb_entry_allowed(
                    strategy_obj,
                    ticker,
                    side,
                    date,
                    data_source,
                )
                if not orb_allowed:
                    self._record_rejection(date, book, orb_reason or "rejected_other")
                    continue
                if self._in_cooldown(ticker, strat, date):
                    self._record_rejection(date, book, "rejected_cooldown")
                    continue
                if self._in_stop_cooldown(ticker, strategy_obj, date):
                    self._record_rejection(date, book, "rejected_cooldown")
                    continue
                if self._in_loss_cooldown(ticker, strategy_obj, date):
                    self._record_rejection(date, book, "rejected_cooldown")
                    continue

                try:
                    price = df.loc[date]['Close']
                except Exception:
                    price = res.get('price', 0.0)

                stop_loss = None
                use_stop_loss = bool(getattr(strategy_obj, "use_stop_loss", True)) if strategy_obj is not None else True
                if use_stop_loss:
                    stop_loss = metrics.get('stop_loss')
                    if stop_loss is None and bool(getattr(strategy_obj, "use_fallback_stop", True) if strategy_obj is not None else True):
                        stop_loss = resolve_stop_loss(
                            date,
                            data_source,
                            ticker,
                            price,
                            stop_loss,
                            side,
                        )
                score = float(metrics.get('score', 0.0) or 0.0)
                if pd.isna(score):
                    score = 0.0
                take_profit = None
                take_profit_levels = None
                take_profit_level_pcts = None
                take_profit_level_pct_mode = None
                if strategy_obj is not None:
                    hist = df.loc[:date]
                    take_profit_levels, take_profit_level_pcts = self._resolve_take_profit_levels(strategy_obj, hist, price)
                    if take_profit_levels:
                        take_profit = take_profit_levels[-1]
                        take_profit_level_pct_mode = getattr(strategy_obj, "take_profit_level_pct_mode", "initial")
                    else:
                        take_profit = self._resolve_take_profit(strategy_obj, hist, price)

                candidate = {
                    "ticker": ticker,
                    "strategy": strat,
                    "price": price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "take_profit_levels": take_profit_levels,
                    "take_profit_level_pcts": take_profit_level_pcts,
                    "take_profit_level_pct_mode": take_profit_level_pct_mode,
                    "side": side,
                    "score": score,
                    "entry_metrics": metrics,
                    "strategy_obj": strategy_obj,
                }
                if best_candidate is None or candidate["score"] > best_candidate["score"]:
                    best_candidate = candidate

            if best_candidate:
                candidates.append(best_candidate)

        candidates.sort(key=lambda c: c["score"], reverse=True)
        slots = self.max_new_positions_per_day
        for candidate in candidates:
            book = self._strategy_book(candidate.get("strategy_obj"))
            if slots <= 0:
                self._record_rejection(date, book, "rejected_max_positions")
                continue
            amount_usd, amt_reason = self._available_trade_amount(
                date,
                data_source,
                candidate["ticker"],
                candidate["price"],
                candidate["stop_loss"],
                regime_state,
                candidate["side"],
                candidate.get("strategy_obj"),
                return_reason=True,
            )
            if amount_usd <= 0:
                if amt_reason == "min_notional":
                    self._record_rejection(date, book, "rejected_min_notional")
                elif amt_reason == "risk_budget":
                    self._record_rejection(date, book, "rejected_risk_budget")
                else:
                    self._record_rejection(date, book, "rejected_other")
                continue
            executed = self._execute_buy(
                candidate["ticker"],
                date,
                candidate["price"],
                amount_usd,
                candidate["stop_loss"],
                candidate["strategy"],
                candidate["side"],
                "futures" if is_future_symbol(candidate["ticker"]) else "equity",
                candidate["take_profit"],
                candidate.get("entry_metrics"),
                data_source,
                take_profit_levels=candidate.get("take_profit_levels"),
                take_profit_level_pcts=candidate.get("take_profit_level_pcts"),
                take_profit_level_pct_mode=candidate.get("take_profit_level_pct_mode"),
            )
            if executed:
                self._record_accept(date, book)
                slots -= 1
            else:
                self._record_rejection(date, book, "rejected_other")

    def _buy(
        self,
        ticker,
        date,
        price,
        amount_usd,
        stop_loss=None,
        strategy=None,
        side=1,
        asset_type="equity",
        take_profit=None,
        entry_metrics=None,
        equity_before: Optional[float] = None,
        data_source=None,
        take_profit_levels: Optional[List[float]] = None,
        take_profit_level_pcts: Optional[List[float]] = None,
        take_profit_level_pct_mode: Optional[str] = None,
        regime_state: Optional[RegimeState] = None,
    ):
        raw_price = price
        exec_price = self._execution_price(raw_price, side, is_entry=True)
        if exec_price is None:
            return
        try:
            exec_price = float(exec_price)
        except (TypeError, ValueError):
            return
        if not math.isfinite(exec_price) or exec_price <= 0:
            return
        if amount_usd is None:
            return
        try:
            amount_usd = float(amount_usd)
        except (TypeError, ValueError):
            return
        if not math.isfinite(amount_usd) or amount_usd <= 0:
            return
        multiplier = contract_multiplier(ticker, self.contract_multipliers)
        margin_pct = self._resolve_futures_margin_pct() if is_future_symbol(ticker) else None
        strategy_obj = self.strategy_map.get(strategy) if isinstance(strategy, str) else None
        debug_alloc = bool(getattr(strategy_obj, "debug_allocation", False)) if strategy_obj is not None else False
        book = self._strategy_book(strategy_obj) if self.book_allocator_enabled else None
        allocator_state = self._allocator_state_label(book)
        book_risk_pct, book_risk_dollars = self._book_risk_snapshot(book, equity_before)
        confirmation_details = self._confirmation_details_for_trade(strategy, ticker, date)
        if regime_state is None:
            regime_state = self._latest_regime_state
        regime_fields = self._regime_trade_fields(regime_state, "entry")
        qty = amount_usd / (exec_price * multiplier)
        if is_future_symbol(ticker):
            qty = int(qty)
            if qty <= 0:
                if debug_alloc:
                    logger.info(
                        "Alloc debug %s %s buy aborted: qty<=0 amount=%.2f price=%.4f mult=%.2f",
                        strategy or "UNKNOWN",
                        ticker,
                        amount_usd,
                        exec_price,
                        multiplier,
                    )
                return
            amount_usd = qty * exec_price * multiplier
        else:
            qty = self._round_equity_qty(qty)
            if qty < self.min_equity_shares:
                if debug_alloc:
                    logger.info(
                        "Alloc debug %s %s buy aborted: qty<min_shares qty=%.4f min=%s amount=%.2f price=%.4f",
                        strategy or "UNKNOWN",
                        ticker,
                        qty,
                        self.min_equity_shares,
                        amount_usd,
                        exec_price,
                    )
                return
            amount_usd = qty * exec_price * multiplier
        trade_notional = qty * exec_price * multiplier
        trade_margin_pct = margin_pct if margin_pct is not None else 1.0
        if (
            self.skip_trade_if_notional_lt_min
            and self.min_trade_notional > 0
            and trade_notional < self.min_trade_notional
        ):
            if debug_alloc:
                logger.info(
                    "Alloc debug %s %s buy aborted: notional<min notional=%.2f min=%.2f qty=%.4f",
                    strategy or "UNKNOWN",
                    ticker,
                    trade_notional,
                    self.min_trade_notional,
                    qty,
                )
            return
        commission = self._trade_commission(trade_notional)
        slippage_cost = abs(exec_price - raw_price) * qty * multiplier
        if margin_pct is not None:
            equity_for_margin = equity_before
            if equity_for_margin is None:
                equity_for_margin = self._current_equity(date, data_source) if data_source is not None else self.cash
            margin_available = self._futures_margin_available(date, data_source, margin_pct, equity_for_margin)
            margin_required = trade_notional * margin_pct
            if margin_available < (margin_required + commission):
                if debug_alloc:
                    logger.info(
                        "Alloc debug %s %s buy aborted: margin<required available=%.2f required=%.2f qty=%.4f",
                        strategy or "UNKNOWN",
                        ticker,
                        margin_available,
                        margin_required + commission,
                        qty,
                    )
                return
        elif side == 1 and self.cash < (trade_notional + commission):
            if debug_alloc:
                logger.info(
                    "Alloc debug %s %s buy aborted: cash<notional cash=%.2f needed=%.2f qty=%.4f",
                    strategy or "UNKNOWN",
                    ticker,
                    self.cash,
                    trade_notional + commission,
                    qty,
                )
            return
        if side == 1:
            self.cash -= (trade_notional + commission)
            action = "BUY"
        else:
            self.cash += (trade_notional - commission)
            action = "SELL_SHORT"
        position_side = "LONG" if side == 1 else "SHORT"
        initial_stop = stop_loss
        initial_risk = abs(exec_price - stop_loss) if stop_loss is not None else None
        position_id = self._next_position_id()
        entry_id = self._next_entry_id()
        position_key = self._position_key(ticker, strategy)
        self.holdings[position_key] = {
            'symbol': ticker,
            'entry_price': exec_price,
            'quantity': qty,
            'entry_date': date,
            'stop_loss': stop_loss,
            'stop_type': "initial" if stop_loss is not None else None,
            'take_profit': take_profit,
            'take_profit_levels': take_profit_levels,
            'take_profit_level_pcts': take_profit_level_pcts,
            'take_profit_level_index': 0 if take_profit_levels else None,
            'take_profit_level_pct_mode': take_profit_level_pct_mode,
            'entry_metrics': entry_metrics,
            'initial_stop': initial_stop,
            'initial_risk': initial_risk,
            'mfe': 0.0,
            'mae': 0.0,
            'mfe_r': 0.0,
            'mae_r': 0.0,
            'peak_r': 0.0,
            'time_to_1r_days': None,
            'entry_notional': trade_notional,
            'be_trigger_index': None,
            'strategy': strategy,
            'side': side,
            'asset_type': asset_type,
            'multiplier': multiplier,
            'entry_commission': commission,
            'entry_slippage_cost': slippage_cost,
            'raw_entry_price': raw_price,
            'partial_taken': False,
            'mmt_hit': False,
            'trailing_active': False,
            'trend_confirmed': False,
            'trend_confirm_date': None,
            'trend_confirm_index': None,
            'add_count': 0,
            'last_add_date': None,
            'initial_quantity': qty,
            'initial_notional': trade_notional,
            'book': book,
            'position_id': position_id,
            'entry_id': entry_id,
            'entry_allocator_state': allocator_state,
            'entry_book_risk_budget_pct': book_risk_pct,
            'entry_book_risk_budget_dollars': book_risk_dollars,
            **confirmation_details,
            **regime_fields,
        }
        self.trades.append({
            'date': date, 'symbol': ticker, 'action': action, 'price': exec_price, 'qty': qty,
            'strategy': strategy, 'side': side, 'asset_type': asset_type,
            'multiplier': multiplier,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'entry_metrics': entry_metrics,
            'position_side': position_side,
            'initial_stop': initial_stop,
            'initial_risk': initial_risk,
            'raw_price': raw_price,
            'exec_price': exec_price,
            'commission': commission,
            'slippage_cost': slippage_cost,
            'trade_notional': trade_notional,
            'margin_pct': trade_margin_pct,
            'book': book,
            'position_id': position_id,
            'entry_id': entry_id,
            'add_number': 0,
            'allocator_state': allocator_state,
            'book_risk_budget_pct': book_risk_pct,
            'book_risk_budget_dollars': book_risk_dollars,
            **confirmation_details,
            **regime_fields,
        })

    def _sell(
        self,
        position_key: Tuple[str, str],
        date,
        price,
        reason,
        qty: Optional[float] = None,
        partial: bool = False,
        regime_state: Optional[RegimeState] = None,
    ):
        if position_key not in self.holdings:
            return
        holding = self.holdings[position_key]
        ticker, strategy_name = position_key
        trade_margin_pct = self._resolve_futures_margin_pct() if is_future_symbol(ticker) else 1.0
        if trade_margin_pct is None:
            trade_margin_pct = 1.0
        entry_price = holding['entry_price']
        entry_date = holding['entry_date']
        strategy = holding.get('strategy') or strategy_name
        side = holding.get('side', 1)
        asset_type = holding.get('asset_type', 'equity')
        if regime_state is None:
            regime_state = self._latest_regime_state
        regime_fields = self._regime_trade_fields(regime_state, "exit")
        holding_qty = holding.get('quantity', 0)
        multiplier = holding.get('multiplier', 1.0)
        stop_loss = holding.get('stop_loss')
        take_profit = holding.get('take_profit')
        entry_metrics = holding.get('entry_metrics')
        book = holding.get('book')
        confirmation_hits = holding.get('confirmation_hits')
        confirmation_any_hits = holding.get('confirmation_any_hits')
        confirmation_all_hits = holding.get('confirmation_all_hits')
        confirmation_score_hits = holding.get('confirmation_score_hits')
        confirmation_score_total = holding.get('confirmation_score_total')
        confirmation_score_threshold = holding.get('confirmation_score_threshold')
        position_side = "LONG" if side == 1 else "SHORT"
        initial_stop = holding.get('initial_stop')
        initial_risk = holding.get('initial_risk')
        mfe = holding.get('mfe')
        mae = holding.get('mae')
        mfe_r = holding.get('mfe_r')
        mae_r = holding.get('mae_r')
        peak_r = holding.get('peak_r')
        time_to_1r_days = holding.get('time_to_1r_days')
        position_id = holding.get("position_id")
        entry_id = holding.get("entry_id")
        allocator_state = self._allocator_state_label(book)
        book_risk_pct = holding.get("entry_book_risk_budget_pct")
        book_risk_dollars = holding.get("entry_book_risk_budget_dollars")
        stop_type_at_exit = holding.get("stop_type") if reason == "Stop Loss" else None

        if holding_qty <= 0:
            return

        qty_to_sell = holding_qty if qty is None else qty
        if is_future_symbol(ticker):
            qty_to_sell = int(qty_to_sell)
        else:
            if qty is not None or partial:
                qty_to_sell = self._round_equity_qty(qty_to_sell)

        if holding_qty <= self.position_qty_epsilon:
            del self.holdings[position_key]
            return

        if qty_to_sell <= 0:
            return
        if qty_to_sell > holding_qty:
            qty_to_sell = holding_qty
        if not is_future_symbol(ticker) and partial and qty_to_sell < self.min_equity_shares:
            return
        if partial and (holding_qty - qty_to_sell) <= self.position_qty_epsilon:
            qty_to_sell = holding_qty

        raw_price = price
        exec_price = self._execution_price(raw_price, side, is_entry=False)
        stop_level = holding.get("stop_level") if reason == "Stop Loss" else None
        if reason == "Stop Loss" and stop_level is None:
            stop_level = holding.get("stop_loss")
        stop_fill_price = holding.get("stop_fill_price") if reason == "Stop Loss" else None
        if reason == "Stop Loss" and stop_fill_price is None:
            stop_fill_price = raw_price
        stop_fill_mode = holding.get("stop_fill_mode") if reason == "Stop Loss" else None
        if reason == "Stop Loss" and stop_fill_mode is None:
            stop_fill_mode = "close_based"
        if reason == "Stop Loss" and stop_type_at_exit == "breakeven":
            if entry_price is not None and stop_level is not None:
                if side == 1:
                    qualifies_be = stop_level >= entry_price
                else:
                    qualifies_be = stop_level <= entry_price
                if not qualifies_be:
                    stop_type_at_exit = "trailing" if holding.get("trailing_active") else "initial"
        trade_notional = qty_to_sell * exec_price * multiplier
        commission = self._trade_commission(trade_notional)
        slippage_cost = abs(exec_price - raw_price) * qty_to_sell * multiplier
        entry_commission = holding.get('entry_commission', 0.0)
        entry_slippage = holding.get('entry_slippage_cost', 0.0)
        if holding_qty > 0:
            commission_alloc = entry_commission * (qty_to_sell / holding_qty)
            slippage_alloc = entry_slippage * (qty_to_sell / holding_qty)
        else:
            commission_alloc = 0.0
            slippage_alloc = 0.0

        proceeds = trade_notional
        if side == 1:
            self.cash += (proceeds - commission)
            action = "SELL"
            gross_pnl = (exec_price - entry_price) * qty_to_sell * multiplier
        else:
            self.cash -= (proceeds + commission)
            action = "BUY_TO_COVER"
            gross_pnl = (entry_price - exec_price) * qty_to_sell * multiplier
        pnl = gross_pnl - commission - commission_alloc
        entry_notional_total = holding.get('entry_notional')
        if entry_notional_total is None:
            entry_notional_total = entry_price * holding_qty * multiplier
        entry_notional = 0.0
        if holding_qty:
            entry_notional = entry_notional_total * (qty_to_sell / holding_qty)
        if (
            entry_notional
            and self.skip_trade_if_notional_lt_min
            and self.min_trade_notional > 0
            and entry_notional < self.min_trade_notional
        ):
            return_pct = None
        else:
            return_pct = (pnl / entry_notional * 100) if entry_notional else None
        holding_days = (date - entry_date).days if entry_date is not None else None
        exit_r = None
        giveback_r = None
        gap_through_r = None
        if initial_risk and initial_risk > 0:
            denom = initial_risk * qty_to_sell * multiplier
            if denom:
                exit_r = pnl / denom
                if peak_r is not None:
                    giveback_r = peak_r - exit_r
        holding["last_exit_r"] = exit_r
        holding["last_peak_r"] = peak_r
        holding["last_mfe_r"] = mfe_r
        if reason == "Stop Loss" and initial_risk and initial_risk > 0 and stop_level is not None and stop_fill_price is not None:
            if side == 1 and stop_fill_price < stop_level:
                gap_through_r = (stop_level - stop_fill_price) / initial_risk
            elif side == -1 and stop_fill_price > stop_level:
                gap_through_r = (stop_fill_price - stop_level) / initial_risk
            else:
                gap_through_r = 0.0

        self.trades.append({
            'date': date, 'symbol': ticker, 'action': action, 'price': exec_price, 'qty': qty_to_sell,
            'reason': reason, 'pnl': pnl, 'strategy': strategy,
            'entry_price': entry_price, 'entry_date': entry_date,
            'return_pct': return_pct, 'holding_days': holding_days,
            'side': side, 'asset_type': asset_type,
            'multiplier': multiplier,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'entry_metrics': entry_metrics,
            'position_side': position_side,
            'initial_stop': initial_stop,
            'initial_risk': initial_risk,
            'mfe': mfe,
            'mae': mae,
            'mfe_r': mfe_r,
            'mae_r': mae_r,
            'peak_r': peak_r,
            'time_to_1r_days': time_to_1r_days,
            'exit_r': exit_r,
            'giveback_r': giveback_r,
            'raw_price': raw_price,
            'exec_price': exec_price,
            'commission': commission,
            'commission_alloc': commission_alloc,
            'slippage_cost': slippage_cost,
            'slippage_alloc': slippage_alloc,
            'trade_notional': trade_notional,
            'margin_pct': trade_margin_pct,
            'partial': bool(partial),
            'book': book,
            'position_id': position_id,
            'entry_id': entry_id,
            'add_number': None,
            'allocator_state': allocator_state,
            'book_risk_budget_pct': book_risk_pct,
            'book_risk_budget_dollars': book_risk_dollars,
            'stop_type_at_exit': stop_type_at_exit,
            'stop_level': stop_level,
            'stop_fill_price': stop_fill_price,
            'stop_fill_mode': stop_fill_mode,
            'gap_through_r': gap_through_r,
            'confirmation_hits': confirmation_hits,
            'confirmation_any_hits': confirmation_any_hits,
            'confirmation_all_hits': confirmation_all_hits,
            'confirmation_score_hits': confirmation_score_hits,
            'confirmation_score_total': confirmation_score_total,
            'confirmation_score_threshold': confirmation_score_threshold,
            **regime_fields,
        })

        if self.book_allocator_enabled and book in self.book_realized_pnl:
            self.book_realized_pnl[book] += pnl

        if partial:
            holding["last_reduction_date"] = date

        holding['entry_commission'] = max(entry_commission - commission_alloc, 0.0)
        holding['entry_slippage_cost'] = max(entry_slippage - slippage_alloc, 0.0)

        remaining_qty = holding_qty - qty_to_sell
        if remaining_qty > self.position_qty_epsilon and partial:
            holding['entry_notional'] = max(entry_notional_total - entry_notional, 0.0)
        if remaining_qty <= self.position_qty_epsilon or not partial:
            if asset_type != "hedge":
                self.last_exit_dates[position_key] = date
                if pnl <= 0 and strategy is not None:
                    self.last_loss_exit_dates[(ticker, strategy)] = date
                if reason == "Stop Loss" and strategy is not None:
                    self.last_stop_exit_dates[(ticker, strategy)] = date
            self._register_orb_exit(holding, reason)
            del self.holdings[position_key]
            return

        holding['quantity'] = remaining_qty

    def _record_equity(self, date, data_source):
        market_val = 0.0
        positions = 0
        invested = 0.0
        margin_pct = self._resolve_futures_margin_pct()

        def _invested_value(symbol: str, gross_notional: float) -> float:
            if is_future_symbol(symbol):
                if margin_pct is None:
                    return abs(gross_notional)
                return abs(gross_notional) * margin_pct
            return abs(gross_notional)

        for (ticker, _), info in self.holdings.items():
            # Get latest price available up to 'date'
            if ticker in data_source:
                try:
                    price = data_source[ticker].loc[date]['Close']
                    side = info.get('side', 1)
                    multiplier = info.get('multiplier', 1.0)
                    gross_notional = price * info['quantity'] * multiplier
                    market_val += side * gross_notional
                    if info.get('asset_type') != 'hedge':
                        positions += 1
                        invested += _invested_value(ticker, gross_notional)
                except Exception:
                    # If missing price for today, use entry or last known? 
                    # use entry as fallback to avoid noise
                    side = info.get('side', 1)
                    multiplier = info.get('multiplier', 1.0)
                    gross_notional = info['entry_price'] * info['quantity'] * multiplier
                    market_val += side * gross_notional
                    if info.get('asset_type') != 'hedge':
                        positions += 1
                        invested += _invested_value(ticker, gross_notional)
                    
        total_equity = self.cash + market_val
        entry = {
            'date': date,
            'equity': total_equity,
            'positions': positions,
            'invested': invested,
        }
        if self._latest_regime_state is not None:
            entry.update({
                "regime_state": "risk_on" if self._latest_regime_state.risk_on else self._latest_regime_state.severity,
                "regime_risk_on_prob": self._latest_regime_state.risk_on_prob,
                "regime_trend_state": self._latest_regime_state.trend_state,
                "regime_vol_state": self._latest_regime_state.vol_state,
                "regime_stress": self._latest_regime_state.stress,
                "regime_usable": self._latest_regime_state.regime_usable,
            })
        vol_scale = portfolio_vol_scale(
            self.equity_curve + [entry],
            self.portfolio_vol_lookback,
            self.portfolio_vol_target,
        )
        entry["vol_scale"] = vol_scale
        self.equity_curve.append(entry)
        return entry

    def _record_book_daily(
        self,
        date: pd.Timestamp,
        data_source: Dict[str, pd.DataFrame],
        regime_state: RegimeState,
        total_equity: float,
        vol_scale: float,
    ) -> None:
        if not self.book_allocator_enabled:
            return
        row = {
            "date": date,
            "equity_total": total_equity,
            "vol_scale": vol_scale,
            "regime_state": "risk_on" if regime_state.risk_on else regime_state.severity,
            "regime_risk_on_prob": regime_state.risk_on_prob,
            "regime_trend_state": regime_state.trend_state,
            "regime_vol_state": regime_state.vol_state,
            "regime_stress": regime_state.stress,
            "regime_usable": regime_state.regime_usable,
            "regime_stable": bool(regime_state.risk_on and regime_state.risk_on_streak >= self.regime_stability_days),
            "regime_flip_risk": bool(
                regime_state.sma200_distance_pct < self.regime_flip_band_pct
                and abs(regime_state.sma200_slope_pct) < self.regime_flip_slope_pct
            ),
        }

        book_stats: Dict[str, Dict[str, float]] = {book: {
            "gross_exposure": 0.0,
            "net_exposure": 0.0,
            "open_positions": 0,
            "open_risk_r": 0.0,
            "open_risk_dollars": 0.0,
        } for book in self.book_state.keys()}

        for (ticker, _), info in self.holdings.items():
            book = info.get("book")
            if not book or book not in book_stats:
                continue
            qty = info.get("quantity", 0.0)
            if qty <= 0:
                continue
            side = info.get("side", 1)
            multiplier = info.get("multiplier", 1.0)
            price = None
            if ticker in data_source:
                try:
                    price = data_source[ticker].loc[date]["Close"]
                except Exception:
                    price = None
            if price is None:
                price = info.get("entry_price")
            if price is None:
                continue
            notional = price * qty * multiplier
            book_stats[book]["gross_exposure"] += abs(notional)
            book_stats[book]["net_exposure"] += notional * side
            book_stats[book]["open_positions"] += 1
            stop = info.get("stop_loss")
            initial_risk = info.get("initial_risk")
            if stop is not None and initial_risk and initial_risk > 0:
                if side == 1:
                    risk_per_unit = max(price - stop, 0.0)
                else:
                    risk_per_unit = max(stop - price, 0.0)
                book_stats[book]["open_risk_dollars"] += risk_per_unit * qty * multiplier
                book_stats[book]["open_risk_r"] += risk_per_unit / initial_risk

        for book, state in self.book_state.items():
            equity_index = state.get("index_raw")
            if equity_index is None:
                equity_index = self._book_equity(book, date, data_source)
            realized = self.book_realized_pnl.get(book, 0.0)
            unrealized = self._book_unrealized_pnl(book, date, data_source)
            pnl_total = realized + unrealized
            allocator_state = self._allocator_state_label(book)
            target_risk_pct = float(state.get("risk_budget_pct", 0.0) or 0.0)
            max_concurrent_pct = float(state.get("max_concurrent_risk_pct", target_risk_pct) or 0.0)
            effective_risk_pct = self._book_active_risk_pct(book)
            risk_budget_dollars = total_equity * self.book_total_risk_pct * effective_risk_pct
            risk_reference = float(state.get("risk_budget_reference", 0.0) or 0.0)
            equity_dollars = pnl_total + risk_reference
            drawdown_pct = float(state.get("drawdown_pct_raw", 0.0) or 0.0) * 100.0
            drawdown_alloc_pct = float(state.get("drawdown_pct", 0.0) or 0.0) * 100.0
            index_raw = state.get("index_raw")
            index_alloc = state.get("index_alloc")
            total_risk_dollars = total_equity * self.book_total_risk_pct
            open_risk_pct = (book_stats[book]["open_risk_dollars"] / total_risk_dollars) if total_risk_dollars else 0.0

            row.update({
                f"equity_{book}": equity_index,
                f"book_index_raw_{book}": index_raw,
                f"book_index_alloc_{book}": index_alloc,
                f"equity_dollars_{book}": equity_dollars,
                f"realized_pnl_{book}": realized,
                f"unrealized_pnl_{book}": unrealized,
                f"pnl_total_{book}": pnl_total,
                f"gross_exposure_{book}": book_stats[book]["gross_exposure"],
                f"net_exposure_{book}": book_stats[book]["net_exposure"],
                f"open_positions_{book}": book_stats[book]["open_positions"],
                f"open_risk_r_{book}": book_stats[book]["open_risk_r"],
                f"open_risk_dollars_{book}": book_stats[book]["open_risk_dollars"],
                f"allocator_state_{book}": allocator_state,
                f"risk_budget_pct_{book}": target_risk_pct,
                f"max_concurrent_risk_pct_{book}": max_concurrent_pct,
                f"effective_risk_budget_pct_{book}": effective_risk_pct,
                f"open_risk_pct_{book}": open_risk_pct,
                f"risk_budget_dollars_{book}": risk_budget_dollars,
                f"risk_budget_reference_{book}": risk_reference,
                f"book_drawdown_pct_{book}": drawdown_pct,
                f"book_drawdown_alloc_pct_{book}": drawdown_alloc_pct,
            })

        self.book_daily_records.append(row)

    def _liquidate_all_positions(self, date, data_source, reason: str = "End of Backtest") -> None:
        if date is None:
            return
        for position_key in list(self.holdings.keys()):
            holding = self.holdings.get(position_key)
            if holding is None:
                continue
            ticker = position_key[0]
            price = None
            if ticker in data_source:
                try:
                    price = data_source[ticker].loc[date]["Close"]
                except Exception:
                    price = None
            if price is None:
                price = holding.get("entry_price")
            if price is None:
                continue
            self._execute_sell(position_key, date, float(price), reason, data_source)

    def get_report(self):
        equity_df = pd.DataFrame(self.equity_curve)
        trades_df = pd.DataFrame(self.trades)
        summary = equity_metrics(equity_df, trades_df)
        by_strategy = group_trade_metrics(trades_df, 'strategy')
        by_ticker = group_trade_metrics(trades_df, 'symbol')
        return equity_df, trades_df, summary, by_strategy, by_ticker

    def _current_market_value(self, date, data_source, asset_type: Optional[str] = None, side: Optional[int] = None):
        market_val = 0.0
        for (ticker, _), info in self.holdings.items():
            if asset_type and info.get('asset_type') != asset_type:
                continue
            if side is not None and info.get('side', 1) != side:
                continue
            if ticker in data_source:
                try:
                    price = data_source[ticker].loc[date]['Close']
                    if price is None or pd.isna(price):
                        price = info.get('entry_price')
                    if price is None or pd.isna(price):
                        continue
                    multiplier = info.get('multiplier', 1.0)
                    market_val += float(price) * info['quantity'] * multiplier
                except Exception:
                    price = info.get('entry_price')
                    if price is None or pd.isna(price):
                        continue
                    multiplier = info.get('multiplier', 1.0)
                    market_val += float(price) * info['quantity'] * multiplier
        return market_val

    def _current_ticker_value(
        self,
        date,
        data_source,
        ticker: str,
        asset_type: Optional[str] = None,
        side: Optional[int] = None,
    ) -> float:
        market_val = 0.0
        for (sym, _), info in self.holdings.items():
            if sym != ticker:
                continue
            if asset_type and info.get('asset_type') != asset_type:
                continue
            if side is not None and info.get('side', 1) != side:
                continue
            if sym in data_source:
                try:
                    price = data_source[sym].loc[date]['Close']
                    if price is None or pd.isna(price):
                        price = info.get('entry_price')
                    if price is None or pd.isna(price):
                        continue
                    multiplier = info.get('multiplier', 1.0)
                    market_val += float(price) * info['quantity'] * multiplier
                except Exception:
                    price = info.get('entry_price')
                    if price is None or pd.isna(price):
                        continue
                    multiplier = info.get('multiplier', 1.0)
                    market_val += float(price) * info['quantity'] * multiplier
        return market_val

    def _resolve_futures_margin_pct(self) -> Optional[float]:
        margin_pct = self.futures_margin_pct
        if margin_pct is None:
            return None
        try:
            margin_pct = float(margin_pct)
        except (TypeError, ValueError):
            return None
        if margin_pct <= 0:
            return None
        return margin_pct

    def _futures_margin_used(self, date, data_source, margin_pct: float) -> float:
        total = 0.0
        for (ticker, _), info in self.holdings.items():
            if not is_future_symbol(ticker):
                continue
            qty = info.get("quantity", 0)
            if qty == 0:
                continue
            price = info.get("entry_price")
            if data_source is not None and ticker in data_source:
                try:
                    price = data_source[ticker].loc[date]["Close"]
                except Exception:
                    price = price
            if price is None or pd.isna(price):
                continue
            multiplier = info.get("multiplier", 1.0)
            total += abs(float(price) * qty * multiplier) * margin_pct
        return total

    def _futures_margin_available(
        self,
        date,
        data_source,
        margin_pct: float,
        equity: Optional[float] = None,
    ) -> float:
        if equity is None:
            if data_source is not None:
                equity = self._current_equity(date, data_source)
            else:
                equity = self.cash
        used = self._futures_margin_used(date, data_source, margin_pct)
        return equity - used

    def _in_cooldown(self, ticker: str, strategy: Optional[str], date: pd.Timestamp) -> bool:
        if self.cooldown_days <= 0:
            return False
        if strategy is None:
            return False
        last_exit = self.last_exit_dates.get(self._position_key(ticker, strategy))
        if last_exit is None:
            return False
        return (date - last_exit).days < self.cooldown_days

    def _passes_liquidity_filter(self, df: pd.DataFrame, ticker: str, date: pd.Timestamp) -> bool:
        if self.liquidity_lookback <= 0:
            return True
        try:
            hist = df.loc[:date]
        except Exception:
            hist = df[df.index <= date]
        if hist.empty or len(hist) < self.liquidity_lookback:
            return False
        recent = hist.iloc[-self.liquidity_lookback:]

        if is_future_symbol(ticker):
            if self.min_avg_volume_futures <= 0:
                return True
            if "FUT_VOL_SMA_20" in df.columns:
                avg_vol = float(df["FUT_VOL_SMA_20"].loc[recent.index[-1]])
            else:
                avg_vol = recent['Volume'].mean()
            return avg_vol >= self.min_avg_volume_futures

        if self.min_avg_dollar_vol <= 0:
            return True
        if "DOLLAR_VOL_SMA_20" in df.columns:
            avg_dollar_vol = float(df["DOLLAR_VOL_SMA_20"].loc[recent.index[-1]])
        else:
            avg_dollar_vol = (recent['Close'] * recent['Volume']).mean()
        return avg_dollar_vol >= self.min_avg_dollar_vol

    def _apply_breakeven_stop(
        self,
        holding: Dict[str, Any],
        price: float,
        date: pd.Timestamp,
        data_source: Dict[str, pd.DataFrame],
        ticker: str,
        strategy: Optional[Any],
    ) -> Optional[float]:
        initial_risk = holding.get("initial_risk")
        if initial_risk is None or initial_risk <= 0:
            return holding.get("stop_loss")
        side = holding.get("side", 1)
        entry_price = holding.get("entry_price")
        if entry_price is None:
            return holding.get("stop_loss")
        entry_date = holding.get("entry_date")
        if entry_date is None:
            return holding.get("stop_loss")
        df = data_source.get(ticker)
        if df is None or df.empty:
            return holding.get("stop_loss")
        # Cache entry/current positions to avoid repeated indexer calls in tight loops
        if "entry_index" not in holding or holding["entry_index"] is None:
            entry_idx_arr = df.index.get_indexer([entry_date], method="pad")
            holding["entry_index"] = entry_idx_arr[0] if entry_idx_arr.size else None
        entry_idx = holding.get("entry_index")
        current_idx_arr = df.index.get_indexer([date], method="pad")
        current_idx = current_idx_arr[0] if current_idx_arr.size else None
        if entry_idx is None or current_idx is None or entry_idx < 0 or current_idx < 0:
            return holding.get("stop_loss")
        if strategy is not None:
            breakeven_r = strategy.effective_breakeven_r(self.breakeven_r)
            breakeven_delay = strategy.effective_breakeven_delay(self.breakeven_delay_bars)
            trigger_r = strategy.effective_breakeven_trigger_r(1.0)
        else:
            breakeven_r = self.breakeven_r
            breakeven_delay = self.breakeven_delay_bars
            trigger_r = 1.0
        target = None
        if side == 1 and price >= entry_price + (initial_risk * trigger_r):
            if holding.get("be_trigger_index") is None:
                holding["be_trigger_index"] = current_idx
            if current_idx - holding["be_trigger_index"] < breakeven_delay:
                return holding.get("stop_loss")
            target = entry_price + (initial_risk * breakeven_r)
            current = holding.get("stop_loss")
            qualifies_be = target >= entry_price
            if current is None or target > current:
                holding["stop_loss"] = target
                if qualifies_be:
                    holding["stop_type"] = "breakeven"
        elif side == -1 and price <= entry_price - (initial_risk * trigger_r):
            if holding.get("be_trigger_index") is None:
                holding["be_trigger_index"] = current_idx
            if current_idx - holding["be_trigger_index"] < breakeven_delay:
                return holding.get("stop_loss")
            target = entry_price - (initial_risk * breakeven_r)
            current = holding.get("stop_loss")
            qualifies_be = target <= entry_price
            if current is None or target < current:
                holding["stop_loss"] = target
                if qualifies_be:
                    holding["stop_type"] = "breakeven"
        return holding.get("stop_loss")

    def _update_excursions(self, holding: Dict[str, Any], row: pd.Series, date: pd.Timestamp):
        initial_risk = holding.get("initial_risk")
        if initial_risk is None or initial_risk <= 0:
            return
        entry_price = holding.get("entry_price")
        if entry_price is None:
            return
        side = holding.get("side", 1)
        high = row.get("High")
        low = row.get("Low")
        if high is None or low is None:
            return

        if side == 1:
            favorable = high - entry_price
            adverse = entry_price - low
        else:
            favorable = entry_price - low
            adverse = high - entry_price

        if favorable > holding.get("mfe", 0.0):
            holding["mfe"] = favorable
        if adverse > holding.get("mae", 0.0):
            holding["mae"] = adverse

        holding["mfe_r"] = holding.get("mfe", 0.0) / initial_risk
        holding["mae_r"] = holding.get("mae", 0.0) / initial_risk
        if holding["mfe_r"] > holding.get("peak_r", 0.0):
            holding["peak_r"] = holding["mfe_r"]

        if holding.get("time_to_1r_days") is None and favorable >= initial_risk:
            entry_date = holding.get("entry_date")
            if entry_date is not None:
                holding["time_to_1r_days"] = (date - entry_date).days

    def _apply_trailing_stop(
        self,
        holding: Dict[str, Any],
        row: pd.Series,
        strategy: Optional[Any],
    ) -> Optional[float]:
        if strategy is None or not getattr(strategy, "trailing_enabled", False):
            return holding.get("stop_loss")
        side = holding.get("side", 1)
        if side not in (1, -1):
            return holding.get("stop_loss")

        take_profit = holding.get("take_profit")
        if take_profit is not None and not holding.get("mmt_hit"):
            high = row.get("High")
            low = row.get("Low")
            if side == 1 and high is not None and high >= take_profit:
                holding["mmt_hit"] = True
            if side == -1 and low is not None and low <= take_profit:
                holding["mmt_hit"] = True

        if not holding.get("trailing_active"):
            start_r = getattr(strategy, "trailing_start_r", None)
            if start_r is not None:
                mfe_r = holding.get("mfe_r", 0.0) or 0.0
                if mfe_r >= start_r:
                    holding["trailing_active"] = True
            if getattr(strategy, "trailing_use_mmt", False) and holding.get("mmt_hit"):
                holding["trailing_active"] = True

        if not holding.get("trailing_active"):
            return holding.get("stop_loss")

        trend_confirmed = holding.get("trend_confirmed") and bool(getattr(strategy, "trend_confirm_enabled", False))
        candidates = []
        method = None
        if trend_confirmed:
            method = (getattr(strategy, "trend_trailing_method", "ema") or "ema").lower()
            ema_len = getattr(strategy, "trend_trailing_ema_length", 50)
            ema_val = row.get(f"EMA_{ema_len}")
            if ema_val is not None and not pd.isna(ema_val):
                candidates.append(ema_val)
            if method in ("max", "min"):
                avwap_key = "AVWAP_SWING_LOW" if side == 1 else "AVWAP_SWING_HIGH"
                avwap_val = row.get(avwap_key)
                if avwap_val is not None and not pd.isna(avwap_val):
                    candidates.append(avwap_val)
        else:
            if getattr(strategy, "trailing_use_ema20", True):
                ema_len = getattr(strategy, "trailing_ema_length", 20)
                ema_val = row.get(f"EMA_{ema_len}")
                if ema_val is not None and not pd.isna(ema_val):
                    candidates.append(ema_val)
            if getattr(strategy, "trailing_use_avwap", True):
                avwap_key = "AVWAP_SWING_LOW" if side == 1 else "AVWAP_SWING_HIGH"
                avwap_val = row.get(avwap_key)
                if avwap_val is not None and not pd.isna(avwap_val):
                    candidates.append(avwap_val)
            method = (getattr(strategy, "trailing_method", "max") or "max").lower()

        if not candidates:
            return holding.get("stop_loss")

        if method == "min":
            trail = min(candidates)
        else:
            trail = max(candidates)
        atr_mult = 0.0
        if trend_confirmed:
            atr_mult = float(getattr(strategy, "trend_trailing_atr_mult", getattr(strategy, "trailing_atr_mult", 0.0)) or 0.0)
        else:
            atr_mult = float(getattr(strategy, "trailing_atr_mult", 0.0) or 0.0)
        if atr_mult:
            atr = row.get("ATR_14")
            if atr is not None and not pd.isna(atr) and atr > 0:
                if side == 1:
                    trail -= atr * atr_mult
                else:
                    trail += atr * atr_mult
        current = holding.get("stop_loss")
        if side == 1:
            if current is None or trail > current:
                holding["stop_loss"] = float(trail)
                holding["stop_type"] = "trailing"
        else:
            if current is None or trail < current:
                holding["stop_loss"] = float(trail)
                holding["stop_type"] = "trailing"
        return holding.get("stop_loss")

    def _should_exhaustion_exit(
        self,
        holding: Dict[str, Any],
        row: pd.Series,
        df: pd.DataFrame,
        strategy: Optional[Any],
        date: pd.Timestamp,
    ) -> bool:
        if strategy is None or not getattr(strategy, "exhaustion_enabled", False):
            return False
        if getattr(strategy, "exhaustion_disable_after_trend_confirm", False) and holding.get("trend_confirmed"):
            return False
        if not holding.get("trailing_active"):
            return False
        side = holding.get("side", 1)
        if side != 1:
            return False

        close = row.get("Close")
        open_ = row.get("Open")
        high = row.get("High")
        low = row.get("Low")
        volume = row.get("Volume")
        if close is None or open_ is None or high is None or low is None:
            return False

        atr = row.get("ATR_14")
        if atr is None or pd.isna(atr) or atr <= 0:
            atr_series = strategy.atr_series(df)
            if atr_series is not None and date in atr_series.index:
                atr = atr_series.loc[date]
        if atr is None or pd.isna(atr) or atr <= 0:
            return False

        lookback = getattr(strategy, "exhaustion_resistance_lookback", 40)
        near_col = f"SR_NEAR_HIGH_{lookback}"
        resistance = row.get(near_col)
        near_resistance = False
        if resistance is not None and not pd.isna(resistance):
            threshold = getattr(strategy, "exhaustion_resistance_atr_mult", 0.4)
            near_resistance = abs(resistance - close) <= (atr * threshold)

        require_near_or_mmt = getattr(strategy, "exhaustion_require_near_or_mmt", True)
        mmt_hit = holding.get("mmt_hit", False)

        rsi_level = getattr(strategy, "exhaustion_rsi_level", 70.0)
        rollover_bars = max(2, int(getattr(strategy, "exhaustion_rsi_rollover_bars", 2)))
        rsi_series = strategy.rsi_series(df, length=14)
        rsi_rollover = False
        if rsi_series is not None and date in rsi_series.index:
            idx = rsi_series.index.get_indexer([date], method="pad")
            if idx.size and idx[0] >= (rollover_bars - 1):
                window = rsi_series.iloc[idx[0] - (rollover_bars - 1) : idx[0] + 1].values
                if len(window) >= rollover_bars and not pd.isna(window).any():
                    if window[-1] > rsi_level:
                        rsi_rollover = all(window[i] < window[i - 1] for i in range(1, len(window)))

        range_ = high - low
        close_loc = (close - low) / range_ if range_ > 0 else 0.0
        candle_bearish = close < open_
        candle_weak = close_loc <= getattr(strategy, "exhaustion_candle_close_loc", 0.4)
        candle_signal = candle_bearish and candle_weak and near_resistance

        vol_avg = row.get("VOL_SMA_20")
        volume_ratio = getattr(strategy, "exhaustion_volume_ratio", 0.9)
        stall_mult = getattr(strategy, "exhaustion_stall_atr_mult", 0.25)
        prev_close = df["Close"].shift(1)
        prev_val = prev_close.loc[date] if date in prev_close.index else None
        stall = prev_val is not None and abs(close - prev_val) <= (atr * stall_mult)
        vol_contraction = vol_avg is not None and not pd.isna(vol_avg) and volume is not None and volume < (vol_avg * volume_ratio)
        volume_stall = vol_contraction and stall
        if require_near_or_mmt and not (near_resistance or mmt_hit):
            volume_stall = False

        signals = sum([bool(rsi_rollover), bool(candle_signal), bool(volume_stall)])
        min_required = int(getattr(strategy, "exhaustion_min_signals", 2))
        return signals >= min_required

    def _should_giveback_exit(
        self,
        holding: Dict[str, Any],
        price: float,
        date: pd.Timestamp,
        strategy: Optional[Any],
    ) -> bool:
        if strategy is None or not getattr(strategy, "giveback_enabled", False):
            return False
        initial_risk = holding.get("initial_risk")
        if initial_risk is None or initial_risk <= 0:
            return False
        peak_r = holding.get("peak_r")
        if peak_r is None:
            return False
        trigger_r = getattr(strategy, "giveback_trigger_r", 2.0)
        floor_r = getattr(strategy, "giveback_floor_r", 1.0)
        min_days = getattr(strategy, "giveback_min_days", 0)
        entry_date = holding.get("entry_date")
        if entry_date is not None and (date - entry_date).days < min_days:
            return False
        entry_price = holding.get("entry_price")
        if entry_price is None:
            return False
        side = holding.get("side", 1)
        current_r = (price - entry_price) / initial_risk if side == 1 else (entry_price - price) / initial_risk
        return peak_r >= trigger_r and current_r <= floor_r

    def _annotate_last_trade(self, equity_before: float, equity_after: float, cash_after: float, position_notional: float):
        if not self.trades:
            return
        equity_pct = (position_notional / equity_before * 100) if equity_before else 0.0
        self.trades[-1].update({
            "equity_before": equity_before,
            "equity_after": equity_after,
            "cash_after": cash_after,
            "position_notional": position_notional,
            "equity_pct": equity_pct,
        })

    def _regime_trade_fields(self, regime_state: Optional[RegimeState], prefix: str) -> Dict[str, Any]:
        if regime_state is None:
            return {}
        return {
            f"{prefix}_regime_state": "risk_on" if regime_state.risk_on else regime_state.severity,
            f"{prefix}_risk_on_prob": regime_state.risk_on_prob,
            f"{prefix}_trend_state": regime_state.trend_state,
            f"{prefix}_vol_state": regime_state.vol_state,
            f"{prefix}_stress": regime_state.stress,
            f"{prefix}_regime_usable": regime_state.regime_usable,
        }

    def _execute_buy(
        self,
        ticker,
        date,
        price,
        amount_usd,
        stop_loss,
        strategy,
        side,
        asset_type,
        take_profit,
        entry_metrics,
        data_source,
        take_profit_levels=None,
        take_profit_level_pcts=None,
        take_profit_level_pct_mode=None,
        regime_state: Optional[RegimeState] = None,
    ):
        trade_count = len(self.trades)
        equity_before = self._current_equity(date, data_source)
        self._buy(
            ticker,
            date,
            price,
            amount_usd,
            stop_loss,
            strategy,
            side=side,
            asset_type=asset_type,
            take_profit=take_profit,
            entry_metrics=entry_metrics,
            equity_before=equity_before,
            data_source=data_source,
            take_profit_levels=take_profit_levels,
            take_profit_level_pcts=take_profit_level_pcts,
            take_profit_level_pct_mode=take_profit_level_pct_mode,
            regime_state=regime_state,
        )
        if len(self.trades) == trade_count:
            return False
        holding = self.holdings.get(self._position_key(ticker, strategy))
        strategy_obj = self.strategy_map.get(strategy) if isinstance(strategy, str) else None
        if holding and strategy_obj and data_source:
            df = data_source.get(ticker)
            if df is not None and not df.empty and date in df.index:
                rsi_len = int(getattr(strategy_obj, "momentum_fail_rsi_length", getattr(strategy_obj, "rsi_length", 14)) or 14)
                rsi_series = strategy_obj.rsi_series(df, length=rsi_len)
                if rsi_series is not None and date in rsi_series.index:
                    rsi_val = rsi_series.loc[date]
                    if pd.notna(rsi_val):
                        holding["entry_rsi"] = float(rsi_val)
                atr_len = int(getattr(strategy_obj, "atr_contraction_lookback", 20) or 20)
                atr_series = strategy_obj.atr_series(df, period=atr_len)
                if atr_series is not None and date in atr_series.index:
                    atr_val = atr_series.loc[date]
                    if pd.notna(atr_val):
                        holding["entry_atr"] = float(atr_val)
        if holding and strategy_obj:
            self._register_orb_entry(strategy_obj, ticker, date, side, holding)
        if holding:
            position_notional = abs(holding['quantity'] * holding.get('entry_price', price) * holding.get('multiplier', 1.0))
        else:
            position_notional = 0.0
        equity_after = self._current_equity(date, data_source)
        self._annotate_last_trade(equity_before, equity_after, self.cash, position_notional)
        return True

    def _execute_sell(
        self,
        position_key: Tuple[str, str],
        date,
        price,
        reason,
        data_source,
        qty: Optional[float] = None,
        partial: bool = False,
        regime_state: Optional[RegimeState] = None,
    ):
        trade_count = len(self.trades)
        equity_before = self._current_equity(date, data_source)
        holding = self.holdings.get(position_key)
        if holding:
            qty_for_notional = holding['quantity'] if qty is None else qty
            position_notional = abs(qty_for_notional * price * holding.get('multiplier', 1.0))
        else:
            position_notional = 0.0
        self._sell(position_key, date, price, reason, qty=qty, partial=partial, regime_state=regime_state)
        if len(self.trades) == trade_count:
            return False
        equity_after = self._current_equity(date, data_source)
        self._annotate_last_trade(equity_before, equity_after, self.cash, position_notional)
        return True

    def _available_trade_amount(
        self,
        date,
        data_source,
        ticker,
        entry_price,
        stop_loss,
        regime_state: RegimeState,
        side,
        strategy: Optional[Any] = None,
        return_reason: bool = False,
    ):
        debug_alloc = bool(getattr(strategy, "debug_allocation", False)) if strategy is not None else False
        strategy_name = strategy.get_name() if strategy is not None else "UNKNOWN"
        multiplier = contract_multiplier(ticker, self.contract_multipliers)
        margin_pct = self._resolve_futures_margin_pct() if is_future_symbol(ticker) else None

        def _log_alloc(
            reason: str,
            amount: float,
            stop_value: Optional[float] = None,
            per_contract_risk: Optional[float] = None,
            risk_budget: Optional[float] = None,
            qty: Optional[float] = None,
            notional: Optional[float] = None,
            remaining: Optional[float] = None,
            remaining_ticker: Optional[float] = None,
        ) -> None:
            if not debug_alloc:
                return
            dist = None
            if stop_value is not None and entry_price is not None:
                dist = abs(entry_price - stop_value)
            logger.info(
                "Alloc debug %s %s side=%s reason=%s entry=%.4f stop=%s dist=%s mult=%.4f "
                "per_risk=%s risk_budget=%s qty=%s notional=%s trade_size=%.2f "
                "remaining=%s remaining_ticker=%s amount=%.2f",
                strategy_name,
                ticker,
                side,
                reason,
                entry_price,
                f"{stop_value:.4f}" if stop_value is not None else None,
                f"{dist:.4f}" if dist is not None else None,
                multiplier,
                f"{per_contract_risk:.4f}" if per_contract_risk is not None else None,
                f"{risk_budget:.2f}" if risk_budget is not None else None,
                f"{qty:.4f}" if qty is not None else None,
                f"{notional:.2f}" if notional is not None else None,
                self.trade_size,
                f"{remaining:.2f}" if remaining is not None else None,
                f"{remaining_ticker:.2f}" if remaining_ticker is not None else None,
                amount,
            )

        apply_alloc_cap = side == 1
        futures_alloc_cap = margin_pct is not None and is_future_symbol(ticker)
        if futures_alloc_cap:
            apply_alloc_cap = True

        equity = self._current_equity(date, data_source)
        max_alloc = equity * self.max_alloc_pct * self._regime_exposure_mult(regime_state)
        market_val = self._current_market_value(date, data_source, asset_type="equity", side=1)
        futures_margin_used = 0.0
        if margin_pct is not None:
            futures_margin_used = self._futures_margin_used(date, data_source, margin_pct)
        remaining = max_alloc - (market_val + futures_margin_used)

        ticker_val = self._current_ticker_value(date, data_source, ticker, asset_type="equity", side=1)
        if futures_alloc_cap:
            ticker_val = self._current_ticker_value(date, data_source, ticker) * margin_pct
        remaining_ticker = max_alloc - ticker_val
        if apply_alloc_cap and remaining <= 0:
            _log_alloc("risk_budget", 0.0, remaining=remaining, remaining_ticker=remaining_ticker)
            return (0.0, "risk_budget") if return_reason else 0.0
        if apply_alloc_cap and remaining_ticker <= 0:
            _log_alloc("risk_budget", 0.0, remaining=remaining, remaining_ticker=remaining_ticker)
            return (0.0, "risk_budget") if return_reason else 0.0
        if (
            apply_alloc_cap
            and self.skip_trade_if_notional_lt_min
            and self.min_trade_notional > 0
            and remaining < (self.min_trade_notional * margin_pct if futures_alloc_cap else self.min_trade_notional)
        ):
            _log_alloc("min_notional", 0.0, remaining=remaining, remaining_ticker=remaining_ticker)
            return (0.0, "min_notional") if return_reason else 0.0

        use_fallback_stop = bool(getattr(strategy, "use_fallback_stop", True)) if strategy is not None else True
        if stop_loss is None and not use_fallback_stop:
            _log_alloc("other", 0.0, remaining=remaining, remaining_ticker=remaining_ticker)
            return (0.0, "other") if return_reason else 0.0
        stop_loss = resolve_stop_loss(date, data_source, ticker, entry_price, stop_loss, side)
        per_contract_risk = abs(entry_price - stop_loss) * multiplier if stop_loss is not None else entry_price * 0.02 * multiplier
        if per_contract_risk <= 0:
            _log_alloc("other", 0.0, stop_value=stop_loss, per_contract_risk=per_contract_risk, remaining=remaining, remaining_ticker=remaining_ticker)
            return (0.0, "other") if return_reason else 0.0

        risk_budget = equity * self.risk_per_trade_pct
        regime_mult = self._regime_risk_mult(regime_state, strategy)
        if regime_mult > 0:
            risk_budget *= regime_mult
        else:
            risk_budget = 0.0
        risk_mult = float(getattr(strategy, "strategy_risk_mult", 1.0) or 1.0) if strategy is not None else 1.0
        if risk_mult > 0:
            risk_budget *= risk_mult
        book = self._strategy_book(strategy) if strategy is not None else None
        per_trade_cap = 0.0
        remaining_book_risk = None
        if self.book_allocator_enabled and book and book in self.book_state:
            if self.book_state[book].get("disabled"):
                _log_alloc("risk_budget", 0.0, stop_value=stop_loss, per_contract_risk=per_contract_risk, risk_budget=risk_budget)
                return (0.0, "risk_budget") if return_reason else 0.0
            per_trade_cap = self._book_per_trade_cap(book, equity)
            remaining_book_risk = self._book_budget_dollars(book, equity) - self._book_open_risk(book, date, data_source)
            if remaining_book_risk <= 0:
                _log_alloc("risk_budget", 0.0, stop_value=stop_loss, per_contract_risk=per_contract_risk, risk_budget=risk_budget)
                return (0.0, "risk_budget") if return_reason else 0.0
            if per_trade_cap > 0:
                risk_budget = min(risk_budget, per_trade_cap)
            risk_budget = min(risk_budget, remaining_book_risk)
        if self.portfolio_throttle_active:
            risk_budget *= self.portfolio_throttle_size_mult
        if risk_budget <= 0:
            _log_alloc("risk_budget", 0.0, stop_value=stop_loss, per_contract_risk=per_contract_risk, risk_budget=risk_budget)
            return (0.0, "risk_budget") if return_reason else 0.0
        qty = risk_budget / per_contract_risk
        if is_future_symbol(ticker):
            qty = int(qty)
            if qty < 1:
                _log_alloc(
                    "risk_budget",
                    0.0,
                    stop_value=stop_loss,
                    per_contract_risk=per_contract_risk,
                    risk_budget=risk_budget,
                    qty=qty,
                )
                return (0.0, "risk_budget") if return_reason else 0.0
        notional = qty * entry_price * multiplier
        if self.skip_trade_if_notional_lt_min and self.min_trade_notional > 0 and notional < self.min_trade_notional:
            _log_alloc(
                "min_notional",
                0.0,
                stop_value=stop_loss,
                per_contract_risk=per_contract_risk,
                risk_budget=risk_budget,
                qty=qty,
                notional=notional,
            )
            return (0.0, "min_notional") if return_reason else 0.0
        vol_scale = portfolio_vol_scale(
            self.equity_curve,
            self.portfolio_vol_lookback,
            self.portfolio_vol_target,
        )
        notional *= vol_scale
        if per_contract_risk > 0:
            scaled_qty = notional / (entry_price * multiplier)
            scaled_risk = scaled_qty * per_contract_risk
            if scaled_risk > risk_budget:
                scaled_qty = risk_budget / per_contract_risk
                notional = scaled_qty * entry_price * multiplier

        allow_risk_off_entries = self._resolve_strategy_override(
            strategy,
            "allow_risk_off_entries",
            self.allow_risk_off_entries,
        )
        allow_entries_in_risk_off = getattr(strategy, "allow_entries_in_risk_off", None) if strategy is not None else None
        if allow_entries_in_risk_off is False:
            allow_risk_off_entries = False
        risk_off_mult = self._resolve_strategy_override(
            strategy,
            "risk_off_size_mult",
            self.risk_off_size_mult,
        )
        if side == 1 and not regime_state.risk_on and allow_risk_off_entries:
            notional *= risk_off_mult

        max_position_pct = getattr(strategy, "strategy_max_position_pct", None) if strategy is not None else None
        if max_position_pct is not None:
            try:
                max_position_pct = float(max_position_pct)
            except (TypeError, ValueError):
                max_position_pct = None
        if max_position_pct is not None and max_position_pct > 0:
            max_notional = equity * max_position_pct
            if margin_pct is not None and is_future_symbol(ticker):
                max_notional = max_notional / margin_pct
            if max_notional > 0:
                notional = min(notional, max_notional)

        trade_size = self.trade_size
        strategy_trade_size = getattr(strategy, "strategy_trade_size", None) if strategy is not None else None
        if strategy_trade_size is not None:
            try:
                strategy_trade_size = float(strategy_trade_size)
            except (TypeError, ValueError):
                strategy_trade_size = None
        if strategy_trade_size is not None and strategy_trade_size > 0:
            trade_size = strategy_trade_size

        cash_cap = self.cash
        if margin_pct is not None:
            margin_available = self._futures_margin_available(date, data_source, margin_pct, equity)
            if margin_available <= 0:
                _log_alloc(
                    "risk_budget",
                    0.0,
                    stop_value=stop_loss,
                    per_contract_risk=per_contract_risk,
                    risk_budget=risk_budget,
                    qty=qty,
                    notional=notional,
                    remaining=remaining,
                    remaining_ticker=remaining_ticker,
                )
                return (0.0, "risk_budget") if return_reason else 0.0
            cash_cap = margin_available / margin_pct
            if trade_size > 0:
                trade_size = trade_size / margin_pct

        if side == 1:
            amount = min(trade_size, cash_cap, remaining, remaining_ticker, notional)
        else:
            amount = min(trade_size, cash_cap, notional)
        if amount <= 0:
            _log_alloc(
                "risk_budget",
                amount,
                stop_value=stop_loss,
                per_contract_risk=per_contract_risk,
                risk_budget=risk_budget,
                qty=qty,
                notional=notional,
                remaining=remaining,
                remaining_ticker=remaining_ticker,
            )
        return (amount, None) if return_reason else amount

    def _current_equity(self, date, data_source):
        market_val = 0.0
        for (ticker, _), info in self.holdings.items():
            if ticker in data_source:
                try:
                    price = data_source[ticker].loc[date]['Close']
                    if price is None or pd.isna(price):
                        price = info.get('entry_price')
                    if price is None or pd.isna(price):
                        continue
                    multiplier = info.get('multiplier', 1.0)
                    market_val += info.get('side', 1) * float(price) * info['quantity'] * multiplier
                except Exception:
                    price = info.get('entry_price')
                    if price is None or pd.isna(price):
                        continue
                    multiplier = info.get('multiplier', 1.0)
                    market_val += info.get('side', 1) * float(price) * info['quantity'] * multiplier
        return self.cash + market_val

    def _update_hedge(self, date, data_source, regime_state: RegimeState):
        if self.hedge_symbol not in data_source:
            return
        hedge_key = self._position_key(self.hedge_symbol, "HEDGE")
        existing = self.holdings.get(hedge_key)
        target_pct = regime_state.hedge_pct
        if target_pct is None or pd.isna(target_pct):
            target_pct = None
        else:
            try:
                target_pct = float(target_pct)
            except (TypeError, ValueError):
                target_pct = None
        if target_pct is None or not math.isfinite(target_pct) or target_pct <= 0:
            if existing and existing.get("asset_type") == "hedge":
                try:
                    price = data_source[self.hedge_symbol].loc[date]["Close"]
                    if price is None or pd.isna(price):
                        return
                except Exception:
                    return
                self._execute_sell(hedge_key, date, float(price), "HEDGE_OFF", data_source)
            return
        long_exposure = self._current_market_value(date, data_source, asset_type="equity", side=1)
        try:
            long_exposure = float(long_exposure)
        except (TypeError, ValueError):
            long_exposure = 0.0
        if not math.isfinite(long_exposure):
            long_exposure = 0.0
        target_notional = long_exposure * target_pct
        if target_notional <= 0:
            if existing and existing.get("asset_type") == "hedge":
                try:
                    price = data_source[self.hedge_symbol].loc[date]["Close"]
                    if price is None or pd.isna(price):
                        return
                except Exception:
                    return
                self._execute_sell(hedge_key, date, float(price), "HEDGE_OFF", data_source)
            return

        try:
            price = data_source[self.hedge_symbol].loc[date]["Close"]
            if price is None or pd.isna(price):
                return
        except Exception:
            return
        try:
            price = float(price)
        except (TypeError, ValueError):
            return
        if not math.isfinite(price) or price <= 0:
            return

        multiplier = contract_multiplier(self.hedge_symbol, self.contract_multipliers)
        target_qty = target_notional / (price * multiplier)
        if existing and existing.get("asset_type") == "hedge":
            current_qty = existing["quantity"]
            if current_qty == 0:
                return
            diff_pct = abs(target_qty - current_qty) / current_qty
            if diff_pct < 0.1:
                return
            self._execute_sell(hedge_key, date, price, "HEDGE_REBAL", data_source)

        self._execute_buy(
            self.hedge_symbol,
            date,
            price,
            target_notional,
            None,
            "HEDGE",
            -1,
            "hedge",
            None,
            None,
            data_source,
        )

    def _entry_side(self, symbol: str, sentiment: Optional[str]) -> Optional[int]:
        if sentiment == "BULLISH":
            return 1
        if sentiment == "BEARISH":
            if is_future_symbol(symbol) or self.allow_short_equity:
                return -1
        return None

    def _orb_trade_group(self, strategy: Optional[Any]) -> Optional[str]:
        if strategy is None:
            return None
        group = getattr(strategy, "orb_trade_group", None)
        if not group:
            return None
        return str(group)

    def _orb_trade_count_settings(self, strategy: Optional[Any]) -> Dict[str, Any]:
        settings: Dict[str, Any] = {}
        if strategy is None:
            return settings
        cfg = getattr(strategy, "trade_count", None)
        if isinstance(cfg, dict):
            settings.update(cfg)
        if "max_trades_per_day" not in settings:
            settings["max_trades_per_day"] = getattr(strategy, "orb_max_trades_per_day", 2)
        return settings

    def _orb_trend_gate(
        self,
        strategy: Optional[Any],
        ticker: str,
        side: int,
        date: pd.Timestamp,
        data_source: Dict[str, pd.DataFrame],
    ) -> bool:
        if strategy is None:
            return False
        df = data_source.get(ticker)
        if df is None or df.empty:
            return False
        try:
            row = df.loc[date]
        except Exception:
            return False
        if side == 1:
            if "MKT_TREND_OK" in row:
                return bool(row["MKT_TREND_OK"])
            if "SPY_TREND_UP" in row:
                return bool(row["SPY_TREND_UP"])
            return False
        if "SPY_TREND_DOWN" in row:
            return bool(row["SPY_TREND_DOWN"])
        if "MKT_TREND_OK" in row:
            return not bool(row["MKT_TREND_OK"])
        return False

    def _orb_session_date(self, ts: pd.Timestamp, strategy: Optional[Any]) -> Optional[pd.Timestamp]:
        if ts is None or strategy is None:
            return None
        tz_name = getattr(strategy, "orb_timezone", None) or getattr(strategy, "session_timezone", None) or "America/New_York"
        ts_val = pd.Timestamp(ts)
        try:
            if ts_val.tz is None:
                ts_local = ts_val.tz_localize(tz_name)
            else:
                ts_local = ts_val.tz_convert(tz_name)
        except Exception:
            ts_local = ts_val
        return ts_local.date()

    def _orb_state_key(
        self,
        strategy: Optional[Any],
        ticker: str,
        date: pd.Timestamp,
    ) -> Optional[Tuple[str, str, pd.Timestamp]]:
        group = self._orb_trade_group(strategy)
        if not group:
            return None
        session_date = self._orb_session_date(date, strategy)
        if session_date is None:
            return None
        return (ticker, group, session_date)

    def _orb_entry_allowed(
        self,
        strategy: Optional[Any],
        ticker: str,
        side: int,
        date: pd.Timestamp,
        data_source: Dict[str, pd.DataFrame],
    ) -> Tuple[bool, Optional[str]]:
        key = self._orb_state_key(strategy, ticker, date)
        if key is None:
            return True, None
        state = self.orb_trade_state.get(key)
        settings = self._orb_trade_count_settings(strategy)
        max_trades = int(settings.get("max_trades_per_day", 0) or 0)
        has_trade_count = isinstance(getattr(strategy, "trade_count", None), dict)
        if max_trades <= 0:
            return True, None
        if state is None:
            return True, None
        if state.get("active"):
            return False, "rejected_other"
        trades = int(state.get("trades", 0))
        if trades >= max_trades:
            return False, "rejected_other"
        if trades == 0:
            return True, None
        if not has_trade_count:
            allow_flip = bool(state.get("allow_flip"))
            first_side = state.get("first_side")
            if allow_flip and first_side is not None and side != first_side:
                return True, None
            return False, "rejected_other"

        peak_threshold = float(settings.get("allow_second_trade_if_first_mfe_r_ge", 0.5))
        exit_block = settings.get("block_second_trade_if_first_exit_r_le", -1.0)
        exit_block = float(exit_block) if exit_block is not None else None
        allow_trend = bool(settings.get("allow_second_trade_if_trend_gate_true", True))
        trend_ok = self._orb_trend_gate(strategy, ticker, side, date, data_source) if allow_trend else False

        if trend_ok:
            return True, None
        first_exit_r = state.get("first_trade_exit_r")
        if exit_block is not None and first_exit_r is not None and first_exit_r <= exit_block:
            return False, "rejected_other"
        first_peak_r = state.get("first_trade_peak_r")
        if first_peak_r is not None and first_peak_r >= peak_threshold:
            return True, None
        return False, "rejected_other"

    def _register_orb_entry(
        self,
        strategy: Optional[Any],
        ticker: str,
        date: pd.Timestamp,
        side: int,
        holding: Dict[str, Any],
    ) -> None:
        key = self._orb_state_key(strategy, ticker, date)
        if key is None:
            return
        state = self.orb_trade_state.setdefault(
            key,
            {"trades": 0, "active": False, "allow_flip": False, "first_side": None},
        )
        if int(state.get("trades", 0)) == 0:
            state["first_side"] = side
        state["trades"] = int(state.get("trades", 0)) + 1
        state["active"] = True
        if int(state.get("trades", 0)) >= 2:
            state["allow_flip"] = False
        holding["orb_trade_group"] = key[1]
        holding["orb_session_date"] = key[2]

    def _register_orb_exit(self, holding: Dict[str, Any], reason: str) -> None:
        group = holding.get("orb_trade_group")
        session_date = holding.get("orb_session_date")
        if not group or session_date is None:
            return
        ticker = holding.get("symbol")
        key = (ticker, group, session_date)
        state = self.orb_trade_state.get(key)
        if not state:
            return
        state["active"] = False
        state["last_exit_reason"] = reason
        if int(state.get("trades", 0)) == 1:
            peak_r = holding.get("last_peak_r")
            if peak_r is None:
                peak_r = holding.get("peak_r")
            state["first_trade_peak_r"] = peak_r
            state["first_trade_exit_r"] = holding.get("last_exit_r")
            strategy_name = holding.get("strategy")
            strategy_obj = self.strategy_map.get(strategy_name) if strategy_name else None
            allow_flip = bool(getattr(strategy_obj, "orb_allow_flip_on_stop", False))
            if allow_flip and reason == "Stop Loss":
                state["allow_flip"] = True
            else:
                state["allow_flip"] = False

    def _resolve_strategy_override(self, strategy: Optional[Any], attr: str, default):
        if strategy is None:
            return default
        value = getattr(strategy, attr, None)
        return default if value is None else value

    def _entry_allowed(
        self,
        strategy: Optional[Any],
        side: int,
        regime_state: RegimeState,
        entry_metrics: Optional[Dict[str, Any]],
        ticker: Optional[str] = None,
        date: Optional[pd.Timestamp] = None,
    ) -> Tuple[bool, Optional[str]]:
        if strategy is not None and date is not None:
            if self._event_skip_active(strategy, ticker, date):
                return False, "rejected_event_calendar"
        if strategy is not None and not bool(getattr(strategy, "entry_enabled", True)):
            return False, "rejected_other"
        allow_risk_off_entries = self._resolve_strategy_override(
            strategy,
            "allow_risk_off_entries",
            self.allow_risk_off_entries,
        )
        allow_entries_in_risk_off = getattr(strategy, "allow_entries_in_risk_off", None) if strategy is not None else None
        if allow_entries_in_risk_off is False:
            allow_risk_off_entries = False
        allow_short_risk_on = self._resolve_strategy_override(
            strategy,
            "allow_short_risk_on",
            self.allow_short_risk_on,
        )
        if not getattr(regime_state, "regime_usable", True):
            return False, "rejected_regime_usable"
        if side == 1 and not regime_state.risk_on and not allow_risk_off_entries:
            return False, "rejected_regime"
        if side == -1 and not allow_short_risk_on and regime_state.risk_on:
            return False, "rejected_regime"
        if strategy is not None:
            allowed_trends = getattr(strategy, "allowed_trend_states", None)
            if allowed_trends:
                allowed = {str(val).upper() for val in allowed_trends}
                if str(getattr(regime_state, "trend_state", "")).upper() not in allowed:
                    return False, "rejected_regime_trend"
            max_vol_state = getattr(strategy, "max_vol_state", None)
            if max_vol_state:
                order = {"LOW": 0, "NORMAL": 1, "HIGH": 2, "CRISIS": 3}
                regime_vol = order.get(str(getattr(regime_state, "vol_state", "")).upper(), 99)
                max_vol = order.get(str(max_vol_state).upper(), 99)
                if regime_vol > max_vol:
                    return False, "rejected_regime_vol"
            min_prob = getattr(strategy, "min_risk_on_prob", None)
            if min_prob is not None:
                try:
                    min_prob = float(min_prob)
                except (TypeError, ValueError):
                    min_prob = None
            if min_prob is not None:
                prob = getattr(regime_state, "risk_on_prob", None)
                try:
                    prob = float(prob)
                except (TypeError, ValueError):
                    prob = None
                if prob is None or not math.isfinite(prob) or prob < min_prob:
                    return False, "rejected_regime_prob"

        if side == 1 and strategy is not None:
            bypass = False
            if getattr(strategy, "allow_regime_bypass_large_gap", False):
                if entry_metrics and entry_metrics.get("gap_bucket") == "large":
                    bypass = True
            if getattr(strategy, "require_regime_stability", False) and not bypass:
                stability_days = strategy.effective_regime_stability_days(self.regime_stability_days)
                if regime_state.risk_on_streak < stability_days:
                    return False, "rejected_regime_stability"
            if getattr(strategy, "avoid_regime_flip", False):
                band = strategy.effective_regime_flip_band_pct(self.regime_flip_band_pct)
                slope = strategy.effective_regime_flip_slope_pct(self.regime_flip_slope_pct)
                if regime_state.sma200_distance_pct < band and abs(regime_state.sma200_slope_pct) < slope:
                    return False, "rejected_regime_flip_avoid"
        book = self._strategy_book(strategy) if strategy is not None else None
        if self.book_allocator_enabled and book and book in self.book_state:
            if self.book_state[book].get("disabled"):
                return False, "rejected_risk_budget"
            if (
                book == self.convex_book_name
                and self.convex_block_if_core_risk_off
                and not self._core_regime_allowed(regime_state)
            ):
                return False, "rejected_regime"

        if strategy is not None and ticker is not None and date is not None:
            if not self._confirmation_gate(strategy, ticker, date):
                return False, "rejected_other"
        return True, None

    def _event_skip_active(
        self,
        strategy: Any,
        ticker: Optional[str],
        date: pd.Timestamp,
    ) -> bool:
        if not bool(getattr(strategy, "skip_events_enabled", False)):
            return False
        calendar_path = getattr(strategy, "skip_events_file", None)
        if not calendar_path:
            return False
        resolved = os.path.abspath(calendar_path)
        calendar = self.event_calendar_cache.get(resolved)
        if calendar is None:
            calendar = EventCalendar(resolved)
            self.event_calendar_cache[resolved] = calendar
        event_types = getattr(strategy, "skip_events_types", None)
        return calendar.should_skip(date, ticker=ticker, event_types=event_types)

    def _core_regime_allowed(self, regime_state: RegimeState) -> bool:
        if not regime_state.risk_on:
            return False
        if regime_state.risk_on_streak < self.regime_stability_days:
            return False
        if (
            regime_state.sma200_distance_pct < self.regime_flip_band_pct
            and abs(regime_state.sma200_slope_pct) < self.regime_flip_slope_pct
        ):
            return False
        return True

    def _normalize_strategy_list(self, value: Optional[Any]) -> List[str]:
        if not value:
            return []
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(v) for v in value if v]
        return []

    def _record_today_signals(
        self,
        ticker: str,
        date: pd.Timestamp,
        strat_signals: Dict[str, pd.Series],
        strategies_by_name: Dict[str, Any],
    ) -> None:
        today = []
        for name in strategies_by_name.keys():
            series = strat_signals.get(name)
            if series is None or date not in series.index:
                continue
            if bool(series.loc[date]):
                today.append(name)
        if today:
            self._record_signal_hits(ticker, date, today)

    def _record_today_match_signals(self, ticker: str, date: pd.Timestamp, matches: List[str]) -> None:
        if matches:
            self._record_signal_hits(ticker, date, matches)

    def _record_signal_hits(self, ticker: str, date: pd.Timestamp, names: List[str]) -> None:
        tracker = self.recent_signals.setdefault(ticker, {})
        for name in names:
            tracker[name] = date

    def _confirmation_gate(self, strategy: Any, ticker: str, date: pd.Timestamp) -> bool:
        ok, _ = self._confirmation_evaluation(strategy, ticker, date)
        return ok

    def _confirmation_evaluation(
        self,
        strategy: Any,
        ticker: str,
        date: pd.Timestamp,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        any_list = self._normalize_strategy_list(getattr(strategy, "confirmation_any", None))
        all_list = self._normalize_strategy_list(getattr(strategy, "confirmation_all", None))
        score_cfg = getattr(strategy, "confirmation_score", None)

        if not any_list and not all_list and not score_cfg:
            return True, None

        def fired_within(name: str) -> bool:
            last = self.recent_signals.get(ticker, {}).get(name)
            if last is None:
                return False
            confirmer = self.strategy_map.get(name)
            lookback = getattr(confirmer, "confirmation_lookback_days", None) if confirmer is not None else None
            if lookback is None or lookback < 0:
                return False
            return (date - last).days <= lookback

        any_hits = [name for name in any_list if fired_within(name)]
        all_hits = [name for name in all_list if fired_within(name)]
        any_ok = True if not any_list else bool(any_hits)
        all_ok = True if not all_list else (len(all_hits) == len(all_list))

        score_ok = True
        score_hits = []
        score_total = 0.0
        threshold = None

        if score_cfg:
            enabled = True
            threshold = getattr(strategy, "confirmation_score_threshold", 1.0)
            weights = {}
            score_strategies = []
            if isinstance(score_cfg, dict):
                enabled = bool(score_cfg.get("enabled", True))
                threshold = float(score_cfg.get("threshold", threshold))
                weights = score_cfg.get("weights", {}) or {}
                score_strategies = self._normalize_strategy_list(score_cfg.get("strategies"))
            elif isinstance(score_cfg, bool):
                enabled = score_cfg
            else:
                enabled = bool(score_cfg)

            if enabled:
                if not score_strategies:
                    score_strategies = list({*any_list, *all_list})
                if not score_strategies and weights:
                    score_strategies = list(weights.keys())
                if score_strategies:
                    for name in score_strategies:
                        if not fired_within(name):
                            continue
                        score_hits.append(name)
                        weight = None
                        if isinstance(weights, dict):
                            weight = weights.get(name)
                        if weight is None:
                            confirmer = self.strategy_map.get(name)
                            weight = getattr(confirmer, "confirmation_weight", 1.0) if confirmer is not None else 1.0
                        score_total += float(weight)
                    if threshold is not None and score_total < threshold:
                        score_ok = False

        overall_ok = any_ok and all_ok and score_ok
        details = {
            "confirmation_hits": sorted({*any_hits, *all_hits, *score_hits}),
            "confirmation_any_hits": any_hits,
            "confirmation_all_hits": all_hits,
            "confirmation_score_hits": score_hits,
            "confirmation_score_total": score_total,
            "confirmation_score_threshold": threshold,
        }
        return overall_ok, details

    def _confirmation_details_for_trade(
        self,
        strategy_name: Optional[str],
        ticker: str,
        date: pd.Timestamp,
    ) -> Dict[str, Any]:
        details = None
        if strategy_name:
            strategy_obj = self.strategy_map.get(strategy_name)
            if strategy_obj is not None:
                _, details = self._confirmation_evaluation(strategy_obj, ticker, date)
        if details is None:
            details = {
                "confirmation_hits": [],
                "confirmation_any_hits": [],
                "confirmation_all_hits": [],
                "confirmation_score_hits": [],
                "confirmation_score_total": None,
                "confirmation_score_threshold": None,
            }
        return details

    def _has_consecutive_hits(self, series: pd.Series, required: int) -> bool:
        if series is None or series.empty or required <= 0:
            return False
        run = 0
        for value in series.fillna(False).astype(bool).tolist():
            if value:
                run += 1
                if run >= required:
                    return True
            else:
                run = 0
        return False

    def _update_trend_confirm(
        self,
        holding: Dict[str, Any],
        row: pd.Series,
        df: pd.DataFrame,
        strategy: Optional[Any],
        date: pd.Timestamp,
    ) -> None:
        if strategy is None or not getattr(strategy, "trend_confirm_enabled", False):
            return
        if holding.get("trend_confirmed"):
            return
        side = holding.get("side", 1)
        mode = (getattr(strategy, "trend_confirm_mode", "either") or "either").lower()
        consecutive = max(1, int(getattr(strategy, "trend_confirm_consecutive_closes", 2)))
        lookback = max(consecutive, int(getattr(strategy, "trend_confirm_lookback_days", 5)))
        if lookback <= 0:
            return

        try:
            hist = df.loc[:date].tail(lookback)
        except Exception:
            return
        if hist.empty:
            return

        close = hist.get("Close")
        ema50 = hist.get("EMA_50")
        sr_high = hist.get("SR_NEAR_HIGH_40")
        sr_low = hist.get("SR_NEAR_LOW_40")
        if close is None:
            return

        if side == 1:
            cond_ema = close > ema50 if ema50 is not None else pd.Series(False, index=hist.index)
            cond_sr = close > sr_high if sr_high is not None else pd.Series(False, index=hist.index)
        else:
            cond_ema = close < ema50 if ema50 is not None else pd.Series(False, index=hist.index)
            cond_sr = close < sr_low if sr_low is not None else pd.Series(False, index=hist.index)

        if mode == "ema50":
            cond = cond_ema
        elif mode == "sr_near_high_40":
            cond = cond_sr
        elif mode == "both":
            cond = cond_ema & cond_sr
        else:
            cond = cond_ema | cond_sr

        if self._has_consecutive_hits(cond, consecutive):
            holding["trend_confirmed"] = True
            holding["trend_confirm_date"] = date
            try:
                holding["trend_confirm_index"] = df.index.get_loc(date)
            except Exception:
                holding["trend_confirm_index"] = None

    def _execute_add_on(
        self,
        position_key: Tuple[str, str],
        date: pd.Timestamp,
        price: float,
        amount_usd: float,
        data_source: Dict[str, pd.DataFrame],
    ) -> bool:
        holding = self.holdings.get(position_key)
        if holding is None:
            return False
        ticker, strategy_name = position_key

        trade_count = len(self.trades)
        equity_before = self._current_equity(date, data_source)

        raw_price = price
        side = holding.get("side", 1)
        exec_price = self._execution_price(raw_price, side, is_entry=True)
        multiplier = holding.get("multiplier", 1.0)
        qty = amount_usd / (exec_price * multiplier)
        if is_future_symbol(ticker):
            qty = int(qty)
            if qty <= 0:
                return False
            amount_usd = qty * exec_price * multiplier
        else:
            qty = self._round_equity_qty(qty)
            if qty < self.min_equity_shares:
                return False
            amount_usd = qty * exec_price * multiplier
        trade_notional = qty * exec_price * multiplier
        trade_margin_pct = self._resolve_futures_margin_pct() if is_future_symbol(ticker) else 1.0
        if trade_margin_pct is None:
            trade_margin_pct = 1.0
        if (
            self.skip_trade_if_notional_lt_min
            and self.min_trade_notional > 0
            and trade_notional < self.min_trade_notional
        ):
            return False

        commission = self._trade_commission(trade_notional)
        slippage_cost = abs(exec_price - raw_price) * qty * multiplier
        if side == 1 and self.cash < (trade_notional + commission):
            return False
        if side == 1:
            self.cash -= (trade_notional + commission)
            action = "BUY_ADD"
        else:
            self.cash += (trade_notional - commission)
            action = "SELL_SHORT_ADD"

        old_qty = holding.get("quantity", 0.0)
        if old_qty <= 0:
            return False
        new_qty = old_qty + qty
        if new_qty <= 0:
            return False

        old_entry_price = holding.get("entry_price", exec_price)
        holding["entry_price"] = ((old_qty * old_entry_price) + (qty * exec_price)) / new_qty
        holding["quantity"] = new_qty

        entry_notional_total = holding.get("entry_notional")
        if entry_notional_total is None:
            entry_notional_total = old_qty * old_entry_price * multiplier
        holding["entry_notional"] = entry_notional_total + trade_notional
        holding["entry_commission"] = holding.get("entry_commission", 0.0) + commission
        holding["entry_slippage_cost"] = holding.get("entry_slippage_cost", 0.0) + slippage_cost
        holding["add_count"] = int(holding.get("add_count", 0)) + 1
        holding["last_add_date"] = date

        confirmation_hits = holding.get("confirmation_hits")
        confirmation_any_hits = holding.get("confirmation_any_hits")
        confirmation_all_hits = holding.get("confirmation_all_hits")
        confirmation_score_hits = holding.get("confirmation_score_hits")
        confirmation_score_total = holding.get("confirmation_score_total")
        confirmation_score_threshold = holding.get("confirmation_score_threshold")
        book = holding.get("book")
        position_id = holding.get("position_id")
        entry_id = holding.get("entry_id")
        allocator_state = self._allocator_state_label(book)
        book_risk_pct = holding.get("entry_book_risk_budget_pct")
        book_risk_dollars = holding.get("entry_book_risk_budget_dollars")

        self.trades.append({
            "date": date,
            "symbol": ticker,
            "action": action,
            "price": exec_price,
            "qty": qty,
            "strategy": strategy_name,
            "side": side,
            "asset_type": holding.get("asset_type"),
            "multiplier": multiplier,
            "stop_loss": holding.get("stop_loss"),
            "take_profit": holding.get("take_profit"),
            "entry_metrics": holding.get("entry_metrics"),
            "position_side": "LONG" if side == 1 else "SHORT",
            "initial_stop": holding.get("initial_stop"),
            "initial_risk": holding.get("initial_risk"),
            "raw_price": raw_price,
            "exec_price": exec_price,
            "commission": commission,
            "slippage_cost": slippage_cost,
            "trade_notional": trade_notional,
            "margin_pct": trade_margin_pct,
            "reason": "Add-On",
            "add_on": True,
            "add_count": holding.get("add_count"),
            "book": book,
            "position_id": position_id,
            "entry_id": entry_id,
            "add_number": holding.get("add_count"),
            "allocator_state": allocator_state,
            "book_risk_budget_pct": book_risk_pct,
            "book_risk_budget_dollars": book_risk_dollars,
            "confirmation_hits": confirmation_hits,
            "confirmation_any_hits": confirmation_any_hits,
            "confirmation_all_hits": confirmation_all_hits,
            "confirmation_score_hits": confirmation_score_hits,
            "confirmation_score_total": confirmation_score_total,
            "confirmation_score_threshold": confirmation_score_threshold,
        })

        if len(self.trades) == trade_count:
            return False

        equity_after = self._current_equity(date, data_source)
        position_notional = abs(new_qty * exec_price * multiplier)
        self._annotate_last_trade(equity_before, equity_after, self.cash, position_notional)
        return True

    def _maybe_add_on(
        self,
        holding: Dict[str, Any],
        ticker: str,
        date: pd.Timestamp,
        price: float,
        data_source: Dict[str, pd.DataFrame],
        regime_state: RegimeState,
        strategy: Optional[Any],
    ) -> None:
        if strategy is None or not getattr(strategy, "addons_enabled", False):
            return
        position_key = self._position_key(ticker, holding.get("strategy"))
        if position_key not in self.holdings:
            return

        if self.portfolio_throttle_active and self.portfolio_throttle_disable_adds:
            return

        book = holding.get("book")
        if self.book_allocator_enabled and book and book in self.book_state:
            if self.book_state[book].get("disabled"):
                return

        if getattr(strategy, "add_requires_confirmations", False):
            if not self._confirmation_gate(strategy, ticker, date):
                return

        last_reduction = holding.get("last_reduction_date")
        if last_reduction is not None and last_reduction == date:
            return

        min_bars_between = int(getattr(strategy, "add_min_bars_between_adds", 0))
        last_add_date = holding.get("last_add_date")
        if min_bars_between > 0 and last_add_date is not None:
            try:
                idx = data_source[ticker].index
                current_idx = idx.get_loc(date)
                last_add_idx = idx.get_loc(last_add_date)
            except Exception:
                return
            if (current_idx - last_add_idx) < min_bars_between:
                return

        max_adds = int(getattr(strategy, "max_adds", 0))
        add_count = int(holding.get("add_count", 0))
        if max_adds <= 0 or add_count >= max_adds:
            return

        if getattr(strategy, "add_requires_trend_confirm", True) and not holding.get("trend_confirmed"):
            return
        min_confirm_bars = int(getattr(strategy, "add_requires_trend_confirm_bars", 0))
        if min_confirm_bars > 0:
            confirm_idx = holding.get("trend_confirm_index")
            if confirm_idx is None:
                return
            try:
                current_idx = data_source[ticker].index.get_loc(date)
            except Exception:
                return
            if (current_idx - confirm_idx) < min_confirm_bars:
                return

        entry_date = holding.get("entry_date")
        if entry_date is None:
            return
        days_since_entry = (date - entry_date).days
        min_days = int(getattr(strategy, "add_min_days_since_entry", 3))
        if days_since_entry < min_days:
            return

        peak_r = holding.get("peak_r", 0.0) or 0.0
        min_peak_r = float(getattr(strategy, "add_min_peak_r", 1.0))
        min_peak_r_second = getattr(strategy, "add_min_peak_r_second", None)
        if add_count >= 1 and min_peak_r_second is not None:
            min_peak_r = float(min_peak_r_second)
        if peak_r < min_peak_r:
            return

        ema_len = getattr(strategy, "add_requires_ema_length", None)
        if ema_len:
            try:
                ema_val = data_source[ticker].loc[date].get(f"EMA_{int(ema_len)}")
            except Exception:
                ema_val = None
            if ema_val is None or pd.isna(ema_val):
                return
            side = holding.get("side", 1)
            if side == 1 and price <= ema_val:
                return
            if side == -1 and price >= ema_val:
                return

        if getattr(strategy, "add_only_if_above_trail", True):
            stop_loss = holding.get("stop_loss")
            side = holding.get("side", 1)
            if stop_loss is None:
                return
            if side == 1 and price <= stop_loss:
                return
            if side == -1 and price >= stop_loss:
                return

        initial_notional = holding.get("initial_notional")
        if initial_notional is None or initial_notional <= 0:
            return
        add_pct = float(getattr(strategy, "add_size_pct_initial", 0.0))
        if add_pct <= 0:
            return
        amount_usd = initial_notional * add_pct

        side = holding.get("side", 1)
        if side == 1:
            max_alloc = self.initial_capital * self.max_alloc_pct
            market_val = self._current_market_value(date, data_source, asset_type="equity", side=1)
            ticker_val = self._current_ticker_value(date, data_source, ticker, asset_type="equity", side=1)
            remaining = min(max_alloc - market_val, max_alloc - ticker_val)
            if remaining <= 0:
                return
            amount_usd = min(amount_usd, remaining)
            if self.skip_trade_if_notional_lt_min and self.min_trade_notional > 0 and remaining < self.min_trade_notional:
                return

        allow_risk_off_entries = self._resolve_strategy_override(
            strategy,
            "allow_risk_off_entries",
            self.allow_risk_off_entries,
        )
        risk_off_mult = self._resolve_strategy_override(
            strategy,
            "risk_off_size_mult",
            self.risk_off_size_mult,
        )
        if side == 1 and not regime_state.risk_on:
            if not allow_risk_off_entries:
                return
            amount_usd *= risk_off_mult

        vol_scale = portfolio_vol_scale(
            self.equity_curve,
            self.portfolio_vol_lookback,
            self.portfolio_vol_target,
        )
        amount_usd *= vol_scale
        amount_usd = min(amount_usd, self.cash)
        if amount_usd <= 0:
            return

        if self.book_allocator_enabled and book and book in self.book_state:
            equity = self._current_equity(date, data_source)
            remaining_book_risk = self._book_budget_dollars(book, equity) - self._book_open_risk(book, date, data_source)
            if remaining_book_risk <= 0:
                return
            per_trade_cap = self._book_per_trade_cap(book, equity)
            stop_loss = holding.get("stop_loss")
            multiplier = holding.get("multiplier", 1.0)
            if stop_loss is not None:
                if side == 1:
                    risk_per_unit = max(price - stop_loss, 0.0)
                else:
                    risk_per_unit = max(stop_loss - price, 0.0)
            else:
                risk_per_unit = holding.get("initial_risk") or 0.0
            if risk_per_unit <= 0:
                return
            add_qty = amount_usd / (price * multiplier)
            if is_future_symbol(ticker):
                add_qty = int(add_qty)
            else:
                add_qty = self._round_equity_qty(add_qty)
            if add_qty <= 0:
                return
            add_risk = risk_per_unit * add_qty * multiplier
            cap = remaining_book_risk
            if per_trade_cap > 0:
                cap = min(cap, per_trade_cap)
            if add_risk > cap and risk_per_unit > 0:
                add_qty = cap / (risk_per_unit * multiplier)
                if is_future_symbol(ticker):
                    add_qty = int(add_qty)
                else:
                    add_qty = self._round_equity_qty(add_qty)
                if add_qty <= 0:
                    return
                amount_usd = add_qty * price * multiplier
                add_risk = risk_per_unit * add_qty * multiplier

            if book == self.core_book_name:
                original_qty = holding.get("initial_quantity", 0.0)
                initial_risk = holding.get("initial_risk") or 0.0
                original_risk = initial_risk * original_qty * multiplier
                new_qty = holding.get("quantity", 0.0) + add_qty
                new_risk = risk_per_unit * new_qty * multiplier
                if new_risk > original_risk:
                    return
            if book == self.convex_book_name:
                entry_price = holding.get("entry_price")
                if entry_price is None:
                    return
                qty = holding.get("quantity", 0.0)
                open_profit = (price - entry_price) * qty * multiplier if side == 1 else (entry_price - price) * qty * multiplier
                if open_profit < add_risk:
                    return

        self._execute_add_on(position_key, date, price, amount_usd, data_source)

    def _resolve_take_profit(self, strategy: Optional[Any], hist: pd.DataFrame, entry_price: float) -> Optional[float]:
        if strategy is None:
            return None
        if not bool(getattr(strategy, "use_take_profit", True)):
            if getattr(strategy, "partial_take_profit_mode", "r_multiple") != "take_profit":
                return None
        return strategy.get_take_profit(hist, entry_price=entry_price)

    def _resolve_take_profit_levels(
        self,
        strategy: Optional[Any],
        hist: pd.DataFrame,
        entry_price: float,
    ) -> Tuple[Optional[List[float]], Optional[List[float]]]:
        if strategy is None:
            return None, None
        get_levels = getattr(strategy, "get_take_profit_levels", None)
        if not callable(get_levels):
            return None, None
        try:
            levels = get_levels(hist, entry_price=entry_price)
        except Exception:
            return None, None
        if not levels:
            return None, None
        cleaned: List[float] = []
        seen = set()
        for level in levels:
            try:
                value = float(level)
            except (TypeError, ValueError):
                continue
            if pd.isna(value):
                continue
            if value in seen:
                continue
            seen.add(value)
            cleaned.append(value)
        if not cleaned:
            return None, None
        direction = (getattr(strategy, "direction", "long") or "long").lower()
        if entry_price is not None:
            if direction == "short":
                cleaned = [lvl for lvl in cleaned if lvl < entry_price]
            else:
                cleaned = [lvl for lvl in cleaned if lvl > entry_price]
        if not cleaned:
            return None, None
        cleaned.sort(reverse=(direction == "short"))
        pcts = None
        get_pcts = getattr(strategy, "get_take_profit_level_pcts", None)
        if callable(get_pcts):
            try:
                pcts = get_pcts(cleaned)
            except Exception:
                pcts = None
        if pcts and len(pcts) != len(cleaned):
            pcts = None
        return cleaned, pcts

    def _execution_price(self, price: float, side: int, is_entry: bool) -> float:
        if not self.slippage_bps:
            return price
        slip = self.slippage_bps / 10000.0
        if side == 1:
            return price * (1 + slip) if is_entry else price * (1 - slip)
        return price * (1 - slip) if is_entry else price * (1 + slip)

    def _stop_fill_price(self, row: pd.Series, stop_level: Optional[float], side: int) -> Tuple[Optional[float], Optional[str]]:
        if stop_level is None:
            return None, None
        try:
            open_px = float(row["Open"])
            high_px = float(row["High"])
            low_px = float(row["Low"])
            close_px = float(row["Close"])
        except Exception:
            close_px = float(row["Close"]) if "Close" in row else None
            return (close_px, "close_based") if close_px is not None else (None, None)
        if np.isnan(open_px) or np.isnan(high_px) or np.isnan(low_px):
            return (close_px, "close_based") if not np.isnan(close_px) else (None, None)
        if side == 1:
            if open_px <= stop_level:
                return open_px, "gap_open"
            if low_px <= stop_level:
                return stop_level, "intraday_stop"
        else:
            if open_px >= stop_level:
                return open_px, "gap_open"
            if high_px >= stop_level:
                return stop_level, "intraday_stop"
        return None, None

    def _round_equity_qty(self, qty: float) -> float:
        mode = (self.equity_qty_rounding or "").lower()
        if mode in ("floor_int", "floor"):
            return float(math.floor(qty))
        if mode in ("round_int", "round"):
            return float(round(qty))
        if mode in ("ceil_int", "ceil"):
            return float(math.ceil(qty))
        return qty

    def _trade_commission(self, notional: float) -> float:
        if not notional:
            return 0.0
        return float(self.commission_per_trade) + (notional * float(self.commission_pct))

    def _in_loss_cooldown(self, ticker: str, strategy: Optional[Any], date: pd.Timestamp) -> bool:
        if strategy is None:
            return False
        cooldown = getattr(strategy, "loss_cooldown_days", None)
        if cooldown is None or cooldown <= 0:
            return False
        if hasattr(strategy, "get_name"):
            strategy_key = strategy.get_name()
        else:
            strategy_key = str(strategy)
        key = (ticker, strategy_key)
        last_loss = self.last_loss_exit_dates.get(key)
        if last_loss is None:
            return False
        return (date - last_loss).days < cooldown

    def _in_stop_cooldown(self, ticker: str, strategy: Optional[Any], date: pd.Timestamp) -> bool:
        if strategy is None:
            return False
        cooldown = getattr(strategy, "stop_cooldown_days", None)
        if cooldown is None or cooldown <= 0:
            return False
        if hasattr(strategy, "get_name"):
            strategy_key = strategy.get_name()
        else:
            strategy_key = str(strategy)
        key = (ticker, strategy_key)
        last_stop = self.last_stop_exit_dates.get(key)
        if last_stop is None:
            return False
        return (date - last_stop).days < cooldown

    def _should_decay_exit(
        self,
        holding: Dict[str, Any],
        date: pd.Timestamp,
        strategy: Optional[Any],
    ) -> bool:
        if strategy is None or not getattr(strategy, "decay_exit_enabled", False):
            return False
        entry_date = holding.get("entry_date")
        if entry_date is None:
            return False
        decay_days = getattr(strategy, "decay_exit_days", 0)
        if decay_days <= 0:
            return False
        if (date - entry_date).days < decay_days:
            return False
        mfe_r = holding.get("mfe_r")
        if mfe_r is None:
            return False
        threshold = getattr(strategy, "decay_exit_mfe_r", 0.5)
        return mfe_r < threshold

    def _should_early_failure_exit(
        self,
        holding: Dict[str, Any],
        row: pd.Series,
        df: pd.DataFrame,
        strategy: Optional[Any],
        date: pd.Timestamp,
    ) -> bool:
        if strategy is None or not getattr(strategy, "early_failure_exit_enabled", False):
            return False
        entry_date = holding.get("entry_date")
        if entry_date is None:
            return False
        bars_limit = int(getattr(strategy, "early_failure_bars", 0) or 0)
        if bars_limit <= 0:
            return False
        if df is None or df.empty:
            return False
        entry_idx = df.index.get_indexer([entry_date], method="pad")
        current_idx = df.index.get_indexer([date], method="pad")
        if entry_idx.size == 0 or current_idx.size == 0:
            return False
        if entry_idx[0] < 0 or current_idx[0] < 0:
            return False
        bars_since_entry = current_idx[0] - entry_idx[0]
        if bars_since_entry < 0 or bars_since_entry > bars_limit:
            return False
        close = float(row["Close"])
        if bool(getattr(strategy, "early_failure_requires_close_below_entry", False)):
            entry_price = holding.get("entry_price")
            if entry_price is not None and close < float(entry_price):
                return True
        peak_r = holding.get("peak_r")
        if peak_r is None:
            return False
        threshold = float(getattr(strategy, "early_failure_peak_r", 0.0) or 0.0)
        if peak_r >= threshold:
            return False
        check_below_ema = bool(getattr(strategy, "early_failure_below_ema", False))
        check_below_prior_low = bool(getattr(strategy, "early_failure_below_prior_low", False))
        if not (check_below_ema or check_below_prior_low):
            return False
        triggered = False
        if check_below_ema:
            ema_len = int(getattr(strategy, "early_failure_ema_length", 20) or 20)
            ema = strategy.ema_series(df, length=ema_len)
            if ema is not None and date in ema.index:
                ema_val = ema.loc[date]
                if pd.notna(ema_val) and close < float(ema_val):
                    triggered = True
        if not triggered and check_below_prior_low:
            prev_idx = current_idx[0] - 1
            if prev_idx >= 0:
                prev_low = df["Low"].iloc[prev_idx]
                if pd.notna(prev_low) and close < float(prev_low):
                    triggered = True
        return triggered

    def _should_momentum_fail_exit(
        self,
        holding: Dict[str, Any],
        df: pd.DataFrame,
        strategy: Optional[Any],
        date: pd.Timestamp,
    ) -> bool:
        if strategy is None or not getattr(strategy, "momentum_fail_exit_enabled", False):
            return False
        entry_date = holding.get("entry_date")
        if entry_date is None:
            return False
        bars_limit = int(getattr(strategy, "momentum_fail_confirm_bars", 0) or 0)
        if bars_limit <= 0:
            return False
        if df is None or df.empty:
            return False
        entry_idx = df.index.get_indexer([entry_date], method="pad")
        current_idx = df.index.get_indexer([date], method="pad")
        if entry_idx.size == 0 or current_idx.size == 0:
            return False
        if entry_idx[0] < 0 or current_idx[0] < 0:
            return False
        bars_since_entry = current_idx[0] - entry_idx[0]
        if bars_since_entry < bars_limit:
            return False
        entry_rsi = holding.get("entry_rsi")
        if entry_rsi is None:
            entry_metrics = holding.get("entry_metrics") or {}
            entry_rsi = entry_metrics.get("rsi")
        if entry_rsi is None:
            return False
        entry_threshold = float(getattr(strategy, "momentum_fail_entry_rsi", 30.0) or 30.0)
        if float(entry_rsi) >= entry_threshold:
            return False
        rsi_len = int(getattr(strategy, "momentum_fail_rsi_length", getattr(strategy, "rsi_length", 14)) or 14)
        rsi_series = strategy.rsi_series(df, length=rsi_len)
        if rsi_series is None or date not in rsi_series.index:
            return False
        current_rsi = rsi_series.loc[date]
        if pd.isna(current_rsi):
            return False
        confirm_threshold = float(getattr(strategy, "momentum_fail_confirm_rsi", 35.0) or 35.0)
        return float(current_rsi) < confirm_threshold

    def _atr_contraction_allows_tighten(
        self,
        holding: Dict[str, Any],
        df: pd.DataFrame,
        strategy: Optional[Any],
        date: pd.Timestamp,
    ) -> bool:
        if strategy is None or not getattr(strategy, "atr_contraction_tighten_enabled", False):
            return True
        entry_atr = holding.get("entry_atr")
        if entry_atr is None or entry_atr <= 0:
            return True
        if df is None or df.empty:
            return True
        atr_len = int(getattr(strategy, "atr_contraction_lookback", 20) or 20)
        atr_series = strategy.atr_series(df, period=atr_len)
        if atr_series is None or date not in atr_series.index:
            return True
        current_atr = atr_series.loc[date]
        if pd.isna(current_atr) or current_atr <= 0:
            return True
        threshold = float(getattr(strategy, "atr_contraction_tighten_threshold", 0.8) or 0.8)
        ratio = float(current_atr) / float(entry_atr)
        return ratio < threshold

    def _should_follow_through_exit(
        self,
        holding: Dict[str, Any],
        row: pd.Series,
        df: pd.DataFrame,
        strategy: Optional[Any],
        date: pd.Timestamp,
    ) -> bool:
        if strategy is None or not getattr(strategy, "follow_through_exit_enabled", False):
            return False
        entry_date = holding.get("entry_date")
        if entry_date is None:
            return False
        bars_limit = int(getattr(strategy, "follow_through_bars", 0) or 0)
        if bars_limit <= 0:
            return False
        if df is None or df.empty:
            return False
        entry_idx = df.index.get_indexer([entry_date], method="pad")
        current_idx = df.index.get_indexer([date], method="pad")
        if entry_idx.size == 0 or current_idx.size == 0:
            return False
        if entry_idx[0] < 0 or current_idx[0] < 0:
            return False
        bars_since_entry = current_idx[0] - entry_idx[0]
        if bars_since_entry < bars_limit:
            return False
        side = holding.get("side", 1)
        if side == 1:
            entry_high = df["High"].iloc[entry_idx[0]]
            window_high = df["High"].iloc[entry_idx[0] : current_idx[0] + 1].max()
            if pd.isna(entry_high) or pd.isna(window_high):
                return False
            return window_high <= entry_high
        entry_low = df["Low"].iloc[entry_idx[0]]
        window_low = df["Low"].iloc[entry_idx[0] : current_idx[0] + 1].min()
        if pd.isna(entry_low) or pd.isna(window_low):
            return False
        return window_low >= entry_low

    def _apply_staged_take_profit(
        self,
        ticker: str,
        holding: Dict[str, Any],
        price: float,
        date: pd.Timestamp,
        strategy: Optional[Any],
        data_source: Dict[str, pd.DataFrame],
    ) -> bool:
        levels = holding.get("take_profit_levels") or []
        if not levels:
            return False
        idx = holding.get("take_profit_level_index") or 0
        if idx >= len(levels):
            return False
        side = holding.get("side", 1)
        position_key = self._position_key(ticker, holding.get("strategy"))
        base_qty = holding.get("initial_quantity", holding.get("quantity", 0))
        pcts = holding.get("take_profit_level_pcts")
        mode = (holding.get("take_profit_level_pct_mode") or "initial").lower()
        executed_any = False

        while idx < len(levels):
            target = levels[idx]
            if side == 1 and price < target:
                break
            if side == -1 and price > target:
                break
            holding_qty = holding.get("quantity", 0)
            if holding_qty <= 0:
                return executed_any
            is_last = idx == len(levels) - 1
            if is_last:
                qty_to_sell = holding_qty
            else:
                if pcts and idx < len(pcts):
                    if mode == "remaining":
                        qty_to_sell = holding_qty * pcts[idx]
                    else:
                        qty_to_sell = base_qty * pcts[idx]
                else:
                    qty_to_sell = base_qty / max(len(levels), 1)
                if qty_to_sell > holding_qty:
                    qty_to_sell = holding_qty
            executed = self._execute_sell(
                position_key,
                date,
                price,
                "Take Profit",
                data_source,
                qty=qty_to_sell,
                partial=not is_last,
            )
            if not executed:
                break
            executed_any = True
            holding = self.holdings.get(position_key)
            if not holding:
                return True
            holding["take_profit_level_index"] = idx + 1
            if not holding.get("mmt_hit"):
                holding["mmt_hit"] = True
            idx += 1
        return executed_any

    def _apply_partial_take_profit(
        self,
        ticker: str,
        holding: Dict[str, Any],
        price: float,
        date: pd.Timestamp,
        strategy: Optional[Any],
        data_source: Dict[str, pd.DataFrame],
    ) -> bool:
        if strategy is None or not getattr(strategy, "partial_take_profit_enabled", False):
            return False
        if holding.get("partial_taken"):
            return False
        mode = getattr(strategy, "partial_take_profit_mode", "r_multiple")
        initial_risk = holding.get("initial_risk")
        if initial_risk is None or initial_risk <= 0:
            return False
        entry_price = holding.get("entry_price")
        if entry_price is None:
            return False
        side = holding.get("side", 1)
        r_target = getattr(strategy, "partial_take_profit_r", 1.0)
        pct = getattr(strategy, "partial_take_profit_pct", 0.5)
        if pct <= 0:
            return False
        if mode == "take_profit":
            target = holding.get("take_profit")
            if target is None:
                return False
            if side == 1 and price < target:
                return False
            if side == -1 and price > target:
                return False
            holding["mmt_hit"] = True
        else:
            if side == 1:
                if price < entry_price + (initial_risk * r_target):
                    return False
            else:
                if price > entry_price - (initial_risk * r_target):
                    return False
        qty = holding.get("quantity", 0)
        if qty <= 0:
            return False
        qty_to_sell = qty * pct
        if is_future_symbol(ticker):
            qty_to_sell = int(qty_to_sell)
        if qty_to_sell <= 0:
            return False
        if qty_to_sell >= qty:
            qty_to_sell = qty
        position_key = self._position_key(ticker, holding.get("strategy"))
        executed = self._execute_sell(
            position_key,
            date,
            price,
            "Partial Take Profit",
            data_source,
            qty=qty_to_sell,
            partial=True,
        )
        if executed:
            holding["partial_taken"] = True
        return executed

    def _time_stop_reached(self, position_key: Tuple[str, str], date: pd.Timestamp) -> bool:
        holding = self.holdings.get(position_key)
        if not holding:
            return False
        strategy_name = holding.get("strategy") or position_key[1]
        if not strategy_name:
            return False
        strategy = self.strategy_map.get(strategy_name)
        if not strategy:
            return False
        if not bool(getattr(strategy, "use_time_stop", True)):
            return False
        time_stop = getattr(strategy, "time_stop_days", None)
        if time_stop is None or time_stop == float("inf"):
            return False
        entry_date = holding.get("entry_date")
        if entry_date is None:
            return False
        return (date - entry_date).days >= time_stop
