import math
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from app.study import main
from engine.strategies.preregistry import CATALOG_TRIALS, GAP_VARIANTS, MOMENTUM_VARIANTS, ORB_VARIANTS


def test_catalog_is_the_three_pre_registered_grids():
    assert len(MOMENTUM_VARIANTS) == 8
    assert [item["exit"] for item in GAP_VARIANTS] == ["15", "30", "eod"]
    assert [item["range_minutes"] for item in ORB_VARIANTS] == [5, 15, 30]
    assert CATALOG_TRIALS == 14
    text = Path("/workspace/docs/preregistro.md").read_text(encoding="utf-8")
    assert "14" in text
    assert "gap_reversal" not in text or "Ceretta" in text
    assert "Ceretta" in text
    assert "`15`, `30` e `eod`" in text
    assert "5, 15, 30" in text or "5, 15 e 30" in text


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
    assert list(table["variant"]) == ["15 min", "30 min", "fim do dia"]
    assert set(table["n_trials"]) == {14}
    assert int(table.loc[table["variant"] == "fim do dia", "trades"].iloc[0]) == 1
    markdown = out.with_suffix(".md").read_text(encoding="utf-8")
    assert "N do Sharpe deflacionado: **14**" in markdown


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
