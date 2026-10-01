from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from engine.backtest import SampleSplit, run_backtest
from engine.costs import CostModel
from engine.instruments import WIN
from engine.strategies.momentum import IntradayMomentumStrategy
from engine.walkforward import WalkForwardConfig

TZ = ZoneInfo("America/Sao_Paulo")


def _stamp(day: date, hour: int, minute: int) -> pd.Timestamp:
    return pd.Timestamp(datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ))


def _day_bars(day: date, open_px: float, signal_px: float, entry_px: float, exit_px: float, contract: str | None = None) -> list[dict]:
    rows = [
        {"timestamp": _stamp(day, 9, 0), "open": open_px, "high": open_px, "low": open_px, "close": open_px, "volume": 10},
        {"timestamp": _stamp(day, 9, 29), "open": signal_px, "high": signal_px, "low": signal_px, "close": signal_px, "volume": 10},
        {"timestamp": _stamp(day, 17, 55), "open": entry_px, "high": entry_px, "low": entry_px, "close": entry_px, "volume": 10},
        {"timestamp": _stamp(day, 18, 24), "open": exit_px, "high": exit_px, "low": exit_px, "close": exit_px, "volume": 10},
    ]
    if contract:
        for row in rows:
            row["contract"] = contract
    return rows


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _business_days(start: date, count: int) -> list[date]:
    days = []
    cursor = start
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def test_positive_morning_buys_the_last_half_hour():
    day = date(2026, 9, 17)
    bars = _frame(_day_bars(day, 100_000, 100_050, 100_100, 100_150))
    result = run_backtest(
        bars,
        IntradayMomentumStrategy(),
        {},
        WIN,
        CostModel(0.50, 1),
        symbol="WIN",
        initial_capital=10_000,
        tax_rate=0.20,
        n_trials=1,
        bar_minutes=1,
    )
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.direction == 1
    assert trade.entry_price == 100_100
    assert trade.exit_price == 100_150
    # 50 points * 0.20 = R$ 10, minus R$ 2 of slippage and R$ 1 of fees.
    assert trade.pnl == pytest.approx(7.0)
    assert trade.entry_time.hour == 17 and trade.entry_time.minute == 55


def test_negative_morning_sells():
    day = date(2026, 9, 18)
    bars = _frame(_day_bars(day, 100_000, 99_950, 100_000, 99_950))
    raw, _ = IntradayMomentumStrategy().generate(bars, {}, WIN, 1)
    assert raw[0].direction == -1
    assert raw[0].signal_return == pytest.approx(-0.0005)


def test_threshold_filters_a_small_move():
    day = date(2026, 9, 17)
    bars = _frame(_day_bars(day, 100_000, 100_050, 100_100, 100_150))
    raw, _ = IntradayMomentumStrategy().generate(bars, {"threshold": 0.01}, WIN, 1)
    assert raw == []


def test_mixed_contracts_on_one_day_are_skipped():
    day = date(2026, 9, 30)
    rows = _day_bars(day, 180_000, 180_050, 180_100, 180_150, contract="WINV26")
    rows.extend(_day_bars(day, 100_000, 99_000, 100_100, 100_150, contract="WINZ26"))
    raw, warnings = IntradayMomentumStrategy().generate(_frame(rows), {}, WIN, 1)
    assert raw == []
    assert any("mistura" in warning for warning in warnings)


def test_prior_close_includes_the_gap_and_skips_the_first_session():
    first = date(2026, 9, 17)
    second = date(2026, 9, 18)
    rows = _day_bars(first, 100_000, 100_050, 100_100, 100_200, contract="WINV26")
    rows.extend(_day_bars(second, 100_000, 100_050, 100_100, 100_150, contract="WINV26"))
    raw, warnings = IntradayMomentumStrategy().generate(
        _frame(rows), {"signal_anchor": "prior_close"}, WIN, 1
    )
    assert len(raw) == 1
    assert raw[0].session_date == second
    assert raw[0].direction == -1
    assert raw[0].signal_return == pytest.approx(100_050 / 100_200 - 1)
    assert any("2026-09-17" in warning and "fechamento anterior" in warning for warning in warnings)

    opened, _ = IntradayMomentumStrategy().generate(_frame(rows), {"signal_anchor": "session_open"}, WIN, 1)
    assert opened[1].session_date == second
    assert opened[1].direction == 1


