from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class CostInput(BaseModel):
    fee_per_side: float = 0.5
    slippage_ticks: float = 1.0


class SampleSplitInput(BaseModel):
    enabled: bool = False
    in_sample_fraction: float = 0.7
    split_date: date | None = None


class WalkForwardInput(BaseModel):
    enabled: bool = False
    train_sessions: int = 6
    test_sessions: int = 3
    step_sessions: int | None = None
    anchored: bool = False
    optimize_metric: str = "sharpe"
    param_grid: dict[str, list] | None = None


class BacktestInput(BaseModel):
    strategy: str = "intraday_momentum"
    symbol: str = "WIN"
    data_source: str = "csv"
    timeframe: str = "1min"
    start: date
    end: date
    csv_source: str | None = "example"
    column_map: dict[str, str] | None = None
    strategy_params: dict = Field(default_factory=dict)
    costs: CostInput = Field(default_factory=CostInput)
    tax_rate: float = 0.20
    initial_capital: float = 10_000
    n_trials: int = 1
    include_after_hours: bool = False
    sample_split: SampleSplitInput = Field(default_factory=SampleSplitInput)
    walk_forward: WalkForwardInput = Field(default_factory=WalkForwardInput)
