from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from engine.costs import CostModel, apply_costs
from engine.instruments import InstrumentSpec
from engine.metrics import performance
from engine.models import BacktestResult, RawTrade, SegmentResult, SkippedSession, Trade
from engine.strategies.base import Strategy
from engine.walkforward import WalkForwardConfig, run_walk_forward


@dataclass
class SampleSplit:
    enabled: bool = False
    in_sample_fraction: float = 0.7
    split_date: date | None = None


def execute(raw_trades: list[RawTrade], instrument: InstrumentSpec, costs: CostModel, symbol: str) -> list[Trade]:
    return [apply_costs(raw, instrument, costs, symbol) for raw in raw_trades]


def _segment(
    trades: list[Trade],
    sessions: list[date],
    initial_capital: float,
    tax_rate: float,
    n_trials: int,
) -> SegmentResult:
    selected = [t for t in trades if t.session_date in set(sessions)]
    metrics, equity = performance(selected, sessions, initial_capital, tax_rate, n_trials)
    return SegmentResult(
        metrics=metrics,
        equity=equity,
        trades=selected,
        start=sessions[0] if sessions else None,
        end=sessions[-1] if sessions else None,
    )


def _split_sessions(sessions: list[date], split: SampleSplit) -> tuple[list[date], list[date]]:
    if split.split_date is not None:
        ins = [d for d in sessions if d <= split.split_date]
        outs = [d for d in sessions if d > split.split_date]
        return ins, outs
    fraction = split.in_sample_fraction
    if not 0 < fraction < 1:
        raise ValueError("A fração dentro da amostra deve estar entre 0 e 1.")
    cut = int(len(sessions) * fraction)
    return sessions[:cut], sessions[cut:]


def run_backtest(
    bars,
    strategy: Strategy,
    params: dict,
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
) -> BacktestResult:
    if bars is None or len(bars) == 0:
        raise ValueError("Não há barras para o período pedido.")
    if bar_minutes < 1:
        raise ValueError("Timeframe inválido.")

    sessions = sorted(set(bars["timestamp"].dt.date))
    raw, warnings = strategy.generate(bars, params, instrument, bar_minutes)
    skipped = _skipped_sessions(warnings)
    trades = execute(raw, instrument, costs, symbol)
    metrics, equity = performance(trades, sessions, initial_capital, tax_rate, n_trials)

    in_sample = out_of_sample = None
    split = sample_split or SampleSplit(enabled=False)
    if split.enabled:
        ins, outs = _split_sessions(sessions, split)
        if not ins or not outs:
            warnings.append(
                "A divisão dentro/fora da amostra ficou com um dos lados vazio e foi ignorada."
            )
        else:
            in_sample = _segment(trades, ins, initial_capital, tax_rate, n_trials)
            out_of_sample = _segment(trades, outs, initial_capital, tax_rate, n_trials)
            warnings.append(
                "O IR de cada fatia é recalculado só com os pregões daquela fatia. "
                "O prejuízo não atravessa o corte."
            )

    wf = None
    if walk_forward is not None and walk_forward.enabled:
        wf, wf_warnings = run_walk_forward(
            bars=bars,
            strategy=strategy,
            params=params,
            instrument=instrument,
            costs=costs,
            symbol=symbol,
            initial_capital=initial_capital,
            tax_rate=tax_rate,
            n_trials=n_trials,
            bar_minutes=bar_minutes,
            config=walk_forward,
        )
        warnings.extend(wf_warnings)

    return BacktestResult(
        trades=trades,
        equity=equity,
        metrics=metrics,
        in_sample=in_sample,
        out_of_sample=out_of_sample,
        walk_forward=wf,
        warnings=_unique(warnings),
        session_dates=sessions,
        skipped=skipped,
    )


def _skipped_sessions(warnings: list[str]) -> list[SkippedSession]:
    marker = ": pregão pulado. "
    found: list[SkippedSession] = []
    seen: set[date] = set()
    for warning in warnings:
        if marker not in warning:
            continue
        day_text, reason = warning.split(marker, 1)
        try:
            day = date.fromisoformat(day_text.strip())
        except ValueError:
            continue
        if day in seen:
            continue
        seen.add(day)
        found.append(SkippedSession(session_date=day, reason=reason.strip()))
    return found


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    rest = [item for item in ordered if "pregão pulado" not in item.lower()]
    kept_rest = set(rest[:30])
    return [
        item
        for item in ordered
        if "pregão pulado" in item.lower() or item in kept_rest
    ]
