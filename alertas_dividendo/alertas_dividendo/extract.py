"""Extrai R$/ação, data-com e data-ex do texto de um aviso ou ata.

O script do estudo usava pdftotext -layout e uma regex que exigia no máximo
10 casas depois da vírgula e a frase "R$ … por ação". Isso falhou em AGRO3
(11 casas e o ano quebrado "202 6") e em IGTI11 (tabela sem "R$" na célula).
Aqui o texto é normalizado e a tabela também é lida. Se nada disso achar o
valor, o alerta sai como "conferir valor".
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from alertas_dividendo.strategy import JCP_WITHHOLDING

MONTHS = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}

_DATE_LONG = re.compile(
    r"(\d{1,2})\s+de\s+(" + "|".join(MONTHS) + r")\s+de\s+(\d{4})",
    re.IGNORECASE,
)
_DATE_NUM = re.compile(r"(\d{1,2})[./](\d{1,2})[./](\d{4})")
_NUM = r"(\d{1,3}(?:\.\d{3})*,\d{2,12}|\d+,\d{2,12})"
_PER_SHARE = re.compile(
    rf"R\$\s*{_NUM}\s*(?:brutos?\s+)?(?:por|/)\s*(?:cada\s+)?"
    rf"(acao(?:es)?(?:\s+ordinarias?|\s+preferenciais?)?|unit(?:s)?|cota(?:s)?)",
    re.IGNORECASE,
)
_LINE_DATE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_LINE_NUM = re.compile(r"^\d{1,3}(?:\.\d{3})*,\d{4,12}$|^\d+,\d{4,12}$")

COM_KEYS = (
    "data-com",
    "data com",
    "posicao acionaria",
    "composicao acionaria",
    "base acionaria",
    "farao jus",
    "detentores de acoes",
    "data de corte",
    "data-base",
)
EX_KEYS = (
    "ex-dividendo",
    "ex-direito",
    "ex dividendo",
    "ex direito",
    "negociadas ex",
    "negociados ex",
)

CLASS_BY_SUFFIX = {
    "3": "on",
    "4": "pn",
    "5": "pn",
    "6": "pn",
    "7": "pn",
    "8": "pn",
    "11": "unit",
}

MAX_PER_SHARE = 500.0


@dataclass(frozen=True)
class Distribution:
    kind: str  # dividendo | jcp | misto | indefinido
    gross: float
    net: float | None
    data_com: date | None
    data_ex: date | None
    share_class: str  # on | pn | unit | any


def fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    without = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return without.lower()


def glue_split_numbers(text: str) -> str:
    """Junta '202 6' e '30 .000' sem colar células de tabela em linhas diferentes."""
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"(\d)[ \t]{1,3}(\d)", r"\1\2", text)
        text = re.sub(r"(\d)[ \t]{1,3}(\.)", r"\1\2", text)
    return text


def flatten(text: str) -> str:
    return fold(re.sub(r"\s+", " ", glue_split_numbers(text)))


def parse_br_number(raw: str) -> float:
    cleaned = raw.strip().replace(" ", "")
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    return float(cleaned)


def ticker_class(ticker: str | None) -> str:
    if not ticker:
        return "any"
    match = re.match(r"^[A-Z0-9]{4}(11|[3-8])$", ticker.upper())
    if not match:
        return "any"
    return CLASS_BY_SUFFIX.get(match.group(1), "any")


def _parse_date(kind: str, groups: tuple[str, ...]) -> date | None:
    try:
        if kind == "long":
            day, month_name, year = groups
            return date(int(year), MONTHS[month_name.lower()], int(day))
        day, month, year = groups
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def find_dates(text: str) -> list[tuple[int, int, date]]:
    found: list[tuple[int, int, date]] = []
    for match in _DATE_LONG.finditer(text):
        parsed = _parse_date("long", match.groups())
        if parsed:
            found.append((match.start(), match.end(), parsed))
    for match in _DATE_NUM.finditer(text):
        parsed = _parse_date("num", match.groups())
        if parsed:
            found.append((match.start(), match.end(), parsed))
    return found


def _date_for_keyword(
    dates: list[tuple[int, int, date]],
    start: int,
    end: int,
    *,
    ahead: int = 240,
    behind: int = 120,
) -> date | None:
    """A data do provento vem depois da expressão ('farão jus ... em').

    Se o texto puser a data antes ('22/10, ex-dividendo'), aceita um trecho
    curto para trás. A data da reunião, mais atrás, não entra.
    """
    forward: list[tuple[int, date]] = []
    backward: list[tuple[int, date]] = []
    for pos_start, pos_end, parsed in dates:
        if start <= pos_start <= end + ahead:
            forward.append((pos_start - start, parsed))
        elif start - behind <= pos_end <= end:
            backward.append((start - pos_end, parsed))
    if forward:
        return min(forward)[1]
    if backward:
        return min(backward)[1]
    return None


def _first_keyword_date(text: str, keys: tuple[str, ...]) -> date | None:
    dates = find_dates(text)
    for key in keys:
        start = 0
        while True:
            pos = text.find(key, start)
            if pos < 0:
                break
            found = _date_for_keyword(dates, pos, pos + len(key))
            if found:
                return found
            start = pos + len(key)
    return None


def infer_kind(text: str) -> str:
    folded = fold(text)
    has_jcp = re.search(r"juros sobre (o )?capital|\b(jcp|jscp)\b", folded) is not None
    has_div = "dividendo" in folded
    if has_jcp and not has_div:
        return "jcp"
    if has_div and not has_jcp:
        return "dividendo"
    if has_jcp and has_div:
        return "misto"
    return "indefinido"


def _share_class(snippet: str) -> str:
    if "unit" in snippet:
        return "unit"
    if "preferenc" in snippet or re.search(r"\bpn\b", snippet):
        return "pn"
    if "ordinar" in snippet or re.search(r"\bon\b", snippet):
        return "on"
    return "any"


def _accept(value: float) -> bool:
    return 0 < value < MAX_PER_SHARE


def _stated_role(window: str) -> str:
    """`bruto`, `liquido` ou `nenhum` nas palavras imediatamente antes do valor."""
    if re.search(r"liquid[oa]", window) and not re.search(r"bruto", window):
        return "liquido"
    if re.search(r"bruto", window) and not re.search(r"liquid[oa]", window):
        return "bruto"
    return "nenhum"


def _dates_near_keys(text: str, keys: tuple[str, ...], radius: int = 160) -> list[tuple[int, date]]:
    dates = find_dates(text)
    found: list[tuple[int, date]] = []
    for key in keys:
        start = 0
        while True:
            pos = text.find(key, start)
            if pos < 0:
                break
            center = pos + len(key) / 2
            for pos_start, pos_end, parsed in dates:
                distance = abs(((pos_start + pos_end) / 2) - center)
                if distance <= radius:
                    found.append((int(distance), parsed))
            start = pos + len(key)
    return found


def _ex_date_from_text(text: str, data_com: date | None) -> date | None:
    """Data-ex fica ao lado de 'ex-direito', antes ou depois da expressão.

    A data de pagamento (meses à frente) também aparece nesse trecho. Com a
    data-com em mãos, fica a data que cai nos dez dias seguintes — na B3 o
    ex é o pregão seguinte.
    """
    ranked = _dates_near_keys(text, EX_KEYS)
    if data_com is not None:
        soon = [
            item
            for _, item in ranked
            if timedelta(days=0) < (item - data_com) <= timedelta(days=10)
        ]
        if soon:
            return min(soon)
    if not ranked:
        return None
    return min(ranked)[1]


def _prose_hits(flat: str, document_kind: str) -> list[Distribution]:
    data_com = _first_keyword_date(flat, COM_KEYS)
    data_ex = _ex_date_from_text(flat, data_com)
    raw: list[tuple[float, str, str]] = []
    for match in _PER_SHARE.finditer(flat):
        try:
            value = parse_br_number(match.group(1))
        except ValueError:
            continue
        if not _accept(value):
            continue
        window = flat[max(0, match.start() - 70) : match.end() + 40]
        share_class = _share_class(match.group(0) + " " + flat[match.end() : match.end() + 30])
        raw.append((value, share_class, _stated_role(window)))

    if not raw:
        return []

    by_class: dict[str, list[tuple[float, str]]] = {}
    for value, share_class, role in raw:
        by_class.setdefault(share_class, []).append((value, role))

    hits: list[Distribution] = []
    for share_class, pairs in by_class.items():
        brutos = [value for value, role in pairs if role == "bruto"]
        liquidos = [value for value, role in pairs if role == "liquido"]
        plain = [value for value, role in pairs if role == "nenhum"]
        kind = document_kind
        net: float | None = None
        if brutos and liquidos and kind in {"jcp", "misto"}:
            gross = brutos[0]
            net = liquidos[0]
            kind = "jcp"
        elif brutos:
            gross = brutos[0]
            if kind == "jcp":
                net = gross * (1.0 - JCP_WITHHOLDING)
        elif liquidos and kind == "jcp":
            net = liquidos[0]
            gross = net / (1.0 - JCP_WITHHOLDING)
        elif liquidos and kind == "misto":
            # Não dá para saber se o único número já é líquido. Não retém 15%.
            gross = liquidos[0]
            net = None
        else:
            gross = (plain or liquidos or brutos)[0]
            if kind == "jcp":
                net = gross * (1.0 - JCP_WITHHOLDING)
        hits.append(
            Distribution(
                kind=kind,
                gross=gross,
                net=net,
                data_com=data_com,
                data_ex=data_ex,
                share_class=share_class,
            )
        )
    return hits


def _table_rows(text: str) -> list[tuple[date, date | None, dict[str, float]]]:
    tokens: list[tuple[str, object]] = []
    for line in glue_split_numbers(text).splitlines():
        stripped = line.strip()
        if _LINE_DATE.match(stripped):
            day, month, year = stripped.split("/")
            try:
                tokens.append(("d", date(int(year), int(month), int(day))))
            except ValueError:
                continue
        elif _LINE_NUM.match(stripped):
            try:
                value = parse_br_number(stripped)
            except ValueError:
                continue
            if _accept(value):
                tokens.append(("n", value))
    rows: list[tuple[date, date | None, dict[str, float]]] = []
    index = 0
    while index + 5 < len(tokens):
        chunk = tokens[index : index + 6]
        kinds = "".join(kind for kind, _ in chunk)
        if kinds == "dddnnn":
            rows.append(
                (
                    chunk[0][1],  # type: ignore[arg-type]
                    chunk[1][1],  # type: ignore[arg-type]
                    {
                        "on": float(chunk[3][1]),  # type: ignore[arg-type]
                        "pn": float(chunk[4][1]),
                        "unit": float(chunk[5][1]),
                    },
                )
            )
            index += 6
            continue
        index += 1
    return rows


def _pick_row(
    rows: list[tuple[date, date | None, dict[str, float]]],
    published: date | None,
) -> tuple[date, date | None, dict[str, float]] | None:
    if not rows:
        return None
    if published is None:
        return rows[-1]
    return min(rows, key=lambda row: (abs((row[0] - published).days), -row[0].toordinal()))


def _installment_sum(text: str, gross: float) -> bool:
    """O R$/ação do parágrafo é a soma de várias parcelas com datas-base diferentes.

    O aviso da Mitre (MTRE3, out/2026) declara R$ 0,11344855041 por ação e, na
    tabela, três parcelas de R$ 0,03781618347. A B3 lista cada parcela. Usar a
    soma como se fosse um único provento erra o yield e o caixa do swing.
    """
    if gross <= 0 or "parcela" not in flatten(text):
        return False
    parts: list[float] = []
    for match in re.finditer(r"(?<!\d)(\d+,\d{4,12})(?!\d)", flatten(text)):
        try:
            value = parse_br_number(match.group(1))
        except ValueError:
            continue
        if 0 < value < gross * 0.98:
            parts.append(round(value, 10))
    if len(parts) < 2:
        return False
    distinct = set(parts)
    if len(distinct) >= 2 and abs(sum(distinct) - gross) / gross <= 0.02:
        return True
    for value in distinct:
        ratio = gross / value
        nearest = round(ratio)
        if nearest >= 2 and abs(ratio - nearest) <= 0.02 * nearest and parts.count(value) >= 2:
            return True
    return False


def extract_distributions(
    text: str,
    *,
    ticker: str | None = None,
    published: date | datetime | None = None,
) -> list[Distribution]:
    """Distribuições de caixa achadas no texto. Lista vazia = conferir valor."""
    if not text or not text.strip():
        return []
    published_day = published.date() if isinstance(published, datetime) else published
    document_kind = infer_kind(text)
    flat = flatten(text)
    prose = _prose_hits(flat, document_kind)
    wanted = ticker_class(ticker)
    chosen = [item for item in prose if item.share_class in {wanted, "any"}] if ticker else prose
    if chosen:
        safe = [item for item in chosen if not _installment_sum(text, item.gross)]
        if len(safe) < len(chosen):
            return safe
        return chosen
    row = _pick_row(_table_rows(text), published_day)
    if row is None:
        return []
    corte, ex, values = row
    share_class = wanted if wanted in values else "any"
    gross = values.get(wanted, values.get("unit", next(iter(values.values()))))
    net = gross * (1.0 - JCP_WITHHOLDING) if document_kind == "jcp" else None
    kind = document_kind if document_kind != "indefinido" else "dividendo"
    return [
        Distribution(
            kind=kind,
            gross=gross,
            net=net,
            data_com=corte,
            data_ex=ex,
            share_class=share_class if share_class in values else "unit",
        )
    ]


def select_for_ticker(items: list[Distribution], ticker: str) -> Distribution | None:
    if not items:
        return None
    wanted = ticker_class(ticker)
    specific = [item for item in items if item.share_class == wanted]
    if specific:
        return specific[0]
    generic = [item for item in items if item.share_class == "any"]
    if generic:
        return generic[0]
    return items[0]
