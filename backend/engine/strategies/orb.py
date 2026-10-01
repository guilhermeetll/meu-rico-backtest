"""Opening-range breakout, the control strategy in docs/preregistro.md.

The range is the high and the low of the first N minutes of the regular
session, N in {5, 15, 30}. The first later bar that closes outside that
range sets the direction. Entry is the open of the next bar, at most one
trade per day. The stop is the other side of the range. There is no target;
whatever is left exits at the end of the regular session.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from engine.instruments import InstrumentSpec
from engine.models import RawTrade, SkippedSession
from engine.series import contracts_of
from engine.sessions import session_bounds
from engine.strategies.base import ParamField, Strategy
from engine.strategies.common import (
    at,
    cash_call_start,
    contract_of,
    coverage_params,
    incomplete_window,
    quantity_of,
    span,
    unique,
)

RANGE_MINUTES = (5, 15, 30)


def _range_minutes(value) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("A faixa de abertura deve ter 5, 15 ou 30 minutos.") from exc
    if minutes not in RANGE_MINUTES:
        raise ValueError("A faixa de abertura deve ter 5, 15 ou 30 minutos.")
    return minutes


class OpeningRangeBreakoutStrategy(Strategy):
    id = "opening_range_breakout"
    label = "Rompimento da faixa de abertura"
    description = (
        "A faixa é a máxima e a mínima dos primeiros 5, 15 ou 30 minutos. "
        "O primeiro fechamento fora da faixa define a direção, e a entrada é "
        "a abertura da barra seguinte. O stop fica no outro extremo. Sem alvo: "
        "o que não parar sai quando começa o leilão do à vista, às 16:55 no pregão "
        "ordinário, no WIN e nas ações. No máximo uma operação por dia."
    )

    def param_schema(self) -> list[ParamField]:
        return [
            ParamField(
                "range_minutes",
                "Faixa (minutos)",
                "select",
                5,
                options=("5", "15", "30"),
                help="Primeiros minutos da sessão regular. A grade é 5, 15 e 30.",
            ),
            ParamField("quantity", "Quantidade", "int", 1, min=1, step=1),
            ParamField(
                "min_bar_coverage",
                "Cobertura mínima da janela",
                "float",
                0.9,
                min=0,
                max=1,
                step=0.05,
                help="Fração mínima de barras. Zero desliga só a fração e mantém as pontas.",
            ),
            ParamField(
                "edge_tolerance_minutes",
                "Tolerância da primeira barra (min)",
                "int",
                5,
                min=0,
                max=120,
                help="A primeira barra de cada janela pode atrasar até esse tanto.",
            ),
        ]

    def generate(
        self,
        bars,
        params: dict,
        instrument: InstrumentSpec,
        bar_minutes: int,
    ) -> tuple[list[RawTrade], list[str], list[SkippedSession]]:
        resolved = self.resolved_params(params)
        range_minutes = _range_minutes(resolved["range_minutes"])
        quantity = quantity_of(resolved)
        min_coverage, edge_minutes = coverage_params(resolved)
        bar_delta = timedelta(minutes=bar_minutes)
        edge = timedelta(minutes=edge_minutes)
        warnings: list[str] = []
        skipped: list[SkippedSession] = []
        trades: list[RawTrade] = []

        frame = bars.sort_values("timestamp")
        tz = frame["timestamp"].dt.tz
        for day, day_bars in frame.groupby(frame["timestamp"].dt.date, sort=True):
            ordered = day_bars.sort_values("timestamp")
            series = contracts_of(ordered)
            if len(series) > 1:
                warnings.append(
                    f"{day.isoformat()}: o pregão mistura {', '.join(series)}. "
                    "Sinal pulado para não transformar o salto entre contratos em retorno."
                )
                continue
            contract = series[0] if series else contract_of(ordered)
            open_t, _ = session_bounds(instrument.family, day, contract)
            session_open = at(day, open_t, tz)
            session_close = at(day, cash_call_start(day), tz)
            range_end = session_open + timedelta(minutes=range_minutes)
            if range_end >= session_close:
                warnings.append(
                    f"{day.isoformat()}: a faixa de {range_minutes} minutos não cabe antes do fechamento."
                )
                continue
            gaps = []
            signal_gap = incomplete_window(
                ordered["timestamp"], session_open, range_end, bar_delta, edge,
                min_coverage, bar_minutes, edge_minutes,
            )
            if signal_gap:
                gaps.append(f"Janela do sinal {span(session_open, range_end)}: {signal_gap}")
            trade_gap = incomplete_window(
                ordered["timestamp"], range_end, session_close, bar_delta, edge,
                min_coverage, bar_minutes, edge_minutes,
            )
            if trade_gap:
                gaps.append(f"Janela da operação {span(range_end, session_close)}: {trade_gap}")
            if gaps:
                warnings.append(f"{day.isoformat()}: pregão pulado. {'. '.join(gaps)}.")
                if signal_gap:
                    skipped.append(SkippedSession(
                        session_date=day,
                        window="signal",
                        reason=f"Janela do sinal {span(session_open, range_end)}: {signal_gap}.",
                    ))
                if trade_gap:
                    skipped.append(SkippedSession(
                        session_date=day,
                        window="trade",
                        reason=f"Janela da operação {span(range_end, session_close)}: {trade_gap}.",
                    ))
                continue

            ranged = ordered[(ordered["timestamp"] >= session_open) & (ordered["timestamp"] < range_end)]
            if ranged.empty:
                continue
            range_high = float(ranged["high"].max())
            range_low = float(ranged["low"].min())
            if range_high <= 0 or range_low <= 0 or range_high < range_low:
                warnings.append(f"{day.isoformat()}: faixa de abertura sem preço válido. Sinal pulado.")
                continue

            after = ordered[(ordered["timestamp"] >= range_end) & (ordered["timestamp"] < session_close)]
            trade = _first_breakout(
                after, range_high, range_low, session_close, bar_delta, quantity, day,
            )
            if trade is not None:
                trades.append(trade)
        return trades, unique(warnings), skipped


def _first_breakout(after: pd.DataFrame, range_high: float, range_low: float, session_close, bar_delta, quantity: int, day):
    rows = list(after.sort_values("timestamp").to_dict("records"))
    for index, row in enumerate(rows):
        close = float(row["close"])
        if close > range_high:
            direction = 1
            level = range_high
            stop = range_low
        elif close < range_low:
            direction = -1
            level = range_low
            stop = range_high
        else:
            continue
        if index + 1 >= len(rows):
            return None
        entry = rows[index + 1]
        signal = close / level - 1.0
        fill = _stop_or_close(rows[index + 1 :], direction, stop, session_close, bar_delta)
        if fill is None:
            return None
        exit_price, exit_time = fill
        return RawTrade(
            session_date=day,
            direction=direction,
            quantity=quantity,
            entry_time=pd.Timestamp(entry["timestamp"]).to_pydatetime(),
            exit_time=pd.Timestamp(exit_time).to_pydatetime(),
            entry_price=float(entry["open"]),
            exit_price=exit_price,
            signal_return=signal,
        )
    return None


def _stop_or_close(path: list[dict], direction: int, stop: float, session_close, bar_delta):
    """Stop first. A bar that opens through the stop fills at the open."""
    for row in path:
        opened = float(row["open"])
        stamp = pd.Timestamp(row["timestamp"])
        if direction == 1:
            if opened < stop:
                return opened, stamp
            if opened == stop or float(row["low"]) <= stop:
                when = stamp if opened == stop else stamp + bar_delta
                return stop, when
        else:
            if opened > stop:
                return opened, stamp
            if opened == stop or float(row["high"]) >= stop:
                when = stamp if opened == stop else stamp + bar_delta
                return stop, when
    last = path[-1]
    if pd.Timestamp(last["timestamp"]) + bar_delta != pd.Timestamp(session_close):
        return None
    return float(last["close"]), pd.Timestamp(last["timestamp"]) + bar_delta
