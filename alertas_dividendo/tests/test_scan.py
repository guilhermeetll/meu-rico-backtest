"""Varredura duas vezes: extração falha não pode derrubar o SQLite, e prazo vencido não abre posição."""

from datetime import date, datetime, timedelta

import pytest

from alertas_dividendo.calendar import TZ, Calendar
from alertas_dividendo.config import Config
from alertas_dividendo.engine import (
    Report,
    _advance,
    _close_position,
    _entry_price,
    _scan,
    _snapshot_from_rows,
    b3_matches,
    realized_returns,
)
from alertas_dividendo.strategy import exit_session
from alertas_dividendo.store import Store

CAL = Calendar()


@pytest.fixture(autouse=True)
def _no_live_b3(monkeypatch):
    monkeypatch.setattr("alertas_dividendo.engine.fetch_b3_dividends", lambda _issuer: [])
    monkeypatch.setattr("alertas_dividendo.engine.fetch_b3_splits", lambda _issuer: [])


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
    # 26/10 às 18:00: a compra é a abertura de 27/10, às 10:00. Às 9h o prazo ainda está aberto.
    cfg = _cfg(tmp_path, datetime(2026, 10, 27, 9, 0, tzinfo=TZ), dry_run=False)
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
    """MTRE3 protocolo 1577530: publicado 15/10 18:00, compra na abertura de 16/10."""
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


def _keep_sixty_sessions_through(store: Store, last_session: str) -> None:
    """Tira o pregão que ainda não fechou e repõe um pregão antigo para a média de 60."""
    store.conn.execute("DELETE FROM quotes WHERE session_date > ?", (last_session,))
    count = store.conn.execute("SELECT COUNT(*) AS n FROM quotes").fetchone()["n"]
    oldest = date.fromisoformat(store.conn.execute("SELECT MIN(session_date) AS d FROM quotes").fetchone()["d"])
    extra = oldest
    while count < 60:
        extra -= timedelta(days=1)
        if not CAL.is_session(extra):
            continue
        store.insert_quotes_if_absent([("ZZZZ3", extra.isoformat(), 10.0, 2_000_000.0, "teste")])
        count += 1
    store.commit()


def test_intraday_notice_buys_the_same_close_with_the_latest_close_labeled(tmp_path, monkeypatch):
    """Aviso às 14:00, visto às 15:00: fechamento de hoje. O yield usa o último fechamento já publicado."""
    sent = []
    monkeypatch.setattr("alertas_dividendo.engine.send_message", lambda *args: sent.append(args[-1]))
    store = Store(tmp_path / "alertas.sqlite")
    published = datetime(2026, 10, 21, 14, 0, tzinfo=TZ)
    _seed(store, "9101", _DIVIDEND, published)
    # Às 15:00 o fechamento de 21/10 ainda não saiu. A tela usa 20/10.
    _keep_sixty_sessions_through(store, "2026-10-20")
    cfg = _cfg(tmp_path, datetime(2026, 10, 21, 15, 0, tzinfo=TZ), dry_run=False)
    try:
        first = Report(now=cfg.now, dry_run=False)
        _scan(cfg, store, CAL, first)
        assert len(sent) == 1
        body = sent[0]
        assert "durante o pregão" in body
        assert "Compra no fechamento de hoje" in body
        assert "último fechamento disponível" in body
        assert "20/10/2026" in body
        assert "ainda não saiu" in body
        signal = store.conn.execute(
            "SELECT status, last_close_date, timing, buy_date, entry_type FROM signals"
        ).fetchone()
        assert signal["status"] == "aguardando_compra"
        assert signal["last_close_date"] == "2026-10-20"
        assert signal["timing"] == "durante_pregao"
        assert signal["entry_type"] == "fechamento"
        assert signal["buy_date"] == "2026-10-21"
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
        assert "Compra na abertura de 22/10/2026" in body
        assert "último fechamento disponível" in body
        assert "21/10/2026" in body
        signal = store.conn.execute(
            "SELECT last_close_date, timing, buy_date, entry_type FROM signals"
        ).fetchone()
        assert signal["last_close_date"] == "2026-10-21"
        assert signal["timing"] == "apos_fechamento"
        assert signal["entry_type"] == "abertura"
        assert signal["buy_date"] == "2026-10-22"
    finally:
        store.close()


