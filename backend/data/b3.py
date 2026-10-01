from __future__ import annotations

import logging
import os
import zipfile
from datetime import date, timedelta
from pathlib import Path

import httpx
import pandas as pd

from data.bars import filter_dates, normalize_bars
from data.base import DataRequest, LoadResult

logger = logging.getLogger(__name__)

B3_URL = "https://arquivos.b3.com.br/rapinegocios/tickercsv/{ticker}/{day}"
TZ_NAME = "America/Sao_Paulo"


def data_root() -> Path:
    return Path(os.environ.get("B3_DATA_ROOT", "/data/b3_ticks"))


def bar_cache_dir() -> Path:
    configured = os.environ.get("B3_BAR_CACHE_DIR")
    if configured:
        return Path(configured)
    return Path("/data/b3_bars")


def meta_root() -> Path:
    return Path(os.environ.get("B3_META_ROOT", "/data/b3_ticks_meta"))


class B3TradesAdapter:
    """Reads original B3 trade ZIPs and downloads the ones that are missing.

    The archive layout is the file the B3 publishes, unchanged:

        {B3_DATA_ROOT}/{TICKER}/{YYYY-MM-DD}.zip

    Each ZIP contains one text member. The member name varies and sometimes
    ends in `_HHMM`, so the reader always opens the first member. Downloads
    are stored at that same path. Nothing in this tree is converted to OHLCV.

    Continuous WIN (`symbol == WIN`) follows `{B3_META_ROOT}/win_ativo.csv`:
    each session uses only that day's active contract. Prices from another
    expiry are never appended on the roll day.
    """

    id = "b3"
    label = "Negócios da B3"
    description = (
        "ZIP original em {B3_DATA_ROOT}/{TICKER}/{AAAA-MM-DD}.zip, "
        "o mesmo corpo de https://arquivos.b3.com.br/rapinegocios/tickercsv/{TICKER}/{data}. "
        "WIN contínuo usa a série de win_ativo.csv em B3_META_ROOT, um vencimento por pregão."
    )

    def __init__(
        self,
        root: Path | None = None,
        cache_dir: Path | None = None,
        meta: Path | None = None,
    ):
        self.root = Path(root) if root is not None else data_root()
        self.cache_dir = Path(cache_dir) if cache_dir is not None else bar_cache_dir()
        self.meta = Path(meta) if meta is not None else meta_root()
        self._active: dict[date, tuple[str, int]] | None = None
        self._manifest: dict[tuple[date, str], int] | None = None
        self._meta_loaded = False

    def load(self, request: DataRequest) -> LoadResult:
        if request.timeframe not in {"1min", "5min", "60min"}:
            raise ValueError("Timeframe da B3 deve ser 1min, 5min ou 60min.")
        minutes = {"1min": 1, "5min": 5, "60min": 60}[request.timeframe]
        warnings: list[str] = []
        frames: list[pd.DataFrame] = []
        trade_frames: list[pd.DataFrame] = []
        day = request.start
        while day <= request.end:
            if day.weekday() < 5:
                frame, note, day_trades = self._load_day(
                    request.symbol,
                    day,
                    request.timeframe,
                    minutes,
                    request.include_after_hours,
                )
                if frame is not None and not frame.empty:
                    frames.append(frame)
                if day_trades is not None and not day_trades.empty:
                    trade_frames.append(day_trades)
                if note:
                    warnings.append(note)
            day += timedelta(days=1)
        if not frames:
            raise ValueError(
                "Nenhum pregão da B3 encontrado no período. A URL pública guarda cerca de "
                "21 pregões, e o diretório local não tinha esses dias. Confira o ativo "
                "(WIN vira o contrato vigente, ex. WINV26) e a variável B3_DATA_ROOT."
            )
        bars = normalize_bars(pd.concat(frames, ignore_index=True))
        bars = filter_dates(bars, request.start, request.end)
        if request.symbol.upper().split(".")[0] == "WIN":
            warnings.append(
                "WIN contínuo usa só a série ativa de cada pregão (win_ativo.csv). "
                "No dia da rolagem os preços de outro vencimento não entram na série, "
                "para o salto entre contratos não virar retorno."
            )
        if not request.include_after_hours:
            warnings.append("Negócios de after-market (TipoSessaoPregao 6) ficaram de fora das barras.")
        trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else None
        return LoadResult(bars=bars, warnings=_unique(warnings), trades=trades)

    def _load_day(self, symbol: str, day: date, timeframe: str, minutes: int, include_after_hours: bool):
        ticker, note = self._resolve_ticker(symbol, day)
        if ticker is None:
            return None, note, None
        path = self.local_path(ticker, day)
        if not _usable(path):
            expected = self._manifest_trades(ticker, day)
            if expected == 0:
                return None, f"{day.isoformat()}: o manifest marca {ticker} sem negócios.", None
            url = B3_URL.format(ticker=ticker, day=day.isoformat())
            try:
                download_raw(url, path)
                logger.info("B3 saved %s", path)
            except FileNotFoundError:
                listed = " O manifest espera esse arquivo." if expected else ""
                return None, f"{day.isoformat()}: sem {path.name} e a B3 respondeu 404 para {ticker}.{listed}", None
            except Exception as exc:  # noqa: BLE001 - surface a short warning per day
                logger.warning("B3 download failed for %s: %s", url, exc)
                return None, f"{day.isoformat()}: falha ao baixar {ticker} ({exc.__class__.__name__}).", None
        try:
            frame = self._bars_for_file(path, ticker, day, timeframe, minutes, include_after_hours)
        except Exception as exc:  # noqa: BLE001
            logger.warning("B3 parse failed for %s: %s", path, exc)
            return None, f"{day.isoformat()}: não foi possível ler {path.name} ({exc.__class__.__name__}).", None
        if frame.empty:
            return None, f"{day.isoformat()}: {ticker} sem negócios do pregão regular.", None
        day_trades = None
        try:
            day_trades = read_trades(path, include_after_hours=include_after_hours)
        except Exception as exc:  # noqa: BLE001 - bars still run; ORB falls back to OHLC
            logger.warning("B3 trades unreadable for %s: %s", path, exc)
            note = (
                f"{day.isoformat()}: não foi possível ler os negócios individuais de {ticker} "
                f"({exc.__class__.__name__}). O ORB stop usa a aproximação OHLC nesse pregão."
            )
            return frame, note, None
        if day_trades is not None and not day_trades.empty:
            day_trades = day_trades.copy()
            day_trades["contract"] = ticker.upper()
        return frame, None, day_trades

    def local_path(self, ticker: str, day: date) -> Path:
        return self.root / ticker.upper() / f"{day.isoformat()}.zip"

    def _bars_for_file(
        self,
        path: Path,
        ticker: str,
        day: date,
        timeframe: str,
        minutes: int,
        include_after_hours: bool,
    ) -> pd.DataFrame:
        cached = self._read_bar_cache(path, ticker, day, timeframe, include_after_hours)
        if cached is not None:
            return cached
        frame = aggregate_trade_file(path, minutes, ticker, include_after_hours=include_after_hours)
        self._write_bar_cache(path, ticker, day, timeframe, include_after_hours, frame)
        return frame

    def _cache_paths(self, ticker: str, day: date, timeframe: str, include_after_hours: bool) -> tuple[Path, Path]:
        directory = self.cache_dir / ticker.upper()
        session = "com-after" if include_after_hours else "regular"
        stem = f"{day.isoformat()}_{timeframe}_{session}"
        return directory / f"{stem}.csv", directory / f"{stem}.meta"

    def _read_bar_cache(
        self,
        source: Path,
        ticker: str,
        day: date,
        timeframe: str,
        include_after_hours: bool,
    ) -> pd.DataFrame | None:
        csv_path, meta_path = self._cache_paths(ticker, day, timeframe, include_after_hours)
        if not csv_path.exists() or not meta_path.exists():
            return None
        stat = source.stat()
        expected = f"{stat.st_size}:{stat.st_mtime_ns}"
        if meta_path.read_text(encoding="utf-8").strip() != expected:
            return None
        frame = pd.read_csv(csv_path)
        frame["contract"] = ticker.upper()
        return frame

    def _write_bar_cache(
        self,
        source: Path,
        ticker: str,
        day: date,
        timeframe: str,
        include_after_hours: bool,
        frame: pd.DataFrame,
    ) -> None:
        try:
            csv_path, meta_path = self._cache_paths(ticker, day, timeframe, include_after_hours)
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            exported = frame.copy()
            exported["timestamp"] = pd.to_datetime(exported["timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S")
            exported.drop(columns=["contract"], errors="ignore").to_csv(csv_path, index=False)
            stat = source.stat()
            meta_path.write_text(f"{stat.st_size}:{stat.st_mtime_ns}", encoding="utf-8")
        except OSError as exc:
            logger.info("Skipping bar cache: %s", exc)

    def _resolve_ticker(self, symbol: str, day: date) -> tuple[str | None, str | None]:
        root = symbol.upper().split(".")[0]
        if root != "WIN":
            return root, None
        table = self._win_ativo()
        row = table.get(day)
        if row is None:
            return None, f"{day.isoformat()}: sem linha em win_ativo.csv para o WIN contínuo."
        ticker, trades = row
        if trades <= 0:
            return None, f"{day.isoformat()}: win_ativo.csv marca {ticker} sem negócios."
        return ticker, None

    def _win_ativo(self) -> dict[date, tuple[str, int]]:
        if self._active is None:
            path = self.meta / "win_ativo.csv"
            if not path.is_file():
                raise ValueError(
                    "O WIN contínuo precisa de win_ativo.csv em B3_META_ROOT "
                    f"({self.meta}). Na máquina do time esse diretório é "
                    "/workspace/quant/dados/b3_ticks_meta."
                )
            frame = pd.read_csv(path, dtype=str)
            active: dict[date, tuple[str, int]] = {}
            for _, row in frame.iterrows():
                session = date.fromisoformat(str(row["date"])[:10])
                trades = int(float(row["trades"]))
                active[session] = (str(row["serie_ativa"]).strip().upper(), trades)
            self._active = active
        return self._active

    def _manifest_trades(self, ticker: str, day: date) -> int | None:
        if self._manifest is None and not self._meta_loaded:
            self._meta_loaded = True
            path = self.meta / "manifest.csv"
            if not path.is_file():
                self._manifest = {}
            else:
                listed = pd.read_csv(path, dtype=str)
                found: dict[tuple[date, str], int] = {}
                for _, row in listed.iterrows():
                    session = date.fromisoformat(str(row["date"])[:10])
                    name = str(row["ticker"]).strip().upper()
                    found[(session, name)] = int(float(row["trades"]))
                self._manifest = found
        if not self._manifest:
            return None
        return self._manifest.get((day, ticker.upper()))


def download_raw(url: str, dest: Path, timeout: float = 180.0) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name(dest.name + ".partial")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; meu-rico-backtest/0.1)"}
    try:
        with httpx.stream("GET", url, headers=headers, timeout=timeout, follow_redirects=True) as response:
            if response.status_code == 404:
                raise FileNotFoundError(url)
            response.raise_for_status()
            with temporary.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
        if temporary.stat().st_size == 0:
            raise FileNotFoundError(url)
        temporary.replace(dest)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def aggregate_trade_file(
    path: Path,
    minutes: int,
    contract: str,
    include_after_hours: bool = False,
) -> pd.DataFrame:
    """Aggregate an original B3 trade ZIP into OHLCV bars.

    The ZIP is not rewritten. AcaoAtualizacao other than 0 is dropped: only 0
    (new trade) was observed, and any other code is treated as a cancellation
    or correction. TipoSessaoPregao 6 is the after-market and is excluded
    unless include_after_hours is set. Session 1 stays, auctions included.
    HoraFechamento is read as text (HHMMSSmmm, leading zero before 10:00).
    """
    pieces: list[pd.DataFrame] = []
    for chunk in _trade_chunks(path):
        piece = _bars_from_chunk(chunk, minutes, include_after_hours)
        if not piece.empty:
            pieces.append(piece)
    columns = ["timestamp", "open", "high", "low", "close", "volume", "contract"]
    if not pieces:
        return pd.DataFrame(columns=columns)
    combined = pd.concat(pieces, ignore_index=True)
    grouped = combined.groupby("timestamp", sort=True).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    grouped = grouped.reset_index()
    grouped["contract"] = contract
    return grouped


