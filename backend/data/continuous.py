"""Keep one WIN series per session when a bar file mixes contracts."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd


def read_win_ativo(path: Path) -> dict[date, tuple[str, int]]:
    table = pd.read_csv(path, comment="#", skipinitialspace=True)
    columns = {str(name).strip().lower(): name for name in table.columns}
    missing = [name for name in ("date", "serie_ativa", "trades") if name not in columns]
    if missing:
        raise ValueError("win_ativo.csv precisa das colunas date, serie_ativa e trades.")
    active: dict[date, tuple[str, int]] = {}
    for record in table.itertuples(index=False):
        raw_day = getattr(record, columns["date"])
        day = pd.to_datetime(raw_day).date()
        ticker = str(getattr(record, columns["serie_ativa"])).strip().upper()
        trades = int(getattr(record, columns["trades"]))
        active[day] = (ticker, trades)
    return active


def restrict_contracts(frame: pd.DataFrame, symbol: str, csv_path: Path) -> tuple[pd.DataFrame, list[str]]:
    """Drop the contracts that are not this request's series.

    A generic WIN symbol follows win_ativo.csv next to the bar file, the same
    rule as the B3 adapter: each session keeps only serie_ativa, and a session
    marked with zero trades is left out. An explicit ticker keeps only itself.
    """
    if frame.empty or "contract" not in frame.columns:
        return frame, []
    present = {
        str(value).strip().upper()
        for value in frame["contract"].dropna().unique()
        if str(value).strip()
    }
    if len(present) <= 1:
        return frame, []

    symbol_u = symbol.upper().strip()
    if symbol_u in {"", "WIN"}:
        ativo_path = csv_path.parent / "win_ativo.csv"
        if not ativo_path.is_file():
            return frame, [
                "O CSV traz mais de um vencimento e não há win_ativo.csv na mesma pasta. "
                "Pregões mistos serão pulados para não transformar o salto entre contratos em retorno."
            ]
        return _keep_active(frame, read_win_ativo(ativo_path))

    kept = frame[frame["contract"].astype(str).str.strip().str.upper() == symbol_u].copy()
    if kept.empty:
        raise ValueError(f"O CSV não tem barras de {symbol_u}.")
    return kept, []


def _keep_active(frame: pd.DataFrame, active: dict[date, tuple[str, int]]) -> tuple[pd.DataFrame, list[str]]:
    days = pd.to_datetime(frame["timestamp"]).dt.date
    contracts = frame["contract"].astype(str).str.strip().str.upper()
    wanted = days.map(lambda day: active.get(day, ("", 0))[0])
    tradable = days.map(lambda day: active.get(day, ("", 0))[1] > 0)
    kept = frame.loc[(contracts.to_numpy() == wanted.to_numpy()) & tradable.to_numpy()].copy()
    warnings: list[str] = []
    missing = sorted({day for day in set(days) if day not in active})
    if missing:
        shown = ", ".join(day.isoformat() for day in missing[:6])
        extra = "" if len(missing) <= 6 else f" e mais {len(missing) - 6}"
        warnings.append(f"Sem linha em win_ativo.csv para: {shown}{extra}.")
    return kept, warnings
