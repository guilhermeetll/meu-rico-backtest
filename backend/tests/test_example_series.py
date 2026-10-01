"""The committed WIN example stays inside one contract.

Each session's return is measured on that session's series. A signal that
needs the previous close uses the previous session of the same contract.
The first session has no such close in the file, so that signal is skipped.
"""

from datetime import date
from pathlib import Path

from data.base import DataRequest
from data.csv_loader import CsvBarsAdapter
from engine.backtest import run_backtest
from engine.costs import CostModel
from engine.instruments import WIN
from engine.series import prior_close_same_contract, return_versus_prior_close
from engine.strategies.momentum import IntradayMomentumStrategy

SAMPLE = Path(__file__).resolve().parents[1] / "sample_data" / "win_exemplo_1min.csv"


def _bars():
    loaded = CsvBarsAdapter().load(
        DataRequest(
            symbol="WINV26",
            start=date(2026, 9, 17),
            end=date(2026, 9, 30),
            timeframe="1min",
            csv_path=SAMPLE,
        )
    )
    return loaded.bars


def test_example_file_is_marked_and_single_series():
    text = SAMPLE.read_text(encoding="utf-8")
    assert text.startswith("# EXEMPLO")
    bars = _bars()
    assert set(bars["contract"].unique()) == {"WINV26"}
    for day, frame in bars.groupby(bars["timestamp"].dt.date):
        assert frame["contract"].nunique() == 1, day


def test_prior_close_signal_uses_the_same_contract_or_is_skipped():
    bars = _bars()
    first = date(2026, 9, 17)
    second = date(2026, 9, 18)
    first_close = float(bars.loc[bars["timestamp"].dt.date == first, "close"].iloc[-1])
    second_open = float(bars.loc[bars["timestamp"].dt.date == second, "open"].iloc[0])

    assert prior_close_same_contract(bars, first, "WINV26") is None
    assert return_versus_prior_close(bars, first, "WINV26", second_open) is None
    assert prior_close_same_contract(bars, second, "WINV26") == first_close
    assert return_versus_prior_close(bars, second, "WINV26", second_open) == second_open / first_close - 1

    borrowed = bars.copy()
    borrowed.loc[borrowed["timestamp"].dt.date == first, "contract"] = "WINZ26"
    assert prior_close_same_contract(borrowed, second, "WINV26") is None
    assert return_versus_prior_close(borrowed, second, "WINV26", second_open) is None


def test_example_momentum_backtest_trades_inside_winv26():
    bars = _bars()
    result = run_backtest(
        bars,
        IntradayMomentumStrategy(),
        {"signal_minutes": 30, "trade_minutes": 30, "threshold": 0, "quantity": 1},
        WIN,
        CostModel(fee_per_side=0.50, slippage_ticks=1),
        symbol="WIN",
        initial_capital=10_000,
        tax_rate=0.20,
        n_trials=1,
        bar_minutes=1,
    )
    assert result.metrics.n_trades > 0
    assert all(trade.symbol == "WIN" for trade in result.trades)
    assert not any("mistura" in warning for warning in result.warnings)