def read_trades(path: Path, include_after_hours: bool = False) -> pd.DataFrame:
    """Individual prints from a tickercsv ZIP: timestamp and price, file order.

    The same filters as the bars apply. AcaoAtualizacao other than 0 is
    dropped, and after-market session 6 stays out unless requested. The
    caller uses the first print that touches a level. Nothing is downloaded.
    """
    pieces: list[pd.DataFrame] = []
    for chunk in _trade_chunks(path):
        piece = _trades_from_chunk(chunk, include_after_hours)
        if not piece.empty:
            pieces.append(piece)
    columns = ["timestamp", "price"]
    if not pieces:
        return pd.DataFrame(columns=columns)
    combined = pd.concat(pieces, ignore_index=True)
    combined["timestamp"] = pd.to_datetime(combined["timestamp"], utc=False)
    if combined["timestamp"].dt.tz is None:
        combined["timestamp"] = combined["timestamp"].dt.tz_localize(
            TZ_NAME, ambiguous="infer", nonexistent="shift_forward",
        )
    else:
        combined["timestamp"] = combined["timestamp"].dt.tz_convert(TZ_NAME)
    return combined.sort_values("timestamp", kind="mergesort").reset_index(drop=True)


def contract_prior_close(
    root: Path,
    contract: str,
    day: date,
    include_after_hours: bool = False,
) -> float | None:
    """Previous session close of this contract, from its own files.

    The continuous WIN series is not a valid source: on the previous day
    another expiry may have been active. None means the file is missing and
    the signal must be skipped.
    """
    cursor = day - timedelta(days=1)
    for _ in range(12):
        if cursor.weekday() < 5:
            path = Path(root) / contract.upper() / f"{cursor.isoformat()}.zip"
            if path.is_file() and path.stat().st_size > 0:
                bars = aggregate_trade_file(path, 1, contract, include_after_hours=include_after_hours)
                if not bars.empty:
                    return float(bars.iloc[-1]["close"])
        cursor -= timedelta(days=1)
    return None


