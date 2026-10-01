"""Opening-gap reversal, pre-registered in docs/preregistro.md.

Ceretta & Da Costa (2017), Economics Bulletin 37(4). The gap is the natural
log of the opening print over the previous close of the same series. A gap
at or below −x buys; a gap at or above +x sells. x is 0.5% for the WIN and
the index, and 1% for equities. It is not a grid axis.
"""

from __future__ import annotations

import math
from datetime import timedelta

import pandas as pd

from engine.instruments import InstrumentSpec
from engine.models import RawTrade, SkippedSession
from engine.series import contracts_of, previous_close
from engine.sessions import session_bounds
from engine.strategies.base import ParamField, Strategy
from engine.strategies.common import (
    at,
    coverage_params,
    hhmm,
    incomplete_window,
    quantity_of,
    regular_close,
    span,
    unique,
    contract_of,
)

EXIT_MODES = ("15", "30", "eod")
# A simple +0.5% move is ln(1.005) ≈ 0.004987, which does not reach 0.005.
WIN_GAP = 0.005
EQUITY_GAP = 0.01
_INDEX = {"BVSP", "IBOV", "IBOVESPA", "^BVSP"}


def gap_threshold(instrument: InstrumentSpec) -> float:
    symbol = instrument.symbol.upper()
    root = symbol.split(".")[0]
    if instrument.family == "WIN" or root in _INDEX or symbol in _INDEX:
        return WIN_GAP
    return EQUITY_GAP


def _exit_mode(value) -> str:
    text = str(value).strip().lower()
    if text not in EXIT_MODES:
        raise ValueError("A saída do gap deve ser 15, 30 ou eod.")
    return text


