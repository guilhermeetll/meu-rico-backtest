from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class RawTrade:
    session_date: date
    direction: int
    quantity: int
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    signal_return: float


@dataclass(frozen=True)
class Trade:
    session_date: date
    symbol: str
    direction: int
    quantity: int
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    entry_price_effective: float
    exit_price_effective: float
    gross_pnl: float
    fees: float
    slippage_cost: float
    pnl: float
    signal_return: float


@dataclass
class Metrics:
    total_pnl: float
    total_return: float | None
    gross_pnl: float
    fees: float
    slippage_cost: float
    sharpe: float | None
    deflated_sharpe: float | None
    max_drawdown: float
    max_drawdown_pct: float | None
    n_trades: int
    win_rate: float | None
    payoff: float | None
    net_pnl_after_tax: float
    tax_paid: float
    n_sessions: int
    n_trials: int


@dataclass
class EquityPoint:
    date: str
    equity: float
    equity_after_tax: float
    daily_pnl: float
    daily_pnl_after_tax: float
    baseline: bool = False


@dataclass
class SegmentResult:
    metrics: Metrics
    equity: list[EquityPoint]
    trades: list[Trade]
    start: date | None
    end: date | None


@dataclass
class WalkForwardWindow:
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    chosen_params: dict
    train_score: float | None
    test_pnl: float


@dataclass
class WalkForwardResult:
    windows: list[WalkForwardWindow]
    oos_metrics: Metrics
    oos_equity: list[EquityPoint]
    oos_trades: list[Trade]
    n_configurations: int


@dataclass(frozen=True)
class SkippedSession:
    """A session dropped because one window did not have enough bars.

    `window` is `signal` or `trade`. A missing trade window can be skipped
    only in a backtest: live, the position would already be open.
    """

    session_date: date
    reason: str
    window: str


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity: list[EquityPoint]
    metrics: Metrics
    in_sample: SegmentResult | None
    out_of_sample: SegmentResult | None
    walk_forward: WalkForwardResult | None
    warnings: list[str] = field(default_factory=list)
    session_dates: list[date] = field(default_factory=list)
    skipped: list[SkippedSession] = field(default_factory=list)
