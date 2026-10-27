"""Varredura duas vezes: extração falha não pode derrubar o SQLite, e prazo vencido não abre posição."""

from datetime import date, datetime, timedelta

import pytest

from alertas_dividendo.calendar import TZ, Calendar
from alertas_dividendo.config import Config
from alertas_dividendo.engine import Report, _advance, _scan, b3_matches, realized_returns
from alertas_dividendo.strategy import exit_session
from alertas_dividendo.store import Store

CAL = Calendar()


@pytest.fixture(autouse=True)
def _no_live_b3(monkeypatch):
    monkeypatch.setattr("alertas_dividendo.engine.fetch_b3_dividends", lambda _issuer: [])


def _cfg(folder, moment: datetime, *, dry_run: bool) -> Config:
    return Config(
        data_dir=folder,
        token="token",
        chat_id="1",
        simulacao=True,
        lookback_days=20,
        dry_run=dry_run,
        now=moment,
    )


def _seed(store: Store, protocol: str, text: str, published: datetime) -> None:
    store.replace_issuers([("99999", "ZZZZ", "Empresa Teste")])
    store.upsert_document(
        {
            "protocol": protocol,
            "version": 1,
            "cvm_code": "99999",
            "company_name": "Empresa Teste",
            "category": "Aviso aos Acionistas",
            "subject": "Dividendos",
            "species": "",
            "published_at": published.isoformat(timespec="minutes"),
            "time_known": 1,
            "reference_date": published.date().isoformat(),
            "url": "https://exemplo.test/doc",
            "source": "teste",
            "active": 1,
            "first_seen": published.isoformat(timespec="seconds"),
        }
    )
    store.save_extraction(protocol, True, text, "")
    day = published.date()
    rows = []
    while len(rows) < 60:
        if CAL.is_session(day):
            rows.append(("ZZZZ3", day.isoformat(), 10.0, 2_000_000.0, "teste"))
        day -= timedelta(days=1)
    store.insert_quotes_if_absent(rows)
    store.commit()


