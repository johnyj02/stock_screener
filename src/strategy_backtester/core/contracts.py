from typing import Dict, Optional

DEFAULT_CONTRACT_MULTIPLIERS: Dict[str, float] = {
    "ES=F": 50.0,
    "MES=F": 5.0,
    "NQ=F": 20.0,
    "MNQ=F": 2.0,
    "RTY=F": 50.0,
    "M2K=F": 5.0,
    "YM=F": 5.0,
    "GC=F": 100.0,
    "MGC=F": 10.0,
    "SI=F": 5000.0,
    "SIL=F": 5000.0,
    "BTC=F": 5.0,
    "MBT=F": 0.1,
}


def contract_multiplier(symbol: str, overrides: Optional[Dict[str, float]] = None) -> float:
    if overrides and symbol in overrides:
        return overrides[symbol]
    return DEFAULT_CONTRACT_MULTIPLIERS.get(symbol, 1.0)


def is_future_symbol(symbol: str) -> bool:
    return symbol.endswith("=F")
