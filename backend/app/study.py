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
from engine.metrics import daily_means, student_t
from engine.strategies.preregistry import CATALOG_TRIALS, is_core, variants_for
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
    core_planned, extra_planned = _split_counts(strategies, symbols, sources)
    planned = core_planned + extra_planned
    if planned < 1:
        print("A grade ficou vazia.", file=sys.stderr)
        return 2
    requested = planned if args.n_trials is None else args.n_trials
    trials = trial_count(requested, planned)

    rows: list[dict] = []
    failures: list[str] = []
    fill_notes: list[str] = []
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
                    trades=getattr(loaded, "trades", None),
                )
                for params, result in results:
                    for warning in result.warnings:
                        if warning.startswith("Preenchimento do ORB") and warning not in fill_notes:
                            fill_notes.append(warning)
                    signal_skips = sum(1 for item in result.skipped if item.window == "signal")
                    trade_skips = sum(1 for item in result.skipped if item.window == "trade")
                    stats = _trade_stats(result, instrument.point_value)
                    rows.append(
                        {
                            "block": "núcleo" if is_core(strategy_id, params) else "extra",
                            "strategy": strategy_id,
                            "symbol": instrument.symbol,
                            "source": source,
                            "timeframe": timeframe,
                            "variant": _variant_label(strategy_id, params),
                            "trades": result.metrics.n_trades,
                            "net_pnl": stats["net_pnl"],
                            "mean_net_return": stats["mean_net_return"],
                            "win_rate": result.metrics.win_rate,
                            "t_stat_trade": stats["t_stat_trade"],
                            "t_stat_day": stats["t_stat_day"],
                            "net_after_tax": result.metrics.net_pnl_after_tax,
                            "sharpe": result.metrics.sharpe,
                            "deflated_sharpe": result.metrics.deflated_sharpe,
                            "n_trials": result.metrics.n_trials,
                            "skipped_signal": signal_skips,
                            "skipped_trade": trade_skips,
                            "sessions": result.metrics.n_sessions,
                            "observations": stats["observations"],
                            "wins": stats["wins"],
                        }
                    )

    stem = _stem(args.out)
    csv_path = stem.with_suffix(".csv")
    md_path = stem.with_suffix(".md")
    _write_csv(csv_path, rows)
    md_path.write_text(
        _markdown(rows, failures, trials, core_planned, extra_planned, start, end, fill_notes),
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


def _split_counts(strategies: list[str], symbols: list[str], sources: list[str]) -> tuple[int, int]:
    core = 0
    extra = 0
    for strategy_id in strategies:
        for params in variants_for(strategy_id):
            if is_core(strategy_id, params):
                core += 1
            else:
                extra += 1
    factor = len(symbols) * len(sources)
    return core * factor, extra * factor


def _trade_stats(result, point_value: float) -> dict:
    observations: list[tuple[date, float]] = []
    wins = 0
    net = 0.0
    for trade in result.trades:
        net += float(trade.pnl)
        if trade.pnl > 0:
            wins += 1
        notional = float(trade.entry_price) * point_value * trade.quantity
        if notional == 0:
            continue
        observations.append((trade.session_date, float(trade.pnl) / notional))
    returns = [value for _, value in observations]
    mean = float(sum(returns) / len(returns)) if returns else None
    return {
        "net_pnl": net,
        "mean_net_return": mean,
        "t_stat_trade": student_t(returns),
        "t_stat_day": student_t(daily_means(observations)),
        "observations": observations,
        "wins": wins,
    }


def _variant_label(strategy_id: str, params: dict) -> str:
    if strategy_id == "gap_reversal":
        exit_mode = str(params.get("exit", "15"))
        exit_name = "fim do dia" if exit_mode == "eod" else f"{exit_mode} min"
        return f"{_threshold_label(params)} · {exit_name}"
    if strategy_id == "opening_range_breakout":
        minutes = params.get("range_minutes")
        if str(params.get("execution", "stop")) == "confirm":
            return f"orb_confirm · {minutes} min"
        return f"{minutes} min"
    anchor = "fechamento anterior" if params.get("signal_anchor") == "prior_close" else "abertura"
    end = "à vista" if params.get("signal_end") == "cash_open" else "ativo"
    window = "antes do leilão" if params.get("trade_window") == "before_cash_auction" else "até o fechamento"
    return f"{anchor} · fim na abertura do {end} · {window}"


def _markdown(
    rows: list[dict],
    failures: list[str],
    trials: int,
    core_planned: int,
    extra_planned: int,
    start: date,
    end: date,
    fill_notes: list[str] | None = None,
) -> str:
    lines = [
        f"# Estudo {start.isoformat()} a {end.isoformat()}",
        "",
        f"N do núcleo: **{core_planned}**. N dos extras: **{extra_planned}**. "
        f"N do Sharpe deflacionado: **{trials}** (núcleo + extras, salvo um `--n-trials` maior). "
        f"Catálogo do projeto: {CATALOG_TRIALS}.",
        "",
        "A média líquida, a taxa de acerto e os t-stats são depois dos custos e antes do IR. "
        "O t-stat diário usa a média dos trades de cada pregão.",
        "",
    ]
    for note in fill_notes or []:
        lines.append(note)
        lines.append("")
    lines.extend(_section("Núcleo", [row for row in rows if row["block"] == "núcleo"]))
    lines.extend(_section("Extras", [row for row in rows if row["block"] == "extra"]))
    if failures:
        lines.extend(["## Carga que não rodou", ""])
        lines.extend(f"- {item}" for item in failures)
        lines.append("")
    return "\n".join(lines)


def _section(title: str, rows: list[dict]) -> list[str]:
    lines = [
        f"## {title}",
        "",
        "| Estratégia | Ativo | Variante | Operações | Média líquida | Acerto | t por trade | t diário | Líquido de custos | Líquido de IR | Sharpe | Sharpe deflacionado | Sinal pulado | Operação pulada | Pregões |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    if not rows:
        lines.append("| — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |")
    for row in rows:
        lines.append(_row_line(row))
    lines.extend(["", f"### {title} agregado", ""])
    lines.append("| Variante | Operações | Média líquida | Acerto | t por trade | t diário | Líquido de custos |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    pooled = _pooled(rows)
    if not pooled:
        lines.append("| — | — | — | — | — | — | — |")
    for item in pooled:
        lines.append(
            "| {variant} | {trades} | {mean} | {win} | {t_trade} | {t_day} | {net} |".format(
                variant=item["variant"],
                trades=item["trades"],
                mean=_pct(item["mean_net_return"]),
                win=_pct(item["win_rate"]),
                t_trade=_ratio(item["t_stat_trade"]),
                t_day=_ratio(item["t_stat_day"]),
                net=_brl(item["net_pnl"]),
            )
        )
    lines.append("")
    return lines


def _row_line(row: dict) -> str:
    return (
        "| {strategy} | {symbol} | {variant} | {trades} | {mean} | {win} | {t_trade} | {t_day} | {net} | {tax} | {sharpe} | {dsr} | {signal} | {trade} | {sessions} |".format(
            strategy=row["strategy"],
            symbol=row["symbol"],
            variant=row["variant"],
            trades=row["trades"],
            mean=_pct(row["mean_net_return"]),
            win=_pct(row["win_rate"]),
            t_trade=_ratio(row["t_stat_trade"]),
            t_day=_ratio(row["t_stat_day"]),
            net=_brl(row["net_pnl"]),
            tax=_brl(row["net_after_tax"]),
            sharpe=_ratio(row["sharpe"]),
            dsr=_pct(row["deflated_sharpe"]),
            signal=row["skipped_signal"],
            trade=row["skipped_trade"],
            sessions=row["sessions"],
        )
    )


def _pooled(rows: list[dict]) -> list[dict]:
    order: list[str] = []
    groups: dict[str, list[dict]] = {}
    for row in rows:
        label = f"{row['strategy']} · {row['variant']}"
        if label not in groups:
            order.append(label)
            groups[label] = []
        groups[label].append(row)
    pooled = []
    for label in order:
        group = groups[label]
        observations = [item for row in group for item in row["observations"]]
        returns = [value for _, value in observations]
        trades = sum(int(row["trades"]) for row in group)
        wins = sum(int(row["wins"]) for row in group)
        pooled.append(
            {
                "variant": label,
                "trades": trades,
                "mean_net_return": float(sum(returns) / len(returns)) if returns else None,
                "win_rate": (wins / trades) if trades else None,
                "t_stat_trade": student_t(returns),
                "t_stat_day": student_t(daily_means(observations)),
                "net_pnl": float(sum(row["net_pnl"] for row in group)),
            }
        )
    return pooled


def _write_csv(path: Path, rows: list[dict]) -> None:
    fields = [
        "strategy",
        "symbol",
        "source",
        "timeframe",
        "block",
        "variant",
        "trades",
        "mean_net_return",
        "win_rate",
        "t_stat_trade",
        "t_stat_day",
        "net_pnl",
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
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _threshold_label(params: dict) -> str:
    number = float(params.get("threshold", 0.005)) * 100
    return f"{number:.1f}%".replace(".", ",")


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