def _trade_chunks(path: Path):
    archive = None
    if _is_zip(path):
        archive = zipfile.ZipFile(path)
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if not names:
            archive.close()
            return
        handle = archive.open(names[0])
    else:
        handle = path.open("rb")
    try:
        reader = pd.read_csv(
            handle,
            sep=";",
            chunksize=1_000_000,
            dtype="string",
            usecols=lambda name: _norm(str(name))
            in {
                "preconegocio",
                "quantidadenegociada",
                "horafechamento",
                "datanegocio",
                "datareferencia",
                "acaoatualizacao",
                "tiposessaopregao",
            },
        )
        yield from reader
    finally:
        handle.close()
        if archive is not None:
            archive.close()


def _trades_from_chunk(chunk: pd.DataFrame, include_after_hours: bool = False) -> pd.DataFrame:
    prepared = _prepared_prints(chunk, include_after_hours)
    if prepared is None or prepared.empty:
        return pd.DataFrame(columns=["timestamp", "price"])
    clock = prepared["clock"]
    stamp = (
        prepared["day"]
        + " "
        + clock.str.slice(0, 2)
        + ":"
        + clock.str.slice(2, 4)
        + ":"
        + clock.str.slice(4, 6)
        + "."
        + clock.str.slice(6, 9)
    )
    built = pd.DataFrame({"timestamp": stamp, "price": prepared["price"]})
    return built.dropna(subset=["price"])


