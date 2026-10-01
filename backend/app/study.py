"""Run a pre-registered grid and write CSV and Markdown tables.

The deflated Sharpe of every row uses one N: the number of rows in the
grid, or a larger --n-trials when the caller passes the project's
accumulated trial count. A smaller value does not shrink N.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime
from pathlib import Path

from data.b3 import B3TradesAdapter
from data.base import DataRequest
from data.csv_loader import CsvBarsAdapter
from data.yahoo import YahooFinanceAdapter
from engine.costs import CostModel
from engine.instruments import resolve_instrument
from engine.strategies.preregistry import CATALOG_TRIALS, variants_for
from engine.strategies.registry import get_strategy
from engine.study import run_study, trial_count
from app.service import sample_csv_path

SOURCE_LABELS = {"b3": "B3", "yahoo": "Yahoo Finance", "csv": "CSV"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Roda a grade pré-registrada e grava CSV e Markdown.",
    )
    parser.add_argument("--start", required=True, help="AAAA-MM-DD")
    parser.add_argument("--end", required=True, help="AAAA-MM-DD")
    parser.add_argument("--symbols", required=True, help="Lista separada por vírgula.")
    parser.add_argument("--sources", default="csv", help="csv, yahoo e/ou b3, separados por vírgula.")
    parser.add_argument(
        "--strategies",
        default="gap_reversal,opening_range_breakout",
        help="Ids separados por vírgula. O padrão é gap e ORB.",
    )
    parser.add_argument(
        "--timeframe",
        default=None,
        help="1min, 5min ou 60min. Sem este argumento, csv e b3 usam 1min e o Yahoo usa 5min.",
    )
    parser.add_argument(
        "--n-trials",
        type=int,
        default=None,
        help=(
            "N do Sharpe deflacionado. O padrão é o número de linhas da grade. "
            "Um valor maior é o N acumulado do projeto; um menor não reduz N."
        ),
    )
    parser.add_argument("--capital", type=float, default=10_000)
    parser.add_argument("--tax", type=float, default=0.20, help="Alíquota, fração. Padrão 0.20.")
    parser.add_argument("--csv", dest="csv_path", default=None, help="CSV de barras. O padrão é o exemplo do repositório.")
    parser.add_argument("--out", required=True, help="Caminho sem extensão, ou terminando em .csv. Grava .csv e .md.")
    parser.add_argument("--include-after-hours", action="store_true")
    args = parser.parse_args(argv)

    start = _date(args.start)
    end = _date(args.end)
    symbols = _items(args.symbols)
    sources = _items(args.sources)
    strategies = _items(args.strategies)
    for strategy_id in strategies:
        variants_for(strategy_id)
    planned = len(symbols) * len(sources) * sum(len(variants_for(item)) for item in strategies)
    if planned < 1:
        print("A grade ficou vazia.", file=sys.stderr)
        return 2
    requested = planned if args.n_trials is None else args.n_trials
    trials = trial_count(requested, planned)

    rows: list[dict] = []
    failures: list[str] = []
    cache: dict[tuple, object] = {}
    for source in sources:
        timeframe = args.timeframe or ("5min" if source == "yahoo" else "1min")
        for symbol in symbols:
            try:
                loaded = cache.get((source, symbol, timeframe))
                if loaded is None:
                    loaded = _load(source, symbol, start, end, timeframe, args.csv_path, args.include_after_hours)
                    cache[(source, symbol, timeframe)] = loaded
            except Exception as exc:
                failures.append(f"{source} {symbol} {timeframe}: {exc}")
                continue
            instrument = resolve_instrument(symbol)
            costs = CostModel(
                fee_per_side=instrument.default_fee_per_side,
                slippage_ticks=instrument.default_slippage_ticks,
                fee_rate=instrument.default_fee_rate,
            )
            for strategy_id in strategies:
                strategy = get_strategy(strategy_id)
                _, results = run_study(
                    loaded.bars,
                    strategy,
                    variants_for(strategy_id),
                    instrument,
                    costs,
                    symbol=instrument.symbol,
                    initial_capital=args.capital,
                    tax_rate=args.tax,
                    n_trials=trials,
                    bar_minutes={"1min": 1, "5min": 5, "60min": 60}[timeframe],
                )
                for params, result in results:
                    signal_skips = sum(1 for item in result.skipped if item.window == "signal")
                    trade_skips = sum(1 for item in result.skipped if item.window == "trade")
                    rows.append(
                        {
                            "strategy": strategy_id,
                            "symbol": instrument.symbol,
                            "source": source,
                            "timeframe": timeframe,
                            "variant": _variant_label(strategy_id, params),
                            "trades": result.metrics.n_trades,
                            "net_after_tax": result.metrics.net_pnl_after_tax,
                            "sharpe": result.metrics.sharpe,
                            "deflated_sharpe": result.metrics.deflated_sharpe,
                            "n_trials": result.metrics.n_trials,
                            "skipped_signal": signal_skips,
                            "skipped_trade": trade_skips,
                            "sessions": result.metrics.n_sessions,
                        }
                    )

    stem = _stem(args.out)
    csv_path = stem.with_suffix(".csv")
    md_path = stem.with_suffix(".md")
    _write_csv(csv_path, rows)
    md_path.write_text(
        _markdown(rows, failures, trials, planned, start, end),
        encoding="utf-8",
    )
    print(md_path.read_text(encoding="utf-8"))
    print(f"CSV: {csv_path}")
    print(f"Markdown: {md_path}")
    return 0


def _load(source: str, symbol: str, start: date, end: date, timeframe: str, csv_path: str | None, include_after_hours: bool):
    path = None
    if source == "csv":
        path = Path(csv_path) if csv_path else sample_csv_path()
    request = DataRequest(
        symbol=symbol,
        start=start,
        end=end,
        timeframe=timeframe,
        csv_path=path,
        include_after_hours=include_after_hours,
    )
    if source == "b3":
        return B3TradesAdapter().load(request)
    if source == "yahoo":
        return YahooFinanceAdapter().load(request)
    if source == "csv":
        return CsvBarsAdapter().load(request)
    raise ValueError(f"Fonte desconhecida: {source}. Use b3, yahoo ou csv.")


def _variant_label(strategy_id: str, params: dict) -> str:
    if strategy_id == "gap_reversal":
        exit_mode = str(params.get("exit", "eod"))
        return "fim do dia" if exit_mode == "eod" else f"{exit_mode} min"
    if strategy_id == "opening_range_breakout":
        return f"{params.get('range_minutes')} min"
    anchor = "fechamento anterior" if params.get("signal_anchor") == "prior_close" else "abertura"
    end = "à vista" if params.get("signal_end") == "cash_open" else "ativo"
    window = "antes do leilão" if params.get("trade_window") == "before_cash_auction" else "até o fechamento"
    return f"{anchor} · fim na abertura do {end} · {window}"


def _markdown(rows: list[dict], failures: list[str], trials: int, planned: int, start: date, end: date) -> str:
    lines = [
        f"# Estudo {start.isoformat()} a {end.isoformat()}",
        "",
        f"N do Sharpe deflacionado: **{trials}**. Linhas pedidas na grade: {planned}. "
        f"Catálogo pré-registrado do projeto: {CATALOG_TRIALS}.",
        "",
        "| Estratégia | Ativo | Fonte | Variante | Operações | Líquido de IR | Sharpe | Sharpe deflacionado | N | Sinal pulado | Operação pulada | Pregões |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| {strategy} | {symbol} | {source} | {variant} | {trades} | {net} | {sharpe} | {dsr} | {n} | {signal} | {trade} | {sessions} |".format(
                strategy=row["strategy"],
                symbol=row["symbol"],
                source=SOURCE_LABELS.get(row["source"], row["source"]),
                variant=row["variant"],
                trades=row["trades"],
                net=_brl(row["net_after_tax"]),
                sharpe=_ratio(row["sharpe"]),
                dsr=_pct(row["deflated_sharpe"]),
                n=row["n_trials"],
                signal=row["skipped_signal"],
                trade=row["skipped_trade"],
                sessions=row["sessions"],
            )
        )
    if not rows:
        lines.append("| — | — | — | — | — | — | — | — | — | — | — | — |")
    if failures:
        lines.extend(["", "## Carga que não rodou", ""])
        lines.extend(f"- {item}" for item in failures)
    lines.append("")
    return "\n".join(lines)


def _write_csv(path: Path, rows: list[dict]) -> None:
    fields = [
        "strategy",
        "symbol",
        "source",
        "timeframe",
        "variant",
        "trades",
        "net_after_tax",
        "sharpe",
        "deflated_sharpe",
        "n_trials",
        "skipped_signal",
        "skipped_trade",
        "sessions",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _items(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _stem(value: str) -> Path:
    path = Path(value)
    if path.suffix.lower() == ".csv":
        return path.with_suffix("")
    return path


def _brl(value: float | None) -> str:
    if value is None:
        return "—"
    text = f"{value:,.2f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def _ratio(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.3f}".replace(".", ",")


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.2f}%".replace(".", ",")


if __name__ == "__main__":
    raise SystemExit(main())
