from datetime import date
from pathlib import Path

import pytest

from data.base import DataRequest
from data.csv_loader import CsvBarsAdapter
from data.yahoo import YahooFinanceAdapter


def test_csv_accepts_portuguese_headers_and_column_map(tmp_path: Path):
    path = tmp_path / "profit.csv"
    path.write_text(
        "Data;Hora;Abertura;Máxima;Mínima;Fechamento;Quantidade\n"
        "17/09/2026;09:00;180.000,00;180.050,00;179.980,00;180.020,00;1200\n"
        "17/09/2026;09:01;180.020,00;180.040,00;180.010,00;180.030,00;800\n",
        encoding="utf-8",
    )
    result = CsvBarsAdapter().load(
        DataRequest(
            symbol="WIN",
            start=date(2026, 9, 17),
            end=date(2026, 9, 17),
            timeframe="1min",
            csv_path=path,
            column_map={"Abertura": "open", "Máxima": "high", "Mínima": "low", "Fechamento": "close"},
        )
    )
    assert len(result.bars) == 2
    assert result.bars.iloc[0]["open"] == pytest.approx(180000)
    assert result.bars.iloc[0]["high"] == pytest.approx(180050)
    assert result.bars.iloc[1]["close"] == pytest.approx(180030)


def test_csv_english_header(tmp_path: Path):
    path = tmp_path / "bars.csv"
    path.write_text(
        "datetime,open,high,low,close,volume\n"
        "2026-09-17 10:00:00,10,11,9,10.5,100\n",
        encoding="utf-8",
    )
    result = CsvBarsAdapter().load(
        DataRequest(symbol="PETR4", start=date(2026, 9, 1), end=date(2026, 9, 30), timeframe="1min", csv_path=path)
    )
    assert len(result.bars) == 1
    assert result.bars.iloc[0]["close"] == pytest.approx(10.5)


def test_yahoo_rejects_win_and_ranges_that_are_too_long():
    adapter = YahooFinanceAdapter()
    with pytest.raises(ValueError, match="WIN"):
        adapter.load(DataRequest("WIN", date(2026, 9, 1), date(2026, 9, 10), "5min"))
    with pytest.raises(ValueError, match="60"):
        adapter.load(DataRequest("PETR4", date(2026, 1, 1), date(2026, 6, 1), "5min"))
    with pytest.raises(ValueError, match="5min"):
        adapter.load(DataRequest("PETR4", date(2026, 9, 1), date(2026, 9, 10), "1min"))
