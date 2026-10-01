"""Opening-gap reversal, pre-registered in docs/preregistro.md.

Ceretta & Da Costa (2017), Economics Bulletin 37(4). The gap is the simple
return of the first regular print over the previous close of the same
series. With tickercsv prints, t0 is that print's exact timestamp: entry is
the last trade at or before t0 + 1 minute, and a 15 or 30 minute exit is the
last trade at or before the entry time plus that hold. Without individual
trades the fill stays on bars. The core grid exits 15 minutes after the
entry. Thirty minutes and the end of continuous trading are extras.
"""

from __future__ import annotations

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
    clock_tolerances,
    contract_of,
    coverage_params,
    early_close_warning,
    first_regular_bar,
    forced_continuous_exit,
    hhmm,
    incomplete_window,
    missing_open_reason,
    quantity_of,
    span,
    unique,
)
from engine.trades import as_lookup, first_regular_trade, last_trade_until

EXIT_MODES = ("15", "30", "eod")
GAP_THRESHOLDS = (0.005, 0.01, 0.015)
ENTRY_LAG = timedelta(minutes=1)
# A price ratio of exactly 0.5% is a binary fraction. This keeps it on the threshold.
_GAP_EPSILON = 1e-9

BAR_FILL_NOTE = (
    "Preenchimento do gap: aproximação por barra. "
    "A entrada é o open da primeira barra que começa pelo menos 1 minuto depois do primeiro negócio, "
    "e a saída de 15 ou 30 minutos é o open da barra que começa nesse horário."
)
TICK_FILL_NOTE = (
    "Preenchimento do gap: último negócio do tickercsv até o prazo. "
    "t0 é o timestamp do primeiro negócio. A entrada é o último negócio até t0 + 1 minuto, "
    "e a saída de 15 ou 30 minutos é o último negócio até o horário da entrada mais esse prazo."
)


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
        "Gap = abertura / fechamento anterior − 1. Os limiares são 0,5%, 1% "
        "e 1,5%, iguais para WIN e ações. Com negócios do tickercsv, a entrada "
        "é o último negócio até 1 minuto depois do primeiro, e a saída de 15 ou "
        "30 minutos é o último negócio até a entrada mais esse prazo. Sem esses "
        "negócios, a entrada é a primeira barra que começa pelo menos 1 minuto "
        "depois, nunca a barra da abertura. O núcleo sai 15 minutos depois da "
        "entrada; 30 minutos e o fim do contínuo são extras."
    )

    def param_schema(self) -> list[ParamField]:
        return [
            ParamField(
                "threshold",
                "Limiar",
                "select",
                0.005,
                options=("0.005", "0.01", "0.015"),
                help="0,5%, 1% ou 1,5%, o mesmo para WIN e ações. A comparação é no retorno simples.",
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
                help="Dentro da janela já ancorada no primeiro negócio, a barra de início pode atrasar até esse tanto.",
            ),
            ParamField(
                "open_tolerance_minutes",
                "Tolerância da abertura (min)",
                "int",
                30,
                min=0,
                max=180,
                help="O primeiro negócio pode sair até esse tanto depois da abertura do calendário. Depois disso o pregão é pulado.",
            ),
            ParamField(
                "close_tolerance_minutes",
                "Tolerância do fechamento (min)",
                "int",
                15,
                min=0,
                max=120,
                help="Se a barra do call não existe, a saída é o último negócio regular dentro desse intervalo antes do leilão.",
            ),
        ]

    def generate(
        self,
        bars,
        params: dict,
        instrument: InstrumentSpec,
        bar_minutes: int,
        trades=None,
    ) -> tuple[list[RawTrade], list[str], list[SkippedSession]]:
        return self.generate_many(bars, [params], instrument, bar_minutes, as_lookup(trades))[0]

    def generate_many(self, bars, variants: list[dict], instrument: InstrumentSpec, bar_minutes: int, lookup):
        """One pass over the sessions. Every variant sees the same day of prints."""
        runs = [self._begin(params, instrument, bar_minutes, lookup is not None) for params in variants]
        frame = bars.sort_values("timestamp")
        tz = frame["timestamp"].dt.tz
        for day, day_bars in frame.groupby(frame["timestamp"].dt.date, sort=True):
            series = contracts_of(day_bars)
            contract = series[0] if len(series) == 1 else None
            day_trades = None
            if lookup is not None and len(series) <= 1:
                day_trades = lookup.get(day, contract)
            for run in runs:
                self._on_day(run, frame, day, day_bars, tz, day_trades)
        return [self._end(run) for run in runs]

    def _begin(self, params: dict, instrument: InstrumentSpec, bar_minutes: int, ticks_available: bool) -> dict:
        resolved = self.resolved_params(params)
        open_minutes, close_minutes = clock_tolerances(resolved)
        min_coverage, edge_minutes = coverage_params(resolved)
        return {
            "instrument": instrument,
            "exit_mode": _exit_mode(resolved["exit"]),
            "threshold": _threshold(resolved["threshold"]),
            "quantity": quantity_of(resolved),
            "min_coverage": min_coverage,
            "edge_minutes": edge_minutes,
            "open_minutes": open_minutes,
            "bar_minutes": bar_minutes,
            "bar_delta": timedelta(minutes=bar_minutes),
            "edge": timedelta(minutes=edge_minutes),
            "open_tolerance": timedelta(minutes=open_minutes),
            "close_tolerance": timedelta(minutes=close_minutes),
            "warnings": [],
            "skipped": [],
            "trades": [],
            "ticks_available": ticks_available,
            "saw_ticks": False,
            "used_bars": False,
            "fallback_days": [],
        }

    def _end(self, run: dict):
        if run["ticks_available"] and run["saw_ticks"]:
            run["warnings"].append(TICK_FILL_NOTE)
        if run["fallback_days"]:
            run["warnings"].append(
                "Preenchimento do gap: sem negócio individual em "
                + ", ".join(run["fallback_days"])
                + ". Nesses pregões a entrada usou a aproximação por barra."
            )
        if run["used_bars"] and not run["saw_ticks"]:
            run["warnings"].append(BAR_FILL_NOTE)
        return run["trades"], unique(run["warnings"]), run["skipped"]

    def _on_day(self, run: dict, frame, day, day_bars, tz, day_trades) -> None:
        instrument = run["instrument"]
        warnings = run["warnings"]
        skipped = run["skipped"]
        series = contracts_of(day_bars)
        if len(series) > 1:
            warnings.append(
                f"{day.isoformat()}: o pregão mistura {', '.join(series)}. "
                "Sinal pulado para não transformar o salto entre contratos em retorno."
            )
            return
        contract = series[0] if series else contract_of(day_bars)
        open_t, _ = session_bounds(instrument.family, day, contract)
        session_open = at(day, open_t, tz)
        session_close = at(day, cash_call_start(day), tz)
        open_tolerance = run["open_tolerance"]
        opening = first_regular_bar(day_bars, session_open, session_close, open_tolerance)
        if opening is None:
            reason = missing_open_reason(day_bars, session_open, open_tolerance, run["open_minutes"])
            warnings.append(f"{day.isoformat()}: pregão pulado. {reason}")
            skipped.append(SkippedSession(session_date=day, window="signal", reason=f"{reason}."))
            return
        anchor = first_regular_trade(day_trades, session_open, session_close, open_tolerance)
        if anchor is not None:
            run["saw_ticks"] = True
            open_price = float(anchor["price"])
            open_ts = pd.Timestamp(anchor["timestamp"])
        else:
            run["used_bars"] = True
            open_price = float(opening["open"])
            open_ts = pd.Timestamp(opening["timestamp"])
        if open_price <= 0:
            warnings.append(f"{day.isoformat()}: abertura sem preço positivo. Sinal pulado.")
            return
        prior = previous_close(frame, day, contract)
        if prior is None:
            if contract:
                warnings.append(
                    f"{day.isoformat()}: sem fechamento anterior de {contract}. "
                    "Sinal pulado para não usar o fechamento de outro vencimento."
                )
            else:
                warnings.append(f"{day.isoformat()}: sem fechamento anterior. Sinal pulado.")
            return
        gap = open_price / prior - 1.0
        threshold = run["threshold"]
        if gap <= -threshold + _GAP_EPSILON:
            direction = 1
        elif gap >= threshold - _GAP_EPSILON:
            direction = -1
        else:
            return

        if anchor is not None:
            day_trades = day_trades.sort_values("timestamp", kind="mergesort")
            filled = _fill_from_trades(
                run, day, day_bars, day_trades, open_ts, session_close, direction, gap,
            )
        else:
            if run["ticks_available"]:
                run["fallback_days"].append(day.isoformat())
            filled = _fill_from_bars(
                run, day, day_bars, open_ts, session_close, direction, gap,
            )
        if filled is not None:
            run["trades"].append(filled)


