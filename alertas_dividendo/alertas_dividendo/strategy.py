"""Regras do swing de dividendo. Não parametrizar por ambiente.

A hora de entrega no ENET (America/Sao_Paulo) escolhe a entrada. Antes das
16:30 num pregão, vista a tempo, a compra é o fechamento desse dia. Às 16:30
ou depois, em dia sem pregão, sem hora na fonte, ou com detecção tardia, a
compra é a abertura do pregão seguinte. A saída continua o fechamento do 20º
pregão depois da data-com.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from alertas_dividendo.calendar import TZ, Calendar

MIN_YIELD = 0.02
MAX_YIELD = 0.30  # exclusivo: o backtest filtra y < 0.30
MIN_AVG_VOLUME = 1_000_000.0
VOLUME_SESSIONS = 60
SESSIONS_AFTER_COM = 20
MAX_POSITIONS = 10
SLOT_WEIGHT = 0.10
JCP_WITHHOLDING = 0.15
# 16:30 ainda é antes do call (16:55; na Cinzas, 17:55). Às 16:30 em ponto
# o fechamento do mesmo dia já não cabe: a varredura é de hora em hora.
SAME_DAY_CUTOFF = time(16, 30)
ENTRY_REMINDER_FROM = time(8, 0)
ENTRY_REMINDER_UNTIL = time(10, 0)


@dataclass(frozen=True)
class BuyPlan:
    buy_date: date
    deadline: datetime
    close_at: datetime
    # durante_pregao | apos_fechamento | antes_da_abertura | dia_sem_pregao | sem_horario
    timing: str
    # fechamento (mesmo dia) | abertura (pregão seguinte)
    entry_type: str
    # antes_de_1630 | entrega_1630 | dia_sem_pregao | sem_horario | deteccao_tardia
    entry_reason: str
    late_detection: bool = False

    @property
    def during_session(self) -> bool:
        return self.timing == "durante_pregao"


def jcp_net(gross: float, withholding: float = JCP_WITHHOLDING) -> float:
    """Líquido do JCP depois da retenção de 15% na fonte."""
    return gross * (1.0 - withholding)


def cash_for_yield(gross: float) -> float:
    """Yield usa o valor bruto. No dividendo, bruto e líquido coincidem."""
    return gross


def yield_ratio(gross_per_share: float, last_close: float) -> float:
    if last_close <= 0:
        raise ValueError("último fechamento tem de ser positivo")
    return cash_for_yield(gross_per_share) / last_close


def classify_yield(value: float) -> str:
    """`ok`, `yield_abaixo` ou `yield_acima`. 2% passa. 30% não: y < 0,30."""
    if value < MIN_YIELD:
        return "yield_abaixo"
    if value >= MAX_YIELD:
        return "yield_acima"
    return "ok"


def passes_liquidity(avg_volume: float | None, sessions: int) -> str:
    """`ok`, `historico_insuficiente` ou `iliquido`."""
    if avg_volume is None or sessions < VOLUME_SESSIONS:
        return "historico_insuficiente"
    if avg_volume < MIN_AVG_VOLUME:
        return "iliquido"
    return "ok"


def average_volume(volumes_oldest_first: list[float], n: int = VOLUME_SESSIONS) -> tuple[float | None, int]:
    """Média dos últimos `n` pregões. A lista já deve estar cortada na data do sinal."""
    if len(volumes_oldest_first) < n:
        return None, len(volumes_oldest_first)
    window = volumes_oldest_first[-n:]
    return sum(window) / n, n


def _timing(day: date, clock: time | None, time_known: bool, calendar: Calendar) -> str:
    if not time_known or clock is None:
        return "sem_horario"
    if not calendar.is_session(day):
        return "dia_sem_pregao"
    hours = calendar.hours(day)
    if clock < hours.open:
        return "antes_da_abertura"
    if clock < hours.close:
        return "durante_pregao"
    return "apos_fechamento"


def buy_plan(
    published_at: datetime | date,
    time_known: bool,
    calendar: Calendar,
    detected_at: datetime | None = None,
) -> BuyPlan:
    """Entrada pelo horário de entrega no ENET.

    Sem `detected_at`, a detecção é o próprio instante da publicação (o aviso
    foi visto na hora). O motor passa o relógio da varredura.

    - Antes das 16:30 num pregão, vista nesse mesmo dia antes das 16:30:
      fechamento desse dia. O prazo do alerta é 16:30, não o call.
    - Entrega às 16:30 ou depois: abertura do pregão seguinte.
    - Dia sem pregão, ou fonte sem hora: abertura do pregão seguinte. Sem
      hora não dá para provar que chegou antes das 16:30.
    - Entrega antes das 16:30, mas a varredura só vê às 16:30 ou depois
      (ou noutro dia): abertura do pregão seguinte, com `late_detection`.

    O prazo da abertura é o início do leilão (10:00; 13:00 na Cinzas).
    Na hora exata o prazo já passou.
    """
    if isinstance(published_at, datetime):
        local = published_at.astimezone(TZ) if published_at.tzinfo else published_at.replace(tzinfo=TZ)
        day = local.date()
        clock: time | None = local.timetz().replace(tzinfo=None)
    else:
        day = published_at
        clock = None
        time_known = False
        local = None
    timing = _timing(day, clock, time_known, calendar)

    def opening(reason: str, *, late: bool) -> BuyPlan:
        buy_date = calendar.next_session(day)
        return BuyPlan(
            buy_date=buy_date,
            deadline=calendar.open_at(buy_date),
            close_at=calendar.close_at(buy_date),
            timing=timing,
            entry_type="abertura",
            entry_reason=reason,
            late_detection=late,
        )

    if not time_known or clock is None:
        return opening("sem_horario", late=False)
    if not calendar.is_session(day):
        return opening("dia_sem_pregao", late=False)
    if clock >= SAME_DAY_CUTOFF:
        return opening("entrega_1630", late=False)

    seen = aware(detected_at) if detected_at is not None else local
    assert seen is not None
    seen_clock = seen.timetz().replace(tzinfo=None)
    if seen.date() == day and seen_clock < SAME_DAY_CUTOFF:
        return BuyPlan(
            buy_date=day,
            deadline=datetime.combine(day, SAME_DAY_CUTOFF, tzinfo=TZ),
            close_at=calendar.close_at(day),
            timing=timing,
            entry_type="fechamento",
            entry_reason="antes_de_1630",
            late_detection=False,
        )
    return opening("deteccao_tardia", late=True)


def entry_reminder_due(now: datetime, entry_date: date, entry_type: str) -> bool:
    """Lembrete da abertura: no dia da entrada, das 8:00 inclusive até as 10:00.

    Na Cinzas a abertura é às 13:00; o lembrete continua antes das 10:00.
    Fechamento do mesmo dia não tem lembrete de manhã.
    """
    if entry_type != "abertura":
        return False
    local = aware(now)
    if local.date() != entry_date:
        return False
    clock = local.timetz().replace(tzinfo=None)
    return ENTRY_REMINDER_FROM <= clock < ENTRY_REMINDER_UNTIL


def exit_session(data_com: date, calendar: Calendar) -> date:
    """Fechamento do 20º pregão depois da data-com. A data-com não conta."""
    return calendar.add_sessions(data_com, SESSIONS_AFTER_COM)


def has_rights(entry_session: date, data_com: date) -> bool:
    """A posição tem de estar aberta na data-com (compra naquele pregão ainda vale)."""
    return entry_session <= data_com


def slots_left(open_positions: int, limit: int = MAX_POSITIONS) -> int:
    return max(0, limit - open_positions)


def dividend_received(kind: str, gross: float, net: float | None, entry: date, data_com: date | None) -> float:
    """Provento que entra no retorno. JCP entra líquido. Sem direito, zero.

    No misto, `net` já é o dividendo integral mais o JCP depois dos 15%.
    O yield continua no bruto somado.
    """
    if data_com is None or not has_rights(entry, data_com):
        return 0.0
    if kind == "jcp":
        return net if net is not None else jcp_net(gross)
    if kind == "misto" and net is not None:
        return net
    return gross


def trade_return(entry_price: float, exit_price: float, dividend: float) -> float:
    if entry_price <= 0:
        raise ValueError("preço de entrada tem de ser positivo")
    return (exit_price - entry_price + dividend) / entry_price


def index_return(entry_level: float, exit_level: float) -> float:
    if entry_level <= 0:
        raise ValueError("nível do índice tem de ser positivo")
    return exit_level / entry_level - 1.0


def cumulative_result(trade_returns: list[float], slot: float = SLOT_WEIGHT) -> float:
    """Carteira de papel: cada trade usa uma vaga de 10% e o resultado compõe na saída.

    O caixa parado não rende. Não desconta custo nem IR — o alerta mostra o
    retorno do trade pedido na regra (preço + provento).
    """
    equity = 1.0
    for result in trade_returns:
        equity *= 1.0 + slot * result
    return equity - 1.0


def same_cash_event(gross_a: float, gross_b: float) -> bool:
    """Dois documentos com o mesmo R$/ação (aviso e ata) são um evento só.

    Só tolerância relativa de 1%. R$ 0,005 de diferença absoluta juntava
    R$ 0,0378 e R$ 0,040, que são proventos diferentes.
    """
    base = max(abs(gross_a), abs(gross_b), 1e-9)
    return abs(gross_a - gross_b) / base <= 0.01


def aware(moment: datetime, tz: ZoneInfo = TZ) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=tz)
    return moment.astimezone(tz)