def test_prior_close_does_not_borrow_another_contract():
    first = date(2026, 9, 17)
    second = date(2026, 9, 18)
    rows = _day_bars(first, 180_000, 180_050, 180_100, 180_200, contract="WINV26")
    rows.extend(_day_bars(second, 100_000, 100_050, 100_100, 100_150, contract="WINZ26"))
    raw, warnings = IntradayMomentumStrategy().generate(
        _frame(rows), {"signal_anchor": "prior_close"}, WIN, 1
    )
    assert raw == []
    assert any("WINZ26" in warning for warning in warnings)


def test_before_cash_auction_trades_from_1625_to_1655():
    day = date(2026, 9, 17)
    rows = [
        {"timestamp": _stamp(day, 9, 0), "open": 100_000, "high": 100_000, "low": 100_000, "close": 100_000, "volume": 1},
        {"timestamp": _stamp(day, 9, 29), "open": 100_050, "high": 100_050, "low": 100_050, "close": 100_050, "volume": 1},
        {"timestamp": _stamp(day, 16, 25), "open": 100_200, "high": 100_200, "low": 100_200, "close": 100_200, "volume": 1},
        {"timestamp": _stamp(day, 16, 54), "open": 100_240, "high": 100_240, "low": 100_240, "close": 100_250, "volume": 1},
        {"timestamp": _stamp(day, 17, 55), "open": 100_800, "high": 100_800, "low": 100_800, "close": 100_800, "volume": 1},
        {"timestamp": _stamp(day, 18, 24), "open": 100_900, "high": 100_900, "low": 100_900, "close": 100_900, "volume": 1},
    ]
    raw, _ = IntradayMomentumStrategy().generate(_frame(rows), {"trade_window": "before_cash_auction"}, WIN, 1)
    assert len(raw) == 1
    assert raw[0].entry_time.hour == 16 and raw[0].entry_time.minute == 25
    assert raw[0].entry_price == 100_200
    assert raw[0].exit_time.hour == 16 and raw[0].exit_time.minute == 55
    assert raw[0].exit_price == 100_250

    default, _ = IntradayMomentumStrategy().generate(_frame(rows), {}, WIN, 1)
    assert default[0].entry_time.hour == 17 and default[0].entry_time.minute == 55
    assert default[0].exit_price == 100_900


def test_before_cash_auction_moves_to_1725_outside_us_dst():
    day = date(2026, 1, 15)
    rows = [
        {"timestamp": _stamp(day, 9, 0), "open": 100_000, "high": 100_000, "low": 100_000, "close": 100_000, "volume": 1},
        {"timestamp": _stamp(day, 9, 29), "open": 100_050, "high": 100_050, "low": 100_050, "close": 100_050, "volume": 1},
        {"timestamp": _stamp(day, 16, 25), "open": 100_180, "high": 100_180, "low": 100_180, "close": 100_180, "volume": 1},
        {"timestamp": _stamp(day, 17, 25), "open": 100_200, "high": 100_200, "low": 100_200, "close": 100_200, "volume": 1},
        {"timestamp": _stamp(day, 17, 54), "open": 100_240, "high": 100_240, "low": 100_240, "close": 100_250, "volume": 1},
        {"timestamp": _stamp(day, 18, 24), "open": 100_900, "high": 100_900, "low": 100_900, "close": 100_900, "volume": 1},
    ]
    raw, _ = IntradayMomentumStrategy().generate(_frame(rows), {"trade_window": "before_cash_auction"}, WIN, 1)
    assert len(raw) == 1
    assert raw[0].entry_time.hour == 17 and raw[0].entry_time.minute == 25
    assert raw[0].exit_time.hour == 17 and raw[0].exit_time.minute == 55
    assert raw[0].entry_price == 100_200
    assert raw[0].exit_price == 100_250


