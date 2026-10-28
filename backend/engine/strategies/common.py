"""Session clocks and bar-coverage checks shared by the strategies.

The coverage rule is the one already used by the momentum strategy: the
start bar may land up to the edge tolerance after the window opens, the end
bar is the one that closes exactly at the window end, and leading minutes
before an accepted start bar count as covered.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pandas as pd

from engine.sessions import cash_session, session_bounds


def clock(value: str) -> time:
    try:
        hour, minute = value.strip().split(":")
        parsed = time(int(hour), int(minute))
    except (ValueError, AttributeError) as exc:
        raise ValueError("Horário deve estar no formato HH:MM ou auto.") from exc
    return parsed


def at(day, clock_value: time, tz) -> pd.Timestamp:
    stamp = pd.Timestamp(datetime.combine(day, clock_value))
    if tz is None:
        return stamp
    return stamp.tz_localize(tz)


def hhmm(stamp) -> str:
    ts = pd.Timestamp(stamp)
    return f"{int(ts.hour):02d}:{int(ts.minute):02d}"


def span(start: pd.Timestamp, end: pd.Timestamp) -> str:
    return f"{hhmm(start)}–{hhmm(end)}"


def minute_key(stamp) -> tuple[int, int, int, int, int]:
    ts = pd.Timestamp(stamp)
    return (int(ts.year), int(ts.month), int(ts.day), int(ts.hour), int(ts.minute))


def _hhmm_key(key: tuple[int, int, int, int, int]) -> str:
    return f"{key[3]:02d}:{key[4]:02d}"


def _pct(value: float) -> str:
    scaled = value * 100
    if abs(scaled - round(scaled)) < 1e-9:
        return f"{int(round(scaled))}%"
    return f"{scaled:.1f}%".replace(".", ",")


def incomplete_window(
    timestamps: pd.Series,
    window_start: pd.Timestamp,
    window_end: pd.Timestamp,
    bar_delta: timedelta,
    edge: timedelta,
    min_coverage: float,
    bar_minutes: int,
    edge_minutes: int,
) -> str | None:
    """Reason the window cannot be used, or None when it passes.

    The start bar may land up to `edge` after the window opens. The end bar
    is the one that closes exactly at `window_end`. Leading minutes before
    an accepted start bar count as covered. Any later hole counts.
    `min_coverage` of 0 keeps the two endpoints and skips the fraction.
    """
    label_start = hhmm(window_start)
    window = window_end - window_start
    if window <= timedelta(0) or window % bar_delta != timedelta(0):
        return (
            f"o timeframe de {bar_minutes} minutos não cabe inteiro "
            f"na janela {label_start}–{hhmm(window_end)}"
        )
    n_slots = window // bar_delta
    expected = [minute_key(window_start + bar_delta * i) for i in range(n_slots)]
    end_key = expected[-1]
    present = {minute_key(ts) for ts in timestamps}
    start_limit = minute_key(window_start + edge)
    start_key = minute_key(window_start)
    window_end_key = minute_key(window_end)
    in_window = [key for key in present if start_key <= key < window_end_key]
    start_hits = [key for key in in_window if key <= start_limit]
    has_start = bool(start_hits)
    has_end = end_key in present
    if has_start:
        first = min(start_hits)
        excused = {key for key in expected if key < first}
    else:
        excused = set()
    covered = len(set(expected) & present | excused)
    ratio = covered / n_slots
    problems: list[str] = []
    if not has_start:
        if in_window:
            problems.append(
                f"sem a barra de início (primeira barra da janela às {_hhmm_key(min(in_window))}, "
                f"fora da tolerância de {edge_minutes} minutos a partir de {label_start})"
            )
        else:
            later = [key for key in present if key >= window_end_key]
            earlier = [key for key in present if key < start_key]
            if later and not earlier:
                problems.append(
                    f"sem a barra de início (primeira barra do pregão às {_hhmm_key(min(present))})"
                )
            elif earlier and not later:
                problems.append(f"sem a barra de início (última barra às {_hhmm_key(max(present))})")
            else:
                problems.append(
                    f"sem a barra de início (tolerância de {edge_minutes} minutos a partir de {label_start})"
                )
    if not has_end:
        before_end = [key for key in present if key < window_end_key]
        if before_end:
            problems.append(
                f"sem a barra de fim {_hhmm_key(end_key)} (última barra às {_hhmm_key(max(before_end))})"
            )
        else:
            problems.append(f"sem a barra de fim {_hhmm_key(end_key)}")
    if min_coverage > 0 and ratio + 1e-9 < min_coverage:
        problems.append(
            f"cobertura {covered}/{n_slots} ({_pct(ratio)}), abaixo de {_pct(min_coverage)}"
        )
    if not problems:
        return None
    return "; ".join(problems)


def contract_of(day_bars) -> str | None:
    if "contract" not in day_bars.columns:
        return None
    value = day_bars["contract"].iloc[0]
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def cash_call_start(day) -> time:
    """When the cash closing call starts.

    Continuous trading ends here for stocks and for the WIN. On an ordinary
    session in the current calendar that is 16:55. Ash Wednesday moves it
    to 17:55. It is not the WIN session close at 18:25.
    """
    _, call_start, _ = cash_session(day)
    return call_start


def first_regular_bar(day_bars: pd.DataFrame, session_open: pd.Timestamp, session_close: pd.Timestamp, tolerance: timedelta):
    """First bar of the regular session, within `tolerance` of the calendar open.

    The auction often prints after the official open. That bar is the open.
    A later print, past the tolerance, is not.
    """
    window = day_bars[
        (day_bars["timestamp"] >= session_open)
        & (day_bars["timestamp"] <= session_open + tolerance)
        & (day_bars["timestamp"] < session_close)
    ]
    if window.empty:
        return None
    return window.sort_values("timestamp").iloc[0]


def missing_open_reason(day_bars: pd.DataFrame, session_open: pd.Timestamp, tolerance: timedelta, tolerance_minutes: int) -> str:
    label = hhmm(session_open)
    limit = session_open + tolerance
    later = day_bars[day_bars["timestamp"] > limit]
    if not later.empty:
        first = later.sort_values("timestamp").iloc[0]["timestamp"]
        return (
            f"Janela do sinal {span(session_open, limit)}: sem o preço de abertura "
            f"(primeira barra às {hhmm(first)}, fora da tolerância de {tolerance_minutes} minutos "
            f"a partir de {label})"
        )
    return (
        f"Janela do sinal {span(session_open, limit)}: sem o preço de abertura "
        f"dentro de {tolerance_minutes} minutos a partir de {label}"
    )


def forced_continuous_exit(day_bars: pd.DataFrame, call_start: pd.Timestamp, tolerance: timedelta, bar_delta: timedelta):
    """Where an open position leaves when continuous trading ends.

    The bar that closes exactly at the call exits at the call. If that bar is
    missing, the last bar that starts inside `tolerance` before the call is
    the exit, and the caller records a warning instead of skipping the day.
    Returns (exit_clock, exit_bar, early). exit_clock is None when nothing
    in the tolerance can close the position.
    """
    exact_start = call_start - bar_delta
    exact = day_bars.loc[day_bars["timestamp"] == exact_start]
    if not exact.empty:
        return call_start, exact.iloc[0], False
    earliest = call_start - tolerance
    candidates = day_bars[(day_bars["timestamp"] >= earliest) & (day_bars["timestamp"] < call_start)]
    if candidates.empty:
        return None, None, False
    row = candidates.sort_values("timestamp").iloc[-1]
    return pd.Timestamp(row["timestamp"]) + bar_delta, row, True


def early_close_warning(day, bar_stamp, call_start) -> str:
    return (
        f"{day.isoformat()}: o contínuo terminou às {hhmm(bar_stamp)}, "
        f"antes do leilão das {hhmm(call_start)}. Saída no último negócio regular."
    )


def regular_close(family: str, day, contract: str | None) -> time:
    """Last instant of the regular session, before the closing auction.

    Equities stop when the cash closing call starts. The WIN calendar does
    not model a separate closing call, so the regular session close is the
    end of the day for that family.
    """
    if family == "WIN":
        _, close_t = session_bounds(family, day, contract)
        return close_t
    _, call_start, _ = cash_session(day)
    return call_start


def coverage_params(resolved: dict) -> tuple[float, int]:
    min_coverage = float(resolved["min_bar_coverage"])
    edge_minutes = int(resolved["edge_tolerance_minutes"])
    if not 0 <= min_coverage <= 1:
        raise ValueError("A cobertura mínima deve estar entre 0 e 1.")
    if edge_minutes < 0:
        raise ValueError("A tolerância da primeira barra não pode ser negativa.")
    return min_coverage, edge_minutes


def clock_tolerances(resolved: dict) -> tuple[int, int]:
    """Minutes the first print may lag the open, and the close may lead the call."""
    open_minutes = int(resolved["open_tolerance_minutes"])
    close_minutes = int(resolved["close_tolerance_minutes"])
    if open_minutes < 0:
        raise ValueError("A tolerância da abertura não pode ser negativa.")
    if close_minutes < 0:
        raise ValueError("A tolerância do fechamento não pode ser negativa.")
    return open_minutes, close_minutes


def quantity_of(resolved: dict) -> int:
    quantity = int(resolved["quantity"])
    if quantity < 1:
        raise ValueError("A quantidade precisa ser pelo menos 1.")
    return quantity
