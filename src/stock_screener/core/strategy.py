"""
Base class for screening strategies.
"""
from abc import ABC, abstractmethod
import pandas as pd
import pandas_ta as ta
from typing import Tuple, Dict, Any, Optional, List

class BaseStrategy(ABC):
    """
    Abstract base class for all screening strategies.
    Changes:
    - Added get_name() method
    - Added get_description() method
    """

    sentiment: Optional[str] = None
    direction: str = "long"
    stop_multiplier: float = 2.0
    take_profit_multiplier: Optional[float] = None
    take_profit_level_pct_mode: str = "initial"
    time_stop_days: Optional[float] = None

    use_stop_loss: bool = True
    use_fallback_stop: bool = True
    use_take_profit: bool = True
    use_exit_signal: bool = True
    use_time_stop: bool = True
    use_regime_exit: bool = True
    use_breakeven: bool = True

    allow_risk_off_entries: Optional[bool] = None
    allow_short_risk_on: Optional[bool] = None
    risk_off_size_mult: Optional[float] = None

    strategy_risk_mult: float = 1.0
    strategy_trade_size: Optional[float] = None
    strategy_max_position_pct: Optional[float] = None

    require_regime_stability: bool = False
    regime_stability_days: Optional[int] = None
    avoid_regime_flip: bool = False
    regime_flip_band_pct: Optional[float] = None
    regime_flip_slope_pct: Optional[float] = None

    allow_regime_bypass_large_gap: bool = False

    breakeven_r: Optional[float] = None
    breakeven_delay_bars: Optional[int] = None
    breakeven_trigger_r: Optional[float] = None

    giveback_enabled: bool = False
    giveback_trigger_r: float = 2.0
    giveback_floor_r: float = 1.0
    giveback_min_days: int = 0

    partial_take_profit_enabled: bool = False
    partial_take_profit_r: float = 1.0
    partial_take_profit_pct: float = 0.5
    partial_take_profit_mode: str = "r_multiple"

    decay_exit_enabled: bool = False
    decay_exit_days: int = 0
    decay_exit_mfe_r: float = 0.5

    early_failure_exit_enabled: bool = False
    early_failure_bars: int = 0
    early_failure_peak_r: float = 0.0
    early_failure_ema_length: int = 20
    early_failure_below_ema: bool = False
    early_failure_below_prior_low: bool = False
    early_failure_requires_close_below_entry: bool = False

    follow_through_exit_enabled: bool = False
    follow_through_bars: int = 0

    atr_contraction_tighten_enabled: bool = False
    atr_contraction_tighten_threshold: float = 0.8
    atr_contraction_lookback: int = 20

    momentum_fail_exit_enabled: bool = False
    momentum_fail_entry_rsi: float = 30.0
    momentum_fail_confirm_rsi: float = 35.0
    momentum_fail_confirm_bars: int = 3
    momentum_fail_rsi_length: int = 14

    loss_cooldown_days: Optional[int] = None
    stop_cooldown_days: Optional[int] = None

    book: Optional[str] = None

    trailing_enabled: bool = False
    trailing_start_r: float = 1.25
    trailing_use_mmt: bool = False
    trailing_use_avwap: bool = True
    trailing_use_ema20: bool = True
    trailing_method: str = "max"
    trailing_ema_length: int = 20
    trailing_atr_mult: float = 0.0

    trend_confirm_enabled: bool = False
    trend_confirm_mode: str = "either"
    trend_confirm_consecutive_closes: int = 2
    trend_confirm_lookback_days: int = 5
    trend_trailing_method: str = "ema"
    trend_trailing_ema_length: int = 50
    trend_trailing_atr_mult: float = 0.0

    exhaustion_enabled: bool = False
    exhaustion_min_signals: int = 2
    exhaustion_rsi_level: float = 70.0
    exhaustion_rsi_rollover_bars: int = 2
    exhaustion_candle_close_loc: float = 0.4
    exhaustion_resistance_lookback: int = 40
    exhaustion_resistance_atr_mult: float = 0.4
    exhaustion_volume_ratio: float = 0.9
    exhaustion_stall_atr_mult: float = 0.25
    exhaustion_require_near_or_mmt: bool = True
    exhaustion_disable_after_trend_confirm: bool = False

    addons_enabled: bool = False
    max_adds: int = 0
    add_size_pct_initial: float = 0.5
    add_min_peak_r: float = 1.0
    add_min_peak_r_second: Optional[float] = None
    add_min_days_since_entry: int = 3
    add_only_if_above_trail: bool = True
    add_requires_trend_confirm: bool = True
    add_requires_trend_confirm_bars: int = 0
    add_min_bars_between_adds: int = 0
    add_requires_ema_length: Optional[int] = None
    add_requires_confirmations: bool = False

    entry_enabled: bool = True
    confirmation_any: Optional[list] = None
    confirmation_all: Optional[list] = None
    confirmation_score: Optional[object] = None
    confirmation_lookback_days: Optional[int] = 5
    confirmation_weight: float = 1.0
    confirmation_score_threshold: float = 1.0

    market_symbols: Optional[List[str]] = None
    
    @abstractmethod
    def check(self, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        """
        Check if the given dataframe matches the strategy logic.
        
        Args:
            df: DataFrame containing OHLCV data. 
                Expected columns: Open, High, Low, Close, Volume.
                Index should be DatetimeIndex.
                
        Returns:
            Tuple containing:
            - bool: True if strategy matched, False otherwise.
            - dict: Dictionary of relevant metrics/info (e.g., {'rsi': 28.5} or {'pattern': 'Bullish Engulfing'}).
                    Return empty dict if no match or no relevant metrics.
        """
    
    def get_name(self) -> str:
        """Return the name of the strategy class by default."""
        return self.__class__.__name__
        
    def get_description(self) -> str:
        """Return a brief description of the strategy."""
        return self.__doc__.strip() if self.__doc__ else "No description available."

    def signal(self, df: pd.DataFrame) -> pd.Series:
        """
        Vectorized signal series. Override in strategies for performance.
        """
        signals = pd.Series(False, index=df.index)
        for idx in range(len(df)):
            is_match, _ = self.check(df.iloc[: idx + 1])
            signals.iat[idx] = bool(is_match)
        return signals

    def score_series(self, df: pd.DataFrame) -> pd.Series:
        """
        Vectorized signal scoring. Override in strategies for ranking.
        """
        return pd.Series(0.0, index=df.index)

    def exit_signal(self, df: pd.DataFrame) -> pd.Series:
        """
        Vectorized exit signal series. Override for strategy-specific exits.
        """
        return pd.Series(False, index=df.index)

    def get_take_profit(self, df: pd.DataFrame, entry_price: Optional[float] = None) -> Optional[float]:
        """
        Resolve take-profit for this strategy. Override for custom logic.
        """
        if not self.use_take_profit:
            return None
        return self.calculate_atr_take_profit(
            df,
            direction=self.direction,
            entry_price=entry_price,
        )

    def get_take_profit_levels(
        self,
        df: pd.DataFrame,
        entry_price: Optional[float] = None,
    ) -> Optional[List[float]]:
        """
        Optional multi-target take-profit levels. Override for staged exits.
        """
        return None

    def get_take_profit_level_pcts(self, levels: List[float]) -> Optional[List[float]]:
        """
        Optional per-target percentages for staged exits. Override if needed.
        """
        return None

    def effective_regime_stability_days(self, default: int) -> int:
        return self.regime_stability_days if self.regime_stability_days is not None else default

    def effective_regime_flip_band_pct(self, default: float) -> float:
        return self.regime_flip_band_pct if self.regime_flip_band_pct is not None else default

    def effective_regime_flip_slope_pct(self, default: float) -> float:
        return self.regime_flip_slope_pct if self.regime_flip_slope_pct is not None else default

    def allow_risk_off(self, default: bool) -> bool:
        return default if self.allow_risk_off_entries is None else self.allow_risk_off_entries

    def allow_short_risk_on_entry(self, default: bool) -> bool:
        return default if self.allow_short_risk_on is None else self.allow_short_risk_on

    def effective_risk_off_size_mult(self, default: float) -> float:
        return self.risk_off_size_mult if self.risk_off_size_mult is not None else default

    def effective_breakeven_r(self, default: float) -> float:
        return self.breakeven_r if self.breakeven_r is not None else default

    def effective_breakeven_delay(self, default: int) -> int:
        return self.breakeven_delay_bars if self.breakeven_delay_bars is not None else default

    def effective_breakeven_trigger_r(self, default: float) -> float:
        return self.breakeven_trigger_r if self.breakeven_trigger_r is not None else default

    def atr_series(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        if period == 14 and "ATR_14" in df.columns:
            return df["ATR_14"]
        high = df["High"]
        low = df["Low"]
        close = df["Close"]

        prev_close = close.shift(1)
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()

    def atr_stop_series(self, df: pd.DataFrame, period: int = 14, multiplier: float = 2.0, direction: str = "long") -> pd.Series:
        high = df["High"]
        low = df["Low"]
        atr = self.atr_series(df, period=period)

        if direction == "long":
            stop_level = low - (atr * multiplier)
        else:
            stop_level = high + (atr * multiplier)

        return stop_level.round(2)

    def rsi_series(self, df: pd.DataFrame, length: int = 14) -> pd.Series:
        col = f"RSI_{length}"
        if col in df.columns:
            return df[col]
        return ta.rsi(df["Close"], length=length)

    def sma_series(self, df: pd.DataFrame, length: int = 50, column: str = "Close") -> pd.Series:
        if column == "Close":
            col = f"SMA_{length}"
            if col in df.columns:
                return df[col]
        series = df[column]
        return ta.sma(series, length=length)

    def ema_series(self, df: pd.DataFrame, length: int = 20, column: str = "Close") -> pd.Series:
        if column == "Close":
            col = f"EMA_{length}"
            if col in df.columns:
                return df[col]
        series = df[column]
        return ta.ema(series, length=length)

    def volume_sma_series(self, df: pd.DataFrame, length: int = 20) -> pd.Series:
        col = f"VOL_SMA_{length}"
        if col in df.columns:
            return df[col]
        return df["Volume"].rolling(length).mean()

    def macd_df(self, df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
        cols = [f"MACD_{fast}_{slow}_{signal}", f"MACDh_{fast}_{slow}_{signal}", f"MACDs_{fast}_{slow}_{signal}"]
        if all(col in df.columns for col in cols):
            return df[cols]
        return ta.macd(df["Close"], fast=fast, slow=slow, signal=signal)

    def bbands_df(self, df: pd.DataFrame, length: int = 20, std: float = 2.0) -> pd.DataFrame:
        std_str = f"{float(std):.1f}"
        cols = [
            f"BBL_{length}_{std_str}",
            f"BBM_{length}_{std_str}",
            f"BBU_{length}_{std_str}",
            f"BBB_{length}_{std_str}",
            f"BBP_{length}_{std_str}",
        ]
        if all(col in df.columns for col in cols):
            return df[cols]
        return ta.bbands(df["Close"], length=length, std=std)

    def supertrend_df(self, df: pd.DataFrame, length: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
        mult_str = f"{float(multiplier):.1f}"
        cols = [
            f"SUPERT_{length}_{mult_str}",
            f"SUPERTd_{length}_{mult_str}",
            f"SUPERTl_{length}_{mult_str}",
            f"SUPERTs_{length}_{mult_str}",
        ]
        if all(col in df.columns for col in cols):
            return df[cols]
        return ta.supertrend(df["High"], df["Low"], df["Close"], length=length, multiplier=multiplier)

    def stop_loss_series(self, df: pd.DataFrame) -> Optional[pd.Series]:
        return self.atr_stop_series(df, multiplier=self.stop_multiplier, direction=self.direction)

    def calculate_atr_take_profit(
        self,
        df: pd.DataFrame,
        period: int = 14,
        multiplier: Optional[float] = None,
        direction: str = 'long',
        entry_price: Optional[float] = None,
    ) -> Optional[float]:
        """
        Calculates a volatility-based take-profit level using ATR.
        """
        if multiplier is None:
            multiplier = self.take_profit_multiplier
        if multiplier is None:
            return None
        atr = self.atr_series(df, period=period)
        curr_atr = atr.iloc[-1]
        if pd.isna(curr_atr) or curr_atr <= 0:
            return None
        reference = entry_price if entry_price is not None else df['Close'].iloc[-1]
        if direction == 'long':
            return round(reference + (curr_atr * multiplier), 2)
        return round(reference - (curr_atr * multiplier), 2)

    def calculate_atr_stop(
        self,
        df: pd.DataFrame,
        period: int = 14,
        multiplier: float = 2.0,
        direction: str = "long",
    ) -> float:
        return float(
            self.atr_stop_series(
                df,
                period=period,
                multiplier=multiplier,
                direction=direction,
            ).iloc[-1]
        )
