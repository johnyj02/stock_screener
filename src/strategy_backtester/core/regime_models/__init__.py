from typing import Dict, Type

from .base import RegimeBuildResult, RegimeModel
from .multi_factor_v1 import MultiFactorRegimeModelV1
from .rule_based_v1 import RuleBasedRegimeModelV1
from .simple_sma import SimpleSmaRegimeModel


REGIME_MODELS: Dict[str, Type[RegimeModel]] = {
    SimpleSmaRegimeModel.name: SimpleSmaRegimeModel,
    MultiFactorRegimeModelV1.name: MultiFactorRegimeModelV1,
    RuleBasedRegimeModelV1.name: RuleBasedRegimeModelV1,
}


def load_regime_model(name: str) -> RegimeModel:
    key = (name or "").strip().lower()
    model_cls = REGIME_MODELS.get(key)
    if model_cls is None:
        raise ValueError(f"Unknown regime model: {name}")
    return model_cls()


__all__ = [
    "RegimeBuildResult",
    "RegimeModel",
    "SimpleSmaRegimeModel",
    "MultiFactorRegimeModelV1",
    "RuleBasedRegimeModelV1",
    "load_regime_model",
]