def test_cash_open_signal_end_works_for_both_anchors():
    prev = date(2026, 9, 16)
    day = date(2026, 9, 17)
    rows = [
        {"timestamp": _stamp(prev, 9, 0), "open": 100_000, "high": 100_000, "low": 100_000, "close": 100_000, "volume": 1, "contract": "WINV26"},
        {"timestamp": _stamp(prev, 18, 24), "open": 100_500, "high": 100_500, "low": 100_500, "close": 100_500, "volume": 1, "contract": "WINV26"},
        {"timestamp": _stamp(day, 9, 0), "open": 100_000, "high": 100_000, "low": 100_000, "close": 100_000, "volume": 1, "contract": "WINV26"},
        {"timestamp": _stamp(day, 9, 29), "open": 99_900, "high": 99_900, "low": 99_900, "close": 99_900, "volume": 1, "contract": "WINV26"},
        {"timestamp": _stamp(day, 10, 29), "open": 101_000, "high": 101_000, "low": 101_000, "close": 101_000, "volume": 1, "contract": "WINV26"},
        {"timestamp": _stamp(day, 17, 55), "open": 101_100, "high": 101_100, "low": 101_100, "close": 101_100, "volume": 1, "contract": "WINV26"},
        {"timestamp": _stamp(day, 18, 24), "open": 101_200, "high": 101_200, "low": 101_200, "close": 101_200, "volume": 1, "contract": "WINV26"},
    ]
    early, _ = IntradayMomentumStrategy().generate(
        _frame(rows), {"signal_anchor": "session_open", "signal_end": "session_open"}, WIN, 1
    )
    late, _ = IntradayMomentumStrategy().generate(
        _frame(rows), {"signal_anchor": "session_open", "signal_end": "cash_open"}, WIN, 1
    )
    early_day = [trade for trade in early if trade.session_date == day]
    late_day = [trade for trade in late if trade.session_date == day]
    assert len(early_day) == 1 and early_day[0].direction == -1
    assert early_day[0].signal_return == pytest.approx(99_900 / 100_000 - 1)
    assert len(late_day) == 1 and late_day[0].direction == 1
    assert late_day[0].signal_return == pytest.approx(101_000 / 100_000 - 1)

    early_gap, _ = IntradayMomentumStrategy().generate(
        _frame(rows), {"signal_anchor": "prior_close", "signal_end": "session_open"}, WIN, 1
    )
    late_gap, _ = IntradayMomentumStrategy().generate(
        _frame(rows), {"signal_anchor": "prior_close", "signal_end": "cash_open"}, WIN, 1
    )
    assert len(early_gap) == 1 and early_gap[0].session_date == day
    assert early_gap[0].direction == -1
    assert early_gap[0].signal_return == pytest.approx(99_900 / 100_500 - 1)
    assert len(late_gap) == 1 and late_gap[0].direction == 1
    assert late_gap[0].signal_return == pytest.approx(101_000 / 100_500 - 1)


def test_cash_open_signal_followed_the_11h_open_in_early_2012():
    day = date(2012, 2, 15)
    rows = [
        {"timestamp": _stamp(day, 9, 0), "open": 100_000, "high": 100_000, "low": 100_000, "close": 100_000, "volume": 1},
        {"timestamp": _stamp(day, 9, 29), "open": 99_000, "high": 99_000, "low": 99_000, "close": 99_000, "volume": 1},
        {"timestamp": _stamp(day, 10, 29), "open": 99_500, "high": 99_500, "low": 99_500, "close": 99_500, "volume": 1},
        {"timestamp": _stamp(day, 11, 29), "open": 102_000, "high": 102_000, "low": 102_000, "close": 102_000, "volume": 1},
        {"timestamp": _stamp(day, 17, 25), "open": 102_100, "high": 102_100, "low": 102_100, "close": 102_100, "volume": 1},
        {"timestamp": _stamp(day, 17, 54), "open": 102_200, "high": 102_200, "low": 102_200, "close": 102_300, "volume": 1},
    ]
    raw, _ = IntradayMomentumStrategy().generate(
        _frame(rows),
        {"signal_end": "cash_open", "trade_window": "before_cash_auction"},
        WIN,
        1,
    )
    assert len(raw) == 1
    assert raw[0].direction == 1
    assert raw[0].signal_return == pytest.approx(102_000 / 100_000 - 1)
    assert raw[0].entry_time.hour == 17 and raw[0].entry_time.minute == 25
    assert raw[0].exit_time.hour == 17 and raw[0].exit_time.minute == 55


