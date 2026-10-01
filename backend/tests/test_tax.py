from datetime import date

import pandas as pd

from engine.tax import monthly_day_trade_tax


def _series(pairs: list[tuple[date, float]]) -> pd.Series:
    return pd.Series({day: pnl for day, pnl in pairs}, dtype=float)


def test_profitable_month_pays_twenty_percent():
    daily = _series(
        [
            (date(2026, 9, 1), 100.0),
            (date(2026, 9, 2), 50.0),
        ]
    )
    result = monthly_day_trade_tax(daily, 0.20)
    assert result.total_tax == 30.0
    assert result.net_pnl == 120.0
    assert result.months[0].taxable_base == 150.0
    assert result.tax_by_date[date(2026, 9, 2)] == 30.0
    assert result.tax_by_date[date(2026, 9, 1)] == 0.0


def test_loss_month_pays_nothing_and_carries_forward():
    daily = _series(
        [
            (date(2026, 9, 1), 150.0),
            (date(2026, 10, 1), -80.0),
            (date(2026, 11, 3), 100.0),
        ]
    )
    result = monthly_day_trade_tax(daily, 0.20)
    assert [month.tax for month in result.months] == [30.0, 0.0, 4.0]
    assert result.months[1].carry_out == -80.0
    assert result.months[2].taxable_base == 20.0
    assert result.total_tax == 34.0
    assert result.net_pnl == 170.0 - 34.0


def test_loss_offsets_the_next_year():
    daily = _series(
        [
            (date(2026, 12, 28), -100.0),
            (date(2027, 1, 4), 100.0),
        ]
    )
    result = monthly_day_trade_tax(daily, 0.20)
    assert result.total_tax == 0.0
    assert result.net_pnl == 0.0
    assert result.months[1].carry_in == -100.0


def test_flat_month_does_not_consume_a_loss():
    daily = _series(
        [
            (date(2026, 9, 1), -40.0),
            (date(2026, 10, 1), 0.0),
            (date(2026, 11, 2), 40.0),
        ]
    )
    result = monthly_day_trade_tax(daily, 0.20)
    assert result.total_tax == 0.0
    assert result.months[2].taxable_base == 0.0