def _fill_from_trades(run, day, day_bars, day_trades, open_ts, session_close, direction, gap):
    """Validator convention: last print at or before each deadline."""
    warnings = run["warnings"]
    skipped = run["skipped"]
    entry = last_trade_until(day_trades, open_ts, open_ts + ENTRY_LAG, session_close)
    if entry is None:
        reason = (
            f"Janela da operação até {hhmm(open_ts + ENTRY_LAG)}: sem negócio até "
            "1 minuto depois do primeiro"
        )
        warnings.append(f"{day.isoformat()}: pregão pulado. {reason}.")
        skipped.append(SkippedSession(session_date=day, window="trade", reason=f"{reason}."))
        return None
    entry_ts = pd.Timestamp(entry["timestamp"])
    entry_price = float(entry["price"])
    exit_mode = run["exit_mode"]
    timed = exit_mode != "eod"
    if timed:
        target = entry_ts + timedelta(minutes=int(exit_mode))
        if target >= session_close:
            timed = False
    if timed:
        exit_row = last_trade_until(day_trades, entry_ts, target, session_close)
        if exit_row is None:
            reason = (
                f"Janela da operação {span(entry_ts, target)}: sem negócio até a saída"
            )
            warnings.append(f"{day.isoformat()}: pregão pulado. {reason}.")
            skipped.append(SkippedSession(session_date=day, window="trade", reason=f"{reason}."))
            return None
        exit_price = float(exit_row["price"])
        exit_time = pd.Timestamp(exit_row["timestamp"]).to_pydatetime()
    else:
        exit_clock, early_exit, _early = forced_continuous_exit(
            day_bars, session_close, run["close_tolerance"], run["bar_delta"],
        )
        if exit_clock is None or early_exit is None:
            reason = f"Janela da operação {span(entry_ts, session_close)}: sem a barra que fecha na saída"
            warnings.append(f"{day.isoformat()}: pregão pulado. {reason}.")
            skipped.append(SkippedSession(session_date=day, window="trade", reason=f"{reason}."))
            return None
        if exit_clock <= entry_ts:
            reason = (
                f"Janela da operação {span(entry_ts, session_close)}: a saída não fica "
                "depois da entrada"
            )
            warnings.append(f"{day.isoformat()}: pregão pulado. {reason}.")
            skipped.append(SkippedSession(session_date=day, window="trade", reason=f"{reason}."))
            return None
        exit_row = last_trade_until(day_trades, entry_ts, exit_clock, session_close)
        if exit_row is None:
            reason = f"Janela da operação {span(entry_ts, exit_clock)}: sem negócio até a saída"
            warnings.append(f"{day.isoformat()}: pregão pulado. {reason}.")
            skipped.append(SkippedSession(session_date=day, window="trade", reason=f"{reason}."))
            return None
        exit_price = float(exit_row["price"])
        exit_time = pd.Timestamp(exit_row["timestamp"]).to_pydatetime()
        if exit_clock != session_close:
            warnings.append(early_close_warning(day, early_exit["timestamp"], session_close))
    return RawTrade(
        session_date=day,
        direction=direction,
        quantity=run["quantity"],
        entry_time=entry_ts.to_pydatetime(),
        exit_time=exit_time,
        entry_price=entry_price,
        exit_price=exit_price,
        signal_return=gap,
    )


