"""SQLite: documentos, cotações, sinais, posições, alertas e o log de ignorados."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    protocol TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    cvm_code TEXT,
    company_name TEXT,
    category TEXT,
    subject TEXT,
    species TEXT,
    published_at TEXT,
    time_known INTEGER,
    reference_date TEXT,
    url TEXT,
    source TEXT,
    active INTEGER,
    first_seen TEXT
);
CREATE TABLE IF NOT EXISTS extractions (
    protocol TEXT PRIMARY KEY,
    ok INTEGER,
    payload_json TEXT,
    error TEXT,
    extracted_at TEXT
);
CREATE TABLE IF NOT EXISTS issuers (
    cvm_code TEXT PRIMARY KEY,
    issuer TEXT,
    company_name TEXT,
    fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS quotes (
    ticker TEXT NOT NULL,
    session_date TEXT NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (ticker, session_date)
);
CREATE TABLE IF NOT EXISTS quote_files (
    file_key TEXT PRIMARY KEY,
    ingested_at TEXT
);
CREATE TABLE IF NOT EXISTS index_quotes (
    session_date TEXT PRIMARY KEY,
    close REAL NOT NULL,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT UNIQUE,
    protocol TEXT,
    ticker TEXT,
    company_name TEXT,
    issuer TEXT,
    kind TEXT,
    gross_per_share REAL,
    net_per_share REAL,
    yield REAL,
    last_close REAL,
    last_close_date TEXT,
    last_close_source TEXT,
    avg_volume REAL,
    approval_date TEXT,
    data_com TEXT,
    data_ex TEXT,
    published_at TEXT,
    time_known INTEGER,
    timing TEXT,
    buy_date TEXT,
    buy_deadline TEXT,
    exit_date TEXT,
    status TEXT,
    ignore_reason TEXT,
    document_url TEXT,
    category TEXT,
    subject TEXT,
    late INTEGER,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER UNIQUE,
    ticker TEXT,
    status TEXT,
    entry_date TEXT,
    entry_price REAL,
    entry_source TEXT,
    exit_date TEXT,
    exit_price REAL,
    exit_source TEXT,
    data_com TEXT,
    kind TEXT,
    gross_per_share REAL,
    net_per_share REAL,
    dividend_received REAL,
    trade_return REAL,
    ibov_entry REAL,
    ibov_exit REAL,
    ibov_return REAL,
    ibov_source TEXT,
    opened_at TEXT,
    closed_at TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
    dedupe_key TEXT PRIMARY KEY,
    kind TEXT,
    signal_id INTEGER,
    body TEXT,
    status TEXT,
    created_at TEXT,
    sent_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    reason TEXT,
    ticker TEXT,
    protocol TEXT,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def commit(self) -> None:
        self.conn.commit()

    def meta_get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return None if row is None else row["value"]

    def meta_set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def log_event(self, reason: str, *, ticker: str = "", protocol: str = "", detail: str = "") -> None:
        self.conn.execute(
            "INSERT INTO events (ts, reason, ticker, protocol, detail) VALUES (?, ?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), reason, ticker, protocol, detail),
        )

    def seen_event(self, reason: str, ticker: str, protocol: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM events WHERE reason=? AND ticker=? AND protocol=? LIMIT 1",
            (reason, ticker, protocol),
        ).fetchone()
        return row is not None

    def upsert_document(self, row: dict) -> None:
        current = self.conn.execute(
            "SELECT version, time_known, published_at, source FROM documents WHERE protocol=?",
            (row["protocol"],),
        ).fetchone()
        if current:
            if int(current["version"]) > int(row["version"]):
                return
            if current["time_known"] and not row["time_known"]:
                row = dict(row)
                row["published_at"] = current["published_at"]
                row["time_known"] = 1
                row["source"] = current["source"]
        self.conn.execute(
            """
            INSERT INTO documents (
                protocol, version, cvm_code, company_name, category, subject, species,
                published_at, time_known, reference_date, url, source, active, first_seen
            ) VALUES (
                :protocol, :version, :cvm_code, :company_name, :category, :subject, :species,
                :published_at, :time_known, :reference_date, :url, :source, :active, :first_seen
            )
            ON CONFLICT(protocol) DO UPDATE SET
                version=excluded.version,
                cvm_code=excluded.cvm_code,
                company_name=excluded.company_name,
                category=excluded.category,
                subject=excluded.subject,
                species=excluded.species,
                published_at=excluded.published_at,
                time_known=excluded.time_known,
                reference_date=excluded.reference_date,
                url=excluded.url,
                source=excluded.source,
                active=excluded.active
            """,
            row,
        )

    def documents_since(self, day_iso: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT * FROM documents
            WHERE active=1 AND substr(published_at, 1, 10) >= ?
            ORDER BY published_at
            """,
            (day_iso,),
        ).fetchall()

    def get_extraction(self, protocol: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM extractions WHERE protocol=?",
            (protocol,),
        ).fetchone()

    def save_extraction(self, protocol: str, ok: bool, payload_json: str, error: str = "") -> None:
        self.conn.execute(
            """
            INSERT INTO extractions (protocol, ok, payload_json, error, extracted_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(protocol) DO UPDATE SET
                ok=excluded.ok,
                payload_json=excluded.payload_json,
                error=excluded.error,
                extracted_at=excluded.extracted_at
            """,
            (protocol, 1 if ok else 0, payload_json, error, datetime.now().isoformat(timespec="seconds")),
        )

    def issuer_map(self) -> dict[str, str]:
        rows = self.conn.execute("SELECT cvm_code, issuer FROM issuers WHERE issuer != ''").fetchall()
        return {row["cvm_code"]: row["issuer"] for row in rows}

    def issuer_loaded(self) -> bool:
        row = self.conn.execute("SELECT value FROM meta WHERE key='issuers_loaded'").fetchone()
        return row is not None

    def replace_issuers(self, rows: list[tuple[str, str, str]]) -> None:
        self.conn.execute("DELETE FROM issuers")
        self.conn.executemany(
            "INSERT INTO issuers (cvm_code, issuer, company_name, fetched_at) VALUES (?, ?, ?, ?)",
            [(code, issuer, name, datetime.now().isoformat(timespec="seconds")) for code, issuer, name in rows],
        )
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES ('issuers_loaded', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (datetime.now().isoformat(timespec="seconds"),),
        )

    def file_done(self, file_key: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM quote_files WHERE file_key=?",
            (file_key,),
        ).fetchone()
        return row is not None

    def mark_file(self, file_key: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO quote_files (file_key, ingested_at) VALUES (?, ?)",
            (file_key, datetime.now().isoformat(timespec="seconds")),
        )

    def insert_quotes_if_absent(self, rows: list[tuple[str, str, float, float, str]]) -> None:
        self.conn.executemany(
            """
            INSERT OR IGNORE INTO quotes (ticker, session_date, close, volume, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )

    def upsert_quotes(self, rows: list[tuple[str, str, float, float, str]]) -> None:
        self.conn.executemany(
            """
            INSERT INTO quotes (ticker, session_date, close, volume, source)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(ticker, session_date) DO UPDATE SET
                close=excluded.close,
                volume=excluded.volume,
                source=excluded.source
            """,
            rows,
        )

    def tickers_of(self, issuer: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT ticker FROM quotes WHERE ticker LIKE ?",
            (issuer + "%",),
        ).fetchall()
        suffix = ("3", "4", "5", "6", "7", "8", "11")
        found = []
        for row in rows:
            ticker = row["ticker"]
            if ticker.startswith(issuer) and ticker[len(issuer) :] in suffix:
                found.append(ticker)
        return sorted(found)

    def history(self, ticker: str, until: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT session_date, close, volume, source FROM quotes
            WHERE ticker=? AND session_date<=?
            ORDER BY session_date
            """,
            (ticker, until),
        ).fetchall()

    def has_session(self, session: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM quotes WHERE session_date=? LIMIT 1",
            (session,),
        ).fetchone()
        return row is not None

    def close_on(self, ticker: str, session: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT close, source FROM quotes WHERE ticker=? AND session_date=?",
            (ticker, session),
        ).fetchone()

    def upsert_index(self, session: str, close: float, source: str) -> None:
        self.conn.execute(
            """
            INSERT INTO index_quotes (session_date, close, source) VALUES (?, ?, ?)
            ON CONFLICT(session_date) DO UPDATE SET close=excluded.close, source=excluded.source
            """,
            (session, close, source),
        )

    def index_on(self, session: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT close, source FROM index_quotes WHERE session_date=?",
            (session,),
        ).fetchone()

    def insert_signal(self, row: dict) -> int:
        columns = list(row)
        placeholders = ",".join(":" + name for name in columns)
        names = ",".join(columns)
        cursor = self.conn.execute(
            f"INSERT INTO signals ({names}) VALUES ({placeholders})",
            row,
        )
        return int(cursor.lastrowid)

    def signals_for_ticker(self, ticker: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM signals WHERE ticker=? AND status IN ('aguardando_compra','aberta','encerrada','conferir')",
            (ticker,),
        ).fetchall()

    def open_position_count(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM positions WHERE status IN ('aguardando_compra','aberta')"
        ).fetchone()
        return int(row["n"])

    def insert_position(self, row: dict) -> int:
        columns = list(row)
        placeholders = ",".join(":" + name for name in columns)
        names = ",".join(columns)
        cursor = self.conn.execute(
            f"INSERT INTO positions ({names}) VALUES ({placeholders})",
            row,
        )
        return int(cursor.lastrowid)

    def positions_active(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM positions WHERE status IN ('aguardando_compra','aberta') ORDER BY id"
        ).fetchall()

    def positions_closed(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM positions WHERE status='encerrada' ORDER BY exit_date, id"
        ).fetchall()

    def update_position(self, position_id: int, **fields: object) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{name}=?" for name in fields)
        values = list(fields.values()) + [position_id]
        self.conn.execute(f"UPDATE positions SET {assignments} WHERE id=?", values)

    def update_signal(self, signal_id: int, **fields: object) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{name}=?" for name in fields)
        values = list(fields.values()) + [signal_id]
        self.conn.execute(f"UPDATE signals SET {assignments} WHERE id=?", values)

    def signal_by_id(self, signal_id: int) -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()

    def alert_exists(self, key: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM alerts WHERE dedupe_key=? AND status IN ('reserved','sent','uncertain')",
            (key,),
        ).fetchone()
        return row is not None

    def reserve_alert(self, key: str, kind: str, signal_id: int | None, body: str) -> bool:
        """Reserva antes de enviar, para não duplicar se o processo cair no meio."""
        try:
            self.conn.execute(
                """
                INSERT INTO alerts (dedupe_key, kind, signal_id, body, status, created_at)
                VALUES (?, ?, ?, ?, 'reserved', ?)
                """,
                (key, kind, signal_id, body, datetime.now().isoformat(timespec="seconds")),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def mark_alert(self, key: str, status: str) -> None:
        self.conn.execute(
            "UPDATE alerts SET status=?, sent_at=? WHERE dedupe_key=?",
            (status, datetime.now().isoformat(timespec="seconds"), key),
        )
        self.conn.commit()

    def recent_events(self, limit: int = 30) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
