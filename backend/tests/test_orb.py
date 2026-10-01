from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from engine.costs import CostModel, apply_costs
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
    ], {"execution": "confirm"})
    down, _, _ = _generate([
        *_range(),
        _bar(DAY, 9, 5, 100, 100, 98, 99),
        _bar(DAY, 9, 6, 98, 98, 98, 98),
        _tail(102, 103, 101),
    ], {"execution": "confirm"})
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
    ], {"execution": "confirm"})
    gapped, _, _ = _generate([
        *_range(),
        _bar(DAY, 9, 5, 110, 112, 110, 111),
        _bar(DAY, 9, 6, 90, 90, 90, 90),
        _tail(),
    ], {"execution": "confirm"})
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
    ], {"execution": "confirm"})
    last, _, skipped = _generate([
        *_range(),
        _bar(DAY, 9, 5, 105, 106, 104, 105),
        _bar(DAY, 16, 54, 120, 120, 120, 120),
    ], {"execution": "confirm"})
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
    ], {"execution": "confirm"}, instrument=PETR4)
    assert skipped == []
    assert raw[0].direction == 1
    assert raw[0].entry_price == 12
    assert raw[0].exit_price == 13
    assert raw[0].exit_time.hour == 16 and raw[0].exit_time.minute == 55


def test_the_range_starts_at_the_first_trade():
    raw, _, skipped = _generate([
        _bar(DAY, 10, 8, 100, 102, 99, 101),
        _bar(DAY, 10, 12, 101, 103, 100, 102),
        _bar(DAY, 10, 13, 103, 105, 103, 104),
        _bar(DAY, 10, 14, 105, 105, 105, 105),
        _bar(DAY, 16, 54, 106, 106, 106, 106),
    ], {"execution": "confirm"}, instrument=PETR4)
    assert skipped == []
    assert len(raw) == 1
    assert raw[0].direction == 1
    assert raw[0].entry_time.hour == 10 and raw[0].entry_time.minute == 14
    assert raw[0].entry_price == 105
    assert raw[0].exit_price == 106


def test_a_first_print_after_the_open_tolerance_skips_the_signal():
    raw, _, skipped = _generate([
        _bar(DAY, 9, 31, 105, 110, 100, 105),
        _bar(DAY, 9, 36, 112, 112, 112, 112),
        _tail(),
    ])
    assert raw == []
    assert any(item.window == "signal" and "30" in item.reason for item in skipped)


def test_continuous_ending_at_16_49_exits_there_with_a_warning():
    raw, warnings, skipped = _generate([
        _bar(DAY, 10, 0, 10, 11, 9, 10),
        _bar(DAY, 10, 4, 10, 11, 9, 10),
        _bar(DAY, 10, 5, 11, 12, 11, 12),
        _bar(DAY, 10, 6, 12, 12, 12, 12),
        _bar(DAY, 16, 49, 13, 13, 13, 13),
        _bar(DAY, 17, 5, 20, 20, 20, 20),
    ], instrument=PETR4)
    assert skipped == []
    assert raw[0].exit_price == 13
    assert raw[0].exit_time.hour == 16 and raw[0].exit_time.minute == 50
    assert raw[0].exit_price != 20
    assert any("16:49" in warning and "pregão pulado" not in warning for warning in warnings)


def _paid(raw, instrument=PETR4):
    return apply_costs(raw, instrument, CostModel(fee_per_side=0, slippage_ticks=1, fee_rate=0), instrument.symbol)


def _equity_range() -> list[dict]:
    return [
        _bar(DAY, 10, 0, 10, 11, 9, 10),
        _bar(DAY, 10, 4, 10, 11, 9, 10),
    ]


