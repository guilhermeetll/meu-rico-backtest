"""Extrai R$/ação, data-com e data-ex do texto de um aviso ou ata.

O script do estudo usava pdftotext -layout e uma regex que exigia no máximo
10 casas depois da vírgula e a frase "R$ … por ação". Isso falhou em AGRO3
(11 casas e o ano quebrado "202 6") e em IGTI11 (tabela sem "R$" na célula).
Aqui o texto é normalizado e a tabela também é lida. Se nada disso achar o
valor, o alerta sai como "conferir valor".
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from alertas_dividendo.strategy import JCP_WITHHOLDING, same_cash_event

log = logging.getLogger(__name__)

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
        before = flat[max(0, match.start() - 64) : match.start()]
        # A espécie pode vir logo depois de "por ação", mas não pode atravessar
        # o R$ seguinte — senão a ordinária herda "preferencial" da frase ao lado.
        tail = flat[match.end() : match.end() + 40]
        nxt = tail.find("r$")
        if nxt >= 0:
            tail = tail[:nxt]
        share_class = _share_class(match.group(0) + " " + tail)
        # O papel (bruto/líquido) olha só as palavras coladas no número.
        # O tipo olha mais atrás, para achar "dividendo" ou "juros sobre".
        raw.append(
            (value, share_class, _stated_role(before[-24:]), _local_kind(before, document_kind))
        )

    if not raw:
        return []

    by_class: dict[str, list[tuple[float, str, str]]] = {}
    for value, share_class, role, local_kind in raw:
        by_class.setdefault(share_class, []).append((value, role, local_kind))

    hits: list[Distribution] = []
    for share_class, pairs in by_class.items():
        for gross, net, kind in _cash_components(pairs, document_kind):
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


def _is_stated_net(net: float, gross: float) -> bool:
    expected = gross * (1.0 - JCP_WITHHOLDING)
    return abs(net - expected) <= max(0.005, 0.02 * gross)


def _local_kind(before: str, document_kind: str) -> str:
    """O rótulo mais perto do valor: dividendo ou JCP."""
    jcp_at = max(before.rfind("juros sobre"), before.rfind("jcp"), before.rfind("jscp"))
    div_at = before.rfind("dividendo")
    if jcp_at < 0 and div_at < 0:
        return document_kind
    if jcp_at > div_at:
        return "jcp"
    return "dividendo"


def _cash_components(
    pairs: list[tuple[float, str, str]],
    document_kind: str,
) -> list[tuple[float, float | None, str]]:
    """Cada pagamento por ação. Bruto e líquido do mesmo JCP contam uma vez.

    Dois valores diferentes na mesma data-com (dividendo e JCP) ficam em
    componentes separados; quem chama soma os da mesma data-com.
    """
    brutos = [(value, local) for value, role, local in pairs if role == "bruto"]
    liquidos = [(value, local) for value, role, local in pairs if role == "liquido"]
    plain = list(dict.fromkeys((value, local) for value, role, local in pairs if role == "nenhum"))
    components: list[tuple[float, float | None, str]] = []
    if (
        brutos
        and liquidos
        and document_kind in {"jcp", "misto"}
        and _is_stated_net(liquidos[0][0], brutos[0][0])
    ):
        components.append((brutos[0][0], liquidos[0][0], "jcp"))
        brutos = brutos[1:]
        liquidos = liquidos[1:]

    def emit_plain(value: float, local: str) -> None:
        kind = local if local in {"jcp", "dividendo"} else document_kind
        if kind == "jcp":
            components.append((value, value * (1.0 - JCP_WITHHOLDING), "jcp"))
        elif kind in {"misto", "indefinido"}:
            components.append((value, None, "dividendo"))
        else:
            components.append((value, None, kind))

    for value, local in plain:
        emit_plain(value, local)
    for value, local in brutos:
        if local == "jcp" or document_kind in {"jcp", "misto"}:
            components.append((value, value * (1.0 - JCP_WITHHOLDING), "jcp"))
        else:
            emit_plain(value, local)
    for value, local in liquidos:
        if local == "jcp" or document_kind == "jcp":
            components.append((value / (1.0 - JCP_WITHHOLDING), value, "jcp"))
        elif document_kind == "misto":
            components.append((value, None, "misto"))
        else:
            emit_plain(value, local)
    return components


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


_INSTALLMENT_ROW = re.compile(
    r"(?:(?<!\d)(\d{1,2})\s*[ªaºo]|parcela\s+(\d{1,2}))\s+"
    r"(?:\d{1,3}(?:\.\d{3})+,\d{2}\s+)?"
    r"(\d+,\d{4,12})\s+"
    r"((?:\d{2}/\d{2}/\d{4}\s*){3,4})",
    re.IGNORECASE,
)
_MULTI_INSTALLMENT = re.compile(
    r"\d+\s*\([^)]{0,20}\)\s*parcelas|\bem\s+\d+\s+parcelas|\bparcelas\b",
    re.IGNORECASE,
)


def _parse_row_dates(blob: str) -> tuple[date, date] | None:
    """Data-com e data-ex de uma linha de cronograma.

    Quatro datas, na ordem do aviso da Mitre: divulgação, base (data-com),
    ex-dividendo, pagamento. Três datas: data-com, ex, pagamento. A data-ex
    tem de cair nos dez dias seguintes à data-com.
    """
    found: list[date] = []
    for match in _DATE_NUM.finditer(blob):
        parsed = _parse_date("num", match.groups())
        if parsed:
            found.append(parsed)
    if len(found) >= 4:
        _disclosure, record, ex_date, payment = found[:4]
        gap = ex_date - record
        if record < _disclosure or not (timedelta(0) < gap <= timedelta(days=10)) or payment < ex_date:
            return None
        return record, ex_date
    if len(found) == 3:
        record, ex_date, payment = found
        gap = ex_date - record
        if not (timedelta(0) < gap <= timedelta(days=10)) or payment < ex_date:
            return None
        return record, ex_date
    return None


def _document_share_class(flat: str) -> str:
    ordinary = "ordinar" in flat
    preferred = "preferenc" in flat
    if ordinary and not preferred:
        return "on"
    if preferred and not ordinary:
        return "pn"
    if "unit" in flat:
        return "unit"
    return "any"


def _installment_events(text: str, document_kind: str) -> list[Distribution] | None:
    """Parcelas com data-com própria. None = o texto não é cronograma de parcelas.

    Lista vazia = fala em várias parcelas, mas a tabela não fecha: conferir valor.
    Cada parcela vira um evento. O total do parágrafo não entra.
    """
    flat = flatten(text)
    if "parcela" not in flat:
        return None
    kind = document_kind if document_kind != "indefinido" else "dividendo"
    if kind == "misto":
        kind = "dividendo"
    share_class = _document_share_class(flat)
    rows: list[Distribution] = []
    seen: set[tuple[date, float]] = set()
    for match in _INSTALLMENT_ROW.finditer(flat):
        try:
            gross = parse_br_number(match.group(3))
        except ValueError:
            continue
        if not _accept(gross):
            continue
        parsed = _parse_row_dates(match.group(4))
        if parsed is None:
            continue
        record, ex_date = parsed
        key = (record, round(gross, 8))
        if key in seen:
            continue
        seen.add(key)
        net = gross * (1.0 - JCP_WITHHOLDING) if kind == "jcp" else None
        rows.append(
            Distribution(
                kind=kind,
                gross=gross,
                net=net,
                data_com=record,
                data_ex=ex_date,
                share_class=share_class,
            )
        )
    distinct_dates = {item.data_com for item in rows}
    if len(rows) >= 2 and len(distinct_dates) >= 2:
        return rows
    if _MULTI_INSTALLMENT.search(flat):
        return []
    return None


def _without_installment_total(items: list[Distribution]) -> list[Distribution]:
    """O total do parágrafo não entra junto com as parcelas que o compõem."""
    if len(items) < 2:
        return items
    kept: list[Distribution] = []
    for item in items:
        by_date: dict[date | None, float] = {}
        for other in items:
            if other is item or other.gross >= item.gross * 0.98:
                continue
            by_date[other.data_com] = other.gross
        dated = [day for day in by_date if day is not None]
        if len(dated) < 2:
            kept.append(item)
            continue
        total = sum(by_date.values())
        if total > 0 and abs(item.gross - total) / total <= 0.02:
            continue
        kept.append(item)
    return kept


def combine_same_record_date(items: list[Distribution]) -> list[Distribution]:
    """Soma o que cai na mesma data-com. Datas-com diferentes não se somam.

    O backtest agrupa por papel, aprovação e data-com e soma o caixa
    (`groupby(['issuer','tk','ap','com']).v.sum()`). Dividendo e JCP do mesmo
    dia viram um evento. Cada parcela, com a sua data-com, continua sozinha.
    """
    items = _without_installment_total(items)
    grouped: dict[date | None, list[Distribution]] = {}
    order: list[date | None] = []
    for item in items:
        if item.data_com not in grouped:
            order.append(item.data_com)
            grouped[item.data_com] = []
        bucket = grouped[item.data_com]
        if any(item.kind == prev.kind and same_cash_event(item.gross, prev.gross) for prev in bucket):
            # O backtest somaria os dois. Aqui o segundo é o mesmo lançamento
            # repetido no texto (aviso e tabela), então fica um só.
            when = item.data_com.isoformat() if item.data_com else "sem data-com"
            log.info(
                "mesmo tipo e valor na data-com %s colapsados em um evento "
                "(o backtest somaria): %s R$ %s",
                when,
                item.kind,
                item.gross,
            )
            continue
        bucket.append(item)
    combined: list[Distribution] = []
    for record in order:
        bucket = grouped[record]
        if len(bucket) == 1:
            combined.append(bucket[0])
            continue
        gross = sum(item.gross for item in bucket)
        net_total = 0.0
        for item in bucket:
            if item.kind == "jcp":
                net_total += item.net if item.net is not None else item.gross * (1.0 - JCP_WITHHOLDING)
            else:
                net_total += item.gross if item.net is None else item.net
        kinds = {item.kind for item in bucket}
        if kinds == {"jcp"}:
            kind = "jcp"
        elif "jcp" in kinds:
            kind = "misto"
        elif len(kinds) == 1:
            kind = bucket[0].kind
        else:
            kind = "misto"
        ex_dates = {item.data_ex for item in bucket if item.data_ex is not None}
        data_ex = next(iter(ex_dates)) if len(ex_dates) == 1 else None
        combined.append(
            Distribution(
                kind=kind,
                gross=gross,
                net=net_total if kind in {"jcp", "misto"} else None,
                data_com=record,
                data_ex=data_ex,
                share_class=bucket[0].share_class,
            )
        )
    return combined


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
    installments = _installment_events(text, document_kind)
    if installments is not None:
        chosen = _for_ticker(installments, ticker)
        return combine_same_record_date(chosen)
    flat = flatten(text)
    prose = _prose_hits(flat, document_kind)
    chosen = _for_ticker(prose, ticker)
    if chosen:
        return combine_same_record_date(chosen)
    row = _pick_row(_table_rows(text), published_day)
    if row is None:
        return []
    corte, ex, values = row
    wanted = ticker_class(ticker)
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


def _for_ticker(items: list[Distribution], ticker: str | None) -> list[Distribution]:
    if not ticker:
        return items
    wanted = ticker_class(ticker)
    specific = [item for item in items if item.share_class == wanted]
    if specific:
        return specific
    generic = [item for item in items if item.share_class == "any"]
    if generic:
        return generic
    return items


def select_for_ticker(items: list[Distribution], ticker: str) -> Distribution | None:
    chosen = _for_ticker(items, ticker)
    return chosen[0] if chosen else None