def test_failed_extraction_scan_twice_does_not_crash(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("alertas_dividendo.engine.send_message", lambda *args: sent.append(args[-1]))
    store = Store(tmp_path / "alertas.sqlite")
    published = datetime(2026, 10, 26, 18, 0, tzinfo=TZ)
    _seed(store, "9001", "Aviso aos acionistas sobre dividendos, sem valor por acao.", published)
    # 26/10 depois do call: a compra é no fechamento de 27/10. Às 11h o prazo ainda está aberto.
    cfg = _cfg(tmp_path, datetime(2026, 10, 27, 11, 0, tzinfo=TZ), dry_run=False)
    try:
        first = Report(now=cfg.now, dry_run=False)
        _scan(cfg, store, CAL, first)
        second = Report(now=cfg.now, dry_run=False)
        _scan(cfg, store, CAL, second)
        rows = store.conn.execute("SELECT status, ignore_reason FROM signals").fetchall()
        assert len(rows) == 1
        assert rows[0]["status"] == "conferir"
        assert store.open_position_count() == 0
        assert len(sent) == 1
        assert "CONFERIR VALOR" in sent[0]
        assert second.messages == []
    finally:
        store.close()


def test_late_conferir_is_ignored_without_alert(tmp_path, monkeypatch):
    """MTRE3 protocolo 1577530: publicado 15/10 18:00, compra no fechamento de 16/10."""
    sent = []
    monkeypatch.setattr("alertas_dividendo.engine.send_message", lambda *args: sent.append(args[-1]))
    store = Store(tmp_path / "alertas.sqlite")
    published = datetime(2026, 10, 15, 18, 0, tzinfo=TZ)
    _seed(store, "1577530", "Aviso de dividendos em tres parcelas, sem um unico valor por acao.", published)
    cfg = _cfg(tmp_path, datetime(2026, 10, 27, 16, 0, tzinfo=TZ), dry_run=False)
    try:
        first = Report(now=cfg.now, dry_run=False)
        _scan(cfg, store, CAL, first)
        assert sent == []
        assert first.messages == []
        assert any("1577530" in line and "prazo de compra já passou" in line for line in first.ignored)
        signal = store.conn.execute(
            "SELECT status, ignore_reason, buy_date FROM signals"
        ).fetchone()
        assert signal["status"] == "ignorado"
        assert signal["ignore_reason"] == "prazo de compra já passou"
        assert signal["buy_date"] == "2026-10-16"
        assert store.open_position_count() == 0
        second = Report(now=cfg.now, dry_run=False)
        _scan(cfg, store, CAL, second)
        assert store.conn.execute("SELECT COUNT(*) AS n FROM signals").fetchone()["n"] == 1
        assert second.messages == []
        assert sent == []
    finally:
        store.close()


def test_missed_buy_deadline_is_ignored_and_not_opened(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("alertas_dividendo.engine.send_message", lambda *args: sent.append(args[-1]))
    store = Store(tmp_path / "alertas.sqlite")
    published = datetime(2026, 10, 21, 18, 0, tzinfo=TZ)
    _seed(
        store,
        "9002",
        "Aprovaram dividendos de R$ 0,50 por ação. "
        "Farão jus os detentores de ações em 30 de outubro de 2026.",
        published,
    )
    cfg = _cfg(tmp_path, datetime(2026, 10, 27, 12, 0, tzinfo=TZ), dry_run=False)
    try:
        first = Report(now=cfg.now, dry_run=False)
        _scan(cfg, store, CAL, first)
        assert any("prazo de compra já passou" in line for line in first.ignored)
        assert first.messages == []
        assert sent == []
        assert store.open_position_count() == 0
        signal = store.conn.execute("SELECT status, ignore_reason FROM signals").fetchone()
        assert signal["status"] == "ignorado"
        assert signal["ignore_reason"] == "prazo de compra já passou"
        second = Report(now=cfg.now, dry_run=False)
        _scan(cfg, store, CAL, second)
        assert store.conn.execute("SELECT COUNT(*) AS n FROM signals").fetchone()["n"] == 1
        assert store.conn.execute("SELECT COUNT(*) AS n FROM positions").fetchone()["n"] == 0
        assert second.messages == []
        store.insert_position(
            {
                "signal_id": 999,
                "ticker": "OUTR3",
                "status": "encerrada",
                "entry_date": "2026-10-01",
                "entry_price": 10.0,
                "exit_date": "2026-10-20",
                "exit_price": 11.0,
                "data_com": "2026-10-02",
                "kind": "dividendo",
                "gross_per_share": 0.5,
                "trade_return": 0.15,
                "opened_at": cfg.now.isoformat(),
                "closed_at": cfg.now.isoformat(),
            }
        )
        store.conn.execute(
            """
            INSERT INTO positions (
                signal_id, ticker, status, entry_date, entry_price, trade_return, opened_at
            ) VALUES (998, 'VAZI3', 'encerrada', '2026-10-01', NULL, 9.9, ?)
            """,
            (cfg.now.isoformat(),),
        )
        assert realized_returns(store) == [0.15]
    finally:
        store.close()


_DIVIDEND = (
    "Aprovaram dividendos de R$ 0,50 por ação. "
    "Farão jus os detentores de ações em 30 de outubro de 2026."
)


def test_intraday_notice_alerts_with_the_latest_close_labeled(tmp_path, monkeypatch):
    """O fechamento de entrada é o pregão seguinte e só sai no call da compra.

    Aviso no meio do pregão: não espera esse fechamento (já seria tarde) e
    avisa com o último disponível, dizendo a data. A hora fica só no texto.
    """
    sent = []
    monkeypatch.setattr("alertas_dividendo.engine.send_message", lambda *args: sent.append(args[-1]))
    store = Store(tmp_path / "alertas.sqlite")
    published = datetime(2026, 10, 21, 14, 0, tzinfo=TZ)
    _seed(store, "9101", _DIVIDEND, published)
    # O seed conta o dia 21. No meio do pregão esse fechamento ainda não existe;
    # reponho um pregão antigo para a média de 60 continuar válida.
    store.conn.execute("DELETE FROM quotes WHERE session_date >= '2026-10-21'")
    oldest = date.fromisoformat(store.conn.execute("SELECT MIN(session_date) AS d FROM quotes").fetchone()["d"])
    extra = oldest
    while True:
        extra -= timedelta(days=1)
        if CAL.is_session(extra):
            break
    store.insert_quotes_if_absent([("ZZZZ3", extra.isoformat(), 10.0, 2_000_000.0, "teste")])
    store.commit()
    cfg = _cfg(tmp_path, datetime(2026, 10, 21, 15, 0, tzinfo=TZ), dry_run=False)
    try:
        first = Report(now=cfg.now, dry_run=False)
        _scan(cfg, store, CAL, first)
        assert len(sent) == 1
        body = sent[0]
        assert "durante o pregão" in body
        assert "último fechamento disponível" in body
        assert "20/10/2026" in body
        assert "22/10/2026" in body
        assert "ainda não saiu" in body
        signal = store.conn.execute(
            "SELECT status, last_close_date, timing, buy_date FROM signals"
        ).fetchone()
        assert signal["status"] == "aguardando_compra"
        assert signal["last_close_date"] == "2026-10-20"
        assert signal["timing"] == "durante_pregao"
        assert signal["buy_date"] == "2026-10-22"
        assert store.open_position_count() == 1
        second = Report(now=cfg.now, dry_run=False)
        _scan(cfg, store, CAL, second)
        assert len(sent) == 1
        assert store.conn.execute("SELECT COUNT(*) AS n FROM signals").fetchone()["n"] == 1
    finally:
        store.close()


def test_after_close_uses_the_publication_close_still_labeled_as_proxy(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("alertas_dividendo.engine.send_message", lambda *args: sent.append(args[-1]))
    store = Store(tmp_path / "alertas.sqlite")
    published = datetime(2026, 10, 21, 18, 0, tzinfo=TZ)
    _seed(store, "9102", _DIVIDEND, published)
    cfg = _cfg(tmp_path, datetime(2026, 10, 21, 19, 0, tzinfo=TZ), dry_run=False)
    try:
        _scan(cfg, store, CAL, Report(now=cfg.now, dry_run=False))
        assert len(sent) == 1
        body = sent[0]
        assert "depois do fechamento" in body
        assert "último fechamento disponível" in body
        assert "21/10/2026" in body
        assert "22/10/2026" in body
        signal = store.conn.execute("SELECT last_close_date, timing, buy_date FROM signals").fetchone()
        assert signal["last_close_date"] == "2026-10-21"
        assert signal["timing"] == "apos_fechamento"
        assert signal["buy_date"] == "2026-10-22"
    finally:
        store.close()


def test_waits_for_a_close_that_prints_before_the_buy_deadline(tmp_path, monkeypatch):
    """Se o fechamento de referência imprime antes do prazo, a hora seguinte espera por ele."""
    sent = []
    monkeypatch.setattr("alertas_dividendo.engine.send_message", lambda *args: sent.append(args[-1]))
    monkeypatch.setattr(
        "alertas_dividendo.engine.last_close_session",
        lambda _day, _calendar: date(2026, 10, 21),
    )
    store = Store(tmp_path / "alertas.sqlite")
    published = datetime(2026, 10, 21, 14, 0, tzinfo=TZ)
    _seed(store, "9103", _DIVIDEND, published)
    early = _cfg(tmp_path, datetime(2026, 10, 21, 15, 0, tzinfo=TZ), dry_run=False)
    try:
        first = Report(now=early.now, dry_run=False)
        _scan(early, store, CAL, first)
        assert sent == []
        assert any("aguardando fechamento de 2026-10-21" in line for line in first.ignored)
        assert store.conn.execute("SELECT COUNT(*) AS n FROM signals").fetchone()["n"] == 0
        later = _cfg(tmp_path, datetime(2026, 10, 21, 18, 0, tzinfo=TZ), dry_run=False)
        _scan(later, store, CAL, Report(now=later.now, dry_run=False))
        assert len(sent) == 1
        assert "fechamento de entrada" in sent[0]
        assert "último fechamento disponível" not in sent[0]
        assert "21/10/2026" in sent[0]
        assert "durante o pregão" in sent[0]
        assert store.conn.execute("SELECT COUNT(*) AS n FROM signals").fetchone()["n"] == 1
        assert store.conn.execute("SELECT last_close_date FROM signals").fetchone()["last_close_date"] == "2026-10-21"
    finally:
        store.close()


def test_exit_reminder_waits_on_ash_wednesday(tmp_path):
    store = Store(tmp_path / "alertas.sqlite")
    try:
        signal_id = store.insert_signal(
            {
                "dedupe_key": "lembrete-teste",
                "protocol": "1",
                "ticker": "ZZZZ3",
                "status": "aberta",
                "kind": "dividendo",
                "gross_per_share": 0.1,
                "created_at": "2026-02-10T10:00:00-03:00",
            }
        )
        store.insert_position(
            {
                "signal_id": signal_id,
                "ticker": "ZZZZ3",
                "status": "aberta",
                "entry_date": "2026-02-10",
                "entry_price": 10.0,
                "entry_source": "teste",
                "exit_date": "2026-02-18",
                "data_com": "2026-02-11",
                "kind": "dividendo",
                "gross_per_share": 0.1,
                "opened_at": "2026-02-10T18:00:00-03:00",
            }
        )
        early = _cfg(tmp_path, datetime(2026, 2, 18, 16, 30, tzinfo=TZ), dry_run=True)
        early_report = Report(now=early.now, dry_run=True)
        _advance(early, store, CAL, early_report, reminders_only=True)
        assert early_report.messages == []
        due = _cfg(tmp_path, datetime(2026, 2, 18, 17, 0, tzinfo=TZ), dry_run=True)
        due_report = Report(now=due.now, dry_run=True)
        _advance(due, store, CAL, due_report, reminders_only=True)
        assert len(due_report.messages) == 1
        assert "LEMBRETE DE SAÍDA" in due_report.messages[0]
        assert "RESULTADO" not in due_report.messages[0]
    finally:
        store.close()


def _position_on_exit(store: Store, exit_day: str, key: str) -> None:
    signal_id = store.insert_signal(
        {
            "dedupe_key": key,
            "protocol": key,
            "ticker": "ZZZZ3",
            "status": "aberta",
            "kind": "dividendo",
            "gross_per_share": 0.1,
            "created_at": "2026-02-10T10:00:00-03:00",
        }
    )
    store.insert_position(
        {
            "signal_id": signal_id,
            "ticker": "ZZZZ3",
            "status": "aberta",
            "entry_date": "2026-02-10",
            "entry_price": 10.0,
            "entry_source": "teste",
            "exit_date": exit_day,
            "data_com": "2026-02-11",
            "kind": "dividendo",
            "gross_per_share": 0.1,
            "opened_at": "2026-02-10T18:00:00-03:00",
        }
    )
    store.insert_quotes_if_absent([("ZZZZ3", exit_day, 11.0, 2_000_000.0, "teste")])
    store.commit()


def test_exit_reminder_once_per_position_across_the_weekday_clocks(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("alertas_dividendo.engine.send_message", lambda *_args, **_kwargs: sent.append(_args[-1]))
    monkeypatch.setattr(
        "alertas_dividendo.engine.yahoo_close",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("yahoo")),
    )
    store = Store(tmp_path / "alertas.sqlite")
    try:
        _position_on_exit(store, "2026-10-22", "saida-comum")
        for hour, minute in ((16, 0), (16, 55), (17, 0), (17, 55)):
            cfg = _cfg(tmp_path, datetime(2026, 10, 22, hour, minute, tzinfo=TZ), dry_run=False)
            _advance(cfg, store, CAL, Report(now=cfg.now, dry_run=False), reminders_only=True)
        reminders = [body for body in sent if "LEMBRETE DE SAÍDA" in body]
        assert len(reminders) == 1
        stored = store.conn.execute(
            "SELECT status FROM alerts WHERE kind='lembrete'"
        ).fetchall()
        assert len(stored) == 1
        assert stored[0]["status"] == "sent"
    finally:
        store.close()


def test_ash_wednesday_reminder_still_once_when_the_early_clocks_are_too_soon(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("alertas_dividendo.engine.send_message", lambda *_args, **_kwargs: sent.append(_args[-1]))
    store = Store(tmp_path / "alertas.sqlite")
    try:
        _position_on_exit(store, "2026-02-18", "saida-cinzas")
        for hour, minute in ((16, 0), (16, 55), (17, 0), (17, 55)):
            cfg = _cfg(tmp_path, datetime(2026, 2, 18, hour, minute, tzinfo=TZ), dry_run=False)
            report = Report(now=cfg.now, dry_run=False)
            _advance(cfg, store, CAL, report, reminders_only=True)
        reminders = [body for body in sent if "LEMBRETE DE SAÍDA" in body]
        assert len(reminders) == 1
        assert store.conn.execute("SELECT COUNT(*) AS n FROM alerts WHERE kind='lembrete'").fetchone()["n"] == 1
        assert store.conn.execute("SELECT status FROM positions").fetchone()["status"] == "aberta"
    finally:
        store.close()


_MTRE = """
aprovou dividendos intermediários no montante de R$ 12.000.000,00,
equivalente a R$ 0,11344855041 por ação ordinária.
O pagamento será efetuado em 3 (três) parcelas de R$ 4.000.000,00.
   01ª      4.000.000,00    0,03781618347       15/10/2026   20/10/2026   21/10/2026   30/10/2026
   02ª      4.000.000,00    0,03781618347       15/10/2026   13/11/2026   16/11/2026   27/11/2026
   03ª      4.000.000,00    0,03781618347       15/10/2026   11/12/2026   14/12/2026   23/12/2026
  Total    12.000.000,00    0,11344855041
"""

_SAME_COM = (
    "Aprovaram dividendos de R$ 0,12 por ação e juros sobre o capital próprio "
    "de R$ 0,08 por ação. Farão jus os detentores de ações em 30 de outubro de 2026."
)


def _set_close(store: Store, price: float) -> None:
    store.conn.execute("UPDATE quotes SET close=?", (price,))
    store.commit()


def test_three_installments_are_judged_alone(tmp_path, monkeypatch):
    """Cada parcela contra o piso de 2%. A soma passaria; cada uma, não."""
    sent = []
    monkeypatch.setattr("alertas_dividendo.engine.send_message", lambda *args: sent.append(args[-1]))
    store = Store(tmp_path / "alertas.sqlite")
    published = datetime(2026, 10, 15, 18, 0, tzinfo=TZ)
    _seed(store, "1577530", _MTRE, published)
    _set_close(store, 5.0)
    cfg = _cfg(tmp_path, datetime(2026, 10, 15, 19, 0, tzinfo=TZ), dry_run=False)
    try:
        report = Report(now=cfg.now, dry_run=False)
        _scan(cfg, store, CAL, report)
        assert sent == []
        assert store.open_position_count() == 0
        assert store.conn.execute("SELECT COUNT(*) AS n FROM signals").fetchone()["n"] == 0
        lows = [line for line in report.ignored if "yield_abaixo" in line]
        assert len(lows) == 3
        for line in lows:
            assert "yield=0.0076" in line
            assert "yield=0.0227" not in line
    finally:
        store.close()


def test_three_installments_open_three_positions_when_each_clears(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("alertas_dividendo.engine.send_message", lambda *args: sent.append(args[-1]))
    store = Store(tmp_path / "alertas.sqlite")
    published = datetime(2026, 10, 15, 18, 0, tzinfo=TZ)
    _seed(store, "1577530", _MTRE, published)
    _set_close(store, 1.5)
    cfg = _cfg(tmp_path, datetime(2026, 10, 15, 19, 0, tzinfo=TZ), dry_run=False)
    try:
        _scan(cfg, store, CAL, Report(now=cfg.now, dry_run=False))
        rows = store.conn.execute(
            "SELECT gross_per_share, data_com, buy_date, exit_date, status, yield FROM signals ORDER BY data_com"
        ).fetchall()
        assert len(rows) == 3
        assert len(sent) == 3
        assert all("COMPRA" in body and "COMPRA ATRASADA" not in body for body in sent)
        coms = [date(2026, 10, 20), date(2026, 11, 13), date(2026, 12, 11)]
        for row, com in zip(rows, coms):
            assert abs(row["gross_per_share"] - 0.03781618347) < 1e-9
            assert row["data_com"] == com.isoformat()
            assert row["buy_date"] == "2026-10-16"
            assert row["exit_date"] == exit_session(com, CAL).isoformat()
            assert row["status"] == "aguardando_compra"
            assert abs(row["yield"] - (0.03781618347 / 1.5)) < 1e-9
        assert store.open_position_count() == 3
        exits = {row["exit_date"] for row in store.conn.execute("SELECT exit_date FROM positions")}
        assert len(exits) == 3
        _scan(cfg, store, CAL, Report(now=cfg.now, dry_run=False))
        assert len(sent) == 3
        assert store.conn.execute("SELECT COUNT(*) AS n FROM signals").fetchone()["n"] == 3
        assert store.open_position_count() == 3
    finally:
        store.close()


def test_same_record_date_is_one_summed_event(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("alertas_dividendo.engine.send_message", lambda *args: sent.append(args[-1]))
    store = Store(tmp_path / "alertas.sqlite")
    published = datetime(2026, 10, 21, 18, 0, tzinfo=TZ)
    _seed(store, "8801", _SAME_COM, published)
    cfg = _cfg(tmp_path, datetime(2026, 10, 21, 19, 0, tzinfo=TZ), dry_run=False)
    try:
        _scan(cfg, store, CAL, Report(now=cfg.now, dry_run=False))
        assert len(sent) == 1
        row = store.conn.execute(
            "SELECT gross_per_share, kind, data_com, status FROM signals"
        ).fetchone()
        assert abs(row["gross_per_share"] - 0.20) < 1e-9
        assert row["kind"] == "misto"
        assert row["data_com"] == "2026-10-30"
        assert row["status"] == "aguardando_compra"
        assert store.open_position_count() == 1
    finally:
        store.close()


def test_b3_check_after_data_com_never_opens_a_trade(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("alertas_dividendo.engine.send_message", lambda *args: sent.append(args[-1]))

    def fake(issuer):
        assert issuer == "ZZZZ"
        return [
            {"typeStock": "ON", "lastDatePrior": "01/10/2026", "rate": "1,00", "label": "DIVIDENDO"},
            {"typeStock": "ON", "lastDatePrior": "20/10/2026", "rate": "0,50", "label": "DIVIDENDO"},
            {"typeStock": "PN", "lastDatePrior": "20/10/2026", "rate": "9,99", "label": "DIVIDENDO"},
        ]

    monkeypatch.setattr("alertas_dividendo.engine.fetch_b3_dividends", fake)
    store = Store(tmp_path / "alertas.sqlite")
    published = datetime(2026, 10, 15, 18, 0, tzinfo=TZ)
    _seed(store, "1579001", "Aviso de dividendos sem valor por acao.", published)
    cfg = _cfg(tmp_path, datetime(2026, 10, 27, 16, 0, tzinfo=TZ), dry_run=False)
    try:
        first = Report(now=cfg.now, dry_run=False)
        _scan(cfg, store, CAL, first)
        assert sent == []
        assert store.open_position_count() == 0
        signal = store.conn.execute(
            "SELECT id, status, gross_per_share, ignore_reason FROM signals"
        ).fetchone()
        assert signal["status"] == "ignorado"
        assert signal["gross_per_share"] is None
        assert signal["ignore_reason"] == "prazo de compra já passou"
        checks = store.b3_checks()
        assert len(checks) == 1
        assert checks[0]["value_per_share"] == 0.5
        assert checks[0]["data_com"] == "2026-10-20"
        assert checks[0]["ticker"] == "ZZZZ3"
        assert any("b3_conferencia" == row["reason"] for row in store.recent_events(10))
        assert "só conferência, sem alerta" in first.ignored[-1]
        second = Report(now=cfg.now, dry_run=False)
        _scan(cfg, store, CAL, second)
        assert sent == []
        assert store.open_position_count() == 0
        assert len(store.b3_checks()) == 1
        assert store.conn.execute("SELECT COUNT(*) AS n FROM signals").fetchone()["n"] == 1
        assert store.conn.execute("SELECT COUNT(*) AS n FROM positions").fetchone()["n"] == 0
    finally:
        store.close()


def test_b3_match_waits_until_the_record_date():
    rows = [{"typeStock": "ON", "lastDatePrior": "30/10/2026", "rate": "0,50", "label": "DIVIDENDO"}]
    assert b3_matches(
        rows,
        ticker="ZZZZ3",
        data_com=date(2026, 10, 30),
        published=date(2026, 10, 21),
        today=date(2026, 10, 27),
    ) == []
    found = b3_matches(
        rows,
        ticker="ZZZZ3",
        data_com=date(2026, 10, 30),
        published=date(2026, 10, 21),
        today=date(2026, 10, 30),
    )
    assert found == [{"data_com": date(2026, 10, 30), "value": 0.5, "label": "DIVIDENDO"}]
