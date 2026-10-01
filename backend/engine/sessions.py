from __future__ import annotations

import calendar
from datetime import date, time, timedelta

# Futures month codes used by B3.
MONTH_CODES = {
    "F": 1,
    "G": 2,
    "H": 3,
    "J": 4,
    "K": 5,
    "M": 6,
    "N": 7,
    "Q": 8,
    "U": 9,
    "V": 10,
    "X": 11,
    "Z": 12,
}
EVEN_MONTH_CODE = {month: code for code, month in MONTH_CODES.items() if month % 2 == 0}

# From 11 Mar 2024 the WIN regular session ends at 18:25 all year
# (B3, 23 Jan 2024 announcement and Circular Letter 013/2024-PRE).
WIN_FULL_SESSION_FROM = date(2024, 3, 11)
# From 4 Nov 2024 the expiring WIN contract stops at 18:00
# (Circular Letter 132/2024-PRE).
WIN_EXPIRY_1800_FROM = date(2024, 11, 4)


def wednesday_closest_to_15(year: int, month: int) -> date:
    """Wednesday nearest the 15th. Ties cannot happen on an integer calendar."""
    target = date(year, month, 15)
    delta = (calendar.WEDNESDAY - target.weekday()) % 7
    if delta > 3:
        delta -= 7
    return target + timedelta(days=delta)


def _nth_sunday(year: int, month: int, n: int) -> date:
    first = date(year, month, 1)
    shift = (calendar.SUNDAY - first.weekday()) % 7
    return first + timedelta(days=shift + 7 * (n - 1))


def us_dst_bounds(year: int) -> tuple[date, date]:
    """US daylight saving: second Sunday of March through the Sunday it ends.

    The end date itself is the first Sunday of November and is not included.
    B3 used to switch schedules on the following Monday, which is the next
    trading day, so comparing trading dates to this interval is enough.
    """
    start = _nth_sunday(year, 3, 2)
    end = _nth_sunday(year, 11, 1)
    return start, end


def is_us_dst(day: date) -> bool:
    start, end = us_dst_bounds(day.year)
    return start <= day < end


def parse_win_contract(symbol: str) -> tuple[int, int] | None:
    """Return (year, month) for a concrete WIN contract such as WINV26."""
    text = symbol.upper().strip()
    if not text.startswith("WIN") or len(text) != 6:
        return None
    code = text[3]
    if code not in MONTH_CODES or not text[4:].isdigit():
        return None
    year = 2000 + int(text[4:])
    return year, MONTH_CODES[code]


def front_win_contract(day: date) -> str:
    """Front-month WIN ticker on `day`.

    The contract expires on the Wednesday closest to the 15th of an even
    month and still trades on that session. The next even month becomes
    the front contract on the following day.
    """
    year, month = day.year, day.month
    if month % 2 == 1:
        month += 1
    if month > 12:
        month = 2
        year += 1
    expiry = wednesday_closest_to_15(year, month)
    if day > expiry:
        month += 2
        if month > 12:
            month -= 12
            year += 1
    return f"WIN{EVEN_MONTH_CODE[month]}{year % 100:02d}"


def is_win_expiration(day: date, contract: str | None) -> bool:
    parsed = parse_win_contract(contract or "")
    if parsed is None:
        return False
    year, month = parsed
    return day == wednesday_closest_to_15(year, month)


# Cash grades below. The closing call is the last five minutes of the
# regular session; continuous trading stops when the call starts.
CASH_CALL_MINUTES = 5
CASH_PRE_AUCTION_MINUTES = 30
# Monday the cash open moved from 11:00 to 10:00 (Exame, 12 Mar 2012).
CASH_OPEN_1000_FROM = date(2012, 3, 12)
# Monday the annual 18:00 extension started (BM&FBOVESPA, 21 Dec 2015).
CASH_EXTENDED_CLOSE_FROM = date(2015, 12, 21)
# From this Monday the auction window follows the regular hourly bar
# (close 17:00) even in winters whose circular publishes a call until 18:00.
CASH_REGULAR_1700_FROM = date(2023, 10, 1)
# Ordinary sessions inside these half-open intervals have a circular (or a
# B3 notice) that prints a 17:55–18:00 cash call. The bars do not. The
# strategy keeps 17:00 and calendar_warnings says so.
WINTER_CALL_1800_NOT_USED = (
    (date(2023, 11, 6), date(2024, 3, 11)),
    (date(2024, 11, 4), date(2025, 3, 10)),
    (date(2025, 11, 3), date(2026, 3, 9)),
)
# Ash Wednesday circulars that spell out 13:00–17:55 and a 17:55–18:00 call.
ASH_CASH_SOURCED = {
    date(2024, 2, 14),
    date(2025, 3, 5),
    date(2026, 2, 18),
}