def test_custom_session_close_still_sets_the_trade_window():
    day = date(2026, 9, 17)
    rows = [
        {"timestamp": _stamp(day, 9, 0), "open": 100_000, "high": 100_000, "low": 100_000, "close": 100_000, "volume": 1},
        {"timestamp": _stamp(day, 9, 29), "open": 100_050, "high": 100_050, "low": 100_050, "close": 100_050, "volume": 1},
        {"timestamp": _stamp(day, 16, 40), "open": 100_300, "high": 100_300, "low": 100_300, "close": 100_300, "volume": 1},
        {"timestamp": _stamp(day, 16, 59), "open": 100_320, "high": 100_320, "low": 100_320, "close": 100_340, "volume": 1},
        {"timestamp": _stamp(day, 17, 55), "open": 100_800, "high": 100_800, "low": 100_800, "close": 100_800, "volume": 1},
    ]
    raw, _ = IntradayMomentumStrategy().generate(
        _frame(rows),
        {"trade_window": "session_close", "session_close": "17:00", "trade_minutes": 20},
        WIN,
        1,
    )
    assert len(raw) == 1
    assert raw[0].entry_time.hour == 16 and raw[0].entry_time.minute == 40
    assert raw[0].exit_time.hour == 17 and raw[0].exit_time.minute == 0
    assert raw[0].exit_price == 100_340


def test_overlapping_windows_are_skipped():
    day = date(2026, 9, 17)
    bars = _frame(_day_bars(day, 100_000, 100_050, 100_100, 100_150))
    raw, warnings = IntradayMomentumStrategy().generate(
        bars, {"signal_minutes": 400, "trade_minutes": 400}, WIN, 1
    )
    assert raw == []
    assert warnings


def test_expiration_uses_the_shorter_session():
    day = date(2026, 10, 14)
    rows = [
        {"timestamp": _stamp(day, 9, 0), "open": 100_000, "high": 100_000, "low": 100_000, "close": 100_000, "volume": 1, "contract": "WINV26"},
        {"timestamp": _stamp(day, 9, 29), "open": 100_100, "high": 100_100, "low": 100_100, "close": 100_100, "volume": 1, "contract": "WINV26"},
        {"timestamp": _stamp(day, 17, 30), "open": 100_200, "high": 100_200, "low": 100_200, "close": 100_200, "volume": 1, "contract": "WINV26"},
        {"timestamp": _stamp(day, 17, 55), "open": 100_500, "high": 100_500, "low": 100_500, "close": 100_500, "volume": 1, "contract": "WINV26"},
        {"timestamp": _stamp(day, 17, 59), "open": 100_250, "high": 100_250, "low": 100_250, "close": 100_300, "volume": 1, "contract": "WINV26"},
    ]
    raw, _ = IntradayMomentumStrategy().generate(_frame(rows), {}, WIN, 1)
    assert len(raw) == 1
    assert raw[0].entry_time.hour == 17 and raw[0].entry_time.minute == 30
    assert raw[0].exit_price == 100_300


def test_walk_forward_picks_the_threshold_that_trades():
    days = _business_days(date(2026, 9, 1), 12)
    rows = []
    for day in days:
        rows.extend(_day_bars(day, 100_000, 100_050, 100_100, 100_150))
    result = run_backtest(
        _frame(rows),
        IntradayMomentumStrategy(),
        {"threshold": 0.0},
        WIN,
        CostModel(0.50, 1),
        symbol="WIN",
        initial_capital=10_000,
        tax_rate=0.0,
        n_trials=1,
        bar_minutes=1,
        sample_split=SampleSplit(enabled=True, in_sample_fraction=0.5),
        walk_forward=WalkForwardConfig(
            enabled=True,
            train_sessions=6,
            test_sessions=3,
            step_sessions=3,
            optimize_metric="total_pnl",
            param_grid={"threshold": [0.0, 0.01]},
        ),
    )
    assert result.in_sample is not None and result.out_of_sample is not None
    assert result.in_sample.metrics.n_trades == 6
    assert result.out_of_sample.metrics.n_trades == 6
    forward = result.walk_forward
    assert forward is not None
    assert len(forward.windows) == 2
    assert forward.n_configurations == 2
    assert all(window.chosen_params["threshold"] == 0.0 for window in forward.windows)
    assert forward.oos_metrics.n_trades == 6
    assert forward.oos_metrics.total_pnl == pytest.approx(42.0)
    assert forward.oos_metrics.n_trials == 2
