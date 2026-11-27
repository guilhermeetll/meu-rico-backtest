#!/usr/bin/env python3
"""Substitui o cron (indisponível no box): run de hora em hora 8h-23h seg-sex; entry-reminder 09:00; run extra 16:25; exit-reminder 16:00/16:55/17:00/17:55 (Cinzas) seg-sex. Horário America/Sao_Paulo.
Robusto a minutos perdidos: cada slot do dia dispara uma vez assim que o horário passar (com tolerância de 30 min para os lembretes de saída)."""
import datetime
import os
import subprocess
import time
import zoneinfo

TZ = zoneinfo.ZoneInfo("America/Sao_Paulo")
BASE = os.environ.get("ALERTAS_BASE_DIR", "/workspace/alertas-dividendo")
RUN = os.path.join(BASE, "run.sh")
LOG = os.path.join(BASE, "scheduler.log")
EXIT_SLOTS = [(16, 0), (16, 55), (17, 0), (17, 55)]
EXTRA_SLOTS = [("entry-reminder", 9, 0), ("run", 16, 25)]  # lembrete de entrada na abertura; checagem extra antes do corte das 16h30
HOURLY_FROM = 8
HOURLY_TO = 23
SLOT_TOLERANCE = datetime.timedelta(minutes=30)


def prune_done(done, today):
    return {k for k in done if k[0] == today}


def slots_due(now, done):
    """Slots que disparariam agora, na mesma ordem do laço. Não altera `done`."""
    if now.weekday() >= 5:
        return []
    today = now.date()
    seen = set(done)
    due = []

    def take(kind, key, open_now):
        if key in seen or not open_now:
            return
        seen.add(key)
        due.append((kind, key))

    if HOURLY_FROM <= now.hour <= HOURLY_TO:
        take("run", (today, "run", now.hour), True)
    for hour, minute in EXIT_SLOTS:
        slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        take(
            "exit-reminder",
            (today, "exit", hour, minute),
            slot <= now < slot + SLOT_TOLERANCE,
        )
    for kind, hour, minute in EXTRA_SLOTS:
        slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        take(
            kind,
            (today, kind, hour, minute),
            slot <= now < slot + SLOT_TOLERANCE,
        )
    return due


def main():
    done = set()

    def log(msg):
        with open(LOG, "a") as handle:
            handle.write(f"{datetime.datetime.now(TZ):%Y-%m-%d %H:%M:%S} {msg}\n")

    def launch(kind, key):
        done.add(key)
        process = subprocess.Popen([RUN, kind])
        log(f"lançado {kind} slot={key[1:]} pid={process.pid}")

    log("scheduler iniciado")
    while True:
        now = datetime.datetime.now(TZ)
        done = prune_done(done, now.date())
        for kind, key in slots_due(now, done):
            launch(kind, key)
        time.sleep(20)


if __name__ == "__main__":
    main()