def test_late_detection_falls_back_to_the_next_open_and_is_logged(tmp_path, monkeypatch):
    """Aviso às 14:00 só visto às 16:40: abertura do dia seguinte, com log de detecção tardia."""
    sent = []
    monkeypatch.setattr("alertas_dividendo.engine.send_message", lambda *args: sent.append(args[-1]))
    store = Store(tmp_path / "alertas.sqlite")
    published = datetime(2026, 10, 21, 14, 0, tzinfo=TZ)
    _seed(store, "9103", _DIVIDEND, published)
    _keep_sixty_sessions_through(store, "2026-10-20")
    late = _cfg(tmp_path, datetime(2026, 10, 21, 16, 40, tzinfo=TZ), dry_run=False)
    try:
        report = Report(now=late.now, dry_run=False)
        _scan(late, store, CAL, report)
        assert len(sent) == 1
        assert "Compra na abertura de 22/10/2026" in sent[0]
        assert "só o viu depois" in sent[0]
        assert "COMPRA ATRASADA" not in sent[0]
        signal = store.conn.execute(
            "SELECT status, buy_date, entry_type FROM signals"
        ).fetchone()
        assert signal["status"] == "aguardando_compra"
        assert signal["entry_type"] == "abertura"
        assert signal["buy_date"] == "2026-10-22"
        position = store.conn.execute("SELECT entry_type, entry_date FROM positions").fetchone()
        assert position["entry_type"] == "abertura"
        assert position["entry_date"] == "2026-10-22"
        logged = store.conn.execute(
            "SELECT detail FROM events WHERE reason='deteccao_tardia'"
        ).fetchall()
        assert len(logged) == 1
        assert "abertura de 2026-10-22" in logged[0]["detail"]
    finally:
        store.close()


