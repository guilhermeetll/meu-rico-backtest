from datetime import date, timedelta

import numpy as np
import pytest

from engine.costs import CostModel
from engine.instruments import WIN
from engine.metrics import annualized_sharpe, deflated_sharpe_ratio, performance
from engine.models import Trade


def _trade(day: date, pnl: float) -> Trade:
    return Trade(
        session_date=day,
        symbol="WIN",
        direction=1,
        quantity=1,
        entry_time=day,
        exit_time=day,
        entry_price=100_000,
        exit_price=100_000,
        entry_price_effective=100_000,
        exit_price_effective=100_000,
        gross_pnl=pnl,
        fees=0.0,
        slippage_cost=0.0,
        pnl=pnl,
        signal_return=0.0,
    )


def test_return_drawdown_win_rate_and_payoff():
    start = date(2026, 9, 1)
    sessions = [start + timedelta(days=offset) for offset in range(4)]
    trades = [_trade(sessions[0], 100), _trade(sessions[1], -50), _trade(sessions[3], 50)]
    metrics, equity = performance(trades, sessions, 10_000, tax_rate=0.20, n_trials=1)
    returns = np.array([100, -50, 0, 50]) / 10_000
    assert metrics.total_pnl == pytest.approx(100)
    assert metrics.total_return == pytest.approx(0.01)
    assert metrics.sharpe == pytest.approx(annualized_sharpe(returns))
    assert metrics.max_drawdown == pytest.approx(50)
    assert metrics.max_drawdown_pct == pytest.approx(50 / 10_100)
    assert metrics.n_trades == 3
    assert metrics.win_rate == pytest.approx(2 / 3)
    assert metrics.payoff == pytest.approx(1.5)
    assert metrics.tax_paid == pytest.approx(20)
    assert metrics.net_pnl_after_tax == pytest.approx(80)
    assert equity[-1].equity == pytest.approx(10_100)
    assert equity[-1].equity_after_tax == pytest.approx(10_080)
    assert metrics.n_sessions == 4


def test_deflated_sharpe_shrinks_as_more_configurations_are_tried():
    rng = np.random.default_rng(7)
    returns = rng.normal(0.002, 0.01, size=80)
    single = deflated_sharpe_ratio(returns, n_trials=1)
    many = deflated_sharpe_ratio(returns, n_trials=50)
    assert single is not None and many is not None
    assert 0.0 <= many <= single <= 1.0
    assert many < single


def test_deflated_sharpe_needs_a_few_sessions():
    assert deflated_sharpe_ratio(np.array([0.01, -0.01]), n_trials=1) is None


def test_zero_cost_backtest_matches_points_times_point_value():
    # Guard the WIN point value used inside performance via a direct trade.
    assert WIN.point_value * 5 == pytest.approx(1.0)
    trade = _trade(date(2026, 9, 1), WIN.point_value * 25)
    metrics, _ = performance([trade], [date(2026, 9, 1)], 10_000, 0.0, 1)
    assert metrics.total_pnl == pytest.approx(5.0)
    assert metrics.tax_paid == 0
