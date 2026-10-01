from __future__ import annotations

from dataclasses import dataclass

from engine.instruments import InstrumentSpec
from engine.models import RawTrade, Trade


@dataclass(frozen=True)
class CostModel:
    """Per-execution costs. Each fill pays the fee and adverse slippage.

    A round trip has two executions (entry and exit). `fee_per_side` is a
    fixed amount in BRL per contract. `fee_rate` is a fraction of the traded
    notional of that fill (effective price times point value times quantity).
    WIN uses the fixed fee. Equities use the percentage fee. Both can be set
    and are added.
    """

    fee_per_side: float = 0.50
    slippage_ticks: float = 1.0
    fee_rate: float = 0.0

    def __post_init__(self) -> None:
        if self.fee_per_side < 0:
            raise ValueError("Custo por execução não pode ser negativo.")
        if self.slippage_ticks < 0:
            raise ValueError("Slippage não pode ser negativo.")
        if self.fee_rate < 0:
            raise ValueError("Taxa percentual não pode ser negativa.")


def apply_costs(raw: RawTrade, instrument: InstrumentSpec, costs: CostModel, symbol: str) -> Trade:
    slip_points = costs.slippage_ticks * instrument.tick_size
    if raw.direction == 1:
        entry_eff = raw.entry_price + slip_points
        exit_eff = raw.exit_price - slip_points
    elif raw.direction == -1:
        entry_eff = raw.entry_price - slip_points
        exit_eff = raw.exit_price + slip_points
    else:
        raise ValueError("Direção da operação deve ser compra (1) ou venda (-1).")

    point = instrument.point_value * raw.quantity
    gross = (raw.exit_price - raw.entry_price) * raw.direction * point
    effective = (exit_eff - entry_eff) * raw.direction * point
    slippage_cost = gross - effective
    entry_notional = abs(entry_eff) * instrument.point_value * raw.quantity
    exit_notional = abs(exit_eff) * instrument.point_value * raw.quantity
    fees = costs.fee_per_side * raw.quantity * 2 + costs.fee_rate * (entry_notional + exit_notional)
    pnl = effective - fees
    return Trade(
        session_date=raw.session_date,
        symbol=symbol,
        direction=raw.direction,
        quantity=raw.quantity,
        entry_time=raw.entry_time,
        exit_time=raw.exit_time,
        entry_price=raw.entry_price,
        exit_price=raw.exit_price,
        entry_price_effective=entry_eff,
        exit_price_effective=exit_eff,
        gross_pnl=gross,
        fees=fees,
        slippage_cost=slippage_cost,
        pnl=pnl,
        signal_return=raw.signal_return,
    )
