"""Price returns that are allowed to cross a session boundary.

A continuous WIN series changes contract over time. A return is only valid
inside one contract. The previous close, when a signal needs it, is the
previous session of that same contract. If that session is missing, the
signal is skipped instead of borrowing the close of another expiry.
"""

from __future__ import annotations

from datetime import date

import pandas as pd


def contracts_of(bars: pd.DataFrame) -> list[str]:
    if bars.empty or "contract" not in bars.columns:
        return []
    found: list[str] = []
    for value in bars["contract"].dropna().unique():
        text = str(value).strip()
        if text and text.lower() != "nan":
            found.append(text)
    return found


def prior_close_same_contract(bars: pd.DataFrame, day: date, contract: str) -> float | None:
    """Last close of `contract` before `day`.

    Bars from any other WIN expiry are ignored. None means the signal that
    needs this close must be skipped.
    """
    if bars.empty or "contract" not in bars.columns:
        return None
    same = bars.loc[bars["contract"].astype(str) == contract]
    previous = same.loc[same["timestamp"].dt.date < day]
    if previous.empty:
        return None
    ordered = previous.sort_values("timestamp")
    close = float(ordered.iloc[-1]["close"])
    if close <= 0:
        return None
    return close


def return_versus_prior_close(bars: pd.DataFrame, day: date, contract: str, price: float) -> float | None:
    """Simple return against the previous close of the same contract.

    None when that close does not exist. Callers skip the signal.
    """
    if price <= 0:
        return None
    previous = prior_close_same_contract(bars, day, contract)
    if previous is None:
        return None
    return price / previous - 1.0
