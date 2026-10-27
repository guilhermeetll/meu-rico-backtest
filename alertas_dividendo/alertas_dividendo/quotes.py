"""Cotações: COTAHIST (oficial) e Yahoo só quando o arquivo do dia ainda não saiu."""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import date, datetime, timedelta
from urllib.parse import quote

from alertas_dividendo.calendar import TZ, Calendar
from alertas_dividendo.cotahist import parse_cotahist
from alertas_dividendo.http_client import get
from alertas_dividendo.store import Store

log = logging.getLogger(__name__)

COTAHIST_URL = "https://bvmf.bmfbovespa.com.br/InstDados/SerHist/{name}"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&period1={start}&period2={end}"


def _zip_text(payload: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        name = archive.namelist()[0]
        return archive.read(name).decode("latin1")


def _download(name: str) -> str | None:
    url = COTAHIST_URL.format(name=name)
    try:
        payload = get(url, timeout=120)
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 404:
            return None
        log.warning("COTAHIST %s falhou: %s", name, exc)
        return None
    try:
        return _zip_text(payload)
    except zipfile.BadZipFile:
        log.warning("COTAHIST %s não é um zip", name)
        return None


def _ingest(store: Store, text: str, file_key: str) -> int:
    bars = parse_cotahist(text)
    rows = [
        (bar.ticker, bar.session.strftime("%Y-%m-%d"), bar.close, bar.volume, "COTAHIST")
        for bar in bars
    ]
    store.upsert_quotes(rows)
    store.mark_file(file_key)
    days = {bar.session.strftime("%Y-%m-%d") for bar in bars}
    for day in days:
        store.mark_file("D" + day.replace("-", ""))
    return len(rows)


def ensure_cotahist(store: Store, calendar: Calendar, start: date, end: date) -> None:
    """Baixa os pregões de `start` a `end` que ainda não estão no SQLite."""
    if end < start:
        return
    sessions = calendar.sessions_between(start, end)
    months = sorted({(day.year, day.month) for day in sessions})
    current_month = (end.year, end.month)
    for year, month in months:
        if (year, month) >= current_month:
            continue
        key = f"M{year}{month:02d}"
        if store.file_done(key):
            continue
        name = f"COTAHIST_M{month:02d}{year}.ZIP"
        log.info("baixando %s", name)
        text = _download(name)
        if text is None:
            log.info("mensal %s indisponível; os dias entram pelo arquivo diário", name)
            continue
        count = _ingest(store, text, key)
        log.info("%s: %s barras", name, count)
    for day in sessions:
        key = "D" + day.strftime("%Y%m%d")
        if store.file_done(key) or store.has_session(day.isoformat()):
            store.mark_file(key)
            continue
        name = "COTAHIST_D" + day.strftime("%d%m%Y") + ".ZIP"
        text = _download(name)
        if text is None:
            continue
        count = _ingest(store, text, key)
        log.info("%s: %s barras", name, count)


def _yahoo(symbol: str, start: date, end: date) -> list[tuple[date, float]]:
    period1 = int(datetime.combine(start - timedelta(days=5), datetime.min.time(), tzinfo=TZ).timestamp())
    period2 = int(datetime.combine(end + timedelta(days=2), datetime.min.time(), tzinfo=TZ).timestamp())
    url = YAHOO_URL.format(symbol=quote(symbol, safe=""), start=period1, end=period2)
    import json

    try:
        payload = json.loads(get(url, timeout=40, headers={"Accept": "application/json"}))
    except Exception as exc:
        log.warning("Yahoo %s falhou: %s", symbol, exc)
        return []
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return []
    block = result[0]
    stamps = block.get("timestamp") or []
    closes = ((block.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    out: list[tuple[date, float]] = []
    for stamp, close in zip(stamps, closes):
        if close is None:
            continue
        session = datetime.fromtimestamp(int(stamp), TZ).date()
        out.append((session, float(close)))
    return out


def ensure_index(store: Store, start: date, end: date) -> None:
    """Ibovespa via Yahoo ^BVSP. O índice não vem no COTAHIST de ações."""
    for session, close in _yahoo("^BVSP", start, end):
        if session < start or session > end:
            continue
        store.upsert_index(session.isoformat(), close, "Yahoo Finance ^BVSP")


def yahoo_close(ticker: str, session: date) -> float | None:
    bars = _yahoo(f"{ticker}.SA", session, session)
    for day, close in bars:
        if day == session:
            return close
    return None
