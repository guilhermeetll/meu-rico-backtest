import math
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from app.study import main
from engine.metrics import daily_means, student_t
from engine.strategies.preregistry import (
    CATALOG_TRIALS,
    CORE_PER_INSTRUMENT,
    EXTRA_PER_INSTRUMENT,
    GAP_CORE,
    GAP_EXTRAS,
    MOMENTUM_VARIANTS,
    ORB_CORE,
    ORB_EXTRAS,
)


def test_catalog_splits_the_core_from_the_extras():
    assert len(MOMENTUM_VARIANTS) == 8
    assert [item["exit"] for item in GAP_CORE] == ["15", "15", "15"]
    assert [item["threshold"] for item in GAP_CORE] == [0.005, 0.01, 0.015]
    assert len(GAP_EXTRAS) == 6
    assert ORB_CORE == [{"range_minutes": 5}]
    assert [item["range_minutes"] for item in ORB_EXTRAS] == [15, 30]
    assert CORE_PER_INSTRUMENT == 4
    assert EXTRA_PER_INSTRUMENT == 8
    assert CATALOG_TRIALS == 20
    text = Path("/workspace/docs/preregistro.md").read_text(encoding="utf-8")
    assert "Núcleo" in text
    assert "Extras" in text
    assert "16:55" in text
    assert "0,5%, 1%, 1,5%" in text


def test_cli_writes_the_grid_and_keeps_a_larger_n(tmp_path: Path):
    path = tmp_path / "petr4.csv"
    _write_equity_csv(path)
    out = tmp_path / "estudo"
    code = main([
        "--start", "2026-09-16",
        "--end", "2026-09-17",
        "--symbols", "PETR4",
        "--sources", "csv",
        "--strategies", "gap_reversal",
        "--timeframe", "1min",
        "--csv", str(path),
        "--out", str(out),
        "--n-trials", "14",
    ])
    assert code == 0
    table = pd.read_csv(out.with_suffix(".csv"))
    assert list(table["variant"])[:3] == ["0,5% · 15 min", "1,0% · 15 min", "1,5% · 15 min"]
    assert set(table["n_trials"]) == {14}
    eod = table[table["variant"].str.contains("fim do dia")]
    assert len(eod) == 3
    assert set(eod["trades"]) == {1}
    markdown = out.with_suffix(".md").read_text(encoding="utf-8")
    assert "N do núcleo: **3**" in markdown
    assert "N dos extras: **6**" in markdown
    assert "N do Sharpe deflacionado: **14**" in markdown
    assert markdown.index("## Núcleo") < markdown.index("## Extras")


def test_daily_t_stat_collapses_trades_from_the_same_session():
    same_day = date(2026, 9, 17)
    other = date(2026, 9, 18)
    per_trade = student_t([0.01, 0.03, -0.02])
    per_day = student_t(daily_means([(same_day, 0.01), (same_day, 0.03), (other, -0.02)]))
    assert per_trade != per_day
    assert daily_means([(same_day, 0.01), (same_day, 0.03)]) == [0.02]


def _write_equity_csv(path: Path) -> None:
    rows = []
    opened = 100 * math.exp(0.02)
    for day, open_price in ((date(2026, 9, 16), 100.0), (date(2026, 9, 17), opened)):
        cursor = datetime(day.year, day.month, day.day, 10, 0)
        end = datetime(day.year, day.month, day.day, 16, 54)
        while cursor <= end:
            price = open_price if cursor.hour == 10 and cursor.minute == 0 else 100.0
            rows.append(
                {
                    "timestamp": cursor.isoformat(),
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": 1,
                }
            )
            cursor += timedelta(minutes=1)
    pd.DataFrame(rows).to_csv(path, index=False)
