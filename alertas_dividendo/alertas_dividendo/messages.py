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
        caixa = (
            f"\nCaixa por ação (15% retido só na parte de JCP): {brl(net)}"
            if net is not None
            else ""
        )
        return (
            "dividendo e JCP na mesma data-com\n"
            f"Valor bruto por ação (usado no yield): {brl(gross)}"
            + caixa
        )
    label = "dividendo" if kind == "dividendo" else "provento (tipo não classificado)"
    return f"{label}\nValor por ação: {brl(gross)}"


def _yield_line(
    yld: float,
    last_close: float,
    last_close_date: date,
    last_close_source: str,
    reference_close_date: date,
    reference_close_used: bool,
    entry_type: str,
    buy_date: date,
) -> str:
    """A tela de yield é o último fechamento já publicado.

    O preço de entrada (fechamento do dia ou abertura do pregão seguinte)
    ainda não saiu quando o alerta tem de ir. `reference_close_used` fica na
    assinatura para os testes antigos; a tela não espera esse preço.
    """
    del reference_close_used
    entrada = (
        f"o fechamento de {day(buy_date)}"
        if entry_type == "fechamento"
        else f"a abertura de {day(buy_date)}"
    )
    tela = day(last_close_date)
    if reference_close_date != last_close_date:
        tela = f"{day(last_close_date)} (sessão da tela {day(reference_close_date)})"
    return (
        f"Yield: {pct(yld)} sobre o último fechamento disponível de {brl(last_close, 2)} "
        f"em {tela} ({last_close_source}). "
        f"Esse número é a tela de yield, porque {entrada} ainda não saiu."
    )


def _entrada(entry_type: str, entry_reason: str, buy_date: date) -> str:
    if entry_type == "fechamento":
        return (
            f"Compra no fechamento de hoje, {day(buy_date)}. "
            "O aviso chegou antes das 16:30 e a varredura viu a tempo de entrar no call de fechamento."
        )
    why = {
        "entrega_1630": "O aviso foi entregue às 16:30 ou depois.",
        "dia_sem_pregao": "O aviso saiu num dia sem pregão.",
        "sem_horario": "A fonte não traz a hora da entrega.",
        "deteccao_tardia": (
            "O aviso chegou antes das 16:30, mas a varredura só o viu depois desse horário."
        ),
    }.get(entry_reason, "A compra no fechamento do mesmo dia não cabe.")
    return f"Compra na abertura de {day(buy_date)}. {why}"


def _execucao(session: date) -> str:
    return (
        f"Execução: ordem limitada perto do fechamento de {day(session)} "
        "(call 16:55–17:00, horário de Brasília; na Quarta de Cinzas o call é 17:55–18:00), "
        "em corretora de taxa zero. Comprar um pregão mais tarde corta cerca de metade do ganho."
    )


def _execucao_entrada(session: date, entry_type: str) -> str:
    if entry_type == "fechamento":
        return _execucao(session)
    return (
        f"Execução: leilão de abertura de {day(session)} "
        "(até 10:00, horário de Brasília; na Quarta de Cinzas a abertura é às 13:00), "
        "em corretora de taxa zero."
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
    reference_close_date: date,
    reference_close_used: bool,
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
    entry_type: str = "abertura",
    entry_reason: str = "entrega_1630",
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
        "durante_pregao": "durante o pregão",
        "apos_fechamento": "depois do fechamento",
        "antes_da_abertura": "antes da abertura",
        "dia_sem_pregao": "dia sem pregão",
        "sem_horario": "fonte sem horário",
    }.get(timing, timing)
    lines.extend(
        [
            f"Papel: {ticker}",
            f"Empresa: {company}",
            f"Tipo: {_tipo(kind, gross, net)}",
            _yield_line(
                yld,
                last_close,
                last_close_date,
                last_close_source,
                reference_close_date,
                reference_close_used,
                entry_type,
                buy_date,
            ),
            f"Aprovação / data de referência: {day(approval) if approval else 'não informada'}",
            f"Publicação na CVM: {when} — {timing_label}",
            f"Data-com: {day(data_com) if data_com else 'não extraída — conferir no PDF'}",
            f"Data ex: {day(data_ex) if data_ex else 'não extraída — conferir no PDF'}",
            _entrada(entry_type, entry_reason, buy_date),
            (
                f"Saída prevista: fechamento de {day(exit_date)} "
                "(20º pregão da B3 depois da data-com)"
                if exit_date
                else "Saída prevista: assim que a data-com for lida (20º pregão depois dela)"
            ),
        ]
    )
    if data_com is None:
        lines.append(
            "ATENÇÃO: a data-com não é conhecida antes desta compra. "
            "A saída (20º pregão) e o direito ao provento ainda não estão confirmados."
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
            _execucao_entrada(buy_date, entry_type),
        ]
    )
    return with_disclaimer("\n".join(lines), simulacao)


