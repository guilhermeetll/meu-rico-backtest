from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd


@dataclass
class DataRequest:
    symbol: str
    start: date
    end: date
    timeframe: str
    csv_path: Path | None = None
    column_map: dict | None = None
    include_after_hours: bool = False


@dataclass
class LoadResult:
    bars: pd.DataFrame
    warnings: list[str] = field(default_factory=list)
    trades: pd.DataFrame | None = None
