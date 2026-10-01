from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from engine.backtest import SampleSplit, run_backtest
from engine.costs import CostModel
from engine.instruments import WIN
from engine.sessions import cash_auction_window
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
        {"min_bar_coverage": 0},
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
    raw, _ = IntradayMomentumStrategy().generate(bars, {"min_bar_coverage": 0}, WIN, 1)
    assert raw[0].direction == -1
    assert raw[0].signal_return == pytest.approx(-0.0005)


def test_threshold_filters_a_small_move():
    day = date(2026, 9, 17)
    bars = _frame(_day_bars(day, 100_000, 100_050, 100_100, 100_150))
    raw, _ = IntradayMomentumStrategy().generate(bars, {"threshold": 0.01, "min_bar_coverage": 0}, WIN, 1)
    assert raw == []


def test_mixed_contracts_on_one_day_are_skipped():
    day = date(2026, 9, 30)
    rows = _day_bars(day, 180_000, 180_050, 180_100, 180_150, contract="WINV26")
    rows.extend(_day_bars(day, 100_000, 99_000, 100_100, 100_150, contract="WINZ26"))
    raw, warnings = IntradayMomentumStrategy().generate(_frame(rows), {"min_bar_coverage": 0}, WIN, 1)
    assert raw == []
    assert any("mistura" in warning for warning in warnings)


def test_prior_close_includes_the_gap_and_skips_the_first_session():
    first = date(2026, 9, 17)
    second = date(2026, 9, 18)
    rows = _day_bars(first, 100_000, 100_050, 100_100, 100_200, contract="WINV26")
    rows.extend(_day_bars(second, 100_000, 100_050, 100_100, 100_150, contract="WINV26"))
    raw, warnings = IntradayMomentumStrategy().generate(
        _frame(rows), {"signal_anchor": "prior_close", "min_bar_coverage": 0}, WIN, 1
    )
    assert len(raw) == 1
    assert raw[0].session_date == second
    assert raw[0].direction == -1
    assert raw[0].signal_return == pytest.approx(100_050 / 100_200 - 1)
    assert any("2026-09-17" in warning and "fechamento anterior" in warning for warning in warnings)

    opened, _ = IntradayMomentumStrategy().generate(_frame(rows), {"signal_anchor": "session_open", "min_bar_coverage": 0}, WIN, 1)
    assert opened[1].session_date == second
    assert opened[1].direction == 1


