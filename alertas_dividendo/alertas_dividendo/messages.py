"""Textos enviados ao Telegram. Toda mensagem leva o aviso de simulação."""

from __future__ import annotations

from datetime import date, datetime

from alertas_dividendo.calendar import TZ
from alertas_dividendo.strategy import MAX_POSITIONS, SLOT_WEIGHT

DISCLAIMER = (
    "SIMULAÇÃO — paper trading da estratégia congelada. "
    "Não é recomendação de investimento nem uma ordem para a corretora."
)


def brl(value: float, digits: int = 6) -> str:
    text = f"{value:,.{digits}f}"
    return "R$ " + text.replace(",", "X").replace(".", ",").replace("X", ".")


def pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    text = f"{sign}{value * 100:.2f}"
    return text.replace(".", ",") + "%"


def day(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def moment(value: datetime) -> str:
    local = value.astimezone(TZ) if value.tzinfo else value
    return local.strftime("%d/%m/%Y %H:%M")


def captcha_message() -> str:
    return (
        "AVISO — a consulta pública da CVM (ENET) pediu captcha.\n"
        "Parei esta varredura e não vou consultar o ENET de novo hoje. "
        "Este aviso sai no máximo uma vez por dia.\n"
        "A base IPE, que atrasa 1 a 3 dias, continua como apoio. "
        "Amanhã o monitor tenta o ENET outra vez, uma vez por hora, sem insistir."
    )


def with_disclaimer(body: str, enabled: bool) -> str:
    if not enabled:
        return body.strip()
    return DISCLAIMER + "\n\n" + body.strip()


def _tipo(kind: str, gross: float, net: float | None) -> str:
    if kind == "jcp":
        liquido = net if net is not None else gross * 0.85
        return (
            f"JCP\nValor bruto por ação: {brl(gross)}\n"
            f"Valor líquido por ação (15% retido na fonte): {brl(liquido)}"
        )
    if kind == "misto":
        return (
            f"dividendo/JCP (o documento mistura os dois — conferir)\n"
            f"Valor por ação usado no yield: {brl(gross)} (bruto, sem reter 15%)"
        )
    label = "dividendo" if kind == "dividendo" else "provento (tipo não classificado)"
    return f"{label}\nValor por ação: {brl(gross)}"


def _execucao(session: date) -> str:
    return (
        f"Execução: ordem limitada perto do fechamento de {day(session)} "
        "(call 16:55–17:00, horário de Brasília; na Quarta de Cinzas o call é 17:55–18:00), "
        "em corretora de taxa zero. Comprar um pregão mais tarde corta cerca de metade do ganho."
    )


def buy_message(
    *,
    ticker: str,
    company: str,
    kind: str,
    gross: float,
    net: float | None,
    yld: float,
    last_close: float,
    last_close_date: date,
    last_close_source: str,
    approval: date | None,
    published_at: datetime,
    time_known: bool,
    timing: str,
    data_com: date | None,
    data_ex: date | None,
    buy_date: date,
    exit_date: date | None,
    url: str,
    protocol: str,
    slot_number: int,
    late: bool,
    entry_price: float | None,
    entry_source: str | None,
    simulacao: bool,
) -> str:
    title = "COMPRA ATRASADA — swing de dividendo" if late else "COMPRA — swing de dividendo"
    lines = [title]
    if late:
        lines.append(
            f"O fechamento de compra da regra ({day(buy_date)}) já passou. "
            "Entrar agora não replica o backtest. No papel, a posição fica registrada "
            "no fechamento oficial daquele pregão, não no preço de agora."
        )
    when = moment(published_at) if time_known else published_at.astimezone(TZ).strftime("%d/%m/%Y") + " (sem hora na fonte)"
    timing_label = {
        "durante_pregao": "durante o pregão — a compra é no pregão seguinte",
        "apos_fechamento": "depois do fechamento",
        "antes_da_abertura": "antes da abertura — a regra por data compra no pregão seguinte",
        "dia_sem_pregao": "dia sem pregão",
        "sem_horario": "fonte sem horário (tratado como fim do dia)",
    }.get(timing, timing)
    lines.extend(
        [
            f"Papel: {ticker}",
            f"Empresa: {company}",
            f"Tipo: {_tipo(kind, gross, net)}",
            (
                f"Yield: {pct(yld)} sobre o último fechamento de {brl(last_close, 2)} "
                f"em {day(last_close_date)} ({last_close_source})"
            ),
            f"Aprovação / data de referência: {day(approval) if approval else 'não informada'}",
            f"Publicação na CVM: {when} — {timing_label}",
            f"Data-com: {day(data_com) if data_com else 'não extraída — conferir no PDF'}",
            f"Data ex: {day(data_ex) if data_ex else 'não extraída — conferir no PDF'}",
            f"Limite para comprar: fechamento de {day(buy_date)}",
            (
                f"Saída prevista: fechamento de {day(exit_date)} "
                "(20º pregão da B3 depois da data-com)"
                if exit_date
                else "Saída prevista: assim que a data-com for lida (20º pregão depois dela)"
            ),
        ]
    )
    if entry_price is not None:
        lines.append(
            f"Entrada de papel: {brl(entry_price, 2)} em {day(buy_date)} ({entry_source or 'fonte não registrada'})"
        )
    lines.extend(
        [
            f"Vaga: {slot_number}/{MAX_POSITIONS} (peso {pct(SLOT_WEIGHT).lstrip('+')} do capital)",
            f"Protocolo CVM: {protocol}",
            f"Documento: {url}",
            _execucao(buy_date),
        ]
    )
    return with_disclaimer("\n".join(lines), simulacao)


def conferir_message(
    *,
    ticker: str,
    company: str,
    published_at: datetime,
    url: str,
    protocol: str,
    category: str,
    subject: str,
    simulacao: bool,
) -> str:
    body = "\n".join(
        [
            "CONFERIR VALOR — possível provento, R$/ação não lido no PDF",
            f"Papel: {ticker}",
            f"Empresa: {company}",
            f"Categoria: {category}",
            f"Assunto: {subject}",
            f"Publicação na CVM: {moment(published_at)}",
            "A liquidez passa o filtro de R$ 1 milhão, mas o valor por ação não saiu do PDF.",
            "Sem valor não há entrada automática: a regra exige yield entre 2% e 30%.",
            f"Protocolo CVM: {protocol}",
            f"Documento: {url}",
        ]
    )
    return with_disclaimer(body, simulacao)


def exit_message(
    *,
    ticker: str,
    exit_date: date,
    data_com: date | None,
    reminder: bool,
    simulacao: bool,
) -> str:
    if reminder:
        title = f"LEMBRETE DE SAÍDA — {ticker}"
        lead = (
            f"Falta pouco para o call de fechamento. Vender {ticker} hoje, {day(exit_date)}."
        )
    else:
        title = f"SAÍDA HOJE — {ticker}"
        lead = (
            f"Vender no fechamento de hoje, {day(exit_date)} "
            "(20º pregão da B3 depois da data-com"
            + (f" de {day(data_com)}" if data_com else "")
            + ")."
        )
    body = "\n".join(
        [
            title,
            lead,
            "A posição da regra tem de ser vendida nesse fechamento.",
            _execucao(exit_date),
        ]
    )
    return with_disclaimer(body, simulacao)


def result_message(
    *,
    ticker: str,
    entry_price: float,
    entry_date: date,
    entry_source: str,
    exit_price: float,
    exit_date: date,
    exit_source: str,
    dividend: float,
    kind: str,
    gross: float,
    trade: float,
    ibov: float | None,
    ibov_source: str | None,
    cumulative: float,
    closed_trades: int,
    simulacao: bool,
) -> str:
    if kind == "jcp":
        provento = (
            f"Provento recebido: {brl(dividend)} por ação "
            f"(JCP líquido; bruto {brl(gross)}, 15% retido)"
        )
    elif dividend == 0:
        provento = "Provento recebido: R$ 0 (a posição não estava aberta na data-com, ou o valor não fechou)"
    else:
        provento = f"Provento recebido: {brl(dividend)} por ação (dividendo)"
    ibov_line = (
        f"Ibovespa no mesmo período: {pct(ibov)} ({ibov_source})"
        if ibov is not None and ibov_source
        else "Ibovespa no mesmo período: indisponível"
    )
    body = "\n".join(
        [
            f"RESULTADO — {ticker}",
            f"Entrada: {brl(entry_price, 2)} em {day(entry_date)} ({entry_source})",
            f"Saída: {brl(exit_price, 2)} em {day(exit_date)} ({exit_source})",
            provento,
            f"Retorno do trade: {pct(trade)}",
            ibov_line,
            (
                f"Resultado acumulado da carteira de papel "
                f"(cada trade usa {pct(SLOT_WEIGHT).lstrip('+')} do capital, composto na saída, "
                f"{closed_trades} trade{'s' if closed_trades != 1 else ''}): {pct(cumulative)}"
            ),
        ]
    )
    return with_disclaimer(body, simulacao)


def update_dates_message(
    *,
    ticker: str,
    data_com: date,
    data_ex: date | None,
    exit_date: date,
    url: str,
    simulacao: bool,
) -> str:
    body = "\n".join(
        [
            f"DATA-COM LIDA — {ticker}",
            "O primeiro alerta saiu sem a data-com. Ela entrou agora e a saída foi marcada.",
            f"Data-com: {day(data_com)}",
            f"Data ex: {day(data_ex) if data_ex else 'não extraída'}",
            f"Saída prevista: fechamento de {day(exit_date)} (20º pregão depois da data-com)",
            f"Documento: {url}",
        ]
    )
    return with_disclaimer(body, simulacao)