def test_a_bar_that_opens_inside_fills_one_tick_beyond_the_edge_before_slippage():
    long, warnings, skipped = _generate([
        *_equity_range(),
        _bar(DAY, 10, 5, 10, 11.5, 10, 11.2),
        _bar(DAY, 16, 54, 12, 12, 12, 12),
    ], instrument=PETR4)
    short, _, _ = _generate([
        *_equity_range(),
        _bar(DAY, 10, 5, 10, 10, 8.5, 9.5),
        _bar(DAY, 16, 54, 8, 8, 8, 8),
    ], instrument=PETR4)
    assert skipped == []
    assert long[0].direction == 1
    assert long[0].entry_price == pytest.approx(11.01)
    assert long[0].entry_time.hour == 10 and long[0].entry_time.minute == 5
    assert _paid(long[0]).entry_price_effective == pytest.approx(11.02)
    assert short[0].direction == -1
    assert short[0].entry_price == pytest.approx(8.99)
    assert _paid(short[0]).entry_price_effective == pytest.approx(8.98)
    assert any("aproximação OHLC" in warning for warning in warnings)


def test_touching_the_edge_does_not_enter():
    high_touch, _, skipped = _generate([
        *_equity_range(),
        _bar(DAY, 10, 5, 10, 11, 10, 10.5),
        _bar(DAY, 16, 54, 10, 10, 10, 10),
    ], instrument=PETR4)
    low_touch, _, _ = _generate([
        *_equity_range(),
        _bar(DAY, 10, 5, 10, 10, 9, 9.5),
        _bar(DAY, 16, 54, 10, 10, 10, 10),
    ], instrument=PETR4)
    both, _, _ = _generate([
        *_equity_range(),
        _bar(DAY, 10, 5, 10, 11, 9, 10),
        _bar(DAY, 16, 54, 10, 10, 10, 10),
    ], instrument=PETR4)
    prints = pd.DataFrame({
        "timestamp": [
            pd.Timestamp(datetime(DAY.year, DAY.month, DAY.day, 10, 5, 1, tzinfo=TZ)),
            pd.Timestamp(datetime(DAY.year, DAY.month, DAY.day, 16, 54, 10, tzinfo=TZ)),
        ],
        "price": [11.0, 10.0],
    })
    from_ticks, _, tick_skipped = OpeningRangeBreakoutStrategy().generate(
        _frame([
            *_equity_range(),
            _bar(DAY, 10, 5, 10, 11, 10, 10.5),
            _bar(DAY, 16, 54, 10, 10, 10, 10),
        ]),
        {"min_bar_coverage": 0, "range_minutes": 5, "execution": "stop"},
        PETR4,
        1,
        trades=prints,
    )
    assert skipped == []
    assert tick_skipped == []
    assert high_touch == []
    assert low_touch == []
    assert both == []
    assert from_ticks == []


def test_a_bar_that_opens_outside_enters_at_the_open_plus_one_tick():
    long, _, skipped = _generate([
        *_equity_range(),
        _bar(DAY, 10, 5, 12, 12.5, 11.5, 12.2),
        _bar(DAY, 16, 54, 13, 13, 13, 13),
    ], instrument=PETR4)
    short, _, _ = _generate([
        *_equity_range(),
        _bar(DAY, 10, 5, 8, 8.5, 7.5, 8),
        _bar(DAY, 16, 54, 8, 8, 8, 8),
    ], instrument=PETR4)
    assert skipped == []
    assert long[0].entry_price == 12
    assert long[0].entry_time.minute == 5
    assert _paid(long[0]).entry_price_effective == pytest.approx(12.01)
    assert short[0].direction == -1
    assert short[0].entry_price == 8
    assert _paid(short[0]).entry_price_effective == pytest.approx(7.99)


def test_entry_and_stop_on_the_same_bar():
    inside, _, skipped = _generate([
        *_equity_range(),
        _bar(DAY, 10, 5, 10, 12, 8, 10),
        _bar(DAY, 16, 54, 15, 15, 15, 15),
    ], instrument=PETR4)
    gapped, _, _ = _generate([
        *_equity_range(),
        _bar(DAY, 10, 5, 12, 12.5, 8, 10),
        _bar(DAY, 16, 54, 15, 15, 15, 15),
    ], instrument=PETR4)
    assert skipped == []
    assert len(inside) == 1
    assert inside[0].direction == 1
    assert inside[0].entry_price == pytest.approx(11.01)
    assert inside[0].exit_price == 9
    assert inside[0].entry_time.minute == 5
    assert inside[0].exit_time.minute == 6
    paid = _paid(inside[0])
    assert paid.entry_price_effective == pytest.approx(11.02)
    assert paid.exit_price_effective == pytest.approx(8.99)
    assert gapped[0].entry_price == 12
    assert gapped[0].exit_price == 9
    assert gapped[0].entry_time.minute == 5
    assert gapped[0].exit_time.minute == 6
    assert inside[0].exit_price != 15


