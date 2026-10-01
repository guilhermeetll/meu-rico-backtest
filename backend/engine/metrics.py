from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd
from scipy.stats import norm, kurtosis, skew

from engine.models import EquityPoint, Metrics, Trade
from engine.tax import monthly_day_trade_tax

ANNUALIZATION_DAYS = 252
EULER_MASCHERONI = 0.5772156649015329


def deflated_sharpe_ratio(returns: np.ndarray, n_trials: int) -> float | None:
    """Bailey & López de Prado (2014) deflated Sharpe ratio.

    `returns` are per-period simple returns. The Sharpe used here is the
    non-annualized mean/std of that series. The result is the probability that
    the true Sharpe exceeds the expected maximum Sharpe under the null, given
    `n_trials` configurations. It is not an annualized Sharpe.

    With a single trial the benchmark Sharpe is zero, so this reduces to the
    probabilistic Sharpe ratio against zero.
    """
    values = np.asarray(returns, dtype=float)
    values = values[np.isfinite(values)]
    n = int(values.size)
    if n < 3:
        return None
    std = float(values.std(ddof=1))
    if std <= 0:
        return None
    sr = float(values.mean() / std)
    gamma3 = float(skew(values, bias=False))
    gamma4 = float(kurtosis(values, fisher=False, bias=False))
    inside = 1.0 - gamma3 * sr + ((gamma4 - 1.0) / 4.0) * sr * sr
    if not math.isfinite(inside) or inside <= 1e-12:
        return None

    trials = max(int(n_trials), 1)
    if trials <= 1:
        sr0 = 0.0
    else:
        sr_variance = inside / (n - 1)
        z1 = float(norm.ppf(1.0 - 1.0 / trials))
        z2 = float(norm.ppf(1.0 - 1.0 / (trials * math.e)))
        sr0 = math.sqrt(sr_variance) * ((1.0 - EULER_MASCHERONI) * z1 + EULER_MASCHERONI * z2)

    z = (sr - sr0) * math.sqrt(n - 1) / math.sqrt(inside)
    if not math.isfinite(z):
        return None
    probability = float(norm.cdf(z))
    return min(1.0, max(0.0, probability))


def annualized_sharpe(returns: np.ndarray) -> float | None:
    values = np.asarray(returns, dtype=float)
    values = values[np.isfinite(values)]
    if values.size < 2:
        return None
    std = float(values.std(ddof=1))
    if std <= 0:
        return None
    return float(values.mean() / std * math.sqrt(ANNUALIZATION_DAYS))


def _payoff(trades: list[Trade]) -> float | None:
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl < 0]
    if not wins or not losses:
        return None
    return float(np.mean(wins) / abs(np.mean(losses)))


def _win_rate(trades: list[Trade]) -> float | None:
    if not trades:
        return None
    wins = sum(1 for t in trades if t.pnl > 0)
    return wins / len(trades)


def performance(
    trades: list[Trade],
    session_dates: list[date],
    initial_capital: float,
    tax_rate: float,
    n_trials: int,
) -> tuple[Metrics, list[EquityPoint]]:
    if initial_capital <= 0:
        raise ValueError("Capital inicial deve ser positivo.")

    sessions = sorted(set(session_dates))
    pnl_by_day = {day: 0.0 for day in sessions}
    gross = fees = slippage = 0.0
    for trade in trades:
        pnl_by_day[trade.session_date] = pnl_by_day.get(trade.session_date, 0.0) + trade.pnl
        gross += trade.gross_pnl
        fees += trade.fees
        slippage += trade.slippage_cost
        if trade.session_date not in sessions:
            sessions.append(trade.session_date)
    sessions = sorted(set(sessions))

    daily = pd.Series({day: pnl_by_day.get(day, 0.0) for day in sessions}, dtype=float)
    daily = daily.sort_index()
    tax = monthly_day_trade_tax(daily, tax_rate)

    equity = initial_capital
    equity_after_tax = initial_capital
    points: list[EquityPoint] = []
    if sessions:
        points.append(
            EquityPoint(
                date="Início",
                equity=initial_capital,
                equity_after_tax=initial_capital,
                daily_pnl=0.0,
                daily_pnl_after_tax=0.0,
                baseline=True,
            )
        )
    peak = initial_capital
    max_dd = 0.0
    max_dd_pct: float | None = 0.0
    for day in sessions:
        day_pnl = float(daily.loc[day])
        day_tax = float(tax.tax_by_date.get(day, 0.0))
        equity += day_pnl
        equity_after_tax += day_pnl - day_tax
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)
        if peak > 0:
            max_dd_pct = max(max_dd_pct or 0.0, dd / peak)
        points.append(
            EquityPoint(
                date=day.isoformat(),
                equity=equity,
                equity_after_tax=equity_after_tax,
                daily_pnl=day_pnl,
                daily_pnl_after_tax=day_pnl - day_tax,
            )
        )

    returns = (daily.to_numpy() / initial_capital) if len(daily) else np.array([])
    total_pnl = float(daily.sum()) if len(daily) else 0.0
    total_return = total_pnl / initial_capital if initial_capital else None
    metrics = Metrics(
        total_pnl=total_pnl,
        total_return=total_return,
        gross_pnl=gross,
        fees=fees,
        slippage_cost=slippage,
        sharpe=annualized_sharpe(returns),
        deflated_sharpe=deflated_sharpe_ratio(returns, n_trials),
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        n_trades=len(trades),
        win_rate=_win_rate(trades),
        payoff=_payoff(trades),
        net_pnl_after_tax=tax.net_pnl,
        tax_paid=tax.total_tax,
        n_sessions=len(sessions),
        n_trials=max(int(n_trials), 1),
    )
    return metrics, points
