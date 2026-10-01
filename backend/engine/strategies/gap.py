"""Opening-gap reversal, pre-registered in docs/preregistro.md.

Ceretta & Da Costa (2017), Economics Bulletin 37(4). The gap is the natural
log of the opening print over the previous close of the same series. The
core grid uses the same three thresholds for stocks and the WIN, enters on
the bar that starts one minute after the regular open, and exits 15 minutes
later. Thirty minutes and the end of continuous trading are extras.
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
    cash_call_start,
    contract_of,
    coverage_params,
    hhmm,
    incomplete_window,
    quantity_of,
    span,
    unique,
)

EXIT_MODES = ("15", "30", "eod")
GAP_THRESHOLDS = (0.005, 0.01, 0.015)
ENTRY_LAG = timedelta(minutes=1)


def _exit_mode(value) -> str:
    text = str(value).strip().lower()
    if text not in EXIT_MODES:
        raise ValueError("A saída do gap deve ser 15, 30 ou eod.")
    return text


def _threshold(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("O limiar do gap deve ser 0,5%, 1% ou 1,5%.") from exc
    for item in GAP_THRESHOLDS:
        if abs(number - item) < 1e-9:
            return item
    raise ValueError("O limiar do gap deve ser 0,5%, 1% ou 1,5%.")


class GapReversalStrategy(Strategy):
    id = "gap_reversal"
    label = "Reversão do gap de abertura"
    description = (
        "Gap = ln(abertura / fechamento anterior). Os limiares são 0,5%, 1% "
        "e 1,5%, iguais para WIN e ações. A entrada é o open da primeira barra "
        "a partir de 1 minuto depois da abertura, dentro da tolerância. O núcleo sai 15 minutos depois; "
        "30 minutos e o fim do contínuo, às 16:55 no pregão ordinário, são extras."
    )

    def param_schema(self) -> list[ParamField]:
        return [
            ParamField(
                "threshold",
                "Limiar",
                "select",
                0.005,
                options=("0.005", "0.01", "0.015"),
                help="0,5%, 1% ou 1,5%, o mesmo para WIN e ações. A comparação é no logaritmo.",
            ),
            ParamField(
                "exit",
                "Saída",
                "select",
                "15",
                options=EXIT_MODES,
                help="15 é o núcleo. 30 e eod são extras. eod é o início do leilão do à vista.",
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
                help="A abertura pode atrasar até esse tanto. No timeframe grosso, a entrada também.",
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
        threshold = _threshold(resolved["threshold"])
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
            session_close = at(day, cash_call_start(day), tz)
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

            scheduled = session_open + ENTRY_LAG
            # The 09:01 bar is the entry when it exists. On the WIN the first
            # print is often 09:02 or 09:03, still inside the same edge
            # tolerance used for the opening bar. A later print is a skip.
            entry_limit = scheduled + edge
            entry = _first_between(day_bars, scheduled, entry_limit, session_close)
            if entry is None:
                reason = (
                    f"Janela da operação {span(scheduled, entry_limit)}: sem a barra que começa "
                    f"1 minuto depois da abertura ({hhmm(scheduled)})"
                )
                warnings.append(f"{day.isoformat()}: pregão pulado. {reason}.")
                skipped.append(SkippedSession(session_date=day, window="trade", reason=f"{reason}."))
                continue
            entry_ts = entry["timestamp"]
            timed = exit_mode != "eod"
            if timed:
                target = entry_ts + timedelta(minutes=int(exit_mode))
                if target >= session_close:
                    timed = False
            if timed:
                exit_clock = entry_ts + timedelta(minutes=int(exit_mode))
                window_end = exit_clock
            else:
                exit_clock = session_close
                window_end = session_close
            if window_end <= entry_ts:
                reason = (
                    f"Janela da operação {span(entry_ts, session_close)}: a saída não fica "
                    "depois da entrada"
                )
                warnings.append(f"{day.isoformat()}: pregão pulado. {reason}.")
                skipped.append(SkippedSession(session_date=day, window="trade", reason=f"{reason}."))
                continue
            trade_gap = incomplete_window(
                day_bars["timestamp"], entry_ts, window_end, bar_delta, edge,
                min_coverage, bar_minutes, edge_minutes,
            )
            if trade_gap:
                reason = f"Janela da operação {span(entry_ts, window_end)}: {trade_gap}"
                warnings.append(f"{day.isoformat()}: pregão pulado. {reason}.")
                skipped.append(SkippedSession(session_date=day, window="trade", reason=f"{reason}."))
                continue
            if timed:
                exit_row = _bar_starting_at(day_bars, exit_clock)
                if exit_row is None:
                    reason = (
                        f"Janela da operação {span(entry_ts, exit_clock)}: sem a barra que abre "
                        f"na saída das {hhmm(exit_clock)}"
                    )
                    warnings.append(f"{day.isoformat()}: pregão pulado. {reason}.")
                    skipped.append(SkippedSession(session_date=day, window="trade", reason=f"{reason}."))
                    continue
                exit_price = float(exit_row["open"])
                exit_time = exit_clock.to_pydatetime()
            else:
                exit_row = _bar_ending_at(day_bars, exit_clock, bar_delta)
                if exit_row is None:
                    reason = f"Janela da operação {span(entry_ts, exit_clock)}: sem a barra que fecha na saída"
                    warnings.append(f"{day.isoformat()}: pregão pulado. {reason}.")
                    skipped.append(SkippedSession(session_date=day, window="trade", reason=f"{reason}."))
                    continue
                exit_price = float(exit_row["close"])
                exit_time = exit_clock.to_pydatetime()
            trades.append(
                RawTrade(
                    session_date=day,
                    direction=direction,
                    quantity=quantity,
                    entry_time=entry_ts.to_pydatetime(),
                    exit_time=exit_time,
                    entry_price=float(entry["open"]),
                    exit_price=exit_price,
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


def _bar_starting_at(day_bars: pd.DataFrame, stamp):
    hits = day_bars.loc[day_bars["timestamp"] == stamp]
    if hits.empty:
        return None
    return hits.iloc[0]


def _bar_ending_at(day_bars: pd.DataFrame, exit_clock, bar_delta: timedelta):
    target = exit_clock - bar_delta
    hits = day_bars.loc[day_bars["timestamp"] == target]
    if hits.empty:
        return None
    return hits.iloc[0]