def test_a_later_bar_stops_at_the_other_extreme():
    touched, _, _ = _generate([
        *_equity_range(),
        _bar(DAY, 10, 5, 10, 11.5, 10, 11.2),
        _bar(DAY, 10, 6, 10.5, 10.5, 8.5, 9),
        _bar(DAY, 16, 54, 12, 12, 12, 12),
    ], instrument=PETR4)
    gapped, _, _ = _generate([
        *_equity_range(),
        _bar(DAY, 10, 5, 10, 11.5, 10, 11.2),
        _bar(DAY, 10, 6, 8, 8, 8, 8),
        _bar(DAY, 16, 54, 12, 12, 12, 12),
    ], instrument=PETR4)
    assert touched[0].entry_price == pytest.approx(11.01)
    assert touched[0].exit_price == 9
    assert touched[0].exit_time.minute == 7
    assert gapped[0].exit_price == 8
    assert gapped[0].exit_time.minute == 6


def test_stop_core_and_orb_confirm_fill_different_prices():
    rows = [
        *_equity_range(),
        _bar(DAY, 10, 5, 10, 11.5, 10, 11.2),
        _bar(DAY, 10, 6, 11.4, 11.4, 11.4, 11.4),
        _bar(DAY, 16, 54, 12, 12, 12, 12),
    ]
    stop, stop_warnings, _ = _generate(rows, instrument=PETR4)
    confirm, confirm_warnings, _ = _generate(rows, {"execution": "confirm"}, instrument=PETR4)
    assert stop[0].entry_price == pytest.approx(11.01)
    assert stop[0].entry_time.minute == 5
    assert confirm[0].entry_price == 11.4
    assert confirm[0].entry_time.minute == 6
    assert any("aproximação OHLC" in item for item in stop_warnings)
    assert all("Preenchimento do ORB" not in item for item in confirm_warnings)


def test_tickercsv_uses_the_first_real_print_not_the_edge():
    rows = [
        *_equity_range(),
        _bar(DAY, 10, 5, 10, 11.5, 8.5, 10),
        _bar(DAY, 16, 54, 12, 12, 12, 12),
    ]
    trades = pd.DataFrame({
        "timestamp": [
            pd.Timestamp(datetime(DAY.year, DAY.month, DAY.day, 10, 5, 0, tzinfo=TZ)),
            pd.Timestamp(datetime(DAY.year, DAY.month, DAY.day, 10, 5, 1, tzinfo=TZ)),
            pd.Timestamp(datetime(DAY.year, DAY.month, DAY.day, 10, 5, 40, tzinfo=TZ)),
            pd.Timestamp(datetime(DAY.year, DAY.month, DAY.day, 16, 54, 10, tzinfo=TZ)),
        ],
        "price": [11.0, 11.4, 8.7, 12.0],
        "contract": ["PETR4", "PETR4", "PETR4", "PETR4"],
    })
    raw, warnings, skipped = OpeningRangeBreakoutStrategy().generate(
        _frame(rows),
        {"min_bar_coverage": 0, "range_minutes": 5, "execution": "stop"},
        PETR4,
        1,
        trades=trades,
    )
    assert skipped == []
    assert raw[0].entry_price == 11.4
    assert raw[0].exit_price == 8.7
    assert raw[0].entry_time.second == 1
    assert raw[0].exit_time.second == 40
    paid = _paid(raw[0])
    assert paid.entry_price_effective == pytest.approx(11.41)
    assert paid.exit_price_effective == pytest.approx(8.69)
    assert any("tickercsv" in item for item in warnings)
