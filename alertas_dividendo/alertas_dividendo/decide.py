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
    """Sinal já registrado do mesmo papel, com o R$/ação e a data do anúncio."""

    gross: float
    data_com: date | None
    kind: str
    approval: date | None = None
    signal_id: int | None = None


@dataclass(frozen=True)
class Decision:
    action: str  # comprar | conferir_valor | atualizar | corrigir_valor | ignorar
    reason: str
    yld: float | None = None
    plan: BuyPlan | None = None
    exit_date: date | None = None
    price_session: date | None = None
    prior_gross: float | None = None
    prior_id: int | None = None


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
            same_record = (
                prior.data_com is not None and data_com is not None and prior.data_com == data_com
            )
            same_announcement = (
                prior.approval is not None and approval is not None and prior.approval == approval
            )
            # Retificação: mesmo papel, mesmo anúncio e mesma data-com, com
            # outro R$/ação. Não abre segunda posição; quem chama atualiza.
            if same_record and same_announcement and not same_cash_event(prior.gross, gross):
                yld = yield_ratio(gross, snapshot.last_close) if snapshot.last_close else None
                band = classify_yield(yld) if yld is not None else "sem_preco"
                return Decision(
                    "corrigir_valor",
                    band if band != "ok" else "valor_corrigido",
                    yld=yld,
                    plan=plan,
                    exit_date=exit_session(data_com, calendar) if data_com else None,
                    prior_gross=prior.gross,
                    prior_id=prior.signal_id,
                )
            if not same_cash_event(prior.gross, gross):
                continue
            # Duplicata só com a mesma data-com, ou com o aviso anterior ainda
            # sem data-com e o mesmo anúncio. JCP trimestral do mesmo valor e
            # parcelas iguais com data-com diferente seguem como eventos novos.
            if same_record:
                return Decision("ignorar", "duplicado", plan=plan)
            if prior.data_com is None and same_announcement:
                if data_com is not None:
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
