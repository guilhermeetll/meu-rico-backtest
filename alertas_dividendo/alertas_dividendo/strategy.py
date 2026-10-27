"""Regras congeladas do swing de dividendo. Não parametrizar por ambiente.

A leitura de horário (noite x durante o pregão) está em `buy_plan`. Os dois
casos compram no fechamento do pregão seguinte à data de publicação na CVM,
que é a regra do backtest (primeiro fechamento depois da Data_Entrega).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from alertas_dividendo.calendar import TZ, Calendar

MIN_YIELD = 0.02
MAX_YIELD = 0.30  # inclusive; o backtest usava y < 0.30
MIN_AVG_VOLUME = 1_000_000.0
VOLUME_SESSIONS = 60
SESSIONS_AFTER_COM = 20
MAX_POSITIONS = 10
SLOT_WEIGHT = 0.10
JCP_WITHHOLDING = 0.15


@dataclass(frozen=True)
class BuyPlan:
    buy_date: date
    deadline: datetime
    close_at: datetime
    # durante_pregao | apos_fechamento | antes_da_abertura | dia_sem_pregao | sem_horario
    timing: str

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
    """`ok`, `yield_abaixo` ou `yield_acima`. 2% e 30% passam."""
    if value < MIN_YIELD:
        return "yield_abaixo"
    if value > MAX_YIELD:
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
) -> BuyPlan:
    """Fechamento do primeiro pregão depois da data em que o documento ficou público.

    Noite (depois do call, fim de semana, feriado ou data sem hora): o pregão
    seguinte. Durante o pregão: também o pregão seguinte — não o call do mesmo
    dia. Antes da abertura vale a mesma data de publicação, porque o backtest
    não tinha horário e a regra congelada é por data.
    """
    if isinstance(published_at, datetime):
        local = published_at.astimezone(TZ) if published_at.tzinfo else published_at.replace(tzinfo=TZ)
        day = local.date()
        clock: time | None = local.timetz().replace(tzinfo=None)
    else:
        day = published_at
        clock = None
        time_known = False
    timing = _timing(day, clock, time_known, calendar)
    buy_date = calendar.next_session(day)
    return BuyPlan(
        buy_date=buy_date,
        deadline=calendar.deadline(buy_date),
        close_at=calendar.close_at(buy_date),
        timing=timing,
    )


def last_close_session(published_day: date, timing: str, calendar: Calendar) -> date:
    """Último fechamento já realizado quando o anúncio fica público.

    Durante o pregão e antes da abertura, o fechamento do dia ainda não existe.
    Depois do call, ou quando só há a data (tratada como fim do dia), o
    fechamento daquele pregão entra na conta se o dia teve pregão.
    """
    if timing in {"durante_pregao", "antes_da_abertura"}:
        return calendar.previous_session(published_day)
    if calendar.is_session(published_day):
        return published_day
    return calendar.previous_session(published_day)


def exit_session(data_com: date, calendar: Calendar) -> date:
    """Fechamento do 20º pregão depois da data-com. A data-com não conta."""
    return calendar.add_sessions(data_com, SESSIONS_AFTER_COM)


def has_rights(entry_session: date, data_com: date) -> bool:
    """A posição tem de estar aberta na data-com (compra naquele pregão ainda vale)."""
    return entry_session <= data_com


def slots_left(open_positions: int, limit: int = MAX_POSITIONS) -> int:
    return max(0, limit - open_positions)


def dividend_received(kind: str, gross: float, net: float | None, entry: date, data_com: date | None) -> float:
    """Provento que entra no retorno. JCP entra líquido. Sem direito, zero."""
    if data_com is None or not has_rights(entry, data_com):
        return 0.0
    if kind == "jcp":
        return net if net is not None else jcp_net(gross)
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
    """Dois documentos com o mesmo R$/ação (aviso e ata) são um evento só."""
    diff = abs(gross_a - gross_b)
    base = max(abs(gross_a), abs(gross_b), 1e-9)
    return diff <= 0.005 or diff / base <= 0.01


def aware(moment: datetime, tz: ZoneInfo = TZ) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=tz)
    return moment.astimezone(tz)
