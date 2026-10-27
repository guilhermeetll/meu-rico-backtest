"""Leitor do COTAHIST diário/mensal da B3 (arquivo oficial de cotações).

O layout é o mesmo de `cotahist_parse.py` do estudo: registro 01, mercado à
vista (tpmerc 010), lote padrão (codbdi 02). Preços vêm divididos por 100 e
pelo fator de cotação. O volume financeiro (voltot) é dividido só por 100.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

TICKER_RE = re.compile(r"^[A-Z0-9]{4}(11|[3-8])$")


@dataclass(frozen=True)
class Bar:
    ticker: str
    session: datetime
    close: float
    volume: float
    open: float | None = None

    @property
    def session_date(self):
        return self.session.date()


def parse_cotahist(text: str) -> list[Bar]:
    """Uma barra por ticker e pregão. Em duplicata, fica o maior volume."""
    found: dict[tuple[str, str], Bar] = {}
    for line in text.splitlines():
        if len(line) < 217 or line[:2] != "01":
            continue
        if line[10:12] != "02" or line[24:27] != "010":
            continue
        ticker = line[12:24].strip()
        if not TICKER_RE.match(ticker):
            continue
        try:
            factor = int(line[210:217]) or 1
            opened = int(line[56:69]) / 100 / factor
            close = int(line[108:121]) / 100 / factor
            volume = int(line[170:188]) / 100
            session = datetime.strptime(line[2:10], "%Y%m%d")
        except ValueError:
            continue
        if close <= 0:
            continue
        key = (ticker, line[2:10])
        current = found.get(key)
        bar = Bar(
            ticker=ticker,
            session=session,
            close=close,
            volume=volume,
            open=opened if opened > 0 else None,
        )
        if current is None or bar.volume >= current.volume:
            found[key] = bar
    return list(found.values())


def build_quote_line(
    session_yyyymmdd: str,
    ticker: str,
    close: float,
    volume: float,
    *,
    factor: int = 1,
    open_price: float | None = None,
) -> str:
    """Linha 01 sintética para os testes. Os campos não usados ficam zerados.

    PREABE é o primeiro preço (abertura). PREULT, o quinto, continua o
    fechamento. Sem `open_price`, a abertura repete o fechamento.
    """
    def price(value: float) -> str:
        return f"{int(round(value * 100 * factor)):013d}"

    opened = close if open_price is None else open_price
    line = ["0"] * 245
    raw = (
        "01"
        + session_yyyymmdd
        + "02"
        + f"{ticker:<12}"
        + "010"
        + f"{'EMPRESA':<12}"
        + f"{'ON      NM':<10}"
        + "000"
        + "R$  "
        + price(opened)
        + price(close)
        + price(close)
        + price(close)
        + price(close)
        + "0" * 13
        + "0" * 13
        + "00001"
        + "0" * 18
        + f"{int(round(volume * 100)):018d}"
        + "0" * 13
        + "0"
        + "00000000"
        + f"{factor:07d}"
    )
    if len(raw) < 217:
        raise RuntimeError(f"linha curta: {len(raw)}")
    padded = raw + ("0" * (245 - len(raw)))
    return padded[:245]
