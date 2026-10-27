"""Regras congeladas: yield, JCP, liquidez, prazo de compra, pregões, vaga e resultado."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from alertas_dividendo.calendar import TZ, Calendar
from alertas_dividendo.decide import Prior, Snapshot, decide
from alertas_dividendo.strategy import (
    SLOT_WEIGHT,
    average_volume,
    buy_plan,
    classify_yield,
    cumulative_result,
    dividend_received,
    exit_session,
    entry_reminder_due,
    has_rights,
    index_return,
    jcp_net,
    passes_liquidity,
    same_cash_event,
    trade_return,
    yield_ratio,
)

FIXTURE = Path(__file__).parent / "fixtures" / "dividendo_alertas_cvm.csv"
CAL = Calendar()


def _at(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=TZ)


def test_jcp_yield_uses_gross_and_net_is_15_percent():
    gross = 1.0
    net = jcp_net(gross)
    assert net == 0.85
    # Preço 40. Yield no bruto é 2,5%. No líquido seria 2,125% — não é a conta da regra.
    assert yield_ratio(gross, 40) == 0.025
    assert classify_yield(yield_ratio(gross, 40)) == "ok"
    assert dividend_received("jcp", gross, net, entry=_d(2026, 10, 22), data_com=_d(2026, 10, 30)) == net


def test_yield_band_includes_2_percent_and_excludes_30():
    assert classify_yield(0.02) == "ok"
    assert classify_yield(0.2999) == "ok"
    assert classify_yield(0.30) == "yield_acima"
    assert classify_yield(0.0199) == "yield_abaixo"
    assert classify_yield(0.3001) == "yield_acima"


def test_csv_fixture_saud3_passes_and_agro_price_math():
    rows = list(csv.DictReader(FIXTURE.open(encoding="utf-8")))
    saud = [row for row in rows if row["tk"] == "SAUD3" and row["rs_acao"]]
    assert len(saud) == 2
    assert same_cash_event(float(saud[0]["rs_acao"]), float(saud[1]["rs_acao"]))
    gross = float(saud[0]["rs_acao"])
    # O CSV usou o último fechamento que o script tinha na mão (14,40).
    assert classify_yield(yield_ratio(gross, 14.40)) == "ok"
    # COTAHIST do dia da publicação (21/10/2026) é 15,42. Continua acima de 2%.
    assert classify_yield(yield_ratio(gross, 15.42)) == "ok"
    assert saud[1]["data_com_txt"] == "30 de outubro de 2026"
    assert saud[0]["sinal_2pct"] == "True"


def _d(year, month, day):
    from datetime import date

    return date(year, month, day)


def test_liquidity_needs_60_sessions_at_or_above_one_million():
    assert passes_liquidity(1_000_000, 60) == "ok"
    assert passes_liquidity(999_999, 60) == "iliquido"
    assert passes_liquidity(5_000_000, 59) == "historico_insuficiente"
    volumes = [500_000.0] * 59 + [80_000_000.0] + [9_000_000.0]
    # O pregão futuro (9 mi) não entra: a média olha só o que já aconteceu.
    happened = volumes[:60]
    average, count = average_volume(happened)
    assert count == 60
    assert average == (500_000 * 59 + 80_000_000) / 60
    assert passes_liquidity(average, count) == "ok"
    short_avg, short_n = average_volume(volumes[:40])
    assert short_avg is None and short_n == 40


def test_night_buys_next_open_and_intraday_buys_the_same_close():
    night = buy_plan(_at(2026, 10, 21, 21, 0), True, CAL)
    intraday = buy_plan(_at(2026, 10, 21, 11, 0), True, CAL)
    after_call = buy_plan(_at(2026, 10, 21, 17, 37), True, CAL)
    assert night.timing == "apos_fechamento"
    assert intraday.timing == "durante_pregao"
    assert after_call.timing == "apos_fechamento"
    assert intraday.entry_type == "fechamento"
    assert intraday.entry_reason == "antes_de_1630"
    assert intraday.buy_date == _d(2026, 10, 21)
    assert intraday.deadline == _at(2026, 10, 21, 16, 30)
    assert night.entry_type == "abertura"
    assert night.entry_reason == "entrega_1630"
    assert night.buy_date == _d(2026, 10, 22)
    assert night.deadline == _at(2026, 10, 22, 10, 0)
    assert after_call.buy_date == night.buy_date
    assert after_call.deadline == night.deadline
    # 9:00 ainda é antes das 16:30: fechamento do dia, não a abertura.
    morning = buy_plan(_at(2026, 10, 21, 9, 0), True, CAL)
    assert morning.entry_type == "fechamento"
    assert morning.buy_date == _d(2026, 10, 21)


def test_friday_night_skips_the_weekend_and_friday_afternoon_buys_friday():
    night = buy_plan(_at(2026, 10, 23, 18, 30), True, CAL)
    intraday = buy_plan(_at(2026, 10, 23, 15, 0), True, CAL)
    assert intraday.entry_type == "fechamento"
    assert intraday.buy_date == _d(2026, 10, 23)
    assert night.entry_type == "abertura"
    assert night.buy_date == _d(2026, 10, 26)
    assert night.deadline == _at(2026, 10, 26, 10, 0)


def test_exit_reminder_follows_the_call_including_ash_wednesday():
    ash = _d(2026, 2, 18)
    assert CAL.hours(ash).call_start.hour == 17 and CAL.hours(ash).call_start.minute == 55
    assert CAL.near_close(_at(2026, 2, 18, 16, 30), ash) is False
    assert CAL.near_close(_at(2026, 2, 18, 17, 0), ash) is True
    regular = _d(2026, 10, 22)
    assert CAL.near_close(_at(2026, 10, 22, 15, 59), regular) is False
    assert CAL.near_close(_at(2026, 10, 22, 16, 0), regular) is True


def test_notice_at_1630_and_on_a_holiday_or_weekend_buys_the_next_open():
    """16:30 já é tarde para o fechamento do dia. Feriado e fim de semana abrem o pregão seguinte."""
    at_cutoff = buy_plan(_at(2026, 10, 21, 16, 30), True, CAL)
    at_call = buy_plan(_at(2026, 10, 21, 16, 55), True, CAL)
    at_close = buy_plan(_at(2026, 10, 21, 17, 0), True, CAL)
    assert at_cutoff.entry_type == at_call.entry_type == at_close.entry_type == "abertura"
    assert at_cutoff.entry_reason == "entrega_1630"
    assert at_call.timing == "durante_pregao"
    assert at_close.timing == "apos_fechamento"
    assert at_cutoff.buy_date == at_call.buy_date == at_close.buy_date == _d(2026, 10, 22)
    assert at_cutoff.deadline == _at(2026, 10, 22, 10, 0)
    holiday = buy_plan(_at(2026, 11, 2, 11, 0), True, CAL)
    assert holiday.timing == "dia_sem_pregao"
    assert holiday.entry_reason == "dia_sem_pregao"
    assert holiday.entry_type == "abertura"
    assert holiday.buy_date == _d(2026, 11, 3)
    assert holiday.deadline == _at(2026, 11, 3, 10, 0)
    weekend = buy_plan(_at(2026, 10, 24, 11, 0), True, CAL)
    assert weekend.entry_reason == "dia_sem_pregao"
    assert weekend.entry_type == "abertura"
    assert weekend.buy_date == _d(2026, 10, 26)
    assert weekend.deadline == _at(2026, 10, 26, 10, 0)
    # Cinzas fecha às 18:00, mas 16:30 já manda para a abertura do dia seguinte.
    ash_during = buy_plan(_at(2026, 2, 18, 17, 0), True, CAL)
    ash_close = buy_plan(_at(2026, 2, 18, 18, 0), True, CAL)
    ash_early = buy_plan(_at(2026, 2, 18, 15, 0), True, CAL)
    assert ash_during.timing == "durante_pregao"
    assert ash_close.timing == "apos_fechamento"
    assert ash_during.entry_type == ash_close.entry_type == "abertura"
    assert ash_during.buy_date == ash_close.buy_date == _d(2026, 2, 19)
    assert ash_during.deadline == _at(2026, 2, 19, 10, 0)
    assert ash_early.entry_type == "fechamento"
    assert ash_early.buy_date == _d(2026, 2, 18)
    assert ash_early.deadline == _at(2026, 2, 18, 16, 30)


def test_record_date_on_christmas_eve_or_a_holiday_is_not_a_session():
    from alertas_dividendo.engine import _session_or_keep

    assert not CAL.is_session(_d(2026, 12, 24))
    assert _session_or_keep(_d(2026, 12, 24), CAL) == _d(2026, 12, 23)
    assert exit_session(_d(2026, 12, 24), CAL) == exit_session(_d(2026, 12, 23), CAL)
    assert has_rights(_d(2026, 12, 23), _session_or_keep(_d(2026, 12, 24), CAL))
    assert not CAL.is_session(_d(2026, 11, 2))
    assert _session_or_keep(_d(2026, 11, 2), CAL) == _d(2026, 10, 30)
    assert exit_session(_d(2026, 11, 2), CAL) == CAL.add_sessions(_d(2026, 10, 30), 20)
    assert not CAL.is_session(_d(2026, 12, 31))
    assert _session_or_keep(_d(2026, 12, 31), CAL) == _d(2026, 12, 30)
    closed = {_d(2026, 12, 24), _d(2026, 12, 25), _d(2026, 12, 31), _d(2027, 1, 1)}
    for record in (_d(2026, 12, 23), _d(2026, 12, 24), _d(2026, 12, 30), _d(2026, 12, 31)):
        exit_day = exit_session(record, CAL)
        assert CAL.is_session(exit_day)
        assert exit_day not in closed
    kept = _session_or_keep(_d(2026, 12, 24), CAL)
    decision = decide(
        ticker="SAUD3",
        kind="dividendo",
        gross=0.50,
        data_com=kept,
        approval=_d(2026, 12, 10),
        published_at=_at(2026, 12, 10, 18, 0),
        time_known=True,
        category_is_aviso_ou_ata=True,
        snapshot=Snapshot(10.0, _d(2026, 12, 10), "COTAHIST", 2_000_000, 60),
        open_positions=0,
        priors=[],
        calendar=CAL,
    )
    assert decision.action == "comprar"
    assert decision.exit_date is not None
    assert CAL.is_session(decision.exit_date)
    assert decision.exit_date not in closed
    year_end = decide(
        ticker="SAUD3",
        kind="dividendo",
        gross=0.50,
        data_com=_session_or_keep(_d(2026, 12, 31), CAL),
        approval=_d(2026, 12, 10),
        published_at=_at(2026, 12, 10, 18, 0),
        time_known=True,
        category_is_aviso_ou_ata=True,
        snapshot=Snapshot(10.0, _d(2026, 12, 10), "COTAHIST", 2_000_000, 60),
        open_positions=0,
        priors=[],
        calendar=CAL,
    )
    assert year_end.action == "comprar"
    assert year_end.exit_date is not None
    assert CAL.is_session(year_end.exit_date)
    assert year_end.exit_date not in closed


def test_date_only_and_holiday_and_ash_wednesday_clock():
    date_only = buy_plan(_d(2026, 10, 21), False, CAL)
    assert date_only.timing == "sem_horario"
    assert date_only.entry_reason == "sem_horario"
    assert date_only.entry_type == "abertura"
    assert date_only.buy_date == _d(2026, 10, 22)
    assert date_only.deadline == _at(2026, 10, 22, 10, 0)
    holiday = buy_plan(_at(2026, 11, 2, 11, 0), True, CAL)
    assert holiday.timing == "dia_sem_pregao"
    assert holiday.buy_date == _d(2026, 11, 3)
    # 13/02/2026 é sexta. 16 e 17 são Carnaval. O pregão seguinte é a Cinzas, 18/02.
    carnival_friday = buy_plan(_at(2026, 2, 13, 18, 0), True, CAL)
    assert carnival_friday.entry_type == "abertura"
    assert carnival_friday.buy_date == _d(2026, 2, 18)
    assert carnival_friday.deadline == _at(2026, 2, 18, 13, 0)


def test_b3_2026_closures_and_exit_twenty_sessions_after_data_com():
    assert not CAL.is_session(_d(2026, 11, 2))  # Finados
    assert not CAL.is_session(_d(2026, 11, 20))  # Consciência Negra
    assert not CAL.is_session(_d(2026, 2, 16))
    assert not CAL.is_session(_d(2026, 2, 17))
    assert CAL.is_session(_d(2026, 2, 18))  # Cinzas tem pregão
    assert CAL.is_session(_d(2026, 7, 9))  # Revolução Constitucionalista, B3 abre
    assert not CAL.is_session(_d(2026, 10, 12))
    assert not CAL.is_session(_d(2026, 12, 24))
    assert not CAL.is_session(_d(2026, 12, 25))
    assert not CAL.is_session(_d(2026, 12, 31))
    # SAUD3: data-com 30/10/2026 (sexta). 20 pregões depois, pulando 2/11 e 20/11.
    assert exit_session(_d(2026, 10, 30), CAL) == _d(2026, 12, 1)
    # Semana com uma quarta fechada: a contagem não usa o feriado.
    assert not CAL.is_session(_d(2026, 11, 20))
    assert CAL.add_sessions(_d(2026, 11, 19), 1) == _d(2026, 11, 23)


def test_ten_slot_cap_ignores_the_eleventh():
    published = _at(2026, 10, 21, 17, 37)
    snap = Snapshot(15.42, _d(2026, 10, 21), "COTAHIST", 2_000_000, 60)
    common = dict(
        ticker="SAUD3",
        kind="dividendo",
        gross=0.342059542,
        data_com=_d(2026, 10, 30),
        approval=_d(2026, 10, 21),
        published_at=published,
        time_known=True,
        category_is_aviso_ou_ata=True,
        snapshot=snap,
        priors=[],
        calendar=CAL,
    )
    assert decide(open_positions=9, **common).action == "comprar"
    blocked = decide(open_positions=10, **common)
    assert blocked.action == "ignorar"
    assert blocked.reason == "carteira_cheia"


def test_dedupe_same_cash_event_and_data_com_update():
    published = _at(2026, 10, 21, 17, 44)
    snap = Snapshot(15.42, _d(2026, 10, 21), "COTAHIST", 2_000_000, 60)
    first = decide(
        ticker="SAUD3",
        kind="dividendo",
        gross=0.342059542,
        data_com=None,
        approval=_d(2026, 10, 21),
        published_at=published,
        time_known=True,
        category_is_aviso_ou_ata=True,
        snapshot=snap,
        open_positions=0,
        priors=[],
        calendar=CAL,
    )
    assert first.action == "comprar"
    again = decide(
        ticker="SAUD3",
        kind="dividendo",
        gross=0.34206,
        data_com=_d(2026, 10, 30),
        approval=_d(2026, 10, 21),
        published_at=published,
        time_known=True,
        category_is_aviso_ou_ata=True,
        snapshot=snap,
        open_positions=1,
        priors=[Prior(0.342059542, None, "dividendo", _d(2026, 10, 21))],
        calendar=CAL,
    )
    assert again.action == "atualizar"
    assert again.exit_date == _d(2026, 12, 1)
    duplicate = decide(
        ticker="SAUD3",
        kind="dividendo",
        gross=0.342059542,
        data_com=_d(2026, 10, 30),
        approval=_d(2026, 10, 21),
        published_at=published,
        time_known=True,
        category_is_aviso_ou_ata=True,
        snapshot=snap,
        open_positions=1,
        priors=[Prior(0.342059542, _d(2026, 10, 30), "dividendo", _d(2026, 10, 21))],
        calendar=CAL,
    )
    assert duplicate.action == "ignorar" and duplicate.reason == "duplicado"
    assert not same_cash_event(0.0378, 0.040)
    assert same_cash_event(0.342059542, 0.34206)


def _cash(gross, data_com, approval, kind="dividendo", *, price=1.0, published=None):
    moment = published or _at(2026, 10, 15, 18, 0)
    return dict(
        ticker="MTRE3",
        kind=kind,
        gross=gross,
        data_com=data_com,
        approval=approval,
        published_at=moment,
        time_known=True,
        category_is_aviso_ou_ata=True,
        snapshot=Snapshot(price, moment.date(), "COTAHIST", 2_000_000, 60),
        open_positions=0,
        calendar=CAL,
    )


def test_equal_installments_and_quarterly_jcp_are_not_duplicates():
    approval = _d(2026, 10, 15)
    first = Prior(0.03781618347, _d(2026, 10, 20), "dividendo", approval)
    second = decide(
        **_cash(0.03781618347, _d(2026, 11, 13), approval),
        priors=[first],
    )
    assert second.action == "comprar"
    assert second.reason == "ok"
    # Mesmo valor, outro trimestre: outro anúncio e outra data-com.
    june_at = _at(2026, 6, 15, 18, 0)
    march = Prior(0.50, _d(2026, 3, 31), "jcp", _d(2026, 3, 12))
    june = decide(
        **_cash(0.50, _d(2026, 6, 30), _d(2026, 6, 15), kind="jcp", price=10.0, published=june_at),
        priors=[march],
    )
    assert june.action == "comprar"
    # Sem data-com no aviso novo, o trimestre antigo com data não o engole.
    undated = decide(
        **_cash(0.50, None, _d(2026, 6, 15), kind="jcp", price=10.0, published=june_at),
        priors=[march],
    )
    assert undated.reason != "duplicado"
    assert undated.action == "comprar"
    # Dois avisos sem data-com só são o mesmo evento se a aprovação coincidir.
    same_notice = decide(
        **_cash(0.50, None, _d(2026, 6, 15), kind="jcp", price=10.0, published=june_at),
        priors=[Prior(0.50, None, "jcp", _d(2026, 6, 15))],
    )
    assert same_notice.reason == "duplicado"
    other_quarter = decide(
        **_cash(0.50, None, _d(2026, 6, 15), kind="jcp", price=10.0, published=june_at),
        priors=[Prior(0.50, None, "jcp", _d(2026, 3, 12))],
    )
    assert other_quarter.action == "comprar"
    # R$ 0,0378 e R$ 0,040 não são o mesmo caixa. Na mesma data-com e no mesmo
    # anúncio, o valor novo corrige o sinal. Com outra aprovação, é outro evento.
    near = decide(
        **_cash(0.040, _d(2026, 10, 20), approval),
        priors=[Prior(0.0378, _d(2026, 10, 20), "dividendo", approval, 7)],
    )
    assert near.action == "corrigir_valor"
    assert near.reason == "valor_corrigido"
    assert near.prior_gross == 0.0378
    assert near.prior_id == 7
    assert near.yld == 0.040
    other = decide(
        **_cash(0.040, _d(2026, 10, 20), _d(2026, 11, 1)),
        priors=[Prior(0.0378, _d(2026, 10, 20), "dividendo", approval)],
    )
    assert other.action == "comprar"


def test_retification_recomputes_yield_and_does_not_open_again():
    approval = _d(2026, 10, 21)
    published = _at(2026, 10, 21, 18, 0)
    prior = Prior(0.50, _d(2026, 10, 30), "dividendo", approval, 3)
    corrected = decide(
        ticker="ZZZZ3",
        kind="dividendo",
        gross=0.80,
        data_com=_d(2026, 10, 30),
        approval=approval,
        published_at=published,
        time_known=True,
        category_is_aviso_ou_ata=True,
        snapshot=Snapshot(10.0, _d(2026, 10, 21), "COTAHIST", 2_000_000, 60),
        open_positions=1,
        priors=[prior],
        calendar=CAL,
    )
    assert corrected.action == "corrigir_valor"
    assert corrected.reason == "valor_corrigido"
    assert corrected.yld == 0.08
    assert corrected.prior_id == 3
    below = decide(
        ticker="ZZZZ3",
        kind="dividendo",
        gross=0.10,
        data_com=_d(2026, 10, 30),
        approval=approval,
        published_at=published,
        time_known=True,
        category_is_aviso_ou_ata=True,
        snapshot=Snapshot(10.0, _d(2026, 10, 21), "COTAHIST", 2_000_000, 60),
        open_positions=1,
        priors=[prior],
        calendar=CAL,
    )
    assert below.action == "corrigir_valor"
    assert below.reason == "yield_abaixo"
    assert below.yld == 0.01


def test_saud3_decision_matches_the_frozen_trade():
    decision = decide(
        ticker="SAUD3",
        kind="dividendo",
        gross=0.342059542,
        data_com=_d(2026, 10, 30),
        approval=_d(2026, 10, 21),
        published_at=_at(2026, 10, 21, 17, 37),
        time_known=True,
        category_is_aviso_ou_ata=True,
        snapshot=Snapshot(15.42, _d(2026, 10, 21), "COTAHIST", 50_000_000, 60),
        open_positions=0,
        priors=[],
        calendar=CAL,
    )
    assert decision.action == "comprar"
    assert decision.plan is not None
    assert decision.plan.entry_type == "abertura"
    assert decision.plan.buy_date == _d(2026, 10, 22)
    assert decision.price_session == _d(2026, 10, 22)
    assert decision.exit_date == _d(2026, 12, 1)
    assert decision.yld == 0.342059542 / 15.42


def test_late_detection_of_a_morning_notice_buys_the_next_open():
    """Aviso antes das 16:30, visto só depois: abertura do pregão seguinte."""
    delivered = _at(2026, 10, 21, 14, 0)
    in_time = buy_plan(delivered, True, CAL, detected_at=_at(2026, 10, 21, 15, 0))
    assert in_time.entry_type == "fechamento"
    assert in_time.late_detection is False
    assert in_time.buy_date == _d(2026, 10, 21)
    late = buy_plan(delivered, True, CAL, detected_at=_at(2026, 10, 21, 16, 40))
    assert late.entry_type == "abertura"
    assert late.entry_reason == "deteccao_tardia"
    assert late.late_detection is True
    assert late.buy_date == _d(2026, 10, 22)
    assert late.deadline == _at(2026, 10, 22, 10, 0)
    exact = buy_plan(delivered, True, CAL, detected_at=_at(2026, 10, 21, 16, 30))
    assert exact.late_detection is True
    assert exact.entry_type == "abertura"


def test_entry_reminder_goes_out_before_10():
    day = _d(2026, 10, 22)
    assert entry_reminder_due(_at(2026, 10, 22, 8, 0), day, "abertura") is True
    assert entry_reminder_due(_at(2026, 10, 22, 9, 0), day, "abertura") is True
    assert entry_reminder_due(_at(2026, 10, 22, 9, 59), day, "abertura") is True
    assert entry_reminder_due(_at(2026, 10, 22, 10, 0), day, "abertura") is False
    assert entry_reminder_due(_at(2026, 10, 22, 7, 59), day, "abertura") is False
    assert entry_reminder_due(_at(2026, 10, 22, 9, 0), day, "fechamento") is False
    assert entry_reminder_due(_at(2026, 10, 21, 9, 0), day, "abertura") is False
    ash = _d(2026, 2, 18)
    assert entry_reminder_due(_at(2026, 2, 18, 9, 0), ash, "abertura") is True


def test_no_rights_when_buy_is_after_data_com():
    decision = decide(
        ticker="IGTI11",
        kind="dividendo",
        gross=0.50,
        data_com=_d(2026, 10, 15),
        approval=_d(2026, 10, 20),
        published_at=_at(2026, 10, 20, 18, 58),
        time_known=True,
        category_is_aviso_ou_ata=True,
        snapshot=Snapshot(10.0, _d(2026, 10, 20), "COTAHIST", 100_000_000, 60),
        open_positions=0,
        priors=[],
        calendar=CAL,
    )
    assert decision.reason == "sem_direito"


def test_trade_result_and_cumulative_slot_math():
    # Entrada 16,07, saída 17,00, dividendo 0,342059542.
    entry, exit_price, dividend = 16.07, 17.00, 0.342059542
    result = trade_return(entry, exit_price, dividend)
    assert result == (exit_price - entry + dividend) / entry
    assert dividend_received("dividendo", dividend, None, _d(2026, 10, 22), _d(2026, 10, 30)) == dividend
    # Misto: 0,12 de dividendo + 0,08 de JCP. O caixa retém 15% só no JCP.
    mixed_net = 0.12 + 0.08 * 0.85
    assert dividend_received("misto", 0.20, mixed_net, _d(2026, 10, 22), _d(2026, 10, 30)) == mixed_net
    assert yield_ratio(0.20, 10) == 0.02
    assert dividend_received("dividendo", dividend, None, _d(2026, 11, 3), _d(2026, 10, 30)) == 0
    assert abs(index_return(200_000, 210_000) - 0.05) < 1e-12
    # Dois trades, +10% e -5%, cada um com 10% do capital.
    cumulative = cumulative_result([0.10, -0.05])
    equity = (1 + SLOT_WEIGHT * 0.10) * (1 + SLOT_WEIGHT * -0.05)
    assert cumulative == equity - 1
