"""Pre-registered grids. Lengths here are the catalog, not a result."""

from __future__ import annotations

SIGNAL_ANCHORS = ("session_open", "prior_close")
SIGNAL_ENDS = ("session_open", "cash_open")
TRADE_WINDOWS = ("session_close", "before_cash_auction")

MOMENTUM_VARIANTS: list[dict] = [
    {"signal_anchor": anchor, "signal_end": end, "trade_window": window}
    for anchor in SIGNAL_ANCHORS
    for end in SIGNAL_ENDS
    for window in TRADE_WINDOWS
]

GAP_VARIANTS: list[dict] = [{"exit": exit_mode} for exit_mode in ("15", "30", "eod")]

ORB_VARIANTS: list[dict] = [{"range_minutes": minutes} for minutes in (5, 15, 30)]

CATALOG_TRIALS = len(MOMENTUM_VARIANTS) + len(GAP_VARIANTS) + len(ORB_VARIANTS)


def variants_for(strategy_id: str) -> list[dict]:
    if strategy_id == "intraday_momentum":
        return list(MOMENTUM_VARIANTS)
    if strategy_id == "gap_reversal":
        return list(GAP_VARIANTS)
    if strategy_id == "opening_range_breakout":
        return list(ORB_VARIANTS)
    raise ValueError(f"Estratégia sem grade pré-registrada: {strategy_id}.")
