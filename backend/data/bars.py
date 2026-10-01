from __future__ import annotations

from datetime import date

import pandas as pd

TZ = "America/Sao_Paulo"
TIMEFRAME_MINUTES = {"1min": 1, "5min": 5, "60min": 60}


def infer_bar_minutes(bars: pd.DataFrame) -> int:
    ts = bars["timestamp"].sort_values()
    delta = ts.diff().dt.total_seconds().div(60.0)
    delta = delta[(delta > 0) & (delta <= 180)]
    if delta.empty:
        return 1
    return max(1, int(round(float(delta.median()))))


def normalize_bars(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "open", "high", "low", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Barras sem colunas obrigatórias: {', '.join(sorted(missing))}.")
    out = frame.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=False)
    if out["timestamp"].dt.tz is None:
        out["timestamp"] = out["timestamp"].dt.tz_localize(TZ, ambiguous="infer", nonexistent="shift_forward")
    else:
        out["timestamp"] = out["timestamp"].dt.tz_convert(TZ)
    for column in ("open", "high", "low", "close"):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    if "volume" not in out.columns:
        out["volume"] = 0.0
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0.0)
    out = out.dropna(subset=["timestamp", "open", "high", "low", "close"])
    out = out.sort_values("timestamp")
    identity = ["timestamp", "contract"] if "contract" in out.columns else ["timestamp"]
    out = out.drop_duplicates(identity, keep="last")
    columns = ["timestamp", "open", "high", "low", "close", "volume"]
    if "contract" in out.columns:
        columns.append("contract")
    return out.loc[:, columns].reset_index(drop=True)


def resample_bars(bars: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if timeframe not in TIMEFRAME_MINUTES:
        raise ValueError("Timeframe deve ser 1min, 5min ou 60min.")
    target = TIMEFRAME_MINUTES[timeframe]
    if bars.empty:
        return bars
    inferred = infer_bar_minutes(bars)
    if inferred > target:
        raise ValueError(
            f"As barras de origem têm cerca de {inferred} min. "
            f"Não dá para refinar para {target} min."
        )
    if inferred == target:
        return bars
    rule = f"{target}min"
    extra = ["contract"] if "contract" in bars.columns else []
    indexed = bars.set_index("timestamp")
    aggregated = indexed.resample(rule, label="left", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    if extra:
        aggregated["contract"] = indexed["contract"].resample(rule, label="left", closed="left").first()
    aggregated = aggregated.dropna(subset=["open", "high", "low", "close"])
    aggregated = aggregated.reset_index()
    return normalize_bars(aggregated)


def filter_dates(bars: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    if end < start:
        raise ValueError("A data final é anterior à data inicial.")
    dates = bars["timestamp"].dt.date
    selected = bars.loc[(dates >= start) & (dates <= end)].copy()
    return selected.reset_index(drop=True)
