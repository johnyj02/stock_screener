from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd


@dataclass
class RegimeBuildResult:
    frame: pd.DataFrame
    diagnostics: Dict[str, Any] = field(default_factory=dict)


class RegimeModel:
    name = "base"

    def required_symbols(self, config: Dict[str, Any]) -> List[str]:
        return []

    def build(
        self,
        data_map: Dict[str, pd.DataFrame],
        master_index: pd.Index,
        interval: str,
        config: Dict[str, Any],
        start_date: Optional[pd.Timestamp] = None,
        end_date: Optional[pd.Timestamp] = None,
    ) -> RegimeBuildResult:
        raise NotImplementedError
