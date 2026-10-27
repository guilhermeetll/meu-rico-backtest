"""Linha de comando. Pensada para o cron, uma vez por hora."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import replace

from alertas_dividendo.config import load_config
from alertas_dividendo.engine import Report, execute
from alertas_dividendo.telegram_api import chat_ids_from_updates


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alertas_dividendo",
        description="Alertas de paper trading do swing de dividendo da B3.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(target: argparse.ArgumentParser, *, telegram: bool) -> None:
        target.add_argument("--data-dir", default=None, help="Pasta do SQLite e dos logs. Também ALERTAS_DATA_DIR.")
        target.add_argument("--dry-run", action="store_true", help="Imprime as mensagens e não chama o Telegram.")
        target.add_argument("--as-of", default=None, help="Data (YYYY-MM-DD) ou data-hora ISO. Padrão: agora em São Paulo.")
        target.add_argument("--lookback", type=int, default=None, help="Dias de CVM para trás. Padrão: ALERTAS_LOOKBACK_DAYS ou 12.")
        target.set_defaults(require_telegram=telegram)

    run = sub.add_parser("run", help="Varre a CVM e envia compras, saídas, lembretes e resultados que estão no prazo.")
    common(run, telegram=True)
    run.add_argument(
        "--hoje",
        action="store_true",
        help="Consulta ENET só do dia corrente (um pedido), como o coletor do Trader.",
    )
    reminder = sub.add_parser("exit-reminder", help="Lembrete de venda perto do fechamento, no dia da saída.")
    common(reminder, telegram=True)
    chat = sub.add_parser("chat-id", help="Lê o getUpdates e imprime o chat id. Fale com o bot antes.")
    status = sub.add_parser("status", help="Mostra posições abertas e os últimos eventos.")
    status.add_argument("--data-dir", default=None)
    return parser


def _logging(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(path, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


def render_report(report: Report) -> str:
    lines = [
        f"as_of={report.now.isoformat(timespec='minutes')}",
        f"dry_run={str(report.dry_run).lower()}",
    ]
    if report.errors:
        lines.append("erros:")
        lines.extend(f"  {item}" for item in report.errors)
    shown = report.ignored[:40]
    lines.append(f"ignorados ({len(report.ignored)}):")
    lines.extend(f"  {item}" for item in shown)
    if len(report.ignored) > len(shown):
        lines.append(f"  ... e mais {len(report.ignored) - len(shown)} (estão no log)")
    lines.append(f"mensagens ({len(report.messages)}):")
    for body in report.messages:
        lines.append("----- MENSAGEM -----")
        lines.append(body)
        lines.append("----- FIM -----")
    return "\n".join(lines)


def _status(data_dir: str | None) -> int:
    from pathlib import Path

    from alertas_dividendo.store import Store

    folder = Path(data_dir or os.environ.get("ALERTAS_DATA_DIR") or "alertas_data")
    store = Store(folder / "alertas.sqlite")
    try:
        positions = store.positions_active()
        print(f"posições abertas: {len(positions)}/10")
        for row in positions:
            price = row["entry_price"]
            print(
                f"  {row['ticker']} {row['status']} entrada={row['entry_date']} "
                f"preço={price if price is not None else '-'} saída={row['exit_date'] or '-'}"
            )
        print("últimos eventos:")
        for row in store.recent_events(20):
            print(f"  {row['ts']} {row['reason']} {row['ticker']} {row['detail'][:140]}")
    finally:
        store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "chat-id":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            print("Defina TELEGRAM_BOT_TOKEN.", file=sys.stderr)
            return 2
        try:
            chats = chat_ids_from_updates(token)
        except Exception as exc:
            print(f"getUpdates falhou: {exc}", file=sys.stderr)
            return 1
        if not chats:
            print("Nenhuma mensagem nova. Abra o bot no Telegram, mande um oi e rode de novo.")
            return 0
        for chat_id, label in chats:
            print(f"TELEGRAM_CHAT_ID={chat_id}  {label}")
        return 0
    if args.command == "status":
        return _status(args.data_dir)

    try:
        cfg = load_config(
            data_dir=args.data_dir,
            dry_run=args.dry_run,
            as_of=args.as_of,
            lookback=args.lookback,
            require_telegram=True,
        )
        if getattr(args, "hoje", False):
            cfg = replace(cfg, lookback_days=0)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2
    _logging(cfg.log_path)
    report = execute(cfg, args.command)
    print(render_report(report))
    return 1 if report.errors and not report.messages else 0