def easter_sunday(year: int) -> date:
    """Gregorian Easter, Anonymous algorithm. Used only for the Carnival exception."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    g = (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    month, day = divmod(h + ell - 7 * ((a + 11 * h + 22 * ell) // 451) + 114, 31)
    return date(year, month, day + 1)


def brazilian_dst_bounds(end_year: int) -> tuple[date, date] | None:
    """Daylight-saving season that ends in February of `end_year`.

    Decreto 6.558/2008: from 00:00 on the third Sunday of October to 00:00
    on the third Sunday of February, moved to the next Sunday when that day
    is Carnival. Decreto 9.242/2017 moved the 2018/2019 start to the first
    Sunday of November. Decreto 9.772/2019 abolished the measure; the last
    season ended on 17 Feb 2019.
    """
    if end_year < 2012 or end_year > 2019:
        return None
    if end_year == 2019:
        start = _nth_sunday(2018, 11, 1)
    else:
        start = _nth_sunday(end_year - 1, 10, 3)
    end = _nth_sunday(end_year, 2, 3)
    carnival = easter_sunday(end_year) - timedelta(days=49)
    if end == carnival:
        end += timedelta(days=7)
    return start, end


def is_brazilian_dst(day: date) -> bool:
    for end_year in (day.year, day.year + 1):
        bounds = brazilian_dst_bounds(end_year)
        if bounds is not None and bounds[0] <= day < bounds[1]:
            return True
    return False


def _plus(clock: time, minutes: int) -> time:
    total = clock.hour * 60 + clock.minute + minutes
    if not 0 <= total < 24 * 60:
        raise ValueError("O horário do à vista saiu do dia civil.")
    return time(total // 60, total % 60)


def ash_wednesday(year: int) -> date:
    """Ash Wednesday, 46 days before Easter Sunday."""
    return easter_sunday(year) - timedelta(days=46)


def is_ash_wednesday(day: date) -> bool:
    return day == ash_wednesday(day.year)


def cash_open_close(day: date) -> tuple[time, time]:
    """Cash open and the end of the closing call on an ordinary session.

    Ash Wednesday is applied in `cash_session`. From October 2023 the
    ordinary close stays at 17:00 all year: winter circulars that print an
    18:00 call are not used as the auction anchor. See the README.
    """
    if is_ash_wednesday(day) and day.year >= 2012:
        return time(13, 0), time(18, 0)
    if day < CASH_OPEN_1000_FROM:
        return time(11, 0), time(18, 0)
    if day < CASH_EXTENDED_CLOSE_FROM:
        return time(10, 0), time(17, 0)
    if day >= CASH_REGULAR_1700_FROM:
        return time(10, 0), time(17, 0)
    # 17:00 only while New York is on daylight saving time and Brazil is not.
    # Brazilian DST no longer shifts the open to 11:00. The Nov–Feb stretch,
    # when 16:00 in New York is 19:00 in Brasília, stays at an 18:00 close.
    if is_us_dst(day) and not is_brazilian_dst(day):
        return time(10, 0), time(17, 0)
    return time(10, 0), time(18, 0)


def cash_session(day: date) -> tuple[time, time, time]:
    """Cash open, start of the closing call, and official close."""
    open_t, close_t = cash_open_close(day)
    return open_t, _plus(close_t, -CASH_CALL_MINUTES), close_t


def cash_auction_window(day: date) -> tuple[time, time]:
    """Continuous window that ends when the cash closing call starts."""
    _, call_start, _ = cash_session(day)
    return _plus(call_start, -CASH_PRE_AUCTION_MINUTES), call_start


def session_bounds(
    family: str,
    day: date,
    contract: str | None = None,
) -> tuple[time, time]:
    """Regular session open and close for the instrument family.

    WIN follows the B3 clock, including the 2024 change that kept the 18:25
    close all year and the earlier close of the expiring contract.
    Equities follow the cash calendar (open and the end of the closing call).
    Both can be overridden by the strategy parameters.
    """
    if family != "WIN":
        open_t, _, close_t = cash_session(day)
        return open_t, close_t

    # Derivatives circulars move only the open on Ash Wednesday. The close
    # of the regular session, including the expiring contract, stays put.
    open_t = time(13, 0) if day.year >= 2012 and is_ash_wednesday(day) else time(9, 0)
    if is_win_expiration(day, contract):
        close = time(18, 0) if day >= WIN_EXPIRY_1800_FROM else time(17, 0)
        return open_t, close

    if day >= WIN_FULL_SESSION_FROM:
        return open_t, time(18, 25)
    if is_us_dst(day):
        return open_t, time(17, 55)
    return open_t, time(18, 25)


def _overlaps(start: date, end: date, left: date, right: date) -> bool:
    """True when inclusive [start, end] meets half-open [left, right)."""
    return start < right and end >= left


def _ordinary_winter_in_range(start: date, end: date) -> bool:
    for left, right in WINTER_CALL_1800_NOT_USED:
        day = max(start, left)
        last = min(end, right - timedelta(days=1))
        while day <= last:
            if day.weekday() < 5 and not is_ash_wednesday(day):
                return True
            day += timedelta(days=1)
    return False


def calendar_warnings(start: date, end: date) -> list[str]:
    """Premises that are not an official circular covering `start`..`end`.

    Empty when every session in the range follows a retrieved notice.
    September 2026 is in that case.
    """
    if end < start:
        return []
    notes: list[str] = []
    if _overlaps(start, end, date(2012, 1, 1), CASH_OPEN_1000_FROM):
        notes.append(
            "Premissa: até 09/03/2012 o à vista fica em 11:00–18:00. A fonte é imprensa "
            "(Estado de Minas, 13/10/2011, e Exame, 12/03/2012), não um ofício circular recuperado."
        )
    if _overlaps(start, end, CASH_OPEN_1000_FROM, CASH_EXTENDED_CLOSE_FROM):
        notes.append(
            "Premissa: de 12/03/2012 a 18/12/2015 o à vista fica em 10:00–17:00 o ano inteiro. "
            "Não foi recuperado ofício com fechamento às 18:00 nesse intervalo."
        )
    if _overlaps(start, end, CASH_EXTENDED_CLOSE_FROM, CASH_REGULAR_1700_FROM):
        notes.append(
            "Premissa: de 21/12/2015 a 29/09/2023 o à vista fecha às 17:00 no horário de verão "
            "dos EUA e às 18:00 fora dele, sem sessão das 19:00. Nem toda troca desse intervalo "
            "tem o PDF do ofício neste calendário."
        )
    if _ordinary_winter_in_range(start, end):
        notes.append(
            "Premissa: nos pregões ordinários de 06/11/2023 a 08/03/2024, de 04/11/2024 a 07/03/2025 "
            "e de 03/11/2025 a 06/03/2026 o fechamento regular do à vista fica às 17:00 e a janela "
            "é 16:25–16:55. Os ofícios desses invernos publicam call até as 18:00, mas a última "
            "barra horária regular do Ibovespa e da PETR4 é a das 16:00. A janela não vai para "
            "17:25–17:55, que é after-market na grade que essas barras mostram."
        )
    if start < WIN_FULL_SESSION_FROM and end >= date(2012, 1, 1):
        notes.append(
            "Premissa: antes de 11/03/2024 o WIN fecha às 17:55 no horário de verão dos EUA e às "
            "18:25 fora dele. A partir dessa segunda o fechamento de 18:25 o ano inteiro está no "
            "Ofício Circular 013/2024-PRE; as trocas anteriores não têm um PDF por ano."
        )
    for year in range(max(start.year, 2012), end.year + 1):
        day = ash_wednesday(year)
        if start <= day <= end and day not in ASH_CASH_SOURCED:
            notes.append(
                "Premissa: na Quarta-feira de Cinzas de 2012 a 2023 a abertura do à vista e do WIN "
                "fica às 13:00 e o call do à vista às 17:55–18:00, no mesmo desenho dos ofícios de "
                "2024, 2025 e 2026. O PDF desses anos anteriores não foi recuperado."
            )
            break
    return notes
