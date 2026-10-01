from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd


@dataclass
class MonthTax:
    year: int
    month: int
    pnl: float
    carry_in: float
    taxable_base: float
    tax: float
    carry_out: float


@dataclass
class TaxResult:
    total_tax: float
    net_pnl: float
    months: list[MonthTax] = field(default_factory=list)
    tax_by_date: dict[date, float] = field(default_factory=dict)


def monthly_day_trade_tax(daily_pnl: pd.Series, rate: float) -> TaxResult:
    """20% day-trade tax with a simple monthly loss carry-forward.

    The daily series is already net of fees and slippage. Each calendar month
    is summed. A loss stays as a negative carry and offsets later months inside
    this sample. A positive base is taxed and the carry resets. There is no
    swing-trade offset, withholding, or minimum DARF. Carry starts at zero
    because losses before the sample are unknown.
    """
    if rate < 0 or rate > 1:
        raise ValueError("Alíquota de IR deve estar entre 0 e 1.")
    if daily_pnl.empty:
        return TaxResult(total_tax=0.0, net_pnl=0.0)

    ordered = daily_pnl.sort_index()
    period = pd.Series([(d.year, d.month) for d in ordered.index], index=ordered.index)
    carry = 0.0
    total_tax = 0.0
    months: list[MonthTax] = []
    tax_by_date: dict[date, float] = {d: 0.0 for d in ordered.index}

    for key, chunk in ordered.groupby(period, sort=True):
        year, month = int(key[0]), int(key[1])
        month_pnl = round(float(chunk.sum()), 2)
        carry_in = carry
        base = round(month_pnl + carry_in, 2)
        if base > 0:
            tax = round(base * rate, 2)
            carry = 0.0
            taxable = base
        else:
            tax = 0.0
            carry = base
            taxable = 0.0
        last_day = chunk.index[-1]
        tax_by_date[last_day] = tax
        total_tax = round(total_tax + tax, 2)
        months.append(
            MonthTax(
                year=year,
                month=month,
                pnl=month_pnl,
                carry_in=carry_in,
                taxable_base=taxable,
                tax=tax,
                carry_out=carry,
            )
        )

    net = float(ordered.sum()) - total_tax
    return TaxResult(total_tax=total_tax, net_pnl=net, months=months, tax_by_date=tax_by_date)
