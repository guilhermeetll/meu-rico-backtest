from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import Base, make_engine, make_session_factory
from app.routes import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def create_app() -> FastAPI:
    engine = make_engine()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        last_error: Exception | None = None
        for _ in range(30):
            try:
                Base.metadata.create_all(bind=engine)
                last_error = None
                break
            except Exception as exc:  # database may still be starting
                last_error = exc
                time.sleep(1)
        if last_error is not None:
            raise RuntimeError(f"Não foi possível preparar o banco: {last_error}") from last_error
        yield
        engine.dispose()

    app = FastAPI(title="Meu Rico Backtest", lifespan=lifespan)
    app.state.session_factory = make_session_factory(engine)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
