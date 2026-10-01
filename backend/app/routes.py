from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BacktestRun
from app.schemas import BacktestInput
from app.service import execute_backtest, sample_csv_path, stamp, upload_dir
from data.b3 import B3TradesAdapter
from data.csv_loader import CsvBarsAdapter
from data.yahoo import YahooFinanceAdapter
from engine.instruments import WIN
from engine.strategies.registry import list_strategies

router = APIRouter(prefix="/api")


def get_db(request: Request):
    db: Session = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/strategies")
def strategies():
    return list_strategies()


@router.get("/instruments")
def instruments():
    return [
        {
            "symbol": "WIN",
            "label": "Mini índice (WIN)",
            "point_value": WIN.point_value,
            "tick_size": WIN.tick_size,
            "tick_value": WIN.tick_value,
            "default_fee_per_side": WIN.default_fee_per_side,
            "default_slippage_ticks": WIN.default_slippage_ticks,
        }
    ]


@router.get("/data-sources")
def data_sources():
    b3 = B3TradesAdapter()
    yahoo = YahooFinanceAdapter()
    csv_adapter = CsvBarsAdapter()
    return [
        {"id": b3.id, "label": b3.label, "description": b3.description, "timeframes": ["1min", "5min", "60min"]},
        {"id": yahoo.id, "label": yahoo.label, "description": yahoo.description, "timeframes": ["5min", "60min"]},
        {"id": csv_adapter.id, "label": csv_adapter.label, "description": csv_adapter.description, "timeframes": ["1min", "5min", "60min"]},
    ]


@router.get("/sample")
def sample_info():
    path = sample_csv_path()
    return {"available": path.is_file(), "name": path.name}


@router.post("/uploads")
async def upload_csv(file: UploadFile):
    filename = file.filename or "barras.csv"
    if Path(filename).suffix.lower() not in {".csv", ".txt"}:
        raise HTTPException(status_code=400, detail="Envie um arquivo .csv ou .txt.")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="O arquivo está vazio.")
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="O CSV passa de 20 MB.")
    name = f"{uuid.uuid4().hex}.csv"
    target = upload_dir() / name
    target.write_bytes(content)
    return {"id": name, "filename": Path(filename).name}


@router.post("/backtests")
def create_backtest(payload: BacktestInput, db: Session = Depends(get_db)):
    try:
        body = execute_backtest(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    run_id = str(uuid.uuid4())
    created = stamp()
    body = {"id": run_id, "created_at": created.isoformat(), **body}
    row = BacktestRun(
        id=run_id,
        created_at=created,
        strategy=payload.strategy,
        symbol=payload.symbol.upper(),
        data_source=payload.data_source,
        timeframe=payload.timeframe,
        status="ok",
        error=None,
        summary=body["summary"],
        request_json=payload.model_dump(mode="json"),
        result_json=body,
    )
    db.add(row)
    db.commit()
    return body


@router.get("/backtests")
def list_backtests(db: Session = Depends(get_db)):
    rows = db.scalars(select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(50)).all()
    items = []
    for row in rows:
        metrics = (row.result_json or {}).get("metrics", {})
        items.append(
            {
                "id": row.id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "strategy": row.strategy,
                "symbol": row.symbol,
                "data_source": row.data_source,
                "timeframe": row.timeframe,
                "summary": row.summary,
                "net_pnl_after_tax": metrics.get("net_pnl_after_tax"),
                "sharpe": metrics.get("sharpe"),
                "n_trades": metrics.get("n_trades"),
                "status": row.status,
            }
        )
    return items


@router.get("/backtests/{run_id}")
def get_backtest(run_id: str, db: Session = Depends(get_db)):
    row = db.get(BacktestRun, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Backtest não encontrado.")
    return row.result_json
