from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from engine.instruments import resolve_instrument
from engine.strategies.orb import OpeningRangeBreakoutStrategy

TZ = ZoneInfo("America/Sao_Paulo")
WIN = resolve_instrument("WIN")
PETR4 = resolve_instrument("PETR4")
DAY = date(2026, 9, 17)


def _bar(day, hour, minute, open_, high, low, close) -> dict:
    return {
        "timestamp": pd.Timestamp(datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1,
    }


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _generate(rows: list[dict], params: dict | None = None, instrument=WIN):
    merged = {"min_bar_coverage": 0, "range_minutes": 5}
    merged.update(params or {})
    return OpeningRangeBreakoutStrategy().generate(_frame(rows), merged, instrument, 1)


def _range() -> list[dict]:
    return [
        _bar(DAY, 9, 0, 105, 110, 100, 105),
        _bar(DAY, 9, 4, 105, 110, 100, 105),
    ]


def _tail(close: float = 105, high: float = 106, low: float = 104) -> dict:
    return _bar(DAY, 16, 54, close, high, low, close)


def test_breakout_up_buys_on_the_next_open_and_breakout_down_sells():
    up, _, skipped = _generate([
        *_range(),
        _bar(DAY, 9, 5, 110, 112, 110, 111),
        _bar(DAY, 9, 6, 112, 112, 112, 112),
        _tail(),
    ])
    down, _, _ = _generate([
        *_range(),
        _bar(DAY, 9, 5, 100, 100, 98, 99),
        _bar(DAY, 9, 6, 98, 98, 98, 98),
        _tail(102, 103, 101),
    ])
    assert skipped == []
    assert len(up) == 1
    assert up[0].direction == 1
    assert up[0].entry_price == 112
    assert up[0].entry_time.minute == 6
    assert up[0].exit_price == 105
    assert up[0].exit_time.hour == 16 and up[0].exit_time.minute == 55
    assert len(down) == 1
    assert down[0].direction == -1
    assert down[0].entry_price == 98
    assert down[0].exit_price == 102


def test_no_breakout_means_no_trade():
    raw, _, skipped = _generate([
        *_range(),
        _bar(DAY, 9, 5, 105, 106, 104, 105),
        _bar(DAY, 9, 6, 105, 106, 104, 105),
        _tail(),
    ])
    assert raw == []
    assert skipped == []


def test_stop_touch_exits_at_the_stop_and_a_gap_exits_at_the_open():
    touched, _, _ = _generate([
        *_range(),
        _bar(DAY, 9, 5, 110, 112, 110, 111),
        _bar(DAY, 9, 6, 108, 108, 108, 108),
        _bar(DAY, 9, 7, 105, 106, 100, 104),
        _tail(),
    ])
    gapped, _, _ = _generate([
        *_range(),
        _bar(DAY, 9, 5, 110, 112, 110, 111),
        _bar(DAY, 9, 6, 90, 90, 90, 90),
        _tail(),
    ])
    assert touched[0].exit_price == 100
    assert touched[0].exit_time.hour == 9 and touched[0].exit_time.minute == 8
    assert gapped[0].exit_price == 90
    assert gapped[0].exit_time.hour == 9 and gapped[0].exit_time.minute == 6
    assert gapped[0].entry_price == 90


def test_at_most_one_trade_and_the_last_bar_cannot_be_entered():
    two, _, _ = _generate([
        *_range(),
        _bar(DAY, 9, 5, 110, 112, 110, 111),
        _bar(DAY, 9, 6, 112, 112, 112, 112),
        _bar(DAY, 9, 7, 105, 105, 90, 90),
        _tail(),
    ])
    last, _, skipped = _generate([
        *_range(),
        _bar(DAY, 9, 5, 105, 106, 104, 105),
        _bar(DAY, 16, 54, 120, 120, 120, 120),
    ])
    assert len(two) == 1
    assert two[0].direction == 1
    assert two[0].exit_price == 100
    assert last == []
    assert skipped == []


def test_incomplete_trade_window_skips_before_the_breakout_counts():
    raw, _, skipped = _generate([
        *_range(),
        _bar(DAY, 9, 5, 110, 112, 110, 111),
        _bar(DAY, 9, 6, 112, 112, 112, 112),
    ])
    assert raw == []
    assert any(item.window == "trade" for item in skipped)


def test_equity_end_of_day_is_the_closing_call():
    raw, _, skipped = _generate([
        _bar(DAY, 10, 0, 10, 11, 9, 10),
        _bar(DAY, 10, 4, 10, 11, 9, 10),
        _bar(DAY, 10, 5, 11, 12, 11, 12),
        _bar(DAY, 10, 6, 12, 12, 12, 12),
        _bar(DAY, 16, 54, 13, 13, 13, 13),
    ], instrument=PETR4)
    assert skipped == []
    assert raw[0].direction == 1
    assert raw[0].entry_price == 12
    assert raw[0].exit_price == 13
    assert raw[0].exit_time.hour == 16 and raw[0].exit_time.minute == 55


def test_opening_print_five_minutes_late_still_rejects_the_range():
    raw, _, skipped = _generate([
        _bar(DAY, 9, 6, 105, 110, 100, 105),
        _bar(DAY, 9, 10, 112, 112, 112, 112),
        _tail(),
    ])
    assert raw == []
    assert any(item.window == "signal" for item in skipped)
