from datetime import date, datetime

import pandas as pd
import pytest

from engine.costs import CostModel, apply_costs
from engine.instruments import EQUITY_FEE_RATE, WIN, resolve_instrument
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
    with pytest.raises(ValueError):
        CostModel(fee_rate=-0.01)


def test_equity_default_is_percentage_fee_plus_one_tick():
    spec = resolve_instrument("PETR4")
    assert spec.tick_size == 0.01
    assert spec.tick_value == pytest.approx(0.01)
    assert spec.point_value == 1.0
    assert spec.default_fee_per_side == 0.0
    assert spec.default_fee_rate == pytest.approx(EQUITY_FEE_RATE)
    assert spec.default_fee_rate == pytest.approx(0.00023)
    assert spec.default_slippage_ticks == 1.0
    assert resolve_instrument("WINQ26").default_fee_rate == 0.0
    assert resolve_instrument("WINQ26").default_fee_per_side == 0.50

    costs = CostModel(
        fee_per_side=spec.default_fee_per_side,
        slippage_ticks=spec.default_slippage_ticks,
        fee_rate=spec.default_fee_rate,
    )
    trade = apply_costs(
        RawTrade(
            session_date=date(2026, 9, 17),
            direction=1,
            quantity=100,
            entry_time=datetime(2026, 9, 17, 16, 25),
            exit_time=datetime(2026, 9, 17, 16, 55),
            entry_price=10.00,
            exit_price=10.50,
            signal_return=0.01,
        ),
        spec,
        costs,
        "PETR4",
    )
    # One tick of R$ 0.01 adverse on each side. Gross is on the raw prices.
    assert trade.entry_price_effective == pytest.approx(10.01)
    assert trade.exit_price_effective == pytest.approx(10.49)
    assert trade.gross_pnl == pytest.approx(50.0)
    assert trade.slippage_cost == pytest.approx(2.0)
    expected_fees = 0.00023 * (10.01 * 100 + 10.49 * 100)
    assert trade.fees == pytest.approx(expected_fees)
    assert trade.pnl == pytest.approx(48.0 - expected_fees)


def test_percentage_fee_is_charged_on_a_short_too():
    spec = resolve_instrument("VALE3")
    costs = CostModel(fee_per_side=0.0, slippage_ticks=0, fee_rate=0.00023)
    trade = apply_costs(_raw(-1, 20.0, 19.0), spec, costs, "VALE3")
    assert trade.fees == pytest.approx(0.00023 * (20.0 + 19.0))
    assert trade.slippage_cost == 0


def test_zero_costs_keep_gross_pnl():
    trade = apply_costs(_raw(1, 100_000, 100_050), WIN, CostModel(0, 0), "WIN")
    assert trade.pnl == pytest.approx(10.0)
    assert trade.fees == 0
    assert trade.slippage_cost == 0
