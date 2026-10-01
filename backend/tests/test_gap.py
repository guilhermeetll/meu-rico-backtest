import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from engine.instruments import resolve_instrument
from engine.strategies.gap import GAP_THRESHOLDS, GapReversalStrategy

TZ = ZoneInfo("America/Sao_Paulo")
WIN = resolve_instrument("WIN")
PETR4 = resolve_instrument("PETR4")
DAY = date(2026, 9, 17)
PRIOR = date(2026, 9, 16)
EARLIER = date(2026, 9, 15)


def _stamp(day: date, hour: int, minute: int) -> pd.Timestamp:
    return pd.Timestamp(datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ))


def _bar(day: date, hour: int, minute: int, open_, high, low, close, contract: str | None = None) -> dict:
    row = {
        "timestamp": _stamp(day, hour, minute),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1,
    }
    if contract:
        row["contract"] = contract
    return row


def _flat(day: date, hour: int, minute: int, price: float, contract: str | None = None) -> dict:
    return _bar(day, hour, minute, price, price, price, price, contract)


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _prior(price: float = 100_000, contract: str | None = "WINV26") -> list[dict]:
    """Two flat sessions so the day under test has a same-series prior close.

    The earlier session is itself a valid open, with no close before it, so
    it only produces the 'no prior close' warning and not a coverage skip.
    """
    return [
        _flat(EARLIER, 9, 0, price, contract),
        _flat(EARLIER, 18, 24, price, contract),
        _flat(PRIOR, 9, 0, price, contract),
        _flat(PRIOR, 18, 24, price, contract),
    ]


def _quiet_equity(price: float = 100) -> list[dict]:
    return [
        _flat(EARLIER, 10, 0, price),
        _flat(EARLIER, 16, 54, price),
        _flat(PRIOR, 10, 0, price),
        _flat(PRIOR, 16, 54, price),
    ]


def _generate(rows: list[dict], params: dict | None = None, instrument=WIN, minutes: int = 1):
    merged = {"min_bar_coverage": 0}
    merged.update(params or {})
    return GapReversalStrategy().generate(_frame(rows), merged, instrument, minutes)


def test_thresholds_are_the_same_three_for_every_instrument():
    assert GAP_THRESHOLDS == (0.005, 0.01, 0.015)
    assert math.log(1.005) < 0.005
    assert resolve_instrument("BOVA11").family != "WIN"


def test_gap_above_threshold_sells_and_gap_below_buys():
    above = 100_000 * math.exp(0.006)
    below = 100_000 * math.exp(-0.006)
    short, _, _ = _generate([
        *_prior(),
        _bar(DAY, 9, 0, above, above, above, above, "WINV26"),
        _bar(DAY, 9, 1, 100_400, 100_400, 100_400, 100_400, "WINV26"),
        _flat(DAY, 9, 15, 100_200, "WINV26"),
        _flat(DAY, 9, 16, 100_180, "WINV26"),
    ], {"exit": "15"})
    long, _, _ = _generate([
        *_prior(),
        _bar(DAY, 9, 0, below, below, below, below, "WINV26"),
        _bar(DAY, 9, 1, 99_600, 99_600, 99_600, 99_600, "WINV26"),
        _flat(DAY, 9, 15, 99_700, "WINV26"),
        _flat(DAY, 9, 16, 99_650, "WINV26"),
    ], {"exit": "15"})
    assert short[0].direction == -1
    assert short[0].signal_return == pytest.approx(0.006)
    assert long[0].direction == 1
    assert long[0].signal_return == pytest.approx(-0.006)


def test_gap_inside_the_threshold_does_not_trade():
    inside = 100_000 * math.exp(0.004)
    raw, warnings, skipped = _generate([
        *_prior(),
        _flat(DAY, 9, 0, inside, "WINV26"),
        _flat(DAY, 9, 1, inside, "WINV26"),
        _flat(DAY, 9, 15, inside, "WINV26"),
    ], {"exit": "15"})
    assert raw == []
    assert skipped == []
    assert not any(warning.startswith(DAY.isoformat()) for warning in warnings)


