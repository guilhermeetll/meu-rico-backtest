from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

from engine.costs import CostModel
from engine.instruments import InstrumentSpec
from engine.metrics import performance
from engine.models import WalkForwardResult, WalkForwardWindow
from engine.strategies.base import Strategy


@dataclass
class WalkForwardConfig:
    enabled: bool = False
    train_sessions: int = 10
    test_sessions: int = 5
    step_sessions: int | None = None
    anchored: bool = False
    optimize_metric: str = "sharpe"
    param_grid: dict | None = field(default=None)


def expand_grid(base: dict, grid: dict | None) -> list[dict]:
    if not grid:
        return [dict(base)]
    keys = [key for key, values in grid.items() if isinstance(values, list) and values]
    if not keys:
        return [dict(base)]
    combos = []
    for values in itertools.product(*(grid[key] for key in keys)):
        params = dict(base)
        params.update(zip(keys, values))
        combos.append(params)
    return combos


def _score(metrics, name: str) -> float | None:
    if name == "total_pnl":
        return metrics.total_pnl
    if name == "net_pnl":
        return metrics.net_pnl_after_tax
    if metrics.sharpe is None or not math.isfinite(metrics.sharpe):
        return None
    return metrics.sharpe


def run_walk_forward(
    *,
    bars,
    strategy: Strategy,
    params: dict,
    instrument: InstrumentSpec,
    costs: CostModel,
    symbol: str,
    initial_capital: float,
    tax_rate: float,
    n_trials: int,
    bar_minutes: int,
    config: WalkForwardConfig,
    trades=None,
) -> tuple[WalkForwardResult, list[str]]:
    from engine.backtest import execute

    warnings: list[str] = []
    if config.train_sessions < 1 or config.test_sessions < 1:
        raise ValueError("Janelas de treino e teste do walk-forward precisam ter ao menos 1 pregão.")
    step = config.step_sessions or config.test_sessions
    if step < 1:
        raise ValueError("O passo do walk-forward precisa ser positivo.")
    if step < config.test_sessions:
        warnings.append(
            "O passo é menor que a janela de teste. Pregões repetidos entram uma vez só, na primeira janela."
        )
    metric_name = config.optimize_metric if config.optimize_metric in {"sharpe", "total_pnl", "net_pnl"} else "sharpe"
    combos = expand_grid(params, config.param_grid)
    sessions = sorted(set(bars["timestamp"].dt.date))
    windows: list[WalkForwardWindow] = []
    oos_trades = []
    used_dates: set = set()
    oos_sessions: list = []

    test_start = config.train_sessions
    while test_start + config.test_sessions <= len(sessions):
        test_dates = sessions[test_start : test_start + config.test_sessions]
        if config.anchored:
            train_dates = sessions[:test_start]
        else:
            train_dates = sessions[test_start - config.train_sessions : test_start]
        train_bars = _filter(bars, train_dates)
        best_params = combos[0]
        best_score: float | None = None
        for candidate in combos:
            raw, _, _ = strategy.generate(train_bars, candidate, instrument, bar_minutes, trades=trades)
            trades = execute(raw, instrument, costs, symbol)
            metrics, _ = performance(trades, train_dates, initial_capital, tax_rate, n_trials=1)
            score = _score(metrics, metric_name)
            if score is None:
                continue
            if best_score is None or score > best_score:
                best_score = score
                best_params = candidate
        if best_score is None:
            warnings.append(
                "Sharpe indefinido no treino de uma janela. A primeira configuração da grade foi mantida."
            )

        fresh = [day for day in test_dates if day not in used_dates]
        for day in fresh:
            used_dates.add(day)
        if fresh:
            test_bars = _filter(bars, fresh)
            raw, _, _ = strategy.generate(test_bars, best_params, instrument, bar_minutes, trades=trades)
            test_trades = execute(raw, instrument, costs, symbol)
            oos_trades.extend(test_trades)
            oos_sessions.extend(fresh)
            test_pnl = sum(t.pnl for t in test_trades)
        else:
            test_pnl = 0.0
        windows.append(
            WalkForwardWindow(
                train_start=train_dates[0],
                train_end=train_dates[-1],
                test_start=test_dates[0],
                test_end=test_dates[-1],
                chosen_params={k: best_params[k] for k in sorted(best_params) if not str(k).startswith("_")},
                train_score=best_score,
                test_pnl=test_pnl,
            )
        )
        test_start += step

    if not windows:
        warnings.append("A amostra não tem pregões suficientes para uma janela de walk-forward.")

    oos_trials = max(n_trials, len(combos))
    oos_metrics, oos_equity = performance(
        oos_trades, oos_sessions, initial_capital, tax_rate, oos_trials
    )
    result = WalkForwardResult(
        windows=windows,
        oos_metrics=oos_metrics,
        oos_equity=oos_equity,
        oos_trades=oos_trades,
        n_configurations=len(combos),
    )
    return result, warnings


def _filter(bars, dates: list):
    wanted = set(dates)
    mask = bars["timestamp"].dt.date.isin(wanted)
    return bars.loc[mask].copy()