def entry_reminder_message(*, ticker: str, buy_date: date, simulacao: bool) -> str:
    body = "\n".join(
        [
            f"LEMBRETE DE ENTRADA — {ticker}",
            f"Compra na abertura de hoje, {day(buy_date)}, antes das 10:00 (horário de Brasília).",
            "Na Quarta de Cinzas a abertura é às 13:00; o lembrete sai mesmo assim antes das 10:00.",
            "A ordem da regra entra no leilão de abertura.",
        ]
    )
    return with_disclaimer(body, simulacao)


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
            "Sem valor não há entrada automática: a regra exige yield de 2% até menos de 30%.",
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
    corporate_action: str | None = None,
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
    if corporate_action:
        body += (
            "\nDesdobramento ou grupamento durante a posição: "
            + corporate_action
            + " O preço de entrada e de saída não foi ajustado."
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
    corporate_action: str | None = None,
) -> str:
    if kind == "jcp":
        provento = (
            f"Provento recebido: {brl(dividend)} por ação "
            f"(JCP líquido; bruto {brl(gross)}, 15% retido)"
        )
    elif kind == "misto":
        provento = (
            f"Provento recebido: {brl(dividend)} por ação "
            f"(dividendo integral e JCP líquido; bruto somado {brl(gross)})"
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
                f"{closed_trades} trade{'s' if closed_trades != 1 else ''}): {pct(cumulative)}. "
                "Este acumulado ignora custos, impostos e o CDI do caixa parado."
            ),
        ]
    )
    if corporate_action:
        body += (
            "\nDesdobramento ou grupamento durante a posição: "
            + corporate_action
            + " O preço de entrada e de saída não foi ajustado."
        )
    return with_disclaimer(body, simulacao)


def corrected_value_message(
    *,
    ticker: str,
    old_gross: float,
    new_gross: float,
    yld: float | None,
    band: str,
    cancelled: bool,
    already_open: bool,
    already_closed: bool,
    kept_waiting: bool,
    buy_date: date | None,
    url: str,
    simulacao: bool,
) -> str:
    lines = [
        f"VALOR CORRIGIDO — {ticker}",
        "O valor por ação deste evento foi retificado. Não é uma compra nova.",
        f"Valor anterior: {brl(old_gross)}",
        f"Valor corrigido: {brl(new_gross)}",
    ]
    if yld is None:
        lines.append("Yield recalculado: sem fechamento para refazer a conta.")
    else:
        lines.append(f"Yield recalculado: {pct(yld)}")
    if cancelled:
        if band == "yield_acima":
            lines.append(
                "O yield corrigido fica em 30% ou mais e a compra ainda não foi feita. "
                "A ordem foi cancelada."
            )
        else:
            lines.append(
                "O yield corrigido fica abaixo de 2% e a compra ainda não foi feita. "
                "A ordem foi cancelada."
            )
    elif already_closed:
        lines.append(
            "A posição já foi encerrada. O valor corrigido fica registrado; "
            "o resultado que já foi avisado não é reescrito."
        )
    elif already_open:
        lines.append(
            "A posição já está aberta. Ela continua; o caixa na saída usa o valor corrigido."
        )
    elif kept_waiting:
        when = f" de {day(buy_date)}" if buy_date else ""
        lines.append(f"A compra marcada{when} continua, com o valor corrigido.")
    else:
        lines.append(
            "O sinal já não estava em compra. O valor foi atualizado e nenhuma compra nova foi aberta."
        )
    lines.append(f"Documento: {url}")
    return with_disclaimer("\n".join(lines), simulacao)


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