def test_same_day_close_fills_with_the_close_price(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("alertas_dividendo.engine.send_message", lambda *args: sent.append(args[-1]))
    store = Store(tmp_path / "alertas.sqlite")
    published = datetime(2026, 10, 21, 14, 0, tzinfo=TZ)
    _seed(store, "9104", _DIVIDEND, published)
    _keep_sixty_sessions_through(store, "2026-10-20")
    cfg = _cfg(tmp_path, datetime(2026, 10, 21, 15, 0, tzinfo=TZ), dry_run=False)
    try:
        _scan(cfg, store, CAL, Report(now=cfg.now, dry_run=False))
        assert any("Compra no fechamento de hoje" in body for body in sent)
        store.upsert_quotes([("ZZZZ3", "2026-10-21", 12.50, 2_000_000.0, "COTAHIST", 11.00)])
        store.commit()
        closing = _cfg(tmp_path, datetime(2026, 10, 21, 17, 5, tzinfo=TZ), dry_run=False)
        _advance(closing, store, CAL, Report(now=closing.now, dry_run=False), reminders_only=False)
        row = store.conn.execute(
            "SELECT status, entry_price, entry_source, entry_type FROM positions"
        ).fetchone()
        assert row["entry_type"] == "fechamento"
        assert row["status"] == "aberta"
        assert row["entry_price"] == 12.50
        assert row["entry_source"] == "COTAHIST"
    finally:
        store.close()


def _remind_and_fill_open(tmp_path, store, sent, entry_day: str, *, open_price: float, close_price: float):
    store.upsert_quotes([("ZZZZ3", entry_day, close_price, 2_000_000.0, "COTAHIST", open_price)])
    store.commit()
    year, month, day = (int(part) for part in entry_day.split("-"))
    morning = _cfg(tmp_path, datetime(year, month, day, 9, 0, tzinfo=TZ), dry_run=False)
    _advance(morning, store, CAL, Report(now=morning.now, dry_run=False), reminders_only=True)
    again = _cfg(tmp_path, datetime(year, month, day, 9, 30, tzinfo=TZ), dry_run=False)
    _advance(again, store, CAL, Report(now=again.now, dry_run=False), reminders_only=True)
    reminders = [body for body in sent if "LEMBRETE DE ENTRADA" in body]
    assert len(reminders) == 1
    assert "antes das 10:00" in reminders[0]
    assert "SIMULAÇÃO" in reminders[0]
    waiting = store.conn.execute("SELECT status, entry_price FROM positions").fetchone()
    assert waiting["status"] == "aguardando_compra"
    assert waiting["entry_price"] is None
    opened = _cfg(tmp_path, datetime(year, month, day, 10, 5, tzinfo=TZ), dry_run=False)
    _advance(opened, store, CAL, Report(now=opened.now, dry_run=False), reminders_only=False)
    filled = store.conn.execute("SELECT status, entry_price, entry_type FROM positions").fetchone()
    assert filled["status"] == "aberta"
    assert filled["entry_type"] == "abertura"
    assert filled["entry_price"] == open_price


def test_notice_after_1630_buys_the_next_open_and_reminds_before_10(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("alertas_dividendo.engine.send_message", lambda *args: sent.append(args[-1]))
    store = Store(tmp_path / "alertas.sqlite")
    published = datetime(2026, 10, 21, 18, 0, tzinfo=TZ)
    _seed(store, "9105", _DIVIDEND, published)
    cfg = _cfg(tmp_path, datetime(2026, 10, 21, 19, 0, tzinfo=TZ), dry_run=False)
    try:
        _scan(cfg, store, CAL, Report(now=cfg.now, dry_run=False))
        assert any("Compra na abertura de 22/10/2026" in body for body in sent)
        _remind_and_fill_open(tmp_path, store, sent, "2026-10-22", open_price=11.00, close_price=12.50)
    finally:
        store.close()


def test_holiday_and_weekend_notices_buy_the_next_open(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("alertas_dividendo.engine.send_message", lambda *args: sent.append(args[-1]))
    holiday_dir = tmp_path / "holiday"
    holiday_dir.mkdir()
    store = Store(holiday_dir / "alertas.sqlite")
    published = datetime(2026, 11, 2, 11, 0, tzinfo=TZ)
    text = _DIVIDEND.replace("30 de outubro de 2026", "13 de novembro de 2026")
    _seed(store, "9106", text, published)
    cfg = _cfg(holiday_dir, datetime(2026, 11, 2, 12, 0, tzinfo=TZ), dry_run=False)
    try:
        _scan(cfg, store, CAL, Report(now=cfg.now, dry_run=False))
        row = store.conn.execute("SELECT buy_date, entry_type, status FROM signals").fetchone()
        assert row["status"] == "aguardando_compra"
        assert row["entry_type"] == "abertura"
        assert row["buy_date"] == "2026-11-03"
        assert any("Compra na abertura de 03/11/2026" in body for body in sent)
        assert any("dia sem pregão" in body for body in sent)
        _remind_and_fill_open(holiday_dir, store, sent, "2026-11-03", open_price=9.10, close_price=9.80)
    finally:
        store.close()

    sent.clear()
    weekend_dir = tmp_path / "weekend"
    weekend_dir.mkdir()
    store = Store(weekend_dir / "alertas.sqlite")
    published = datetime(2026, 10, 24, 11, 0, tzinfo=TZ)
    _seed(store, "9107", _DIVIDEND, published)
    cfg = _cfg(weekend_dir, datetime(2026, 10, 24, 12, 0, tzinfo=TZ), dry_run=False)
    try:
        _scan(cfg, store, CAL, Report(now=cfg.now, dry_run=False))
        row = store.conn.execute("SELECT buy_date, entry_type FROM signals").fetchone()
        assert row["entry_type"] == "abertura"
        assert row["buy_date"] == "2026-10-26"
        assert any("Compra na abertura de 26/10/2026" in body for body in sent)
        _remind_and_fill_open(weekend_dir, store, sent, "2026-10-26", open_price=8.20, close_price=8.90)
    finally:
        store.close()


def test_yahoo_open_does_not_store_an_intraday_close(tmp_path, monkeypatch):
    """A abertura do Yahoo não grava o preço do dia como fechamento."""
    monkeypatch.setattr("alertas_dividendo.engine.yahoo_session", lambda *_args, **_kwargs: (99.0, 11.0))
    store = Store(tmp_path / "alertas.sqlite")
    published = datetime(2026, 10, 21, 18, 0, tzinfo=TZ)
    _seed(store, "9108", _DIVIDEND, published)
    now = datetime(2026, 10, 22, 10, 5, tzinfo=TZ)
    try:
        filled = _entry_price(store, "ZZZZ3", date(2026, 10, 22), now, CAL, "abertura")
        assert filled == (11.0, "Yahoo Finance (abertura; COTAHIST ainda não publicado)")
        row = store.conn.execute(
            "SELECT open_price, close, volume, source FROM quotes WHERE ticker='ZZZZ3' AND session_date='2026-10-22'"
        ).fetchone()
        assert row["open_price"] == 11.0
        assert row["close"] is None
        assert row["volume"] == 0.0
        assert row["source"].startswith("Yahoo")
        snap = _snapshot_from_rows(store.history("ZZZZ3", "2026-10-22"))
        assert snap.last_close == 10.0
        assert snap.last_close_date == date(2026, 10, 21)
        assert snap.volume_sessions == 60
        assert snap.avg_volume == 2_000_000
    finally:
        store.close()


def test_open_entry_compares_with_the_ibov_open(tmp_path, monkeypatch):
    monkeypatch.setattr("alertas_dividendo.engine.fetch_b3_splits", lambda _issuer: [])
    store = Store(tmp_path / "alertas.sqlite")
    try:
        signal_id = store.insert_signal(
            {
                "dedupe_key": "ibov-abertura",
                "protocol": "ibov-abertura",
                "ticker": "ZZZZ3",
                "issuer": "ZZZZ",
                "status": "aguardando_compra",
                "kind": "dividendo",
                "gross_per_share": 0.5,
                "data_com": "2026-10-22",
                "entry_type": "abertura",
                "created_at": "2026-10-21T19:00:00-03:00",
            }
        )
        store.insert_position(
            {
                "signal_id": signal_id,
                "ticker": "ZZZZ3",
                "status": "aguardando_compra",
                "entry_type": "abertura",
                "entry_date": "2026-10-22",
                "exit_date": "2026-10-23",
                "data_com": "2026-10-22",
                "kind": "dividendo",
                "gross_per_share": 0.5,
                "opened_at": "2026-10-21T19:00:00-03:00",
            }
        )
        store.upsert_quotes([("ZZZZ3", "2026-10-22", 12.50, 2_000_000.0, "COTAHIST", 11.00)])
        store.upsert_quotes([("ZZZZ3", "2026-10-23", 13.00, 2_000_000.0, "COTAHIST", 12.00)])
        store.upsert_index("2026-10-22", 1100.0, "Yahoo Finance ^BVSP", open_price=1000.0)
        store.upsert_index("2026-10-23", 1200.0, "Yahoo Finance ^BVSP", open_price=1110.0)
        store.commit()
        opening = _cfg(tmp_path, datetime(2026, 10, 22, 10, 5, tzinfo=TZ), dry_run=False)
        _advance(opening, store, CAL, Report(now=opening.now, dry_run=False), reminders_only=False)
        filled = store.conn.execute(
            "SELECT entry_price, ibov_entry, ibov_source, status FROM positions"
        ).fetchone()
        assert filled["status"] == "aberta"
        assert filled["entry_price"] == 11.00
        assert filled["ibov_entry"] == 1000.0
        assert "abertura" in filled["ibov_source"]
        closing = _cfg(tmp_path, datetime(2026, 10, 23, 17, 5, tzinfo=TZ), dry_run=True)
        report = Report(now=closing.now, dry_run=True)
        position = store.conn.execute("SELECT * FROM positions").fetchone()
        _close_position(
            closing,
            store,
            report,
            position,
            (13.0, "COTAHIST"),
        )
        body = "\n".join(report.messages)
        assert "RESULTADO" in body
        # 1200 / 1000 - 1. O fechamento 1100 do dia da entrada não entra.
        assert "+20,00%" in body
    finally:
        store.close()


def test_next_day_sighting_of_a_morning_notice_is_logged_and_ignored(tmp_path, monkeypatch):
    """Entregue antes das 16:30 e só visto no ENET no dia seguinte, depois da abertura."""
    sent = []
    monkeypatch.setattr("alertas_dividendo.engine.send_message", lambda *args: sent.append(args[-1]))
    store = Store(tmp_path / "alertas.sqlite")
    published = datetime(2026, 10, 21, 14, 0, tzinfo=TZ)
    _seed(store, "9109", _DIVIDEND, published)
    cfg = _cfg(tmp_path, datetime(2026, 10, 22, 11, 0, tzinfo=TZ), dry_run=False)
    try:
        report = Report(now=cfg.now, dry_run=False)
        _scan(cfg, store, CAL, report)
        assert sent == []
        assert report.messages == []
        signal = store.conn.execute(
            "SELECT status, ignore_reason, entry_type FROM signals"
        ).fetchone()
        assert signal["status"] == "ignorado"
        assert signal["ignore_reason"] == "prazo de compra já passou"
        assert signal["entry_type"] == "abertura"
        assert store.open_position_count() == 0
        logged = store.conn.execute(
            "SELECT detail FROM events WHERE reason='deteccao_tardia'"
        ).fetchall()
        assert len(logged) == 1
        assert "16:30" in logged[0]["detail"]
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


def test_cross_check_uses_parse_br_number(monkeypatch):
    from alertas_dividendo.engine import _b3_number, _cross_check

    assert _b3_number("1 234,56") == 1234.56
    assert _b3_number("0.03781618347") == 0.03781618347
    monkeypatch.setattr(
        "alertas_dividendo.engine.fetch_b3_dividends",
        lambda _issuer: [{"lastDatePrior": "30/10/2026", "rate": "1.234,56", "label": "DIVIDENDO"}],
    )
    note = _cross_check("ZZZZ", date(2026, 10, 30), 0.50, "dividendo")
    assert note is not None and "1.234,56" in note


def test_same_jcp_in_another_quarter_is_not_a_duplicate(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("alertas_dividendo.engine.send_message", lambda *args: sent.append(args[-1]))
    store = Store(tmp_path / "alertas.sqlite")
    published = datetime(2026, 6, 15, 18, 0, tzinfo=TZ)
    text = (
        "Aprovar o pagamento de juros sobre o capital próprio no valor de R$ 0,50 por ação. "
        "Farão jus os detentores de ações em 30 de junho de 2026."
    )
    _seed(store, "9003", text, published)
    store.insert_signal(
        {
            "dedupe_key": "jcp-marco",
            "protocol": "100",
            "ticker": "ZZZZ3",
            "issuer": "ZZZZ",
            "status": "encerrada",
            "kind": "jcp",
            "gross_per_share": 0.50,
            "data_com": "2026-03-31",
            "approval_date": "2026-03-12",
            "created_at": "2026-03-12T18:00:00-03:00",
        }
    )
    store.insert_signal(
        {
            "dedupe_key": "jcp-sem-data",
            "protocol": "101",
            "ticker": "ZZZZ3",
            "issuer": "ZZZZ",
            "status": "aguardando_compra",
            "kind": "jcp",
            "gross_per_share": 0.50,
            "approval_date": "2026-03-12",
            "created_at": "2026-03-12T18:00:00-03:00",
        }
    )
    store.commit()
    cfg = _cfg(tmp_path, datetime(2026, 6, 15, 19, 0, tzinfo=TZ), dry_run=False)
    try:
        report = Report(now=cfg.now, dry_run=False)
        _scan(cfg, store, CAL, report)
        assert not any("duplicado" in line for line in report.ignored)
        fresh = store.conn.execute(
            "SELECT data_com, status FROM signals WHERE protocol='9003'"
        ).fetchone()
        assert fresh["data_com"] == "2026-06-30"
        assert fresh["status"] == "aguardando_compra"
        old = store.conn.execute(
            "SELECT data_com FROM signals WHERE protocol='101'"
        ).fetchone()
        assert old["data_com"] is None
        assert len(sent) == 1
    finally:
        store.close()


def test_undated_notice_does_not_collapse_into_an_older_quarter(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("alertas_dividendo.engine.send_message", lambda *args: sent.append(args[-1]))
    store = Store(tmp_path / "alertas.sqlite")
    published = datetime(2026, 6, 15, 18, 0, tzinfo=TZ)
    text = "Aprovar o pagamento de juros sobre o capital próprio no valor de R$ 0,50 por ação."
    _seed(store, "9004", text, published)
    store.insert_signal(
        {
            "dedupe_key": "jcp-marco",
            "protocol": "100",
            "ticker": "ZZZZ3",
            "issuer": "ZZZZ",
            "status": "encerrada",
            "kind": "jcp",
            "gross_per_share": 0.50,
            "data_com": "2026-03-31",
            "approval_date": "2026-03-12",
            "created_at": "2026-03-12T18:00:00-03:00",
        }
    )
    store.commit()
    cfg = _cfg(tmp_path, datetime(2026, 6, 15, 19, 0, tzinfo=TZ), dry_run=False)
    try:
        report = Report(now=cfg.now, dry_run=False)
        _scan(cfg, store, CAL, report)
        assert not any("duplicado" in line for line in report.ignored)
        fresh = store.conn.execute(
            "SELECT data_com, status FROM signals WHERE protocol='9004'"
        ).fetchone()
        assert fresh["status"] == "aguardando_compra"
        assert fresh["data_com"] is None
        assert len(sent) == 1
        assert "ATENÇÃO: a data-com não é conhecida antes desta compra" in sent[0]
    finally:
        store.close()


def test_yahoo_zero_volume_does_not_block_cotahist_or_the_average(tmp_path, monkeypatch):
    from alertas_dividendo.cotahist import build_quote_line
    from alertas_dividendo.engine import _snapshot_from_rows
    from alertas_dividendo.quotes import ensure_cotahist

    store = Store(tmp_path / "alertas.sqlite")
    day = date(2026, 10, 21)
    cursor = day - timedelta(days=1)
    rows = []
    while len(rows) < 60:
        if CAL.is_session(cursor):
            rows.append(("SAUD3", cursor.isoformat(), 10.0, 2_000_000.0, "COTAHIST"))
        cursor -= timedelta(days=1)
    rows.append(("SAUD3", day.isoformat(), 15.42, 0.0, "Yahoo Finance"))
    store.insert_quotes_if_absent(rows)
    store.mark_file("D" + day.strftime("%Y%m%d"))
    try:
        snap = _snapshot_from_rows(store.history("SAUD3", day.isoformat()))
        assert snap.volume_sessions == 60
        assert snap.avg_volume == 2_000_000
        assert snap.last_close == 15.42
        assert str(snap.last_close_source).startswith("Yahoo")
        downloaded = []

        def fake(name):
            downloaded.append(name)
            return build_quote_line(day.strftime("%Y%m%d"), "SAUD3", 15.50, 5_000_000.0) + "\n"

        monkeypatch.setattr("alertas_dividendo.quotes._download", fake)
        ensure_cotahist(store, CAL, day, day)
        assert any(day.strftime("%d%m%Y") in name for name in downloaded)
        stored = store.conn.execute(
            "SELECT close, volume, source FROM quotes WHERE ticker='SAUD3' AND session_date=?",
            (day.isoformat(),),
        ).fetchone()
        assert stored["source"] == "COTAHIST"
        assert stored["close"] == 15.50
        assert stored["volume"] == 5_000_000.0
        again = _snapshot_from_rows(store.history("SAUD3", day.isoformat()))
        assert again.volume_sessions == 60
        assert again.avg_volume == (2_000_000 * 59 + 5_000_000) / 60
        assert again.last_close_source == "COTAHIST"
    finally:
        store.close()


def test_split_during_position_is_flagged_and_the_price_stays(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "alertas_dividendo.engine.fetch_b3_splits",
        lambda _issuer: [
            {
                "assetIssued": "ZZZZ4",
                "label": "GRUPAMENTO",
                "lastDatePrior": "15/10/2026",
                "factor": "10",
            },
            {
                "assetIssued": "ZZZZ3",
                "label": "DESDOBRAMENTO",
                "lastDatePrior": "15/10/2026",
                "factor": "2",
            },
        ],
    )
    store = Store(tmp_path / "alertas.sqlite")
    try:
        signal_id = store.insert_signal(
            {
                "dedupe_key": "split-1",
                "protocol": "split-1",
                "ticker": "ZZZZ3",
                "issuer": "ZZZZ",
                "status": "aberta",
                "kind": "dividendo",
                "gross_per_share": 0.5,
                "data_com": "2026-10-05",
                "created_at": "2026-10-01T18:00:00-03:00",
            }
        )
        store.insert_position(
            {
                "signal_id": signal_id,
                "ticker": "ZZZZ3",
                "status": "aberta",
                "entry_date": "2026-10-02",
                "entry_price": 10.0,
                "entry_source": "COTAHIST",
                "exit_date": "2026-10-22",
                "data_com": "2026-10-05",
                "kind": "dividendo",
                "gross_per_share": 0.5,
                "opened_at": "2026-10-02T18:00:00-03:00",
            }
        )
        store.insert_quotes_if_absent([("ZZZZ3", "2026-10-22", 11.0, 2_000_000.0, "COTAHIST")])
        store.commit()
        mid = _cfg(tmp_path, datetime(2026, 10, 15, 18, 0, tzinfo=TZ), dry_run=True)
        _advance(mid, store, CAL, Report(now=mid.now, dry_run=True), reminders_only=False)
        note = store.latest_event("desdobramento", "ZZZZ3", "split-1")
        assert note is not None and "DESDOBRAMENTO" in note and "não foi ajustado" in note
        assert "GRUPAMENTO" not in note
        _advance(mid, store, CAL, Report(now=mid.now, dry_run=True), reminders_only=False)
        assert store.conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE reason='desdobramento'"
        ).fetchone()["n"] == 1
        closing = _cfg(tmp_path, datetime(2026, 10, 22, 17, 5, tzinfo=TZ), dry_run=True)
        report = Report(now=closing.now, dry_run=True)
        _advance(closing, store, CAL, report, reminders_only=False)
        body = "\n".join(report.messages)
        assert "RESULTADO" in body
        assert "não foi ajustado" in body
        assert store.conn.execute("SELECT entry_price, exit_price FROM positions").fetchone()["entry_price"] == 10.0
        assert store.conn.execute("SELECT exit_price FROM positions").fetchone()["exit_price"] is None
    finally:
        store.close()


def _awaiting_dividend(store: Store, gross: float, *, status: str = "aguardando_compra", entry_price=None) -> int:
    signal_id = store.insert_signal(
        {
            "dedupe_key": f"aberto|{gross}",
            "protocol": "8000",
            "ticker": "ZZZZ3",
            "issuer": "ZZZZ",
            "company_name": "Empresa Teste",
            "status": status,
            "kind": "dividendo",
            "gross_per_share": gross,
            "yield": gross / 10,
            "approval_date": "2026-10-21",
            "data_com": "2026-10-30",
            "buy_date": "2026-10-22",
            "buy_deadline": "2026-10-22T16:55:00-03:00",
            "exit_date": "2026-12-01",
            "created_at": "2026-10-21T18:00:00-03:00",
        }
    )
    store.insert_position(
        {
            "signal_id": signal_id,
            "ticker": "ZZZZ3",
            "status": status,
            "entry_date": "2026-10-22",
            "entry_price": entry_price,
            "entry_source": "COTAHIST" if entry_price is not None else None,
            "exit_date": "2026-12-01",
            "data_com": "2026-10-30",
            "kind": "dividendo",
            "gross_per_share": gross,
            "opened_at": "2026-10-21T18:00:00-03:00",
        }
    )
    store.commit()
    return signal_id


def test_retification_updates_the_open_signal_and_sends_one_notice(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("alertas_dividendo.engine.send_message", lambda *args: sent.append(args[-1]))
    store = Store(tmp_path / "alertas.sqlite")
    published = datetime(2026, 10, 21, 18, 30, tzinfo=TZ)
    text = (
        "Aprovaram dividendos de R$ 0,80 por ação. "
        "Farão jus os detentores de ações em 30 de outubro de 2026."
    )
    _seed(store, "9008", text, published)
    _awaiting_dividend(store, 0.50)
    cfg = _cfg(tmp_path, datetime(2026, 10, 21, 19, 0, tzinfo=TZ), dry_run=False)
    try:
        report = Report(now=cfg.now, dry_run=False)
        _scan(cfg, store, CAL, report)
        assert len(sent) == 1
        assert "VALOR CORRIGIDO" in sent[0]
        assert "COMPRA —" not in sent[0]
        assert "0,800000" in sent[0]
        assert "continua" in sent[0]
        signals = store.conn.execute("SELECT gross_per_share, status, yield FROM signals").fetchall()
        assert len(signals) == 1
        assert abs(signals[0]["gross_per_share"] - 0.80) < 1e-9
        assert signals[0]["status"] == "aguardando_compra"
        assert abs(signals[0]["yield"] - 0.08) < 1e-9
        position = store.conn.execute(
            "SELECT status, gross_per_share FROM positions"
        ).fetchone()
        assert position["status"] == "aguardando_compra"
        assert abs(position["gross_per_share"] - 0.80) < 1e-9
        assert store.open_position_count() == 1
        again = Report(now=cfg.now, dry_run=False)
        _scan(cfg, store, CAL, again)
        assert sent == [sent[0]]
        assert store.conn.execute("SELECT COUNT(*) AS n FROM signals").fetchone()["n"] == 1
        assert store.conn.execute("SELECT COUNT(*) AS n FROM positions").fetchone()["n"] == 1
    finally:
        store.close()


def test_retification_below_the_floor_cancels_before_the_buy(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("alertas_dividendo.engine.send_message", lambda *args: sent.append(args[-1]))
    store = Store(tmp_path / "alertas.sqlite")
    published = datetime(2026, 10, 21, 18, 30, tzinfo=TZ)
    text = (
        "Aprovaram dividendos de R$ 0,10 por ação. "
        "Farão jus os detentores de ações em 30 de outubro de 2026."
    )
    _seed(store, "9009", text, published)
    _awaiting_dividend(store, 0.50)
    cfg = _cfg(tmp_path, datetime(2026, 10, 21, 19, 0, tzinfo=TZ), dry_run=False)
    try:
        report = Report(now=cfg.now, dry_run=False)
        _scan(cfg, store, CAL, report)
        assert len(sent) == 1
        assert "VALOR CORRIGIDO" in sent[0]
        assert "cancelada" in sent[0]
        assert "abaixo de 2%" in sent[0]
        assert "COMPRA —" not in sent[0]
        signal = store.conn.execute(
            "SELECT status, ignore_reason, gross_per_share FROM signals"
        ).fetchone()
        assert signal["status"] == "ignorado"
        assert signal["ignore_reason"] == "yield_abaixo"
        assert abs(signal["gross_per_share"] - 0.10) < 1e-9
        assert store.conn.execute("SELECT status FROM positions").fetchone()["status"] == "cancelada"
        assert store.open_position_count() == 0
        logged = [
            row["detail"]
            for row in store.conn.execute(
                "SELECT detail FROM events WHERE reason='valor_corrigido'"
            )
        ]
        assert logged and "compra cancelada" in logged[0]
        again = Report(now=cfg.now, dry_run=False)
        _scan(cfg, store, CAL, again)
        assert len(sent) == 1
        assert store.conn.execute("SELECT COUNT(*) AS n FROM signals").fetchone()["n"] == 1
    finally:
        store.close()


def test_retification_after_the_buy_keeps_the_open_position(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("alertas_dividendo.engine.send_message", lambda *args: sent.append(args[-1]))
    store = Store(tmp_path / "alertas.sqlite")
    published = datetime(2026, 10, 23, 18, 0, tzinfo=TZ)
    text = (
        "Aprovaram dividendos de R$ 0,10 por ação. "
        "Farão jus os detentores de ações em 30 de outubro de 2026."
    )
    _seed(store, "9010", text, published)
    store.conn.execute(
        "UPDATE documents SET reference_date='2026-10-21' WHERE protocol='9010'"
    )
    store.commit()
    _awaiting_dividend(store, 0.50, status="aberta", entry_price=10.0)
    cfg = _cfg(tmp_path, datetime(2026, 10, 23, 19, 0, tzinfo=TZ), dry_run=False)
    try:
        report = Report(now=cfg.now, dry_run=False)
        _scan(cfg, store, CAL, report)
        assert len(sent) == 1
        assert "VALOR CORRIGIDO" in sent[0]
        assert "já está aberta" in sent[0]
        assert "cancelada" not in sent[0]
        signal = store.conn.execute("SELECT status, gross_per_share FROM signals").fetchone()
        assert signal["status"] == "aberta"
        assert abs(signal["gross_per_share"] - 0.10) < 1e-9
        position = store.conn.execute(
            "SELECT status, gross_per_share, entry_price FROM positions"
        ).fetchone()
        assert position["status"] == "aberta"
        assert position["entry_price"] == 10.0
        assert abs(position["gross_per_share"] - 0.10) < 1e-9
        assert store.open_position_count() == 1
    finally:
        store.close()
