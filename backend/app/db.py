from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "sqlite+pysqlite:////tmp/meu-rico-backtest.db")


def make_engine(url: str | None = None):
    target = url or database_url()
    kwargs = {"pool_pre_ping": True}
    if target.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(target, **kwargs)


def make_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)
