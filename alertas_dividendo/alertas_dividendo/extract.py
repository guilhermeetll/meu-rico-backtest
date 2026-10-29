"""Extrai R$/ação, data-com e data-ex do texto de um aviso ou ata.

O script do estudo usava pdftotext -layout e uma regex que exigia no máximo
10 casas depois da vírgula e a frase "R$ … por ação". Isso falhou em AGRO3
(11 casas e o ano quebrado "202 6") e em IGTI11 (tabela sem "R$" na célula).
Aqui o texto é normalizado e a tabela também é lida. Se nada disso achar o
valor, o alerta sai como "conferir valor".

"R$ 0,34 (trinta e quatro centavos) por ação, ordinária e preferencial" vale
para as duas espécies. Num aviso de duas companhias, cada linha fica com o
próprio R$/ação; a linha de ADR não entra.
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
# O extenso pode ficar entre o número e "por ação":
# "R$ 0,34 (trinta e quatro centavos de real) por ação".
_PER_SHARE = re.compile(
    rf"R\$\s*{_NUM}(?:\s*\([^)]{{0,100}}\))?\s*(?:brutos?\s+)?(?:por|/)\s*(?:cada\s+)?"
    rf"(acao(?:es)?(?:\s+ordinarias?|\s+preferenciais?)?|unit(?:s)?|cota(?:s)?)",
    re.IGNORECASE,
)
_B3_ALIAS = re.compile(
    r"((?:[A-ZÁÉÍÓÚÃÕÇÜ][\wÁÉÍÓÚÃÕÇÜáéíóúãõç.'’\-]*\s+){1,6}S\.A\.)\s*\(\s*B3\s*:\s*([A-Z0-9]{4})"
)
_JOINT_ROW = re.compile(
    rf"(\d{{2}}/\d{{2}}/\d{{4}})\s{{1,40}}(\d{{2}}/\d{{2}}/\d{{4}})"
    rf"\s{{1,40}}(\d{{2}}/\d{{2}}/\d{{4}})\s{{1,40}}R\$\s*{_NUM}"
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
    """Espécie do trecho. Unit vem antes de 'ordinária e preferencial'.

    As duas espécies com o mesmo valor ficam em `any`. Se o trecho fala em
    unit, o valor é de unit — não vale para ON nem PN.
    """
    if re.search(r"\bunits?\b", snippet):
        return "unit"
    has_pn = "preferenc" in snippet or re.search(r"\bpn\b", snippet) is not None
    has_on = "ordinar" in snippet or re.search(r"\bon\b", snippet) is not None
    if has_on and has_pn:
        return "any"
    if has_pn:
        return "pn"
    if has_on:
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


def _credit_record_date(flat: str) -> date | None:
    """'Crédito em 10/11' com as ações inscritas é a data-base, não o pagamento.

    A ata da Gerdau não diz 'data-com'. 'Crédito em' sozinho é a data em que
    o dinheiro cai; sem 'inscritas' ou 'posição acionária' ao lado, não vale.
    """
    if "data do credito" not in flat:
        return None
    for match in re.finditer(r"credito em\s+(\d{1,2})/(\d{1,2})/(\d{4})", flat):
        before = flat[max(0, match.start() - 80) : match.start()]
        cut = before.rfind(". ")
        if cut >= 0:
            before = before[cut + 2 :]
        after = flat[match.end() : match.end() + 220]
        cut = after.find(". ")
        if cut >= 0:
            after = after[:cut]
        near = before + flat[match.start() : match.end()] + after
        if "inscritas" not in near and "posicao acionaria" not in near:
            continue
        parsed = _parse_date("num", match.groups())
        if parsed:
            return parsed
    return None


_DECLARED = re.compile(r"\b(?:declar\w*|aprov\w*|deliber\w*)\b")
_HISTORICAL = re.compile(r"anteriormente|exercicio anterior|pagos em")


def _amount_passage(flat: str, start: int, end: int) -> str:
    """Trecho deste R$/ação, sem atravessar o valor ao lado nem a frase anterior."""
    left = flat[max(0, start - 90) : start]
    prev = left.rfind("r$")
    if prev >= 0:
        left = left[prev + 2 :]
    dot = left.rfind(". ")
    if dot >= 0:
        left = left[dot + 2 :]
    right = flat[end : end + 48]
    nxt = right.find("r$")
    if nxt >= 0:
        right = right[:nxt]
    dot = right.find(". ")
    if dot >= 0:
        right = right[:dot]
    return left + flat[start:end] + right


def _historical_amount(passage: str) -> bool:
    if _HISTORICAL.search(passage):
        return True
    # "pagos R$ 1,20 (um real e vinte centavos) por ação em 2025"
    return (
        re.search(r"\bpagos\b", passage) is not None
        and re.search(r"\bem\s+(?:19|20)\d{2}\b", passage) is not None
    )


def _declared_or_at_record(flat: str, start: int, end: int, data_com: date | None) -> bool:
    """O valor tem de estar na declaração ou junto da data-com."""
    wide = flat[max(0, start - 400) : end + 200]
    if _DECLARED.search(wide):
        return True
    if any(key in wide for key in COM_KEYS):
        return True
    if data_com is None:
        return False
    numeric = f"{data_com.day}/{data_com.month}/{data_com.year}"
    padded = data_com.strftime("%d/%m/%Y")
    return numeric in wide or padded in wide


def _prose_hits(flat: str, document_kind: str) -> list[Distribution]:
    data_com = _first_keyword_date(flat, COM_KEYS) or _credit_record_date(flat)
    data_ex = _ex_date_from_text(flat, data_com)
    raw: list[tuple[float, str, str]] = []
    for match in _PER_SHARE.finditer(flat):
        try:
            value = parse_br_number(match.group(1))
        except ValueError:
            continue
        if not _accept(value):
            continue
        passage = _amount_passage(flat, match.start(), match.end())
        if _historical_amount(passage):
            continue
        if not _declared_or_at_record(flat, match.start(), match.end(), data_com):
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


def _ticker_issuer(ticker: str | None) -> str | None:
    if not ticker:
        return None
    match = re.match(r"^([A-Z0-9]{4})(?:11|[3-8])$", ticker.upper())
    return match.group(1) if match else None


def _name_tokens(name: str) -> list[str]:
    return [part for part in re.findall(r"[a-z0-9]+", fold(name)) if len(part) >= 4]


def _b3_aliases(text: str) -> dict[str, str]:
    """`Metalúrgica Gerdau S.A. (B3: GOAU)` → código da B3 para o nome."""
    found: dict[str, str] = {}
    for match in _B3_ALIAS.finditer(text):
        found[match.group(2).upper()] = re.sub(r"\s+", " ", match.group(1)).strip()
    return found


def _joint_header(text: str) -> bool:
    cut = fold(text).find("notice to shareholders")
    sample = text if cut < 0 else text[:cut]
    folded = fold(re.sub(r"\s+", " ", sample))
    return "valor por acao" in folded and "ordinar" in folded and "preferenc" in folded


def _is_company_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 80 or re.search(r"\d{2}/\d{2}/\d{4}", stripped):
        return False
    letters = [ch for ch in stripped if ch.isalpha()]
    if len(letters) < 4 or sum(ch.isupper() for ch in letters) / len(letters) < 0.75:
        return False
    folded = fold(stripped)
    if folded in {
        "empresa",
        "data",
        "data de",
        "data-base",
        "data base",
        "ex-direito",
        "ex direito",
        "pagamento",
        "dividendos",
        "dividendo",
        "date",
        "payment",
    }:
        return False
    return not (folded.startswith("valor por") or folded.startswith("ordinaria") or folded.startswith("ex-"))


def _company_lines_before(text: str, start: int) -> list[str]:
    window = text[max(0, start - 500) : start]
    cut = window.lower().rfind("r$")
    if cut >= 0:
        window = window[cut + 2 :]
    collected: list[str] = []
    for line in reversed(window.splitlines()):
        raw = line.strip()
        if not raw:
            continue
        piece = re.split(r"\d{2}/\d{2}/\d{4}", raw)[0].strip()
        if not piece:
            continue
        if _is_company_line(piece):
            collected.append(piece)
            continue
        if collected:
            break
    collected.reverse()
    return collected[-4:]


def _continuation_after(text: str, end: int) -> list[str]:
    """Nome da empresa quebrado na linha de baixo da célula, no pdftotext -layout."""
    extra: list[str] = []
    for line in text[end : end + 180].splitlines():
        raw = line.strip()
        if not raw:
            continue
        if re.search(r"\d{2}/\d{2}/\d{4}", raw) or "r$" in raw.lower():
            break
        if _is_company_line(raw):
            extra.append(raw)
            if len(extra) >= 2:
                break
            continue
        break
    return extra


def _issuer_for_label(label: str, aliases: dict[str, str]) -> str | None:
    folded = fold(label)
    if re.search(r"\badr\b", folded):
        return None
    best: str | None = None
    best_score = 0
    for code, name in aliases.items():
        needed = _name_tokens(name)
        if not needed or not all(token in folded for token in needed):
            continue
        if len(needed) > best_score:
            best = code
            best_score = len(needed)
    return best


def _label_issuer(text: str, start: int, end: int, aliases: dict[str, str]) -> str | None:
    lines = _company_lines_before(text, start)
    code = _issuer_for_label(" ".join(lines), aliases)
    if code:
        return code
    lines = lines + _continuation_after(text, end)
    return _issuer_for_label(" ".join(lines), aliases)


def _joint_table(
    text: str,
    ticker: str | None,
    document_kind: str,
) -> list[Distribution] | None:
    """Tabela de duas companhias com uma coluna só, ordinária e preferencial.

    O aviso conjunto da Gerdau traz Metalúrgica (GOAU) e Gerdau S.A. (GGBR) na
    mesma grade, mais uma linha de ADR. Cada código fica com o próprio R$/ação.
    None = o texto não é essa tabela.
    """
    aliases = _b3_aliases(text)
    if not (_joint_header(text) and len(aliases) >= 2):
        return None
    kind = document_kind if document_kind != "indefinido" else "dividendo"
    net_factor = (1.0 - JCP_WITHHOLDING) if kind == "jcp" else None
    tagged: list[tuple[str, Distribution]] = []
    for match in _JOINT_ROW.finditer(text):
        record = _parse_date("num", tuple(match.group(1).split("/")))
        ex_date = _parse_date("num", tuple(match.group(2).split("/")))
        payment = _parse_date("num", tuple(match.group(3).split("/")))
        if record is None or ex_date is None or payment is None:
            continue
        gap = ex_date - record
        if not (timedelta(0) < gap <= timedelta(days=10)) or payment < ex_date:
            continue
        try:
            gross = parse_br_number(match.group(4))
        except ValueError:
            continue
        if not _accept(gross):
            continue
        code = _label_issuer(text, match.start(), match.end(), aliases)
        if not code:
            continue
        tagged.append(
            (
                code,
                Distribution(
                    kind=kind,
                    gross=gross,
                    net=gross * net_factor if net_factor is not None else None,
                    data_com=record,
                    data_ex=ex_date,
                    share_class="any",
                ),
            )
        )
    if not tagged:
        return None
    issuer = _ticker_issuer(ticker)
    if ticker:
        if ticker_class(ticker) == "unit":
            return []
        tagged = [item for item in tagged if item[0] == issuer]
    order: list[str] = []
    buckets: dict[str, list[Distribution]] = {}
    for code, item in tagged:
        if code not in buckets:
            order.append(code)
            buckets[code] = []
        buckets[code].append(item)
    chosen: list[Distribution] = []
    for code in order:
        chosen.extend(combine_same_record_date(buckets[code]))
    return chosen


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
    joint = _joint_table(text, ticker, document_kind)
    if joint is not None:
        return joint
    flat = flatten(text)
    prose = _prose_hits(flat, document_kind)
    chosen = _for_ticker(prose, ticker)
    if chosen:
        return combine_same_record_date(chosen)
    row = _pick_row(_table_rows(text), published_day)
    if row is None:
        return []
    corte, ex, values = row
    kind = document_kind if document_kind != "indefinido" else "dividendo"

    def _row(share_class: str, gross: float) -> Distribution:
        net = gross * (1.0 - JCP_WITHHOLDING) if kind == "jcp" else None
        return Distribution(
            kind=kind,
            gross=gross,
            net=net,
            data_com=corte,
            data_ex=ex,
            share_class=share_class,
        )

    if not ticker:
        return [_row(share_class, gross) for share_class, gross in values.items()]
    wanted = ticker_class(ticker)
    # A coluna de unit não serve para ON nem PN: o yield inflaria. Conferir valor.
    if wanted not in values:
        return []
    return [_row(wanted, values[wanted])]


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
    # Sem a espécie deste papel, inclusive quando o texto só tem unit. Conferir valor.
    return []


def select_for_ticker(items: list[Distribution], ticker: str) -> Distribution | None:
    chosen = _for_ticker(items, ticker)
    return chosen[0] if chosen else None
