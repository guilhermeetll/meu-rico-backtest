import io
import zipfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from data.b3 import B3TradesAdapter, aggregate_trade_file, contract_prior_close
from data.base import DataRequest
from engine.series import prior_close_same_contract, return_versus_prior_close

FIXTURE = Path(__file__).parent / "fixtures" / "amostra_WINV26_2026-09-01.zip"
TZ = ZoneInfo("America/Sao_Paulo")

TRADES = """DataReferencia;CodigoInstrumento;AcaoAtualizacao;PrecoNegocio;QuantidadeNegociada;HoraFechamento;CodigoIdentificadorNegocio;TipoSessaoPregao;DataNegocio;CodigoParticipanteComprador;CodigoParticipanteVendedor
2026-09-30;WINV26;0;184700,000;10;090000000;10;1;2026-09-30;3;3
2026-09-30;WINV26;0;184710,000;5;090030000;20;1;2026-09-30;3;3
2026-09-30;WINV26;0;184705,000;1;090100000;30;1;2026-09-30;3;3
2026-09-30;WINV26;2;184800,000;9;090200000;40;1;2026-09-30;3;3
2026-09-30;WINV26;0;184900,000;4;090500000;50;6;2026-09-30;3;3
"""


def _zip_bytes(member: str = "30-09-2026_NEGOCIOSAVISTA_WINV26.txt", body: str = TRADES) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member, body)
    return buffer.getvalue()


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _meta(root: Path, rows: str) -> Path:
    meta = root
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "win_ativo.csv").write_text("date,serie_ativa,trades\n" + rows, encoding="utf-8")
    return meta


def test_original_zip_drops_cancel_and_after_hours(tmp_path: Path):
    path = tmp_path / "WINV26" / "2026-09-30.zip"
    _write(path, _zip_bytes())
    bars = aggregate_trade_file(path, 1, "WINV26")
    assert len(bars) == 2
    first = bars.iloc[0]
    assert first["open"] == 184700
    assert first["high"] == 184710
    assert first["close"] == 184710
    assert first["volume"] == 15
    assert bars.iloc[1]["close"] == 184705
    assert list(bars["timestamp"].astype(str)) == ["2026-09-30 09:00:00", "2026-09-30 09:01:00"]


def test_after_hours_can_be_included(tmp_path: Path):
    path = tmp_path / "session.zip"
    _write(path, _zip_bytes())
    bars = aggregate_trade_file(path, 1, "WINV26", include_after_hours=True)
    assert len(bars) == 3
    assert bars.iloc[2]["close"] == 184900


def test_clock_keeps_the_leading_zero(tmp_path: Path):
    body = TRADES.replace("090000000", "090039906", 1)
    path = tmp_path / "clock.zip"
    _write(path, _zip_bytes(body=body))
    bars = aggregate_trade_file(path, 1, "WINV26")
    assert str(bars.iloc[0]["timestamp"]).startswith("2026-09-30 09:00")


def test_first_zip_member_is_used_even_with_hhmm_suffix(tmp_path: Path):
    path = tmp_path / "suffixed.zip"
    _write(path, _zip_bytes("30-09-2026_NEGOCIOSAVISTA_WINV26_2359.txt"))
    bars = aggregate_trade_file(path, 1, "WINV26")
    assert bars.iloc[0]["open"] == 184700


def test_truncated_real_sample_is_one_opening_minute():
    """AMOSTRA TRUNCADA: primeiras 2000 linhas de WINV26 em 01/09/2026, não o pregão inteiro."""
    assert FIXTURE.is_file()
    with zipfile.ZipFile(FIXTURE) as archive:
        assert len(archive.namelist()) == 1
    bars = aggregate_trade_file(FIXTURE, 1, "WINV26")
    assert len(bars) == 1
    assert str(bars.iloc[0]["timestamp"]).startswith("2026-09-01 09:02")
    assert bars.iloc[0]["open"] == 179390
    assert bars.iloc[0]["contract"] == "WINV26"


def test_local_zip_layout_is_read_without_download(tmp_path: Path, monkeypatch):
    root = tmp_path / "b3_ticks"
    _write(root / "WINV26" / "2026-09-30.zip", _zip_bytes())

    def fail_download(url, dest, timeout=180.0):
        raise AssertionError(f"não deveria baixar {url}")

    monkeypatch.setattr("data.b3.download_raw", fail_download)
    result = B3TradesAdapter(root=root, cache_dir=tmp_path / "bars", meta=tmp_path / "meta").load(
        DataRequest(symbol="WINV26", start=date(2026, 9, 30), end=date(2026, 9, 30), timeframe="1min")
    )
    assert len(result.bars) == 2
    assert result.bars["contract"].iloc[0] == "WINV26"
    assert str(result.bars["timestamp"].dt.tz) == "America/Sao_Paulo"
    assert list(result.trades["price"]) == [184700, 184710, 184705]
    assert result.trades["contract"].iloc[0] == "WINV26"
    assert str(result.trades["timestamp"].iloc[0]).startswith("2026-09-30 09:00:00")


