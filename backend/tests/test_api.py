from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def _client(tmp_path: Path, monkeypatch):
    csv_path = tmp_path / "win_exemplo_1min.csv"
    lines = ["datetime,open,high,low,close,volume"]
    sessions = [
        (date(2026, 9, 17), 100_000, 100_080, 100_100, 100_160),
        (date(2026, 9, 18), 100_000, 99_900, 99_800, 99_700),
    ]
    for day, open_px, signal_px, entry_px, exit_px in sessions:
        for minute in range(9 * 60, 18 * 60 + 25):
            hour, mm = divmod(minute, 60)
            if minute <= 9 * 60 + 29:
                frac = (minute - 9 * 60) / 29
                price = open_px + (signal_px - open_px) * frac
            elif minute <= 17 * 60 + 55:
                price = entry_px if minute == 17 * 60 + 55 else signal_px
            else:
                span = (18 * 60 + 24) - (17 * 60 + 55)
                frac = (minute - (17 * 60 + 55)) / span
                price = entry_px + (exit_px - entry_px) * frac
            lines.append(f"{day.isoformat()} {hour:02d}:{mm:02d}:00,{price:.2f},{price:.2f},{price:.2f},{price:.2f},10")
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path}/backtests.db")
    monkeypatch.setenv("SAMPLE_DATA_PATH", str(csv_path))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("B3_DATA_ROOT", str(tmp_path / "b3_ticks"))
    return TestClient(create_app())


def test_backtest_round_trip_and_history(tmp_path: Path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        created = client.post(
            "/api/backtests",
            json={
                "strategy": "intraday_momentum",
                "symbol": "WIN",
                "data_source": "csv",
                "timeframe": "1min",
                "start": "2026-09-01",
                "end": "2026-09-30",
                "csv_source": "example",
                "strategy_params": {"signal_minutes": 30, "trade_minutes": 30, "threshold": 0},
                "costs": {"fee_per_side": 0.5, "slippage_ticks": 1},
                "tax_rate": 0.2,
                "initial_capital": 10000,
                "n_trials": 3,
                "sample_split": {"enabled": True, "in_sample_fraction": 0.5},
            },
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["metrics"]["n_trades"] == 2
        assert body["metrics"]["n_trials"] == 3
        assert body["in_sample"]["metrics"]["n_trades"] == 1
        assert body["out_of_sample"]["metrics"]["n_trades"] == 1
        assert body["trades"][0]["direction"] == "long"
        assert body["trades"][1]["direction"] == "short"
        assert "líquido de IR" in body["summary"]
        listed = client.get("/api/backtests")
        assert listed.status_code == 200
        assert listed.json()[0]["id"] == body["id"]
        detail = client.get(f"/api/backtests/{body['id']}")
        assert detail.status_code == 200
        assert detail.json()["metrics"]["n_trades"] == 2


def test_study_counts_variants_as_deflated_sharpe_trials(tmp_path: Path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        created = client.post(
            "/api/studies",
            json={
                "strategy": "intraday_momentum",
                "symbol": "WIN",
                "data_source": "csv",
                "timeframe": "1min",
                "start": "2026-09-17",
                "end": "2026-09-18",
                "csv_source": "example",
                "strategy_params": {"signal_minutes": 30, "trade_minutes": 30, "threshold": 0},
                "costs": {"fee_per_side": 0.5, "slippage_ticks": 1, "fee_rate": 0},
                "n_trials": 1,
                "variants": [
                    {"signal_anchor": "session_open", "trade_window": "session_close"},
                    {"signal_anchor": "prior_close", "trade_window": "before_cash_auction"},
                ],
            },
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["n_variants"] == 2
        assert body["n_trials"] == 2
        assert all(item["metrics"]["n_trials"] == 2 for item in body["variants"])
        assert "N=2" in body["variants"][0]["notes"][2] or any("N=2" in note for note in body["variants"][0]["notes"])
        instruments = client.get("/api/instruments").json()
        equity = next(item for item in instruments if item["symbol"] == "ACAO")
        assert equity["tick_size"] == 0.01
        assert equity["default_fee_rate"] == 0.00023
        assert equity["default_slippage_ticks"] == 1