def test_the_same_threshold_applies_to_the_win_and_to_the_stock():
    """ln(1.01) is above 0.5% and below 1% for both families."""
    win_rows = [
        *_prior(100),
        _flat(DAY, 9, 0, 101, "WINV26"),
        _flat(DAY, 9, 1, 101, "WINV26"),
        _flat(DAY, 9, 15, 101, "WINV26"),
        _flat(DAY, 9, 16, 101, "WINV26"),
    ]
    stock_rows = [
        *_quiet_equity(),
        _flat(DAY, 10, 0, 101),
        _flat(DAY, 10, 1, 101),
        _flat(DAY, 10, 15, 101),
        _flat(DAY, 10, 16, 101),
    ]
    win_loose, _, _ = _generate(win_rows, {"exit": "15", "threshold": 0.005}, instrument=WIN)
    stock_loose, _, _ = _generate(stock_rows, {"exit": "15", "threshold": 0.005}, instrument=PETR4)
    win_tight, _, _ = _generate(win_rows, {"exit": "15", "threshold": 0.01}, instrument=WIN)
    stock_tight, _, skipped = _generate(stock_rows, {"exit": "15", "threshold": 0.01}, instrument=PETR4)
    assert len(win_loose) == 1 and len(stock_loose) == 1
    assert win_tight == [] and stock_tight == []
    assert skipped == []


def test_entry_is_the_bar_one_minute_after_the_open_and_exit_is_fifteen_minutes_later():
    rows = [*_prior()]
    for minute in range(0, 17):
        price = 100_800 if minute == 0 else 100_500
        if minute == 1:
            price = 100_250
        if minute == 16:
            price = 100_100
        rows.append(_bar(DAY, 9, minute, price, price, price, price, "WINV26"))
    raw, warnings, skipped = GapReversalStrategy().generate(_frame(rows), {"exit": "15", "threshold": 0.005}, WIN, 1)
    assert not any(warning.startswith(DAY.isoformat()) for warning in warnings)
    assert [item for item in skipped if item.session_date == DAY] == []
    assert len(raw) == 1
    trade = raw[0]
    assert trade.entry_price == 100_250
    assert trade.entry_time.hour == 9 and trade.entry_time.minute == 1
    assert trade.exit_price == 100_100
    assert trade.exit_time.hour == 9 and trade.exit_time.minute == 16
    assert trade.direction == -1


def test_a_missing_09_01_bar_uses_the_next_print_inside_the_tolerance():
    raw, _, skipped = _generate([
        *_prior(),
        _flat(DAY, 9, 0, 100_000 * math.exp(0.006), "WINV26"),
        _flat(DAY, 9, 2, 100_300, "WINV26"),
        _flat(DAY, 9, 16, 100_100, "WINV26"),
        _flat(DAY, 9, 17, 100_050, "WINV26"),
    ], {"exit": "15"})
    assert [item for item in skipped if item.session_date == DAY] == []
    assert len(raw) == 1
    assert raw[0].entry_time.hour == 9 and raw[0].entry_time.minute == 2
    assert raw[0].entry_price == 100_300
    assert raw[0].exit_time.minute == 17
    assert raw[0].exit_price == 100_050
    late, _, late_skipped = _generate([
        *_prior(),
        _flat(DAY, 9, 0, 100_000 * math.exp(0.006), "WINV26"),
        _flat(DAY, 9, 7, 100_300, "WINV26"),
    ], {"exit": "15"})
    assert late == []
    assert any(item.window == "trade" and item.session_date == DAY for item in late_skipped)


def test_five_minute_bars_skip_the_opening_bar_and_enter_on_the_next_one():
    rows = [
        *_quiet_equity(),
        _bar(DAY, 10, 0, 100 * math.exp(0.006), 100 * math.exp(0.006), 100 * math.exp(0.006), 100 * math.exp(0.006)),
        _bar(DAY, 10, 5, 111, 111, 111, 111),
        _bar(DAY, 10, 15, 111, 111, 111, 111),
        _bar(DAY, 10, 20, 112, 112, 112, 112),
    ]
    raw, _, skipped = _generate(rows, {"exit": "15", "threshold": 0.005}, instrument=PETR4, minutes=5)
    assert [item for item in skipped if item.session_date == DAY] == []
    assert len(raw) == 1
    assert raw[0].entry_price == 111
    assert raw[0].entry_time.minute == 5
    assert raw[0].exit_price == 112
    assert raw[0].exit_time.minute == 20


