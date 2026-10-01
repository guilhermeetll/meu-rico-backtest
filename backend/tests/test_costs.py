from datetime import date, datetime

import pandas as pd
import pytest

from engine.costs import CostModel, apply_costs
from engine.instruments import WIN, resolve_instrument
from engine.models import RawTrade


def _raw(direction: int, entry: float, exit_price: float) -> RawTrade:
    return RawTrade(
        session_date=date(2026, 9, 17),
        direction=direction,
        quantity=1,
        entry_time=datetime(2026, 9, 17, 17, 55),
        exit_time=datetime(2026, 9, 17, 18, 25),
        entry_price=entry,
        exit_price=exit_price,
        signal_return=0.001,
    )


def test_win_tick_is_one_real():
    spec = resolve_instrument("WINV26")
    assert spec.point_value == 0.20
    assert spec.tick_size == 5
    assert spec.tick_value == pytest.approx(1.0)
    assert WIN.default_fee_per_side == 0.50


def test_round_turn_costs_on_long_and_short():
    costs = CostModel(fee_per_side=0.50, slippage_ticks=1)
    long_trade = apply_costs(_raw(1, 100_000, 100_010), WIN, costs, "WIN")
    short_trade = apply_costs(_raw(-1, 100_000, 99_990), WIN, costs, "WIN")
    # 10 points * R$ 0.20 = R$ 2 gross. Two ticks of slippage = R$ 2. Two fees = R$ 1.
    assert long_trade.gross_pnl == pytest.approx(2.0)
    assert long_trade.slippage_cost == pytest.approx(2.0)
    assert long_trade.fees == pytest.approx(1.0)
    assert long_trade.pnl == pytest.approx(-1.0)
    assert long_trade.entry_price_effective == pytest.approx(100_005)
    assert long_trade.exit_price_effective == pytest.approx(100_005)
    assert short_trade.pnl == pytest.approx(-1.0)
    assert short_trade.entry_price_effective == pytest.approx(99_995)
    assert short_trade.exit_price_effective == pytest.approx(99_995)


def test_slippage_is_adverse_for_a_losing_short():
    costs = CostModel(fee_per_side=0.50, slippage_ticks=2)
    trade = apply_costs(_raw(-1, 100_000, 100_010), WIN, costs, "WIN")
    # Gross loss R$ 2, plus 2 ticks * 2 sides * R$ 1, plus R$ 1 fees.
    assert trade.gross_pnl == pytest.approx(-2.0)
    assert trade.slippage_cost == pytest.approx(4.0)
    assert trade.fees == pytest.approx(1.0)
    assert trade.pnl == pytest.approx(-7.0)


def test_costs_reject_negative_inputs():
    with pytest.raises(ValueError):
        CostModel(fee_per_side=-1)
    with pytest.raises(ValueError):
        CostModel(slippage_ticks=-0.1)


def test_zero_costs_keep_gross_pnl():
    trade = apply_costs(_raw(1, 100_000, 100_050), WIN, CostModel(0, 0), "WIN")
    assert trade.pnl == pytest.approx(10.0)
    assert trade.fees == 0
    assert trade.slippage_cost == 0