def _fill_from_bars(run, day, day_bars, open_ts, session_close, direction, gap):
    """Bar approximation used when the individual prints are not available."""
    warnings = run["warnings"]
    skipped = run["skipped"]
    entry_from = open_ts + ENTRY_LAG
    entry = _first_from(day_bars, entry_from, session_close)
    if entry is None or pd.Timestamp(entry["timestamp"]) <= open_ts:
        reason = (
            f"Janela da operação a partir de {hhmm(entry_from)}: sem a barra que começa "
            f"pelo menos 1 minuto depois do primeiro negócio ({hhmm(open_ts)})"
        )
        warnings.append(f"{day.isoformat()}: pregão pulado. {reason}.")
        skipped.append(SkippedSession(session_date=day, window="trade", reason=f"{reason}."))
        return None
    entry_ts = pd.Timestamp(entry["timestamp"])
    exit_mode = run["exit_mode"]
    timed = exit_mode != "eod"
    if timed:
        target = entry_ts + timedelta(minutes=int(exit_mode))
        if target >= session_close:
            timed = False
    early_exit = None
    if timed:
        exit_clock = entry_ts + timedelta(minutes=int(exit_mode))
        window_end = exit_clock
    else:
        exit_clock, early_exit, _early = forced_continuous_exit(
            day_bars, session_close, run["close_tolerance"], run["bar_delta"],
        )
        window_end = exit_clock if exit_clock is not None else session_close
    if window_end <= entry_ts:
        reason = (
            f"Janela da operação {span(entry_ts, session_close)}: a saída não fica "
            "depois da entrada"
        )
        warnings.append(f"{day.isoformat()}: pregão pulado. {reason}.")
        skipped.append(SkippedSession(session_date=day, window="trade", reason=f"{reason}."))
        return None
    trade_gap = incomplete_window(
        day_bars["timestamp"], entry_ts, window_end, run["bar_delta"], run["edge"],
        run["min_coverage"], run["bar_minutes"], run["edge_minutes"],
    )
    if trade_gap:
        reason = f"Janela da operação {span(entry_ts, window_end)}: {trade_gap}"
        warnings.append(f"{day.isoformat()}: pregão pulado. {reason}.")
        skipped.append(SkippedSession(session_date=day, window="trade", reason=f"{reason}."))
        return None
    if timed:
        exit_row = _bar_starting_at(day_bars, exit_clock)
        if exit_row is None:
            reason = (
                f"Janela da operação {span(entry_ts, exit_clock)}: sem a barra que abre "
                f"na saída das {hhmm(exit_clock)}"
            )
            warnings.append(f"{day.isoformat()}: pregão pulado. {reason}.")
            skipped.append(SkippedSession(session_date=day, window="trade", reason=f"{reason}."))
            return None
        exit_price = float(exit_row["open"])
        exit_time = exit_clock.to_pydatetime()
    else:
        if early_exit is None:
            reason = f"Janela da operação {span(entry_ts, session_close)}: sem a barra que fecha na saída"
            warnings.append(f"{day.isoformat()}: pregão pulado. {reason}.")
            skipped.append(SkippedSession(session_date=day, window="trade", reason=f"{reason}."))
            return None
        exit_price = float(early_exit["close"])
        exit_time = exit_clock.to_pydatetime()
        if exit_clock != session_close:
            warnings.append(early_close_warning(day, early_exit["timestamp"], session_close))
    return RawTrade(
        session_date=day,
        direction=direction,
        quantity=run["quantity"],
        entry_time=entry_ts.to_pydatetime(),
        exit_time=exit_time,
        entry_price=float(entry["open"]),
        exit_price=exit_price,
        signal_return=gap,
    )


def _first_from(day_bars: pd.DataFrame, start, session_close):
    window = day_bars[
        (day_bars["timestamp"] >= start)
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
