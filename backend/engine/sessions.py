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


def session_bounds(
    family: str,
    day: date,
    contract: str | None = None,
) -> tuple[time, time]:
    """Regular session open and close for the instrument family.

    WIN follows the B3 clock, including the 2024 change that kept the 18:25
    close all year and the earlier close of the expiring contract.
    Equities use a fixed 10:00–17:00 window. Both can be overridden by the
    strategy parameters.
    """
    if family != "WIN":
        return time(10, 0), time(17, 0)

    if is_win_expiration(day, contract):
        close = time(18, 0) if day >= WIN_EXPIRY_1800_FROM else time(17, 0)
        return time(9, 0), close

    if day >= WIN_FULL_SESSION_FROM:
        return time(9, 0), time(18, 25)
    if is_us_dst(day):
        return time(9, 0), time(17, 55)
    return time(9, 0), time(18, 25)
