from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InstrumentSpec:
    """Contract economics used to turn price differences into BRL."""

    symbol: str
    family: str
    point_value: float
    tick_size: float
    asset_class: str
    default_fee_per_side: float
    default_fee_rate: float
    default_slippage_ticks: float

    @property
    def tick_value(self) -> float:
        return self.tick_size * self.point_value


# B3 mini Ibovespa future. Point value and tick come from the contract spec:
# each index point is R$ 0.20 per contract and the minimum tick is 5 points,
# so one tick is R$ 1.00 per contract.
WIN = InstrumentSpec(
    symbol="WIN",
    family="WIN",
    point_value=0.20,
    tick_size=5.0,
    asset_class="future",
    default_fee_per_side=0.50,
    default_fee_rate=0.0,
    default_slippage_ticks=1.0,
)

# Cash equities. B3's public tick-size table (January 2021, the reference
# still published for the cash market) sets the stock tick, round lot and
# odd lot, at R$ 0.01. The default cost is an assumption, not a broker
# schedule: 0.023% of traded value per side, plus one tick of slippage.
EQUITY_FEE_RATE = 0.00023
EQUITY = InstrumentSpec(
    symbol="ACAO",
    family="EQUITY",
    point_value=1.0,
    tick_size=0.01,
    asset_class="equity",
    default_fee_per_side=0.0,
    default_fee_rate=EQUITY_FEE_RATE,
    default_slippage_ticks=1.0,
)


def resolve_instrument(symbol: str) -> InstrumentSpec:
    root = symbol.upper().split(".")[0]
    if root.startswith("WIN"):
        return InstrumentSpec(
            symbol=root,
            family="WIN",
            point_value=WIN.point_value,
            tick_size=WIN.tick_size,
            asset_class="future",
            default_fee_per_side=WIN.default_fee_per_side,
            default_fee_rate=WIN.default_fee_rate,
            default_slippage_ticks=WIN.default_slippage_ticks,
        )
    return InstrumentSpec(
        symbol=root,
        family=EQUITY.family,
        point_value=EQUITY.point_value,
        tick_size=EQUITY.tick_size,
        asset_class=EQUITY.asset_class,
        default_fee_per_side=EQUITY.default_fee_per_side,
        default_fee_rate=EQUITY.default_fee_rate,
        default_slippage_ticks=EQUITY.default_slippage_ticks,
    )
