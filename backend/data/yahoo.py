from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

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
    frame = _download_yfinance(ticker, request)
    if frame is None or frame.empty:
        # yfinance often comes back empty when the crumb endpoint fails.
        # The chart endpoint is the same public series, with the bar open as the clock.
        frame = _download_chart(ticker, request)
    return frame


def _download_yfinance(ticker: str, request: DataRequest) -> pd.DataFrame | None:
    try:
        import yfinance as yf

        end = request.end + timedelta(days=1)
        return yf.download(
            ticker,
            start=request.start.isoformat(),
            end=end.isoformat(),
            interval=YAHOO_INTERVAL[request.timeframe],
            progress=False,
            auto_adjust=False,
            threads=False,
        )
    except Exception:
        return None


def _download_chart(ticker: str, request: DataRequest) -> pd.DataFrame:
    tz = ZoneInfo("America/Sao_Paulo")
    period1 = int(datetime.combine(request.start, time.min, tzinfo=tz).timestamp())
    period2 = int(datetime.combine(request.end + timedelta(days=1), time.min, tzinfo=tz).timestamp())
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{ticker}?interval={YAHOO_INTERVAL[request.timeframe]}"
        f"&period1={period1}&period2={period2}&includePrePost=false"
    )
    fetched = urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30)
    payload = json.load(fetched)
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return pd.DataFrame()
    block = result[0]
    stamps = block.get("timestamp") or []
    quote = ((block.get("indicators") or {}).get("quote") or [{}])[0]
    rows = []
    for index, stamp in enumerate(stamps):
        rows.append(
            {
                "timestamp": datetime.fromtimestamp(int(stamp), tz),
                "open": _at(quote.get("open"), index),
                "high": _at(quote.get("high"), index),
                "low": _at(quote.get("low"), index),
                "close": _at(quote.get("close"), index),
                "volume": _at(quote.get("volume"), index) or 0,
            }
        )
    return pd.DataFrame(rows)


def _at(values, index: int):
    if not values or index >= len(values):
        return None
    return values[index]


def _to_bars(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)
    names = {str(column).lower().replace(" ", "") for column in out.columns}
    if not names & {"timestamp", "datetime", "date", "index"}:
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
