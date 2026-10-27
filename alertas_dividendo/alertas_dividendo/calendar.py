"""Pregões de ações da B3.

Um pregão é um dia de semana que não está no calendário de fechamento da B3.
Feriado municipal de São Paulo (9 de julho) é pregão normal. Quarta-feira de
Cinzas é pregão, com abertura tarde.

Base: Ofício Circular 054/2025-VNC (calendário 2026) e o mesmo conjunto nos
outros anos — Carnaval (segunda e terça), Sexta-feira Santa, Corpus Christi,
1/1, 21/4, 1/5, 7/9, 12/10, 2/11, 15/11, 20/11 a partir de 2024 (Lei 14.759/2023),
24/12, 25/12 e 31/12 (sem sessão de negociação de ações). O horário especial da
Cinzas está confirmado para 18/02/2026 (abertura 13:00, call 17:55–18:00) e é
aplicado também às Cinzas de 2022 em diante, quando a B3 passou a abrir nesse dia.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Sao_Paulo")

# Horário do mercado à vista (lote padrão), página de horários da B3.
# O preço de fechamento oficial sai no call, que termina às 17:00.
DEFAULT_OPEN = time(10, 0)
DEFAULT_CALL_START = time(16, 55)
DEFAULT_CLOSE = time(17, 0)

# 18/02/2026 e demais Quartas de Cinzas desde 2022.
ASH_OPEN = time(13, 0)
ASH_CALL_START = time(17, 55)
ASH_CLOSE = time(18, 0)


@dataclass(frozen=True)
class SessionHours:
    open: time
    call_start: time
    close: time


REGULAR = SessionHours(DEFAULT_OPEN, DEFAULT_CALL_START, DEFAULT_CLOSE)
ASH = SessionHours(ASH_OPEN, ASH_CALL_START, ASH_CLOSE)


def easter(year: int) -> date:
    """Domingo de Páscoa (algoritmo gregoriano anônimo)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def ash_wednesday(year: int) -> date:
    return easter(year) - timedelta(days=46)


def b3_closures(year: int) -> set[date]:
    """Dias sem pregão de ações. Não inclui fim de semana."""
    pascoa = easter(year)
    days = {
        date(year, 1, 1),
        pascoa - timedelta(days=48),  # segunda de Carnaval
        pascoa - timedelta(days=47),  # terça de Carnaval
        pascoa - timedelta(days=2),  # Sexta-feira Santa
        date(year, 4, 21),
        date(year, 5, 1),
        pascoa + timedelta(days=60),  # Corpus Christi
        date(year, 9, 7),
        date(year, 10, 12),
        date(year, 11, 2),
        date(year, 11, 15),
        date(year, 12, 24),
        date(year, 12, 25),
        date(year, 12, 31),
    }
    if year >= 2024:
        days.add(date(year, 11, 20))
    return days


class Calendar:
    def __init__(self) -> None:
        self._closures: dict[int, set[date]] = {}

    def closures(self, year: int) -> set[date]:
        cached = self._closures.get(year)
        if cached is None:
            cached = b3_closures(year)
            self._closures[year] = cached
        return cached

    def is_session(self, day: date) -> bool:
        if day.weekday() >= 5:
            return False
        return day not in self.closures(day.year)

    def hours(self, day: date) -> SessionHours:
        if day >= date(2022, 1, 1) and day == ash_wednesday(day.year):
            return ASH
        return REGULAR

    def next_session(self, day: date) -> date:
        """Primeiro pregão estritamente depois de `day`."""
        cursor = day + timedelta(days=1)
        for _ in range(14):
            if self.is_session(cursor):
                return cursor
            cursor += timedelta(days=1)
        raise RuntimeError(f"sem pregão nas duas semanas após {day.isoformat()}")

    def previous_session(self, day: date) -> date:
        """Último pregão estritamente antes de `day`."""
        cursor = day - timedelta(days=1)
        for _ in range(14):
            if self.is_session(cursor):
                return cursor
            cursor -= timedelta(days=1)
        raise RuntimeError(f"sem pregão nas duas semanas antes de {day.isoformat()}")

    def add_sessions(self, day: date, n: int) -> date:
        """O n-ésimo pregão estritamente depois de `day`.

        `day` não entra na conta, mesmo que seja pregão. A saída da regra
        (20º pregão depois da data-com) é `add_sessions(data_com, 20)`.
        """
        if n < 1:
            raise ValueError("n tem de ser >= 1")
        cursor = day
        for _ in range(n):
            cursor = self.next_session(cursor)
        return cursor

    def sessions_between(self, start: date, end: date) -> list[date]:
        """Pregões com `start` <= dia <= `end`."""
        out: list[date] = []
        cursor = start
        while cursor <= end:
            if self.is_session(cursor):
                out.append(cursor)
            cursor += timedelta(days=1)
        return out

    def on_or_before_session(self, day: date) -> date:
        if self.is_session(day):
            return day
        return self.previous_session(day)

    def deadline(self, session: date) -> datetime:
        """Hora-limite para a ordem limitada entrar no call desse pregão."""
        hours = self.hours(session)
        return datetime.combine(session, hours.call_start, tzinfo=TZ)

    def close_at(self, session: date) -> datetime:
        hours = self.hours(session)
        return datetime.combine(session, hours.close, tzinfo=TZ)
