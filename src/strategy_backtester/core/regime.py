from dataclasses import dataclass
import pandas as pd

from .regime_models.simple_sma import SimpleSmaRegimeModel


@dataclass(frozen=True)
class RegimeState:
    risk_on: bool
    severity: str
    hedge_pct: float
    risk_on_streak: int
    sma200_slope_pct: float
    sma200_distance_pct: float
    risk_on_prob: float = 1.0
    trend_state: str = "UP"
    vol_state: str = "NORMAL"
    stress: float = 0.0
    regime_usable: bool = True


def build_regime_series(data: pd.DataFrame, master_index) -> pd.DataFrame:
    model = SimpleSmaRegimeModel()
    result = model.build(data, master_index, interval="1d", config={})
    return result.frame


def regime_state(date, regime_df: pd.DataFrame) -> RegimeState:
    if date not in regime_df.index:
        return RegimeState(
            risk_on=True,
            severity="risk_on",
            hedge_pct=0.0,
            risk_on_streak=0,
            sma200_slope_pct=0.0,
            sma200_distance_pct=0.0,
            risk_on_prob=1.0,
            trend_state="UP",
            vol_state="NORMAL",
            stress=0.0,
            regime_usable=True,
        )
    row = regime_df.loc[date]
    return RegimeState(
        risk_on=bool(row["risk_on"]),
        severity=row["severity"],
        hedge_pct=float(row["hedge_pct"]),
        risk_on_streak=int(row.get("risk_on_streak", 0)),
        sma200_slope_pct=float(row.get("sma200_slope_pct", 0.0) or 0.0),
        sma200_distance_pct=float(row.get("sma200_distance_pct", 0.0) or 0.0),
        risk_on_prob=float(row.get("risk_on_prob", 1.0) or 0.0),
        trend_state=str(row.get("trend_state", "UP") or "UP"),
        vol_state=str(row.get("vol_state", "NORMAL") or "NORMAL"),
        stress=float(row.get("stress", 0.0) or 0.0),
        regime_usable=bool(row.get("regime_usable", True)),
    )