class GapReversalStrategy(Strategy):
    id = "gap_reversal"
    label = "Reversão do gap de abertura"
    description = (
        "Gap = ln(abertura / fechamento anterior). Gap no limiar ou além "
        "vende; gap no limiar negativo ou além compra. O limiar é 0,5% no "
        "WIN e no índice e 1% nas ações, fixo. A entrada é a abertura da "
        "primeira barra depois do leilão. A saída é em 15 min, 30 min ou no "
        "fim do pregão regular."
    )

    def param_schema(self) -> list[ParamField]:
        return [
            ParamField(
                "exit",
                "Saída",
                "select",
                "eod",
                options=EXIT_MODES,
                help="15, 30 ou eod (fim do pregão regular, antes do leilão de fechamento).",
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
                help="A abertura e a entrada podem atrasar até esse tanto.",
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
        exit_mode = _exit_mode(resolved["exit"])
        quantity = quantity_of(resolved)
        min_coverage, edge_minutes = coverage_params(resolved)
        threshold = gap_threshold(instrument)
        bar_delta = timedelta(minutes=bar_minutes)
        edge = timedelta(minutes=edge_minutes)
        warnings: list[str] = []
        skipped: list[SkippedSession] = []
        trades: list[RawTrade] = []

        frame = bars.sort_values("timestamp")
        tz = frame["timestamp"].dt.tz
        for day, day_bars in frame.groupby(frame["timestamp"].dt.date, sort=True):
            series = contracts_of(day_bars)
            if len(series) > 1:
                warnings.append(
                    f"{day.isoformat()}: o pregão mistura {', '.join(series)}. "
                    "Sinal pulado para não transformar o salto entre contratos em retorno."
                )
                continue
            contract = series[0] if series else contract_of(day_bars)
            open_t, _ = session_bounds(instrument.family, day, contract)
            session_open = at(day, open_t, tz)
            close_t = regular_close(instrument.family, day, contract)
            session_close = at(day, close_t, tz)
            opening = _opening_print(day_bars, session_open, edge, session_close)
            if opening is None:
                reason = _missing_open_reason(day_bars, session_open, edge, edge_minutes)
                warnings.append(f"{day.isoformat()}: pregão pulado. {reason}")
                skipped.append(SkippedSession(session_date=day, window="signal", reason=f"{reason}."))
                continue
            open_price = float(opening["open"])
            if open_price <= 0:
                warnings.append(f"{day.isoformat()}: abertura sem preço positivo. Sinal pulado.")
                continue
            prior = previous_close(frame, day, contract)
            if prior is None:
                if contract:
                    warnings.append(
                        f"{day.isoformat()}: sem fechamento anterior de {contract}. "
                        "Sinal pulado para não usar o fechamento de outro vencimento."
                    )
                else:
                    warnings.append(f"{day.isoformat()}: sem fechamento anterior. Sinal pulado.")
                continue
            gap = math.log(open_price / prior)
            if gap <= -threshold:
                direction = 1
            elif gap >= threshold:
                direction = -1
            else:
                continue

            opening_ts = opening["timestamp"]
            expected = opening_ts + bar_delta
            entry = _first_between(day_bars, expected, expected + edge, session_close)
            if entry is None:
                reason = (
                    f"Janela da operação {span(expected, session_close)}: sem barra de entrada "
                    f"depois da abertura das {hhmm(opening_ts)} "
                    f"(tolerância de {edge_minutes} minutos a partir de {hhmm(expected)})"
                )
                warnings.append(f"{day.isoformat()}: pregão pulado. {reason}.")
                skipped.append(SkippedSession(session_date=day, window="trade", reason=f"{reason}."))
                continue
            entry_ts = entry["timestamp"]
            if exit_mode == "eod":
                exit_clock = session_close
            else:
                exit_clock = entry_ts + timedelta(minutes=int(exit_mode))
                if exit_clock > session_close:
                    exit_clock = session_close
            if exit_clock <= entry_ts:
                reason = (
                    f"Janela da operação {span(entry_ts, session_close)}: a saída não fica "
                    "depois da entrada"
                )
                warnings.append(f"{day.isoformat()}: pregão pulado. {reason}.")
                skipped.append(SkippedSession(session_date=day, window="trade", reason=f"{reason}."))
                continue
            trade_gap = incomplete_window(
                day_bars["timestamp"], entry_ts, exit_clock, bar_delta, edge,
                min_coverage, bar_minutes, edge_minutes,
            )
            if trade_gap:
                reason = f"Janela da operação {span(entry_ts, exit_clock)}: {trade_gap}"
                warnings.append(f"{day.isoformat()}: pregão pulado. {reason}.")
                skipped.append(SkippedSession(session_date=day, window="trade", reason=f"{reason}."))
                continue
            exit_row = _bar_ending_at(day_bars, exit_clock, bar_delta)
            if exit_row is None:
                reason = f"Janela da operação {span(entry_ts, exit_clock)}: sem a barra que fecha na saída"
                warnings.append(f"{day.isoformat()}: pregão pulado. {reason}.")
                skipped.append(SkippedSession(session_date=day, window="trade", reason=f"{reason}."))
                continue
            trades.append(
                RawTrade(
                    session_date=day,
                    direction=direction,
                    quantity=quantity,
                    entry_time=entry_ts.to_pydatetime(),
                    exit_time=(exit_row["timestamp"] + bar_delta).to_pydatetime(),
                    entry_price=float(entry["open"]),
                    exit_price=float(exit_row["close"]),
                    signal_return=gap,
                )
            )
        return trades, unique(warnings), skipped


def _opening_print(day_bars: pd.DataFrame, session_open, edge: timedelta, session_close):
    window = day_bars[
        (day_bars["timestamp"] >= session_open)
        & (day_bars["timestamp"] <= session_open + edge)
        & (day_bars["timestamp"] < session_close)
    ]
    if window.empty:
        return None
    return window.sort_values("timestamp").iloc[0]


def _missing_open_reason(day_bars: pd.DataFrame, session_open, edge: timedelta, edge_minutes: int) -> str:
    label = hhmm(session_open)
    later = day_bars[day_bars["timestamp"] > session_open + edge]
    if not later.empty:
        first = later.sort_values("timestamp").iloc[0]["timestamp"]
        return (
            f"Janela do sinal {span(session_open, session_open + edge)}: sem o preço de abertura "
            f"(primeira barra às {hhmm(first)}, fora da tolerância de {edge_minutes} minutos "
            f"a partir de {label})"
        )
    return (
        f"Janela do sinal {span(session_open, session_open + edge)}: sem o preço de abertura "
        f"dentro de {edge_minutes} minutos a partir de {label}"
    )


def _first_between(day_bars: pd.DataFrame, start, end, session_close):
    window = day_bars[
        (day_bars["timestamp"] >= start)
        & (day_bars["timestamp"] <= end)
        & (day_bars["timestamp"] < session_close)
    ]
    if window.empty:
        return None
    return window.sort_values("timestamp").iloc[0]


def _bar_ending_at(day_bars: pd.DataFrame, exit_clock, bar_delta: timedelta):
    target = exit_clock - bar_delta
    hits = day_bars.loc[day_bars["timestamp"] == target]
    if hits.empty:
        return None
    return hits.iloc[0]