def test_prior_close_does_not_borrow_another_contract():
    first = date(2026, 9, 17)
    second = date(2026, 9, 18)
    rows = _day_bars(first, 180_000, 180_050, 180_100, 180_200, contract="WINV26")
    rows.extend(_day_bars(second, 100_000, 100_050, 100_100, 100_150, contract="WINZ26"))
    raw, warnings = IntradayMomentumStrategy().generate(
        _frame(rows), {"signal_anchor": "prior_close", "min_bar_coverage": 0}, WIN, 1
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
    raw, _ = IntradayMomentumStrategy().generate(_frame(rows), {"trade_window": "before_cash_auction", "min_bar_coverage": 0}, WIN, 1)
    assert len(raw) == 1
    assert raw[0].entry_time.hour == 16 and raw[0].entry_time.minute == 25
    assert raw[0].entry_price == 100_200
    assert raw[0].exit_time.hour == 16 and raw[0].exit_time.minute == 55
    assert raw[0].exit_price == 100_250

    default, _ = IntradayMomentumStrategy().generate(_frame(rows), {"min_bar_coverage": 0}, WIN, 1)
    assert default[0].entry_time.hour == 17 and default[0].entry_time.minute == 55
    assert default[0].exit_price == 100_900


def test_before_cash_auction_stays_at_1625_in_recent_winters():
    day = date(2026, 1, 15)
    rows = [
        {"timestamp": _stamp(day, 9, 0), "open": 100_000, "high": 100_000, "low": 100_000, "close": 100_000, "volume": 1},
        {"timestamp": _stamp(day, 9, 29), "open": 100_050, "high": 100_050, "low": 100_050, "close": 100_050, "volume": 1},
        {"timestamp": _stamp(day, 16, 25), "open": 100_180, "high": 100_180, "low": 100_180, "close": 100_180, "volume": 1},
        {"timestamp": _stamp(day, 16, 54), "open": 100_240, "high": 100_240, "low": 100_240, "close": 100_250, "volume": 1},
        {"timestamp": _stamp(day, 17, 25), "open": 100_200, "high": 100_200, "low": 100_200, "close": 100_200, "volume": 1},
        {"timestamp": _stamp(day, 17, 54), "open": 100_900, "high": 100_900, "low": 100_900, "close": 100_900, "volume": 1},
    ]
    raw, _ = IntradayMomentumStrategy().generate(_frame(rows), {"trade_window": "before_cash_auction", "min_bar_coverage": 0}, WIN, 1)
    assert len(raw) == 1
    assert raw[0].entry_time.hour == 16 and raw[0].entry_time.minute == 25
    assert raw[0].exit_time.hour == 16 and raw[0].exit_time.minute == 55
    assert raw[0].entry_price == 100_180
    assert raw[0].exit_price == 100_250


def test_cash_open_signal_end_works_for_both_anchors():
    prev = date(2026, 9, 16)
    day = date(2026, 9, 17)
    rows = [
        {"timestamp": _stamp(prev, 9, 0), "open": 100_000, "high": 100_000, "low": 100_000, "close": 100_000, "volume": 1, "contract": "WINV26"},
        {"timestamp": _stamp(prev, 18, 24), "open": 100_500, "high": 100_500, "low": 100_500, "close": 100_500, "volume": 1, "contract": "WINV26"},
        {"timestamp": _stamp(day, 9, 0), "open": 100_000, "high": 100_000, "low": 100_000, "close": 100_000, "volume": 1, "contract": "WINV26"},
        {"timestamp": _stamp(day, 9, 29), "open": 99_900, "high": 99_900, "low": 99_900, "close": 99_900, "volume": 1, "contract": "WINV26"},
        {"timestamp": _stamp(day, 10, 0), "open": 100_200, "high": 100_200, "low": 100_200, "close": 100_200, "volume": 1, "contract": "WINV26"},
        {"timestamp": _stamp(day, 10, 29), "open": 101_000, "high": 101_000, "low": 101_000, "close": 101_000, "volume": 1, "contract": "WINV26"},
        {"timestamp": _stamp(day, 17, 55), "open": 101_100, "high": 101_100, "low": 101_100, "close": 101_100, "volume": 1, "contract": "WINV26"},
        {"timestamp": _stamp(day, 18, 24), "open": 101_200, "high": 101_200, "low": 101_200, "close": 101_200, "volume": 1, "contract": "WINV26"},
    ]
    early, _ = IntradayMomentumStrategy().generate(
        _frame(rows), {"signal_anchor": "session_open", "signal_end": "session_open", "min_bar_coverage": 0}, WIN, 1
    )
    late, _ = IntradayMomentumStrategy().generate(
        _frame(rows), {"signal_anchor": "session_open", "signal_end": "cash_open", "min_bar_coverage": 0}, WIN, 1
    )
    early_day = [trade for trade in early if trade.session_date == day]
    late_day = [trade for trade in late if trade.session_date == day]
    assert len(early_day) == 1 and early_day[0].direction == -1
    assert early_day[0].signal_return == pytest.approx(99_900 / 100_000 - 1)
    assert len(late_day) == 1 and late_day[0].direction == 1
    assert late_day[0].signal_return == pytest.approx(101_000 / 100_000 - 1)

    early_gap, _ = IntradayMomentumStrategy().generate(
        _frame(rows), {"signal_anchor": "prior_close", "signal_end": "session_open", "min_bar_coverage": 0}, WIN, 1
    )
    late_gap, _ = IntradayMomentumStrategy().generate(
        _frame(rows), {"signal_anchor": "prior_close", "signal_end": "cash_open", "min_bar_coverage": 0}, WIN, 1
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
        {"signal_end": "cash_open", "trade_window": "before_cash_auction", "min_bar_coverage": 0},
        WIN,
        1,
    )
    assert len(raw) == 1
    assert raw[0].direction == 1
    assert raw[0].signal_return == pytest.approx(102_000 / 100_000 - 1)
    assert raw[0].entry_time.hour == 17 and raw[0].entry_time.minute == 25
    assert raw[0].exit_time.hour == 17 and raw[0].exit_time.minute == 55


def _ash_rows(day: date) -> list[dict]:
    return [
        {"timestamp": _stamp(day, 9, 0), "open": 90_000, "high": 90_000, "low": 90_000, "close": 90_000, "volume": 1},
        {"timestamp": _stamp(day, 9, 29), "open": 91_000, "high": 91_000, "low": 91_000, "close": 91_000, "volume": 1},
        {"timestamp": _stamp(day, 13, 0), "open": 100_000, "high": 100_000, "low": 100_000, "close": 100_000, "volume": 1},
        {"timestamp": _stamp(day, 13, 29), "open": 102_000, "high": 102_000, "low": 102_000, "close": 102_000, "volume": 1},
        {"timestamp": _stamp(day, 16, 25), "open": 103_000, "high": 103_000, "low": 103_000, "close": 103_000, "volume": 1},
        {"timestamp": _stamp(day, 17, 25), "open": 104_000, "high": 104_000, "low": 104_000, "close": 104_000, "volume": 1},
        {"timestamp": _stamp(day, 17, 54), "open": 105_000, "high": 105_000, "low": 105_000, "close": 105_500, "volume": 1},
        {"timestamp": _stamp(day, 17, 55), "open": 106_000, "high": 106_000, "low": 106_000, "close": 106_000, "volume": 1},
        {"timestamp": _stamp(day, 18, 24), "open": 107_000, "high": 107_000, "low": 107_000, "close": 107_000, "volume": 1},
    ]


def test_ash_wednesday_is_skipped_by_default_and_tradable_from_the_real_open():
    for day in (date(2016, 2, 10), date(2024, 2, 14), date(2025, 3, 5), date(2026, 2, 18)):
        skipped, warnings = IntradayMomentumStrategy().generate(
            _frame(_ash_rows(day)),
            {"signal_end": "cash_open", "trade_window": "before_cash_auction", "min_bar_coverage": 0},
            WIN,
            1,
        )
        assert skipped == []
        assert any(day.isoformat() in warning and "Cinzas" in warning for warning in warnings)

        cash, _ = IntradayMomentumStrategy().generate(
            _frame(_ash_rows(day)),
            {
                "signal_end": "cash_open",
                "trade_window": "before_cash_auction",
                "skip_ash_wednesday": False,
                "min_bar_coverage": 0,
            },
            WIN,
            1,
        )
        assert len(cash) == 1
        assert cash[0].signal_return == pytest.approx(102_000 / 100_000 - 1)
        assert cash[0].entry_time.hour == 17 and cash[0].entry_time.minute == 25
        assert cash[0].exit_time.hour == 17 and cash[0].exit_time.minute == 55
        assert cash[0].entry_price == 104_000

        opened, _ = IntradayMomentumStrategy().generate(
            _frame(_ash_rows(day)),
            {"signal_anchor": "session_open", "signal_end": "session_open", "skip_ash_wednesday": False, "min_bar_coverage": 0},
            WIN,
            1,
        )
        assert len(opened) == 1
        assert opened[0].signal_return == pytest.approx(102_000 / 100_000 - 1)
        assert opened[0].entry_time.hour == 17 and opened[0].entry_time.minute == 55


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
        {"trade_window": "session_close", "session_close": "17:00", "trade_minutes": 20, "min_bar_coverage": 0},
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
    raw, _ = IntradayMomentumStrategy().generate(_frame(rows), {"min_bar_coverage": 0}, WIN, 1)
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
        {"threshold": 0.0, "min_bar_coverage": 0},
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


def _fill(day: date, start: tuple[int, int], end: tuple[int, int], price) -> list[dict]:
    rows = []
    cursor = start[0] * 60 + start[1]
    last = end[0] * 60 + end[1]
    while cursor <= last:
        hour, minute = divmod(cursor, 60)
        value = price(hour, minute)
        rows.append({
            "timestamp": _stamp(day, hour, minute),
            "open": value,
            "high": value,
            "low": value,
            "close": value,
            "volume": 1,
        })
        cursor += 1
    return rows


def _price(hour: int, minute: int) -> float:
    return 100_000 + hour * 60 + minute


def _run_day(rows: list[dict], params: dict | None = None):
    return run_backtest(
        _frame(rows),
        IntradayMomentumStrategy(),
        params or {},
        WIN,
        CostModel(0.50, 1),
        symbol="WIN",
        initial_capital=10_000,
        tax_rate=0.0,
        n_trials=1,
        bar_minutes=1,
    )


def test_session_that_ends_at_noon_skips_the_trade_window():
    day = date(2026, 1, 30)
    rows = _fill(day, (9, 0), (12, 0), _price)
    for params, window in (
        ({}, "17:55–18:25"),
        ({"trade_window": "before_cash_auction"}, "16:25–16:55"),
    ):
        result = _run_day(rows, params)
        assert result.trades == []
        assert len(result.skipped) == 1
        assert result.skipped[0].session_date == day
        assert window in result.skipped[0].reason
        assert "12:00" in result.skipped[0].reason
        assert "Janela do sinal" not in result.skipped[0].reason
        assert any(f"{day.isoformat()}: pregão pulado." in warning for warning in result.warnings)

    flat = _run_day(_fill(day, (9, 0), (12, 0), lambda hour, minute: 100_000))
    assert flat.trades == []
    assert "Janela da operação" in flat.skipped[0].reason


def test_session_that_starts_at_15h_does_not_reuse_that_print_as_the_open():
    day = date(2026, 2, 2)
    result = _run_day(_fill(day, (15, 0), (18, 24), _price))
    assert result.trades == []
    assert len(result.skipped) == 1
    reason = result.skipped[0].reason
    assert "Janela do sinal 09:00–09:30" in reason
    assert "15:00" in reason
    assert "Janela da operação" not in reason


def test_late_first_prints_skip_the_morning_window():
    cases = (
        (date(2024, 7, 16), 11),
        (date(2025, 5, 15), 12),
        (date(2025, 8, 1), 13),
        (date(2026, 7, 31), 11),
    )
    for day, hour in cases:
        result = _run_day(_fill(day, (hour, 0), (18, 24), _price))
        assert result.trades == [], day
        assert len(result.skipped) == 1
        reason = result.skipped[0].reason
        assert result.skipped[0].session_date == day
        assert "Janela do sinal 09:00–09:30" in reason
        assert f"{hour:02d}:00" in reason
        assert "Janela da operação" not in reason


def test_a_few_minutes_of_opening_auction_still_count():
    day = date(2026, 9, 17)
    rows = _fill(day, (9, 5), (18, 24), _price)
    traded = _run_day(rows)
    assert len(traded.trades) == 1
    assert traded.skipped == []
    assert traded.trades[0].entry_time.hour == 17 and traded.trades[0].entry_time.minute == 55

    # 30/09/2026 is missing 09:31. That hole is inside 09:00–10:30 and still above 90%.
    cash_rows = [row for row in _fill(day, (9, 2), (18, 24), _price) if not (row["timestamp"].hour == 9 and row["timestamp"].minute == 31)]
    cash = _run_day(cash_rows, {"signal_end": "cash_open"})
    assert len(cash.trades) == 1
    assert cash.skipped == []

    late = _run_day(_fill(day, (9, 6), (18, 24), _price))
    assert late.trades == []
    assert "09:06" in late.skipped[0].reason
    assert "fora da tolerância" in late.skipped[0].reason
    allowed = _run_day(_fill(day, (9, 6), (18, 24), _price), {"edge_tolerance_minutes": 6})
    assert len(allowed.trades) == 1
    assert allowed.skipped == []


def test_coverage_is_configurable_and_endpoints_stay_mandatory():
    day = date(2026, 9, 18)
    rows = [
        row for row in _fill(day, (9, 0), (18, 24), _price)
        if not (row["timestamp"].hour == 9 and 5 <= row["timestamp"].minute <= 20)
    ]
    skipped = _run_day(rows)
    assert skipped.trades == []
    assert "cobertura" in skipped.skipped[0].reason
    assert "barra de início" not in skipped.skipped[0].reason
    assert "barra de fim" not in skipped.skipped[0].reason
    traded = _run_day(rows, {"min_bar_coverage": 0})
    assert len(traded.trades) == 1

    without_close = [
        row for row in _fill(day, (9, 0), (18, 24), _price)
        if not (row["timestamp"].hour == 9 and row["timestamp"].minute == 29)
    ]
    forced = _run_day(without_close, {"min_bar_coverage": 0})
    assert forced.trades == []
    assert "barra de fim 09:29" in forced.skipped[0].reason


def test_ash_wednesday_pre_auction_window_is_1725_when_the_day_is_traded():
    day = date(2026, 2, 18)
    assert cash_auction_window(day) == (time(17, 25), time(17, 55))
    rows = _fill(day, (13, 0), (18, 24), lambda hour, minute: 100_000)
    for row in rows:
        stamp = row["timestamp"]
        if stamp.hour == 13 and stamp.minute == 29:
            row["close"] = 102_000
        if stamp.hour == 16 and stamp.minute == 25:
            row["open"] = 103_000
        if stamp.hour == 17 and stamp.minute == 25:
            row["open"] = 104_000
        if stamp.hour == 17 and stamp.minute == 54:
            row["close"] = 105_500
        if stamp.hour == 17 and stamp.minute == 55:
            row["open"] = 106_000
        if stamp.hour == 18 and stamp.minute == 24:
            row["close"] = 107_000
    auction = _run_day(rows, {
        "skip_ash_wednesday": False,
        "trade_window": "before_cash_auction",
        "signal_end": "cash_open",
    })
    assert auction.skipped == []
    assert len(auction.trades) == 1
    trade = auction.trades[0]
    assert trade.entry_time.hour == 17 and trade.entry_time.minute == 25
    assert trade.exit_time.hour == 17 and trade.exit_time.minute == 55
    assert trade.entry_price == 104_000
    assert trade.exit_price == 105_500
    assert trade.signal_return == pytest.approx(102_000 / 100_000 - 1)

    closing = _run_day(rows, {"skip_ash_wednesday": False, "trade_window": "session_close"})
    assert len(closing.trades) == 1
    assert closing.trades[0].entry_time.hour == 17 and closing.trades[0].entry_time.minute == 55
    assert closing.trades[0].exit_time.hour == 18 and closing.trades[0].exit_time.minute == 25
    assert closing.trades[0].entry_price == 106_000
    assert closing.trades[0].exit_price == 107_000


def test_skipped_sessions_are_kept_past_the_warning_cap():
    days = _business_days(date(2026, 9, 1), 40)
    rows = []
    for day in days:
        rows.extend(_fill(day, (9, 0), (12, 0), _price))
    result = _run_day(rows)
    assert len(result.skipped) == 40
    assert len(result.trades) == 0
    assert sum("pregão pulado" in warning for warning in result.warnings) == 40