def test_end_of_day_is_the_cash_call_for_the_win_and_for_stocks():
    win, _, _ = _generate([
        *_prior(),
        _flat(DAY, 9, 0, 100_000 * math.exp(0.006), "WINV26"),
        _flat(DAY, 9, 1, 100_100, "WINV26"),
        _flat(DAY, 16, 54, 100_050, "WINV26"),
        _flat(DAY, 18, 24, 100_900, "WINV26"),
    ], {"exit": "eod"})
    stock, _, _ = _generate([
        *_quiet_equity(),
        _flat(DAY, 10, 0, 100 * math.exp(0.02)),
        _flat(DAY, 10, 1, 110),
        _flat(DAY, 16, 54, 108),
    ], {"exit": "eod"}, instrument=PETR4)
    assert win[0].exit_time.hour == 16 and win[0].exit_time.minute == 55
    assert win[0].exit_price == 100_050
    assert win[0].entry_price == 100_100
    assert stock[0].exit_time.hour == 16 and stock[0].exit_time.minute == 55
    assert stock[0].exit_price == 108
    assert stock[0].entry_price == 110


def test_a_late_first_print_skips_the_signal_window():
    raw, _, skipped = _generate([
        *_prior(),
        _flat(DAY, 9, 6, 100_000 * math.exp(0.02), "WINV26"),
    ], {"exit": "eod"})
    assert raw == []
    assert len(skipped) == 1
    assert skipped[0].window == "signal"
    assert skipped[0].session_date == DAY


def test_day_without_a_prior_close_does_not_trade():
    raw, warnings, skipped = _generate([
        _flat(DAY, 9, 0, 101_000, "WINV26"),
        _flat(DAY, 9, 1, 101_000, "WINV26"),
        _flat(DAY, 18, 24, 101_000, "WINV26"),
    ], {"exit": "eod"})
    assert raw == []
    assert skipped == []
    assert any("fechamento anterior" in warning for warning in warnings)


def test_win_does_not_borrow_another_contracts_close():
    rows = [
        _flat(EARLIER, 9, 0, 100_000, "WINZ26"),
        _flat(EARLIER, 18, 24, 100_000, "WINZ26"),
        _flat(PRIOR, 9, 0, 100_000, "WINZ26"),
        _flat(PRIOR, 18, 24, 100_000, "WINZ26"),
        _flat(DAY, 9, 0, 102_000, "WINV26"),
        _flat(DAY, 9, 1, 102_000, "WINV26"),
        _flat(DAY, 18, 24, 102_000, "WINV26"),
    ]
    raw, warnings, skipped = _generate(rows, {"exit": "eod"})
    assert raw == []
    assert skipped == []
    assert any("WINV26" in warning for warning in warnings)


def test_same_contract_prior_close_is_used():
    rows = [
        _flat(PRIOR, 18, 24, 100_000, "WINV26"),
        _flat(PRIOR, 18, 24, 50_000, "WINZ26"),
        _flat(DAY, 9, 0, 100_000 * math.exp(0.006), "WINV26"),
        _flat(DAY, 9, 1, 100_200, "WINV26"),
        _flat(DAY, 9, 15, 100_100, "WINV26"),
        _flat(DAY, 9, 16, 100_100, "WINV26"),
    ]
    raw, _, _ = _generate(rows, {"exit": "15"})
    assert len(raw) == 1
    assert raw[0].signal_return == pytest.approx(0.006)


def test_thirty_minute_exit_and_a_hold_that_does_not_fit_the_bar():
    raw, _, _ = _generate([
        *_prior(),
        _flat(DAY, 9, 0, 100_000 * math.exp(0.006), "WINV26"),
        _flat(DAY, 9, 1, 100_200, "WINV26"),
        _flat(DAY, 9, 30, 100_150, "WINV26"),
        _bar(DAY, 9, 31, 100_140, 100_140, 100_140, 100_140, "WINV26"),
    ], {"exit": "30"})
    assert raw[0].exit_time.hour == 9 and raw[0].exit_time.minute == 31
    assert raw[0].exit_price == 100_140
    coarse, _, skipped = _generate([
        _flat(EARLIER, 10, 0, 100),
        _flat(PRIOR, 10, 0, 100),
        _flat(DAY, 10, 0, 100 * math.exp(0.02)),
        _flat(DAY, 11, 0, 110),
        _flat(DAY, 16, 0, 108),
    ], {"exit": "15"}, instrument=PETR4, minutes=60)
    assert coarse == []
    day_skips = [item for item in skipped if item.session_date == DAY]
    assert day_skips[0].window == "trade"
    assert "1 minuto" in day_skips[0].reason
