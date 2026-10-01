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


def cash_open_close(day: date) -> tuple[time, time]:
    """Official cash open and the end of the closing call.

    The call itself is the last five minutes. See the README for the
    circulars and for the winters that this function treats as 10:00–17:00
    because no 18:00 extension was found before 21 Dec 2015.
    """
    if day < CASH_OPEN_1000_FROM:
        return time(11, 0), time(18, 0)
    if day < CASH_EXTENDED_CLOSE_FROM:
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

    if is_win_expiration(day, contract):
        close = time(18, 0) if day >= WIN_EXPIRY_1800_FROM else time(17, 0)
        return time(9, 0), close

    if day >= WIN_FULL_SESSION_FROM:
        return time(9, 0), time(18, 25)
    if is_us_dst(day):
        return time(9, 0), time(17, 55)
    return time(9, 0), time(18, 25)
