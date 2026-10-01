from datetime import date

import pytest

from engine.backtest import run_backtest
from engine.costs import CostModel
from engine.instruments import WIN
from engine.strategies.momentum import IntradayMomentumStrategy
from engine.study import run_study, trial_count
from tests.test_momentum import _business_days, _day_bars, _frame


def test_trial_count_is_at_least_the_number_of_variants():
    assert trial_count(1, 4) == 4
    assert trial_count(10, 4) == 10
    with pytest.raises(ValueError):
        trial_count(1, 0)


def test_each_variant_uses_the_study_trial_count():
    days = _business_days(date(2026, 9, 1), 6)
    rows = []
    for index, day in enumerate(days):
        rows.extend(_day_bars(day, 100_000, 100_050, 100_100, 100_120 + index * 20))
    bars = _frame(rows)
    variants = [
        {"signal_anchor": "session_open", "trade_window": "session_close", "min_bar_coverage": 0},
        {"signal_anchor": "session_open", "trade_window": "before_cash_auction", "min_bar_coverage": 0},
        {"threshold": 1.0, "min_bar_coverage": 0},
    ]
    trials, results = run_study(
        bars,
        IntradayMomentumStrategy(),
        variants,
        WIN,
        CostModel(0.50, 1),
        symbol="WIN",
        initial_capital=10_000,
        tax_rate=0.0,
        n_trials=1,
        bar_minutes=1,
    )
    assert trials == 3
    assert len(results) == 3
    assert all(result.metrics.n_trials == 3 for _, result in results)
    alone = run_backtest(
        bars,
        IntradayMomentumStrategy(),
        variants[0],
        WIN,
        CostModel(0.50, 1),
        symbol="WIN",
        initial_capital=10_000,
        tax_rate=0.0,
        n_trials=1,
        bar_minutes=1,
    )
    studied = results[0][1].metrics.deflated_sharpe
    single = alone.metrics.deflated_sharpe
    assert studied is not None and single is not None
    assert studied < single
