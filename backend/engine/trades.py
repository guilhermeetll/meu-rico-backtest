"""One lookup of the prints for a session, shared by every variant.

A month of WIN trades does not fit comfortably in memory if each variant
filters the whole frame again. A DataFrame is indexed once. A lazy source
loads a single session when asked and keeps only that session.
"""

from __future__ import annotations

import pandas as pd


class IndexedTrades:
    def __init__(self, frame: pd.DataFrame):
        ordered = frame.sort_values("timestamp", kind="mergesort")
        days = ordered["timestamp"].dt.date
        self._groups: dict[tuple, pd.DataFrame] = {}
        if "contract" in ordered.columns:
            contracts = ordered["contract"].astype(str).str.upper()
            grouped = ordered.groupby([days, contracts], sort=False)
            for key, group in grouped:
                self._groups[(key[0], key[1])] = group
        else:
            for day, group in ordered.groupby(days, sort=False):
                self._groups[(day, None)] = group

    def get(self, day, contract):
        if contract:
            hit = self._groups.get((day, str(contract).upper()))
            if hit is not None and not hit.empty:
                return hit
        hit = self._groups.get((day, None))
        if hit is not None and not hit.empty:
            return hit
        matches = [group for (session, _), group in self._groups.items() if session == day]
        if len(matches) == 1 and not matches[0].empty:
            return matches[0]
        return None


def as_lookup(trades):
    """DataFrame becomes an index. An object with get() is already a lookup."""
    if trades is None:
        return None
    if isinstance(trades, pd.DataFrame):
        if trades.empty:
            return None
        return IndexedTrades(trades)
    if hasattr(trades, "get"):
        return trades
    return None


def first_regular_trade(day_trades: pd.DataFrame, session_open, session_close, tolerance):
    """First print of the regular session, within `tolerance` of the open."""
    if day_trades is None or len(day_trades) == 0:
        return None
    limit = session_open + tolerance
    stamps = day_trades["timestamp"]
    window = day_trades[(stamps >= session_open) & (stamps <= limit) & (stamps < session_close)]
    if window.empty:
        return None
    return window.sort_values("timestamp", kind="mergesort").iloc[0]


def price_as_of(day_trades: pd.DataFrame, instant, not_before=None) -> float | None:
    """Last print at or before `instant`.

    `not_before` drops earlier sessions. The instant itself counts. None means
    every print is outside that interval, and the caller keeps the bar price.
    The frame is already ordered by timestamp.
    """
    if day_trades is None or len(day_trades) == 0 or "price" not in day_trades.columns:
        return None
    stamps = day_trades["timestamp"]
    selected = day_trades[stamps <= instant]
    if not_before is not None:
        selected = selected[selected["timestamp"] >= not_before]
    if selected.empty:
        return None
    return float(selected.iloc[-1]["price"])


def last_trade_until(day_trades: pd.DataFrame, start, deadline, session_close):
    """Last print in [start, deadline], still inside the continuous session.

    The frame is already ordered by timestamp. The deadline is inclusive,
    which is the validator's 'até'.
    """
    if day_trades is None or len(day_trades) == 0:
        return None
    stamps = day_trades["timestamp"]
    selected = day_trades[(stamps >= start) & (stamps <= deadline) & (stamps < session_close)]
    if selected.empty:
        return None
    return selected.iloc[-1]
