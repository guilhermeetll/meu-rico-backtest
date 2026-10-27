"""Consulta ENET do dia, hora de entrega e parada no captcha."""

from datetime import date, datetime

import pytest

from alertas_dividendo.calendar import TZ, Calendar
from alertas_dividendo.config import Config
from alertas_dividendo.cvm_parse import parse_enet_dados
from alertas_dividendo.engine import Report, _ingest
from alertas_dividendo.sources import CaptchaRequired, enet_body, fetch_enet, month_spans
from alertas_dividendo.store import Store
from alertas_dividendo.strategy import buy_plan, last_close_session

CAL = Calendar()

ROW = (
    "02012-5$&HOSPITAL MATER DEI S.A.$&Aviso aos Acionistas$&Aviso aos Acionistas$&"
    "Dividendos$&21/10/2026$&21/10/2026 17:37$&Ativo$&1$&$&"
    "<a href=\"javascript:OpenDownloadDocumentos('900','1','147309','IPE')\">Download</a>$&"
    "Pagamento de dividendos intermediários&*"
)
ROW_DIA = ROW.replace("21/10/2026 17:37", "21/10/2026 11:02")


def test_corpo_do_pedido_e_o_da_consulta_publica():
    body = enet_body(date(2026, 10, 27), date(2026, 10, 27))
    expected = (
        "{ dataDe: '27/10/2026', dataAte: '27/10/2026' , empresa: '', setorAtividade: '-1', "
        "categoriaEmissor: '-1', situacaoEmissor: '-1', tipoParticipante: '-1', dataReferencia: '', "
        "categoria: 'IPE_3_-1_-1,IPE_4_-1_-1,IPE_6_-1_-1,IPE_2_-1_-1,IPE_1_-1_-1,IPE_107_-1_-1', "
        "periodo: '2', horaIni: '', horaFim: '', palavraChave:'',ultimaDtRef:'false', "
        "tipoEmpresa:'0', token: '', versaoCaptcha: ''}"
    )
    assert body == expected
    assert month_spans(date(2026, 10, 27), date(2026, 10, 27)) == [
        (date(2026, 10, 27), date(2026, 10, 27))
    ]


def test_hoje_faz_um_pedido_e_nao_pausa():
    calls = []

    def poster(url, body):
        calls.append((url, body))
        return {"d": {"dados": ROW, "SolicitarCaptcha": "N"}}

    def sleeper(_seconds):
        raise AssertionError("um dia só não deve pausar")

    filings = fetch_enet(
        date(2026, 10, 21),
        date(2026, 10, 21),
        sleeper=sleeper,
        poster=poster,
    )
    assert len(calls) == 1
    assert len(filings) == 1
    assert filings[0].time_known is True
    assert filings[0].published_at == datetime(2026, 10, 21, 17, 37, tzinfo=TZ)
    assert filings[0].protocol == "147309"


def test_http_500_repete_uma_vez_e_captcha_nao():
    calls = {"n": 0}
    sleeps = []

    def poster(url, body):
        calls["n"] += 1
        if calls["n"] == 1:
            raise type("HTTPError", (Exception,), {})("500")
        return {"d": {"dados": ROW, "SolicitarCaptcha": "N"}}

    filings = fetch_enet(
        date(2026, 10, 27),
        date(2026, 10, 27),
        pause=5,
        sleeper=sleeps.append,
        poster=poster,
    )
    assert calls["n"] == 2
    assert sleeps == [5]
    assert len(filings) == 1

    calls["n"] = 0

    def captcha_after_500(url, body):
        calls["n"] += 1
        if calls["n"] == 1:
            raise type("HTTPError", (Exception,), {})("500")
        return {"d": {"dados": "", "SolicitarCaptcha": "S"}}

    with pytest.raises(CaptchaRequired):
        fetch_enet(
            date(2026, 10, 27),
            date(2026, 10, 27),
            pause=5,
            sleeper=sleeps.append,
            poster=captcha_after_500,
        )
    assert calls["n"] == 2


def test_captcha_para_no_segundo_mes_e_nao_repete():
    calls = []
    sleeps = []

    def poster(url, body):
        calls.append(body)
        if len(calls) == 1:
            return {"d": {"dados": ROW, "SolicitarCaptcha": "N"}}
        return {"d": {"dados": "", "SolicitarCaptcha": "S"}}

    with pytest.raises(CaptchaRequired) as caught:
        fetch_enet(
            date(2026, 10, 20),
            date(2026, 11, 3),
            pause=5,
            sleeper=sleeps.append,
            poster=poster,
        )
    assert len(calls) == 2
    assert sleeps == [5]
    assert len(caught.value.partial) == 1
    assert "01/11/2026" in calls[1]


def test_hora_de_entrega_nao_muda_o_fechamento_do_yield():
    """A hora fica no rótulo. O yield usa o pregão seguinte, nos dois horários."""
    night = parse_enet_dados(ROW)[0]
    day = parse_enet_dados(ROW_DIA)[0]
    night_plan = buy_plan(night.published_at, night.time_known, CAL)
    day_plan = buy_plan(day.published_at, day.time_known, CAL)
    assert night_plan.timing == "apos_fechamento"
    assert day_plan.timing == "durante_pregao"
    assert night_plan.buy_date == date(2026, 10, 22)
    assert day_plan.buy_date == date(2026, 10, 22)
    assert last_close_session(night.published_at.date(), CAL) == date(2026, 10, 22)
    assert last_close_session(day.published_at.date(), CAL) == date(2026, 10, 22)


def test_captcha_avisa_uma_vez_e_nao_consulta_de_novo(tmp_path, monkeypatch):
    import alertas_dividendo.engine as engine

    calls = {"n": 0}

    def boom(start, end):
        calls["n"] += 1
        raise CaptchaRequired("CVM pediu captcha — parando (não contornar).")

    monkeypatch.setattr(engine, "fetch_enet", boom)
    monkeypatch.setattr(engine, "fetch_ipe", lambda year: [])
    now = datetime(2026, 10, 27, 11, 0, tzinfo=TZ)
    cfg = Config(
        data_dir=tmp_path,
        token="",
        chat_id="",
        simulacao=True,
        lookback_days=0,
        dry_run=True,
        now=now,
    )
    store = Store(tmp_path / "alertas.sqlite")
    try:
        first = Report(now=now, dry_run=True)
        _ingest(cfg, store, first)
        assert calls["n"] == 1
        assert store.meta_get("enet_captcha_day") == "2026-10-27"
        assert len(first.messages) == 1
        assert first.messages[0].startswith("SIMULAÇÃO")
        assert "captcha" in first.messages[0].lower()
        second = Report(now=now, dry_run=True)
        _ingest(cfg, store, second)
        assert calls["n"] == 1
        assert second.messages == []
        assert any("pausa" in item for item in second.errors)
    finally:
        store.close()
