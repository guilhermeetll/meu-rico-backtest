"""Decisão pura de um candidato: comprar, conferir, atualizar ou ignorar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from alertas_dividendo.calendar import Calendar
from alertas_dividendo.strategy import (
    MAX_POSITIONS,
    BuyPlan,
    aware,
    buy_plan,
    classify_yield,
    exit_session,
    has_rights,
    last_close_session,
    passes_liquidity,
    same_cash_event,
    yield_ratio,
)


@dataclass(frozen=True)
class Snapshot:
    last_close: float | None
    last_close_date: date | None
    last_close_source: str | None
    avg_volume: float | None
    volume_sessions: int


@dataclass(frozen=True)
class Prior:
    """Sinal já registrado com o mesmo papel e o mesmo R$/ação."""

    gross: float
    data_com: date | None
    kind: str


@dataclass(frozen=True)
class Decision:
    action: str  # comprar | conferir_valor | atualizar | ignorar
    reason: str
    yld: float | None = None
    plan: BuyPlan | None = None
    exit_date: date | None = None
    price_session: date | None = None


def _anchor(data_com: date | None, approval: date | None, published: date) -> date:
    return data_com or approval or published


def event_key(ticker: str, kind: str, gross: float, anchor: date) -> str:
    return f"{ticker}|{kind}|{gross:.4f}|{anchor.isoformat()}"


def decide(
    *,
    ticker: str,
    kind: str,
    gross: float | None,
    data_com: date | None,
    approval: date | None,
    published_at: datetime,
    time_known: bool,
    category_is_aviso_ou_ata: bool,
    snapshot: Snapshot,
    open_positions: int,
    priors: list[Prior],
    calendar: Calendar,
) -> Decision:
    """Aplica as regras na ordem: duplicata, liquidez, valor, yield, direito, vaga."""
    published_day = aware(published_at).date()
    plan = buy_plan(published_at, time_known, calendar)
    if gross is not None:
        for prior in priors:
            if prior.kind != kind:
                continue
            if not same_cash_event(prior.gross, gross):
                continue
            # Parcela com outra data-com é outro evento, mesmo com o mesmo R$/ação.
            if prior.data_com is not None and data_com is not None and prior.data_com != data_com:
                continue
            if prior.data_com is None and data_com is not None:
                if not has_rights(plan.buy_date, data_com):
                    return Decision("ignorar", "sem_direito", plan=plan)
                return Decision(
                    "atualizar",
                    "data_com",
                    plan=plan,
                    exit_date=exit_session(data_com, calendar),
                )
            return Decision("ignorar", "duplicado", plan=plan)

    if passes_liquidity(snapshot.avg_volume, snapshot.volume_sessions) == "historico_insuficiente":
        return Decision("ignorar", "historico_insuficiente", plan=plan)
    if passes_liquidity(snapshot.avg_volume, snapshot.volume_sessions) == "iliquido":
        return Decision("ignorar", "iliquido", plan=plan)

    if gross is None or snapshot.last_close is None or snapshot.last_close_date is None:
        if gross is None and category_is_aviso_ou_ata:
            return Decision("conferir_valor", "extracao_falhou", plan=plan)
        if gross is None:
            return Decision("ignorar", "extracao_falhou", plan=plan)
        return Decision("ignorar", "sem_preco", plan=plan)

    yld = yield_ratio(gross, snapshot.last_close)
    band = classify_yield(yld)
    if band != "ok":
        return Decision("ignorar", band, yld=yld, plan=plan)

    if data_com is not None and not has_rights(plan.buy_date, data_com):
        return Decision("ignorar", "sem_direito", yld=yld, plan=plan)

    if open_positions >= MAX_POSITIONS:
        return Decision("ignorar", "carteira_cheia", yld=yld, plan=plan)

    exit_date = exit_session(data_com, calendar) if data_com else None
    price_session = last_close_session(published_day, calendar)
    return Decision(
        "comprar",
        "ok",
        yld=yld,
        plan=plan,
        exit_date=exit_date,
        price_session=price_session,
    )
