"""Opening-range breakout, the control strategy in docs/preregistro.md.

The range is the high and the low from the first regular print through the
next N minutes, N in {5, 15, 30}. The core buys only above the high and
sells only below the low. Touching the edge is not an entry. On OHLC bars
the raw fill is one tick beyond that edge, unless the bar already opened
further out, and the cost model then adds another tick of slippage. A
tickercsv print is used as-is and only the cost-model tick is added. The
stop still fires on a touch. `execution=confirm` keeps the previous rule
(a close outside the range, filled on the next bar's open) as the extra
named orb_confirm. There is no target. Whatever is left exits at the end of
continuous trading.
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
    clock_tolerances,
    contract_of,
    coverage_params,
    early_close_warning,
    first_regular_bar,
    forced_continuous_exit,
    incomplete_window,
    missing_open_reason,
    quantity_of,
    span,
    unique,
)

RANGE_MINUTES = (5, 15, 30)
EXECUTIONS = ("stop", "confirm")

OHLC_FILL_NOTE = (
    "Preenchimento do ORB: aproximação OHLC. "
    "A compra dispara se a máxima da barra passa da máxima da faixa, e a venda se a mínima fica abaixo da mínima. "
    "Encostar na borda não entra. "
    "O preço cru é o maior entre a abertura e a borda mais 1 tick na compra, "
    "ou o menor entre a abertura e a borda menos 1 tick na venda, "
    "e o modelo de custos soma mais 1 tick de slippage. "
    "O stop dispara quando o preço encosta ou atravessa o outro extremo, com 1 tick de slippage. "
    "Se a barra da entrada também toca o stop, a saída é nessa mesma barra. "
    "Corretagem e emolumentos não mudam."
)
TICK_FILL_NOTE = (
    "Preenchimento do ORB: primeiro negócio do tickercsv estritamente fora da faixa, mais 1 tick de slippage. "
    "Encostar na borda não entra. "
    "O stop dispara no primeiro negócio que encosta ou atravessa o outro extremo, com 1 tick de slippage. "
    "Corretagem e emolumentos não mudam."
)


def _range_minutes(value) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("A faixa de abertura deve ter 5, 15 ou 30 minutos.") from exc
    if minutes not in RANGE_MINUTES:
        raise ValueError("A faixa de abertura deve ter 5, 15 ou 30 minutos.")
    return minutes


def _execution(value) -> str:
    mode = str(value or "stop").strip().lower()
    if mode not in EXECUTIONS:
        raise ValueError("A execução do ORB deve ser stop ou confirm.")
    return mode


class OpeningRangeBreakoutStrategy(Strategy):
    id = "opening_range_breakout"
    label = "Rompimento da faixa de abertura"
    description = (
        "A faixa começa no primeiro negócio e dura 5, 15 ou 30 minutos. "
        "No núcleo, a compra só dispara acima da máxima da faixa e a venda só abaixo da mínima. "
        "Encostar na borda não entra. O stop fica no outro extremo e dispara ao encostar. "
        "orb_confirm espera o fechamento fora da faixa e entra na abertura da barra seguinte. "
        "Sem alvo: o que não parar sai no fim do contínuo, às 16:55 no pregão ordinário, "
        "ou no último negócio regular se o pregão parar antes. "
        "No máximo uma operação por dia."
    )

    def param_schema(self) -> list[ParamField]:
        return [
            ParamField(
                "range_minutes",
                "Faixa (minutos)",
                "select",
                5,
                options=("5", "15", "30"),
                help="Minutos a partir do primeiro negócio. A grade é 5, 15 e 30.",
            ),
            ParamField(
                "execution",
                "Execução",
                "select",
                "stop",
                options=("stop", "confirm"),
                help="stop é a ordem na borda (núcleo na faixa de 5). confirm é a variante orb_confirm.",
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
        resolved = self.resolved_params(params)
        range_minutes = _range_minutes(resolved["range_minutes"])
        execution = _execution(resolved["execution"])
        quantity = quantity_of(resolved)
        min_coverage, edge_minutes = coverage_params(resolved)
        open_minutes, close_minutes = clock_tolerances(resolved)
        bar_delta = timedelta(minutes=bar_minutes)
        edge = timedelta(minutes=edge_minutes)
        open_tolerance = timedelta(minutes=open_minutes)
        close_tolerance = timedelta(minutes=close_minutes)
        use_ticks = trades is not None and len(trades) > 0 and execution == "stop"
        warnings: list[str] = []
        skipped: list[SkippedSession] = []
        raw_trades: list[RawTrade] = []
        ohlc_fallback_days: list[str] = []

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
            opening = first_regular_bar(ordered, session_open, session_close, open_tolerance)
            if opening is None:
                reason = missing_open_reason(ordered, session_open, open_tolerance, open_minutes)
                warnings.append(f"{day.isoformat()}: pregão pulado. {reason}")
                skipped.append(SkippedSession(session_date=day, window="signal", reason=f"{reason}."))
                continue
            range_start = pd.Timestamp(opening["timestamp"])
            range_end = range_start + timedelta(minutes=range_minutes)
            if range_end >= session_close:
                warnings.append(
                    f"{day.isoformat()}: a faixa de {range_minutes} minutos não cabe antes do fechamento."
                )
                continue
            exit_clock, exit_bar, early = forced_continuous_exit(
                ordered, session_close, close_tolerance, bar_delta,
            )
            trade_end = exit_clock if exit_clock is not None else session_close
            gaps = []
            signal_gap = incomplete_window(
                ordered["timestamp"], range_start, range_end, bar_delta, edge,
                min_coverage, bar_minutes, edge_minutes,
            )
            if signal_gap:
                gaps.append(f"Janela do sinal {span(range_start, range_end)}: {signal_gap}")
            trade_gap = incomplete_window(
                ordered["timestamp"], range_end, trade_end, bar_delta, edge,
                min_coverage, bar_minutes, edge_minutes,
            )
            if trade_gap:
                gaps.append(f"Janela da operação {span(range_end, trade_end)}: {trade_gap}")
            if gaps:
                warnings.append(f"{day.isoformat()}: pregão pulado. {'. '.join(gaps)}.")
                if signal_gap:
                    skipped.append(SkippedSession(
                        session_date=day,
                        window="signal",
                        reason=f"Janela do sinal {span(range_start, range_end)}: {signal_gap}.",
                    ))
                if trade_gap:
                    skipped.append(SkippedSession(
                        session_date=day,
                        window="trade",
                        reason=f"Janela da operação {span(range_end, trade_end)}: {trade_gap}.",
                    ))
                continue

            ranged = ordered[(ordered["timestamp"] >= range_start) & (ordered["timestamp"] < range_end)]
            if ranged.empty:
                continue
            range_high = float(ranged["high"].max())
            range_low = float(ranged["low"].min())
            if range_high <= 0 or range_low <= 0 or range_high < range_low:
                warnings.append(f"{day.isoformat()}: faixa de abertura sem preço válido. Sinal pulado.")
                continue

            after = ordered[(ordered["timestamp"] >= range_end) & (ordered["timestamp"] < session_close)]
            if execution == "confirm":
                trade = _first_breakout(
                    after, range_high, range_low, trade_end, bar_delta, quantity, day,
                )
            else:
                day_trades = _session_trades(trades, day, contract) if use_ticks else None
                if day_trades is not None:
                    trade = _stop_order_from_trades(
                        day_trades, range_high, range_low, range_end, trade_end, quantity, day,
                    )
                else:
                    if use_ticks:
                        ohlc_fallback_days.append(day.isoformat())
                    trade = _stop_order(
                        after, range_high, range_low, trade_end, bar_delta, quantity, day,
                        instrument.tick_size,
                    )
            if trade is not None:
                if (
                    early
                    and exit_bar is not None
                    and pd.Timestamp(trade.exit_time) == pd.Timestamp(trade_end)
                    and float(trade.exit_price) == float(exit_bar["close"])
                ):
                    warnings.append(early_close_warning(day, exit_bar["timestamp"], session_close))
                raw_trades.append(trade)
        if execution == "stop":
            if use_ticks:
                warnings.append(TICK_FILL_NOTE)
                if ohlc_fallback_days:
                    warnings.append(
                        "Preenchimento do ORB: sem negócio individual em "
                        + ", ".join(ohlc_fallback_days)
                        + ". Nesses pregões a entrada usou a aproximação OHLC."
                    )
            else:
                warnings.append(OHLC_FILL_NOTE)
        return raw_trades, unique(warnings), skipped


def _session_trades(trades, day, contract: str | None):
    if trades is None or len(trades) == 0:
        return None
    frame = trades
    if contract and "contract" in frame.columns:
        wanted = str(contract).upper()
        frame = frame[frame["contract"].astype(str).str.upper() == wanted]
    if frame.empty:
        return None
    mask = frame["timestamp"].dt.date == day
    day_frame = frame.loc[mask]
    if day_frame.empty:
        return None
    return day_frame.sort_values("timestamp", kind="mergesort")


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


def _boundary_fill(opened: float, level: float, *, upper: bool) -> float:
    """Edge if the bar opened inside or on it; the open if the bar gapped through."""
    if upper:
        return level if opened <= level else opened
    return level if opened >= level else opened


def _entry_fill(opened: float, level: float, tick: float, *, upper: bool) -> float:
    """First price strictly outside the edge, unless the bar already opened beyond it."""
    if upper:
        return max(opened, level + tick)
    return min(opened, level - tick)


def _breakout(opened: float, high: float, low: float, range_high: float, range_low: float, tick: float):
    """Return (direction, entry, stop_level_or_fill, same_bar) or None.

    A buy needs the high strictly above the range. A sell needs the low
    strictly below it. Touching the edge is not an entry. The stop still
    fires on a touch. same_bar means that stop is on the entry bar.
    An inside open that leaves both sides is a long: the larger notional.
    """
    up = high > range_high
    down = low < range_low
    if not up and not down:
        return None
    if up and down:
        if opened > range_high:
            return 1, _entry_fill(opened, range_high, tick, upper=True), _boundary_fill(opened, range_low, upper=False), True
        if opened < range_low:
            return -1, _entry_fill(opened, range_low, tick, upper=False), _boundary_fill(opened, range_high, upper=True), True
        if opened == range_low and opened < range_high:
            return -1, _entry_fill(opened, range_low, tick, upper=False), range_high, True
        return (
            1,
            _entry_fill(opened, range_high, tick, upper=True),
            _boundary_fill(opened, range_low, upper=False),
            True,
        )
    if up:
        entry = _entry_fill(opened, range_high, tick, upper=True)
        if low <= range_low:
            return 1, entry, _boundary_fill(opened, range_low, upper=False), True
        return 1, entry, range_low, False
    entry = _entry_fill(opened, range_low, tick, upper=False)
    if high >= range_high:
        return -1, entry, _boundary_fill(opened, range_high, upper=True), True
    return -1, entry, range_high, False


def _stop_order(after: pd.DataFrame, range_high: float, range_low: float, trade_end, bar_delta, quantity: int, day, tick: float):
    rows = list(after.sort_values("timestamp").to_dict("records"))
    for index, row in enumerate(rows):
        decision = _breakout(
            float(row["open"]), float(row["high"]), float(row["low"]), range_high, range_low, tick,
        )
        if decision is None:
            continue
        direction, entry_price, stop, same_bar = decision
        stamp = pd.Timestamp(row["timestamp"])
        level = range_high if direction == 1 else range_low
        if same_bar:
            exit_price = stop
            exit_time = stamp + bar_delta
        else:
            later = rows[index + 1 :]
            fill = _stop_or_close(later, direction, stop, trade_end, bar_delta) if later else None
            if fill is None:
                if stamp + bar_delta == pd.Timestamp(trade_end):
                    exit_price = float(row["close"])
                    exit_time = stamp + bar_delta
                else:
                    return None
            else:
                exit_price, exit_time = fill
        if entry_price <= 0:
            return None
        signal = entry_price / level - 1.0 if direction == 1 else level / entry_price - 1.0
        return RawTrade(
            session_date=day,
            direction=direction,
            quantity=quantity,
            entry_time=stamp.to_pydatetime(),
            exit_time=pd.Timestamp(exit_time).to_pydatetime(),
            entry_price=entry_price,
            exit_price=exit_price,
            signal_return=signal,
        )
    return None


def _stop_order_from_trades(
    day_trades: pd.DataFrame,
    range_high: float,
    range_low: float,
    range_end,
    trade_end,
    quantity: int,
    day,
):
    """First print strictly outside the range. The stop is the next print that touches the other side."""
    end = pd.Timestamp(trade_end)
    start = pd.Timestamp(range_end)
    stamps = day_trades["timestamp"]
    selected = day_trades[(stamps >= start) & (stamps < end)]
    records = list(selected.to_dict("records"))
    for index, row in enumerate(records):
        price = float(row["price"])
        if price > range_high:
            direction = 1
            level = range_high
            stop = range_low
        elif price < range_low:
            direction = -1
            level = range_low
            stop = range_high
        else:
            continue
        exit_price = None
        exit_time = None
        for later in records[index + 1 :]:
            later_price = float(later["price"])
            hit = (direction == 1 and later_price <= stop) or (direction == -1 and later_price >= stop)
            if hit:
                exit_price = later_price
                exit_time = pd.Timestamp(later["timestamp"])
                break
        if exit_price is None:
            exit_price = float(records[-1]["price"])
            exit_time = end
        signal = price / level - 1.0 if direction == 1 else level / price - 1.0
        return RawTrade(
            session_date=day,
            direction=direction,
            quantity=quantity,
            entry_time=pd.Timestamp(row["timestamp"]).to_pydatetime(),
            exit_time=pd.Timestamp(exit_time).to_pydatetime(),
            entry_price=price,
            exit_price=exit_price,
            signal_return=signal,
        )
    return None


def _stop_or_close(path: list[dict], direction: int, stop: float, session_close, bar_delta):
    """Stop first. A bar that opens through the stop fills at the open."""
    if not path:
        return None
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
