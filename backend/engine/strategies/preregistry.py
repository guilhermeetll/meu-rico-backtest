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

GAP_THRESHOLDS = (0.005, 0.01, 0.015)

GAP_CORE: list[dict] = [
    {"threshold": threshold, "exit": "15"} for threshold in GAP_THRESHOLDS
]
GAP_EXTRAS: list[dict] = [
    {"threshold": threshold, "exit": exit_mode}
    for exit_mode in ("30", "eod")
    for threshold in GAP_THRESHOLDS
]
GAP_VARIANTS: list[dict] = GAP_CORE + GAP_EXTRAS

ORB_CORE: list[dict] = [{"range_minutes": 5}]
ORB_EXTRAS: list[dict] = [{"range_minutes": minutes} for minutes in (15, 30)]
ORB_VARIANTS: list[dict] = ORB_CORE + ORB_EXTRAS

CORE_PER_INSTRUMENT = len(GAP_CORE) + len(ORB_CORE)
EXTRA_PER_INSTRUMENT = len(GAP_EXTRAS) + len(ORB_EXTRAS)
CATALOG_TRIALS = len(MOMENTUM_VARIANTS) + len(GAP_VARIANTS) + len(ORB_VARIANTS)


def variants_for(strategy_id: str) -> list[dict]:
    if strategy_id == "intraday_momentum":
        return list(MOMENTUM_VARIANTS)
    if strategy_id == "gap_reversal":
        return list(GAP_VARIANTS)
    if strategy_id == "opening_range_breakout":
        return list(ORB_VARIANTS)
    raise ValueError(f"Estratégia sem grade pré-registrada: {strategy_id}.")


def is_core(strategy_id: str, params: dict) -> bool:
    if strategy_id == "gap_reversal":
        return str(params.get("exit", "15")) == "15"
    if strategy_id == "opening_range_breakout":
        return int(params.get("range_minutes", 5)) == 5
    return False
