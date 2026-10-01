from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from engine.backtest import SampleSplit, run_backtest
from engine.costs import CostModel
from engine.instruments import resolve_instrument
from engine.models import BacktestResult, EquityPoint, Metrics, SegmentResult, Trade, WalkForwardResult
from engine.strategies.registry import get_strategy
from engine.study import run_study
from engine.walkforward import WalkForwardConfig
from data.b3 import B3TradesAdapter
from data.base import DataRequest
from data.csv_loader import CsvBarsAdapter
from data.yahoo import YahooFinanceAdapter
from app.schemas import BacktestInput, StudyInput

TIMEFRAME_MINUTES = {"1min": 1, "5min": 5, "60min": 60}
SOURCE_LABELS = {"b3": "B3", "yahoo": "Yahoo Finance", "csv": "CSV"}


def sample_csv_path() -> Path:
    configured = os.environ.get("SAMPLE_DATA_PATH")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / "sample_data" / "win_exemplo_1min.csv"


def upload_dir() -> Path:
    path = Path(os.environ.get("UPLOAD_DIR", "/data/uploads"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def execute_backtest(payload: BacktestInput) -> dict:
    instrument, strategy, loaded, costs, split, forward = _context(payload)
    result = run_backtest(
        loaded.bars,
        strategy,
        payload.strategy_params,
        instrument,
        costs,
        symbol=instrument.symbol,
        initial_capital=payload.initial_capital,
        tax_rate=payload.tax_rate,
        n_trials=payload.n_trials,
        bar_minutes=TIMEFRAME_MINUTES[payload.timeframe],
        sample_split=split,
        walk_forward=forward,
    )
    warnings = list(loaded.warnings) + list(result.warnings)
    return _serialize(payload, instrument, costs, result, warnings)


def execute_study(payload: StudyInput) -> dict:
    variants = _merge_variants(payload.strategy_params, payload.variants)
    instrument, strategy, loaded, costs, split, forward = _context(payload)
    trials, results = run_study(
        loaded.bars,
        strategy,
        variants,
        instrument,
        costs,
        symbol=instrument.symbol,
        initial_capital=payload.initial_capital,
        tax_rate=payload.tax_rate,
        n_trials=payload.n_trials,
        bar_minutes=TIMEFRAME_MINUTES[payload.timeframe],
        sample_split=split,
        walk_forward=forward,
    )
    bodies = []
    for params, result in results:
        adjusted = payload.model_copy(update={"n_trials": trials, "strategy_params": params})
        warnings = list(loaded.warnings) + list(result.warnings)
        bodies.append(_serialize(adjusted, instrument, costs, result, warnings))
    return {
        "n_variants": len(variants),
        "n_trials": trials,
        "variants": bodies,
    }


def _context(payload: BacktestInput):
    if payload.end < payload.start:
        raise ValueError("A data final é anterior à data inicial.")
    if payload.timeframe not in TIMEFRAME_MINUTES:
        raise ValueError("Timeframe deve ser 1min, 5min ou 60min.")
    if payload.n_trials < 1:
        raise ValueError("O número de configurações testadas deve ser pelo menos 1.")
    if not 0 <= payload.tax_rate <= 1:
        raise ValueError("A alíquota de IR deve estar entre 0 e 1.")
    if payload.initial_capital <= 0:
        raise ValueError("O capital inicial deve ser positivo.")
    if payload.costs.fee_per_side < 0 or payload.costs.fee_rate < 0 or payload.costs.slippage_ticks < 0:
        raise ValueError("Custos não podem ser negativos.")

    instrument = resolve_instrument(payload.symbol)
    strategy = get_strategy(payload.strategy)
    loaded = _load(payload)
    costs = CostModel(
        fee_per_side=payload.costs.fee_per_side,
        slippage_ticks=payload.costs.slippage_ticks,
        fee_rate=payload.costs.fee_rate,
    )
    split = SampleSplit(
        enabled=payload.sample_split.enabled,
        in_sample_fraction=payload.sample_split.in_sample_fraction,
        split_date=payload.sample_split.split_date,
    )
    forward = WalkForwardConfig(
        enabled=payload.walk_forward.enabled,
        train_sessions=payload.walk_forward.train_sessions,
        test_sessions=payload.walk_forward.test_sessions,
        step_sessions=payload.walk_forward.step_sessions,
        anchored=payload.walk_forward.anchored,
        optimize_metric=payload.walk_forward.optimize_metric,
        param_grid=payload.walk_forward.param_grid,
    )
    return instrument, strategy, loaded, costs, split, forward


def _merge_variants(base: dict, variants: list[dict]) -> list[dict]:
    merged = []
    for variant in variants:
        if not isinstance(variant, dict):
            raise ValueError("Cada variante do estudo precisa ser um objeto de parâmetros.")
        merged.append({**base, **variant})
    return merged


def _load(payload: BacktestInput):
    request = DataRequest(
        symbol=payload.symbol,
        start=payload.start,
        end=payload.end,
        timeframe=payload.timeframe,
        csv_path=_csv_path(payload),
        column_map=payload.column_map,
        include_after_hours=payload.include_after_hours,
    )
    if payload.data_source == "b3":
        return B3TradesAdapter().load(request)
    if payload.data_source == "yahoo":
        return YahooFinanceAdapter().load(request)
    if payload.data_source == "csv":
        return CsvBarsAdapter().load(request)
    raise ValueError("Fonte de dados desconhecida. Use b3, yahoo ou csv.")


def _csv_path(payload: BacktestInput) -> Path | None:
    if payload.data_source != "csv":
        return None
    source = (payload.csv_source or "example").strip()
    if source in {"", "example"}:
        path = sample_csv_path()
        if not path.is_file():
            raise ValueError("O CSV de exemplo não está disponível nesta instalação.")
        return path
    name = Path(source).name
    if name != source or not name.endswith(".csv"):
        raise ValueError("Identificador de CSV inválido.")
    path = upload_dir() / name
    if not path.is_file():
        raise ValueError("O CSV enviado não foi encontrado. Envie o arquivo de novo.")
    return path


def _serialize(payload: BacktestInput, instrument, costs: CostModel, result: BacktestResult, warnings: list[str]) -> dict:
    tick_value = instrument.tick_value
    round_turn_slip = costs.slippage_ticks * tick_value * 2
    unit = "contrato" if instrument.family == "WIN" else "ação"
    fee_bits = []
    if costs.fee_per_side:
        fee_bits.append(f"R$ {costs.fee_per_side:.2f} fixos por {unit} por lado")
    if costs.fee_rate:
        fee_bits.append(
            f"{costs.fee_rate * 100:.3f}% por lado sobre o valor negociado "
            "(preço com slippage × quantidade × valor do ponto)"
        )
    if not fee_bits:
        fee_bits.append("sem taxa")
    notes = [
        (
            f"{instrument.symbol}: ponto de R$ {instrument.point_value:.2f} e tick de "
            f"{instrument.tick_size:g} ponto(s), então 1 tick = R$ {tick_value:.2f} por {unit}."
        ),
        (
            f"Custos desta execução: {'; '.join(fee_bits)}. "
            f"Slippage de {costs.slippage_ticks:g} tick(s) adverso(s) por lado "
            f"(R$ {round_turn_slip:.2f} na operação, por {unit})."
        ),
        (
            "IR de day trade apurado mês a mês. Prejuízo de um mês compensa lucro dos meses "
            "seguintes dentro da amostra. A alíquota desta execução é "
            f"{payload.tax_rate:.0%}."
        ),
        (
            f"Retorno e Sharpe usam o capital de referência de R$ {payload.initial_capital:,.2f} "
            f"(pnl dividido por esse capital, Sharpe anualizado com √252, taxa livre de risco zero). "
            f"O Sharpe deflacionado é a probabilidade de Bailey & López de Prado com N={payload.n_trials} "
            "e o Sharpe diário, não o anualizado."
        ),
    ]
    if instrument.family == "WIN":
        notes.append(
            "Horário automático do WIN: 09:00–18:25 desde 11/03/2024; antes disso, 09:00–17:55 "
            "no horário de verão dos EUA e 09:00–18:25 fora dele. No vencimento do contrato "
            "específico, o pregão encerra mais cedo."
        )
        notes.append(
            "O retorno de cada pregão fica dentro de um único vencimento, o da série ativa "
            "naquele dia. O fechamento de outro contrato não entra no sinal. Se um sinal "
            "precisar do fechamento anterior, usa o do mesmo vencimento ou o pregão é pulado."
        )
    text = _summary(result.metrics, payload.symbol)
    return {
        "strategy": payload.strategy,
        "symbol": payload.symbol.upper(),
        "data_source": payload.data_source,
        "timeframe": payload.timeframe,
        "summary": text,
        "metrics": _metrics(result.metrics),
        "in_sample": _segment(result.in_sample),
        "out_of_sample": _segment(result.out_of_sample),
        "walk_forward": _walk(result.walk_forward),
        "equity": [_point(point) for point in result.equity],
        "trades": [_trade(trade) for trade in result.trades],
        "warnings": _unique(warnings),
        "notes": notes,
        "assumptions": {
            "point_value": instrument.point_value,
            "tick_size": instrument.tick_size,
            "tick_value": tick_value,
            "fee_per_side": costs.fee_per_side,
            "fee_rate": costs.fee_rate,
            "slippage_ticks": costs.slippage_ticks,
            "tax_rate": payload.tax_rate,
            "initial_capital": payload.initial_capital,
            "n_trials": payload.n_trials,
        },
        "request": {
            "start": payload.start.isoformat(),
            "end": payload.end.isoformat(),
            "strategy_params": payload.strategy_params,
            "sample_split": payload.sample_split.model_dump(mode="json"),
            "walk_forward": payload.walk_forward.model_dump(mode="json"),
        },
    }


def _summary(metrics: Metrics, symbol: str) -> str:
    net = _brl(metrics.net_pnl_after_tax)
    sharpe = "indefinido" if metrics.sharpe is None else f"{metrics.sharpe:.2f}".replace(".", ",")
    return (
        f"{metrics.n_sessions} pregões, {metrics.n_trades} operações em {symbol.upper()}. "
        f"Resultado líquido de IR {net}. Sharpe {sharpe}."
    )


def _metrics(metrics: Metrics) -> dict:
    return {
        "total_pnl": metrics.total_pnl,
        "total_return": metrics.total_return,
        "gross_pnl": metrics.gross_pnl,
        "fees": metrics.fees,
        "slippage_cost": metrics.slippage_cost,
        "sharpe": metrics.sharpe,
        "deflated_sharpe": metrics.deflated_sharpe,
        "max_drawdown": metrics.max_drawdown,
        "max_drawdown_pct": metrics.max_drawdown_pct,
        "n_trades": metrics.n_trades,
        "win_rate": metrics.win_rate,
        "payoff": metrics.payoff,
        "net_pnl_after_tax": metrics.net_pnl_after_tax,
        "tax_paid": metrics.tax_paid,
        "n_sessions": metrics.n_sessions,
        "n_trials": metrics.n_trials,
    }


def _segment(segment: SegmentResult | None) -> dict | None:
    if segment is None:
        return None
    return {
        "start": segment.start.isoformat() if segment.start else None,
        "end": segment.end.isoformat() if segment.end else None,
        "metrics": _metrics(segment.metrics),
        "equity": [_point(point) for point in segment.equity],
    }


def _walk(result: WalkForwardResult | None) -> dict | None:
    if result is None:
        return None
    return {
        "n_configurations": result.n_configurations,
        "windows": [
            {
                "train_start": window.train_start.isoformat(),
                "train_end": window.train_end.isoformat(),
                "test_start": window.test_start.isoformat(),
                "test_end": window.test_end.isoformat(),
                "chosen_params": window.chosen_params,
                "train_score": window.train_score,
                "test_pnl": window.test_pnl,
            }
            for window in result.windows
        ],
        "oos_metrics": _metrics(result.oos_metrics),
        "oos_equity": [_point(point) for point in result.oos_equity],
    }


def _point(point: EquityPoint) -> dict:
    return {
        "date": point.date,
        "equity": point.equity,
        "equity_after_tax": point.equity_after_tax,
        "daily_pnl": point.daily_pnl,
        "daily_pnl_after_tax": point.daily_pnl_after_tax,
        "baseline": point.baseline,
    }


def _trade(trade: Trade) -> dict:
    return {
        "session_date": trade.session_date.isoformat(),
        "symbol": trade.symbol,
        "direction": "long" if trade.direction == 1 else "short",
        "quantity": trade.quantity,
        "entry_time": trade.entry_time.isoformat(),
        "exit_time": trade.exit_time.isoformat(),
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "gross_pnl": trade.gross_pnl,
        "fees": trade.fees,
        "slippage_cost": trade.slippage_cost,
        "pnl": trade.pnl,
        "signal_return": trade.signal_return,
    }


def _brl(value: float) -> str:
    sign = "-" if value < 0 else ""
    quantized = f"{abs(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{sign}R$ {quantized}"


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def stamp() -> datetime:
    return datetime.now(timezone.utc)
