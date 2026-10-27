"""Configuração por ambiente e argumentos da linha de comando."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from alertas_dividendo.calendar import TZ
from alertas_dividendo.strategy import aware


def _flag(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() not in {"0", "false", "nao", "não", "off", "no"}


def parse_as_of(text: str | None) -> datetime:
    """Data ou data-hora. Data sozinha vira 18:00 em São Paulo (depois do call)."""
    if not text:
        return datetime.now(TZ)
    raw = text.strip()
    if len(raw) == 10:
        moment = datetime.strptime(raw, "%Y-%m-%d").replace(hour=18, minute=0, tzinfo=TZ)
        return moment
    moment = datetime.fromisoformat(raw)
    return aware(moment)


@dataclass(frozen=True)
class Config:
    data_dir: Path
    token: str
    chat_id: str
    simulacao: bool
    lookback_days: int
    dry_run: bool
    now: datetime

    @property
    def db_path(self) -> Path:
        return self.data_dir / "alertas.sqlite"

    @property
    def log_path(self) -> Path:
        return self.data_dir / "logs" / "alertas.log"

    @property
    def pdf_dir(self) -> Path:
        return self.data_dir / "pdfs"


def load_config(
    *,
    data_dir: str | None,
    dry_run: bool,
    as_of: str | None,
    lookback: int | None,
    require_telegram: bool,
) -> Config:
    folder = Path(data_dir or os.environ.get("ALERTAS_DATA_DIR") or "alertas_data")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if require_telegram and not dry_run and (not token or not chat_id):
        raise SystemExit(
            "Defina TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID, ou use --dry-run. "
            "O comando chat-id mostra o id depois que você fala com o bot."
        )
    days = lookback
    if days is None:
        days = int(os.environ.get("ALERTAS_LOOKBACK_DAYS", "12"))
    return Config(
        data_dir=folder,
        token=token,
        chat_id=chat_id,
        simulacao=_flag(os.environ.get("ALERTAS_SIMULACAO"), True),
        lookback_days=days,
        dry_run=dry_run,
        now=parse_as_of(as_of),
    )
