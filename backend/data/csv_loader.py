from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import pandas as pd

from data.bars import filter_dates, normalize_bars, resample_bars
from data.base import DataRequest, LoadResult

CANONICAL = {
    "timestamp": ["timestamp", "datetime", "date_time", "datahora", "data_hora"],
    "date": ["date", "data", "dia", "pregao"],
    "time": ["time", "hora", "horario"],
    "open": ["open", "abertura", "open_price"],
    "high": ["high", "maxima", "maximo"],
    "low": ["low", "minima", "minimo"],
    "close": ["close", "fechamento", "ultimo", "close_price"],
    "volume": ["volume", "vol", "quantidade", "qtd", "qty"],
    "contract": ["contract", "ticker", "symbol", "ativo", "contrato"],
}


class CsvBarsAdapter:
    id = "csv"
    label = "CSV de barras"
    description = (
        "Barras OHLCV. Colunas aceitas: datetime (ou data + hora), open/abertura, "
        "high/máxima, low/mínima, close/fechamento e volume. Separador vírgula ou "
        "ponto e vírgula. Horário sem fuso é America/Sao_Paulo. Um mapeamento de "
        "colunas pode ser informado no pedido."
    )

    def load(self, request: DataRequest) -> LoadResult:
        if request.csv_path is None:
            raise ValueError("Informe um CSV de barras ou use o arquivo de exemplo.")
        path = Path(request.csv_path)
        if not path.is_file():
            raise ValueError(f"CSV não encontrado: {path.name}.")
        frame = _read_table(path)
        renamed = _rename_columns(frame, request.column_map)
        built = _build_frame(renamed)
        bars = normalize_bars(built)
        bars = filter_dates(bars, request.start, request.end)
        if bars.empty:
            raise ValueError("O CSV não tem barras no período pedido.")
        bars = resample_bars(bars, request.timeframe)
        warning = None
        if _looks_like_example(path):
            warning = "Este arquivo é um exemplo incluído no repositório, não a base operacional."
        return LoadResult(bars=bars, warnings=[warning] if warning else [])


def _looks_like_example(path: Path) -> bool:
    name = path.name.lower()
    return "exemplo" in name or "example" in name


def _read_table(path: Path) -> pd.DataFrame:
    text = path.read_text(encoding="utf-8-sig")
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        raise ValueError("O CSV está vazio.")
    delimiter = ";" if lines[0].count(";") >= lines[0].count(",") else ","
    return pd.read_csv(path, sep=delimiter, comment="#", encoding="utf-8-sig", skipinitialspace=True)


def _rename_columns(frame: pd.DataFrame, column_map: dict | None) -> pd.DataFrame:
    lookup = {_key(column): column for column in frame.columns}
    mapping: dict[str, str] = {}
    if column_map:
        canonical = set(CANONICAL)
        values = {_key(str(value)) for value in column_map.values()}
        keys = {_key(str(key)) for key in column_map}
        # file column -> canonical, or canonical -> file column
        if values <= canonical or values <= {_key(name) for name in canonical}:
            pairs = ((str(src), str(dst)) for src, dst in column_map.items())
        elif keys <= {_key(name) for name in canonical}:
            pairs = ((str(dst), str(src)) for src, dst in column_map.items())
        else:
            pairs = ((str(src), str(dst)) for src, dst in column_map.items())
        for src, dst in pairs:
            original = lookup.get(_key(src))
            target = _canonical_name(dst)
            if original and target:
                mapping[original] = target
    else:
        for column in frame.columns:
            target = _match_alias(column)
            if target:
                mapping[column] = target
    out = frame.rename(columns=mapping)
    if column_map:
        # Fill anything the explicit map did not cover.
        extra = {}
        for column in out.columns:
            if column in CANONICAL:
                continue
            target = _match_alias(str(column))
            if target and target not in out.columns:
                extra[column] = target
        out = out.rename(columns=extra)
    return out


def _build_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if "timestamp" in frame.columns:
        timestamp = _parse_datetime(frame["timestamp"])
    elif "date" in frame.columns:
        if "time" in frame.columns:
            combined = frame["date"].astype(str).str.strip() + " " + frame["time"].astype(str).str.strip()
            timestamp = _parse_datetime(combined)
        else:
            timestamp = _parse_datetime(frame["date"])
    else:
        raise ValueError(
            "Não encontrei a coluna de data. Use datetime, ou data e hora, "
            "ou informe o mapeamento de colunas."
        )
    missing = [name for name in ("open", "high", "low", "close") if name not in frame.columns]
    if missing:
        raise ValueError(
            "Não encontrei as colunas " + ", ".join(missing) + ". "
            "Nomes aceitos incluem abertura, máxima, mínima e fechamento."
        )
    built = pd.DataFrame(
        {
            "timestamp": timestamp,
            "open": _numbers(frame["open"]),
            "high": _numbers(frame["high"]),
            "low": _numbers(frame["low"]),
            "close": _numbers(frame["close"]),
            "volume": _numbers(frame["volume"]) if "volume" in frame.columns else 0.0,
        }
    )
    if "contract" in frame.columns:
        built["contract"] = frame["contract"]
    return built


def _parse_datetime(series: pd.Series) -> pd.Series:
    sample = [str(value) for value in series.head(30).tolist()]
    dayfirst = any(re.match(r"\s*\d{1,2}/\d{1,2}/\d{2,4}", value) for value in sample)
    return pd.to_datetime(series, dayfirst=dayfirst, errors="coerce")


def _numbers(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")

    def one(value: object) -> str | None:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        text = str(value).strip().replace(" ", "")
        if text == "" or text.lower() in {"nan", "none"}:
            return None
        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        elif "," in text:
            text = text.replace(",", ".")
        return text

    return pd.to_numeric(series.map(one), errors="coerce")


def _key(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", folded.lower())


def _match_alias(name: str) -> str | None:
    token = _key(name)
    for canonical, aliases in CANONICAL.items():
        if token == _key(canonical) or token in {_key(alias) for alias in aliases}:
            return canonical
    return None


def _canonical_name(name: str) -> str | None:
    token = _key(name)
    for canonical in CANONICAL:
        if token == _key(canonical):
            return canonical
    return _match_alias(name)


def parse_column_map(raw: str | dict | None) -> dict | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("O mapeamento de colunas precisa ser um JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("O mapeamento de colunas precisa ser um objeto JSON.")
    return parsed
