"""Varredura duas vezes: extração falha não pode derrubar o SQLite, e prazo vencido não abre posição."""

from datetime import date, datetime, timedelta

from alertas_dividendo.calendar import TZ, Calendar
from alertas_dividendo.config import Config
from alertas_dividendo.engine import Report, _advance, _scan, realized_returns
from alertas_dividendo.store import Store

CAL = Calendar()


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
    published = datetime(2026, 10, 21, 18, 0, tzinfo=TZ)
    _seed(store, "9001", "Aviso aos acionistas sobre dividendos, sem valor por acao.", published)
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
