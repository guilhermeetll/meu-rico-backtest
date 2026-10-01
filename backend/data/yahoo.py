from __future__ import annotations

from datetime import timedelta

import pandas as pd

from data.bars import filter_dates, normalize_bars
from data.base import DataRequest, LoadResult

YAHOO_LIMITS = {"5min": 59, "60min": 729}
YAHOO_INTERVAL = {"5min": "5m", "60min": "60m"}


class YahooFinanceAdapter:
    id = "yahoo"
    label = "Yahoo Finance"
    description = (
        "Ações no Yahoo Finance, com o sufixo .SA quando ele não é informado "
        "(PETR4 vira PETR4.SA). Barras de 5 min cobrem cerca de 60 dias corridos; "
        "barras de 60 min, cerca de 730. O Yahoo não tem o WIN."
    )

    def load(self, request: DataRequest) -> LoadResult:
        if request.timeframe not in YAHOO_INTERVAL:
            raise ValueError("No Yahoo use 5min (até 60 dias) ou 60min (até 730 dias).")
        symbol = request.symbol.upper().strip()
        if symbol.startswith("WIN"):
            raise ValueError("O Yahoo Finance não tem o WIN. Use a fonte B3 ou um CSV.")
        span = (request.end - request.start).days
        if span > YAHOO_LIMITS[request.timeframe]:
            limit = YAHOO_LIMITS[request.timeframe] + 1
            raise ValueError(
                f"O Yahoo limita barras de {request.timeframe} a cerca de {limit} dias corridos."
            )
        ticker = symbol if "." in symbol or symbol.startswith("^") else f"{symbol}.SA"
        frame = _download(ticker, request)
        if frame is None or frame.empty:
            raise ValueError(f"O Yahoo não retornou barras para {ticker} nesse período.")
        bars = _to_bars(frame)
        bars = filter_dates(bars, request.start, request.end)
        if bars.empty:
            raise ValueError(f"O Yahoo não retornou barras de {ticker} dentro do período.")
        return LoadResult(
            bars=bars,
            warnings=[
                "O relógio das barras do Yahoo é o de abertura do candle. "
                "Confira o fuso se for cruzar com negócios da B3."
            ],
        )


def _download(ticker: str, request: DataRequest) -> pd.DataFrame:
    import yfinance as yf

    end = request.end + timedelta(days=1)
    frame = yf.download(
        ticker,
        start=request.start.isoformat(),
        end=end.isoformat(),
        interval=YAHOO_INTERVAL[request.timeframe],
        progress=False,
        auto_adjust=False,
        threads=False,
    )
    return frame


def _to_bars(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)
    out = out.reset_index()
    renamed = {}
    for column in out.columns:
        key = str(column).lower().replace(" ", "")
        if key in {"datetime", "date", "index"}:
            renamed[column] = "timestamp"
        elif key == "open":
            renamed[column] = "open"
        elif key == "high":
            renamed[column] = "high"
        elif key == "low":
            renamed[column] = "low"
        elif key == "close":
            renamed[column] = "close"
        elif key == "volume":
            renamed[column] = "volume"
    out = out.rename(columns=renamed)
    return normalize_bars(out)