def test_download_uses_the_zip_layout(tmp_path: Path, monkeypatch):
    root = tmp_path / "b3_ticks"
    meta = _meta(tmp_path / "meta", "2026-09-30,WINV26,10\n")
    calls = {"n": 0}

    def fake_download(url, dest, timeout=180.0):
        calls["n"] += 1
        assert url.endswith("/WINV26/2026-09-30")
        assert dest == root / "WINV26" / "2026-09-30.zip"
        _write(dest, _zip_bytes())

    monkeypatch.setattr("data.b3.download_raw", fake_download)
    adapter = B3TradesAdapter(root=root, cache_dir=tmp_path / "bars", meta=meta)
    request = DataRequest(symbol="WIN", start=date(2026, 9, 30), end=date(2026, 9, 30), timeframe="5min")
    adapter.load(request)
    assert calls["n"] == 1
    assert (root / "WINV26" / "2026-09-30.zip").is_file()
    adapter.load(request)
    assert calls["n"] == 1


def test_continuous_win_does_not_mix_contracts_on_the_roll(tmp_path: Path, monkeypatch):
    root = tmp_path / "b3_ticks"
    meta = _meta(tmp_path / "meta", "2026-09-29,WINV26,10\n2026-09-30,WINZ26,10\n")
    _write(root / "WINV26" / "2026-09-29.zip", _session_zip("2026-09-29", "WINV26", 180_000))
    _write(root / "WINV26" / "2026-09-30.zip", _session_zip("2026-09-30", "WINV26", 181_000))
    _write(root / "WINZ26" / "2026-09-29.zip", _session_zip("2026-09-29", "WINZ26", 100_000))
    _write(root / "WINZ26" / "2026-09-30.zip", _session_zip("2026-09-30", "WINZ26", 100_500))
    monkeypatch.setattr("data.b3.download_raw", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("download")))
    result = B3TradesAdapter(root=root, cache_dir=tmp_path / "bars", meta=meta).load(
        DataRequest(symbol="WIN", start=date(2026, 9, 29), end=date(2026, 9, 30), timeframe="1min")
    )
    by_day = {
        day: frame["contract"].unique().tolist()
        for day, frame in result.bars.groupby(result.bars["timestamp"].dt.date)
    }
    assert by_day[date(2026, 9, 29)] == ["WINV26"]
    assert by_day[date(2026, 9, 30)] == ["WINZ26"]
    day_one_close = float(result.bars.loc[result.bars["timestamp"].dt.date == date(2026, 9, 29), "close"].iloc[-1])
    day_two_open = float(result.bars.loc[result.bars["timestamp"].dt.date == date(2026, 9, 30), "open"].iloc[0])
    assert day_one_close == pytest.approx(180_000)
    assert day_two_open == pytest.approx(100_500)
    assert day_two_open / day_one_close - 1 < -0.4
    # The spliced series does not contain WINZ26's previous session, because
    # that day the active contract was WINV26. Using it as prior close is wrong.
    assert prior_close_same_contract(result.bars, date(2026, 9, 30), "WINZ26") is None
    own_close = contract_prior_close(root, "WINZ26", date(2026, 9, 30))
    assert own_close == pytest.approx(100_000)
    history = pd.concat(
        [
            aggregate_trade_file(root / "WINZ26" / "2026-09-29.zip", 1, "WINZ26"),
            aggregate_trade_file(root / "WINZ26" / "2026-09-30.zip", 1, "WINZ26"),
        ],
        ignore_index=True,
    )
    history["timestamp"] = pd.to_datetime(history["timestamp"]).dt.tz_localize("America/Sao_Paulo")
    assert return_versus_prior_close(history, date(2026, 9, 30), "WINZ26", day_two_open) == pytest.approx(0.005)


def test_missing_same_contract_prior_close_skips_the_signal(tmp_path: Path):
    bars = _minute_frame(date(2026, 9, 30), "WINZ26", 100_500)
    assert prior_close_same_contract(bars, date(2026, 9, 30), "WINZ26") is None
    assert return_versus_prior_close(bars, date(2026, 9, 30), "WINZ26", 100_500) is None
    other = _minute_frame(date(2026, 9, 29), "WINV26", 180_000)
    mixed = pd.concat([other, bars], ignore_index=True)
    assert prior_close_same_contract(mixed, date(2026, 9, 30), "WINZ26") is None


def test_continuous_win_requires_the_meta_file(tmp_path: Path):
    with pytest.raises(ValueError, match="win_ativo.csv"):
        B3TradesAdapter(root=tmp_path / "ticks", cache_dir=tmp_path / "bars", meta=tmp_path / "missing").load(
            DataRequest(symbol="WIN", start=date(2026, 9, 30), end=date(2026, 9, 30), timeframe="1min")
        )


def _session_zip(day: str, contract: str, price: int) -> bytes:
    header = (
        "DataReferencia;CodigoInstrumento;AcaoAtualizacao;PrecoNegocio;"
        "QuantidadeNegociada;HoraFechamento;CodigoIdentificadorNegocio;"
        "TipoSessaoPregao;DataNegocio;CodigoParticipanteComprador;CodigoParticipanteVendedor\n"
    )
    line = f"{day};{contract};0;{price},000;1;100000000;10;1;{day};3;3\n"
    return _zip_bytes(f"{day}_NEGOCIOSAVISTA_{contract}.txt", header + line)


def _minute_frame(day: date, contract: str, price: float) -> pd.DataFrame:
    stamp = pd.Timestamp(datetime(day.year, day.month, day.day, 18, 24, tzinfo=TZ))
    return pd.DataFrame(
        {
            "timestamp": [stamp],
            "open": [price],
            "high": [price],
            "low": [price],
            "close": [price],
            "volume": [1],
            "contract": [contract],
        }
    )
