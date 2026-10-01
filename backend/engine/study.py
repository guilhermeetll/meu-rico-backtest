"""Several configurations of one strategy, scored as a single study.

The deflated Sharpe of every variant uses the number of configurations in the
study, so the best one is not reported as if it had been the only trial.
"""

from __future__ import annotations

from engine.backtest import SampleSplit, run_backtest
from engine.costs import CostModel
from engine.instruments import InstrumentSpec
from engine.models import BacktestResult
from engine.strategies.base import Strategy
from engine.walkforward import WalkForwardConfig


def trial_count(requested: int, n_variants: int) -> int:
    """Trials that enter the deflated Sharpe of each variant.

    The count is at least the number of variants run together. A larger
    requested N is kept, because those extra trials were also searched.
    """
    if n_variants < 1:
        raise ValueError("O estudo precisa de ao menos uma configuração.")
    if requested < 1:
        raise ValueError("O número de configurações testadas deve ser pelo menos 1.")
    return max(int(requested), int(n_variants))


def run_study(
    bars,
    strategy: Strategy,
    variants: list[dict],
    instrument: InstrumentSpec,
    costs: CostModel,
    *,
    symbol: str,
    initial_capital: float,
    tax_rate: float,
    n_trials: int,
    bar_minutes: int,
    sample_split: SampleSplit | None = None,
    walk_forward: WalkForwardConfig | None = None,
    trades=None,
) -> tuple[int, list[tuple[dict, BacktestResult]]]:
    trials = trial_count(n_trials, len(variants))
    results: list[tuple[dict, BacktestResult]] = []
    for params in variants:
        results.append(
            (
                params,
                run_backtest(
                    bars,
                    strategy,
                    params,
                    instrument,
                    costs,
                    symbol=symbol,
                    initial_capital=initial_capital,
                    tax_rate=tax_rate,
                    n_trials=trials,
                    bar_minutes=bar_minutes,
                    sample_split=sample_split,
                    walk_forward=walk_forward,
                    trades=trades,
                ),
            )
        )
    return trials, results