def _prepared_prints(chunk: pd.DataFrame, include_after_hours: bool):
    renamed = {_norm(str(column)): column for column in chunk.columns}
    required = ["preconegocio", "quantidadenegociada", "horafechamento"]
    if any(name not in renamed for name in required):
        raise ValueError("Arquivo da B3 sem as colunas de preço, quantidade ou hora.")
    frame = chunk
    if "acaoatualizacao" in renamed:
        action = frame[renamed["acaoatualizacao"]].fillna("").str.replace(r"\.0$", "", regex=True)
        frame = frame[action.eq("0")]
    if "tiposessaopregao" in renamed:
        session = frame[renamed["tiposessaopregao"]].fillna("").str.replace(r"\.0$", "", regex=True)
        allowed = {"1", "6"} if include_after_hours else {"1"}
        frame = frame[session.isin(allowed)]
    if frame.empty:
        return None
    date_column = renamed.get("datanegocio", renamed.get("datareferencia"))
    if date_column is None:
        raise ValueError("Arquivo da B3 sem a data do negócio.")
    prices = pd.to_numeric(
        frame[renamed["preconegocio"]].str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
        errors="coerce",
    )
    quantity = pd.to_numeric(
        frame[renamed["quantidadenegociada"]].str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
        errors="coerce",
    ).fillna(0)
    clock = frame[renamed["horafechamento"]].fillna("").str.replace(r"\D", "", regex=True).str.zfill(9)
    day = frame[date_column].astype(str).str.slice(0, 10)
    prepared = pd.DataFrame({"day": day, "clock": clock, "price": prices, "qty": quantity})
    return prepared.dropna(subset=["price"])


def _bars_from_chunk(chunk: pd.DataFrame, minutes: int, include_after_hours: bool = False) -> pd.DataFrame:
    empty = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    prepared = _prepared_prints(chunk, include_after_hours)
    if prepared is None or prepared.empty:
        return empty
    clock = prepared["clock"]
    total_minutes = clock.str.slice(0, 2).astype(int) * 60 + clock.str.slice(2, 4).astype(int)
    total_minutes = total_minutes - (total_minutes % minutes)
    hour = (total_minutes // 60).astype(str).str.zfill(2)
    minute = (total_minutes % 60).astype(str).str.zfill(2)
    timestamp = prepared["day"] + " " + hour + ":" + minute + ":00"
    built = pd.DataFrame({"timestamp": timestamp, "price": prepared["price"].to_numpy(), "qty": prepared["qty"].to_numpy()})
    built = built[built["timestamp"].str.len() >= 19]
    if built.empty:
        return empty
    return (
        built.groupby("timestamp", sort=False)
        .agg(open=("price", "first"), high=("price", "max"), low=("price", "min"), close=("price", "last"), volume=("qty", "sum"))
        .reset_index()
    )


def _usable(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _is_zip(path: Path) -> bool:
    with path.open("rb") as handle:
        return handle.read(2) == b"PK"


def _norm(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out
