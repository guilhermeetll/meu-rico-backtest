from datetime import date, time

from engine.sessions import (
    cash_auction_window,
    cash_session,
    front_win_contract,
    is_brazilian_dst,
    session_bounds,
    wednesday_closest_to_15,
)


def test_expiration_is_the_wednesday_closest_to_the_15th():
    assert wednesday_closest_to_15(2024, 10) == date(2024, 10, 16)
    assert wednesday_closest_to_15(2024, 8) == date(2024, 8, 14)
    assert wednesday_closest_to_15(2026, 10) == date(2026, 10, 14)


def test_front_win_contract_rolls_after_expiration():
    assert front_win_contract(date(2026, 9, 30)) == "WINV26"
    assert front_win_contract(date(2026, 10, 14)) == "WINV26"
    assert front_win_contract(date(2026, 10, 15)) == "WINZ26"
    assert front_win_contract(date(2026, 8, 10)) == "WINQ26"
    assert front_win_contract(date(2026, 8, 13)) == "WINV26"
    assert front_win_contract(date(2026, 12, 17)) == "WING27"


def test_win_session_clock_follows_b3_schedule_changes():
    assert session_bounds("WIN", date(2023, 7, 14)) == (time(9, 0), time(17, 55))
    assert session_bounds("WIN", date(2024, 1, 15)) == (time(9, 0), time(18, 25))
    assert session_bounds("WIN", date(2024, 7, 15)) == (time(9, 0), time(18, 25))
    assert session_bounds("WIN", date(2026, 9, 30)) == (time(9, 0), time(18, 25))


def test_expiring_contract_closes_early():
    assert session_bounds("WIN", date(2024, 6, 12), contract="WINM24") == (time(9, 0), time(17, 0))
    assert session_bounds("WIN", date(2026, 10, 14), contract="WINV26") == (time(9, 0), time(18, 0))
    assert session_bounds("WIN", date(2026, 10, 13), contract="WINV26") == (time(9, 0), time(18, 25))


def test_brazilian_dst_follows_the_decrees_and_stops_in_2019():
    assert is_brazilian_dst(date(2012, 2, 25))
    assert not is_brazilian_dst(date(2012, 2, 26))
    assert not is_brazilian_dst(date(2012, 10, 20))
    assert is_brazilian_dst(date(2012, 10, 21))
    assert is_brazilian_dst(date(2015, 2, 21))
    assert not is_brazilian_dst(date(2015, 2, 22))
    assert not is_brazilian_dst(date(2018, 11, 3))
    assert is_brazilian_dst(date(2018, 11, 4))
    assert is_brazilian_dst(date(2019, 2, 16))
    assert not is_brazilian_dst(date(2019, 2, 17))
    assert not is_brazilian_dst(date(2019, 11, 4))
    assert not is_brazilian_dst(date(2026, 1, 15))


def test_cash_session_changes_on_both_sides_of_each_clock_shift():
    # 2011/12 grade held until the Monday the open moved to 10:00.
    assert cash_session(date(2012, 2, 15)) == (time(11, 0), time(17, 55), time(18, 0))
    assert cash_session(date(2012, 2, 27)) == (time(11, 0), time(17, 55), time(18, 0))
    assert cash_session(date(2012, 3, 9)) == (time(11, 0), time(17, 55), time(18, 0))
    assert cash_session(date(2012, 3, 12)) == (time(10, 0), time(16, 55), time(17, 0))
    # 2012 daylight saving did not shift the cash session back to 11:00–18:00.
    assert cash_session(date(2012, 10, 19)) == (time(10, 0), time(16, 55), time(17, 0))
    assert cash_session(date(2012, 10, 22)) == (time(10, 0), time(16, 55), time(17, 0))
    assert cash_session(date(2014, 1, 15)) == (time(10, 0), time(16, 55), time(17, 0))
    assert cash_session(date(2014, 7, 15)) == (time(10, 0), time(16, 55), time(17, 0))
    # Permanent extension starts 21 Dec 2015. Open stays at 10:00.
    assert cash_session(date(2015, 12, 18)) == (time(10, 0), time(16, 55), time(17, 0))
    assert cash_session(date(2015, 12, 21)) == (time(10, 0), time(17, 55), time(18, 0))
    assert cash_session(date(2016, 3, 11)) == (time(10, 0), time(17, 55), time(18, 0))
    assert cash_session(date(2016, 3, 14)) == (time(10, 0), time(16, 55), time(17, 0))
    # October 2016: close moves to 18:00 on the Brazilian-DST Monday, before US DST ends.
    assert cash_session(date(2016, 10, 14)) == (time(10, 0), time(16, 55), time(17, 0))
    assert cash_session(date(2016, 10, 17)) == (time(10, 0), time(17, 55), time(18, 0))
    assert cash_session(date(2017, 1, 16)) == (time(10, 0), time(17, 55), time(18, 0))
    # November 2018: both clocks change on the same weekend; open does not go to 11:00.
    assert cash_session(date(2018, 11, 2)) == (time(10, 0), time(16, 55), time(17, 0))
    assert cash_session(date(2018, 11, 5)) == (time(10, 0), time(17, 55), time(18, 0))
    # End of the last Brazilian DST does not, by itself, move the close.
    assert cash_session(date(2019, 2, 15)) == (time(10, 0), time(17, 55), time(18, 0))
    assert cash_session(date(2019, 2, 18)) == (time(10, 0), time(17, 55), time(18, 0))
    assert cash_session(date(2019, 3, 8)) == (time(10, 0), time(17, 55), time(18, 0))
    assert cash_session(date(2019, 3, 11)) == (time(10, 0), time(16, 55), time(17, 0))
    # 2020 follows the US clock. No shortened pandemic session.
    assert cash_session(date(2020, 3, 6)) == (time(10, 0), time(17, 55), time(18, 0))
    assert cash_session(date(2020, 3, 9)) == (time(10, 0), time(16, 55), time(17, 0))
    assert cash_session(date(2020, 4, 15)) == (time(10, 0), time(16, 55), time(17, 0))
    # 2 Nov 2020 was a holiday; the first winter session was 3 Nov. The grade
    # itself already changes on the Monday after the US clocks fall back.
    assert cash_session(date(2020, 10, 30)) == (time(10, 0), time(16, 55), time(17, 0))
    assert cash_session(date(2020, 11, 2)) == (time(10, 0), time(17, 55), time(18, 0))
    assert cash_session(date(2020, 11, 3)) == (time(10, 0), time(17, 55), time(18, 0))
    # Current grade, including both sides of the 2026 US switch.
    assert cash_session(date(2026, 1, 15)) == (time(10, 0), time(17, 55), time(18, 0))
    assert cash_session(date(2026, 3, 6)) == (time(10, 0), time(17, 55), time(18, 0))
    assert cash_session(date(2026, 3, 9)) == (time(10, 0), time(16, 55), time(17, 0))
    assert cash_session(date(2026, 9, 17)) == (time(10, 0), time(16, 55), time(17, 0))
    assert cash_session(date(2026, 9, 30)) == (time(10, 0), time(16, 55), time(17, 0))
    assert cash_session(date(2026, 11, 2)) == (time(10, 0), time(17, 55), time(18, 0))
    assert cash_auction_window(date(2026, 9, 30)) == (time(16, 25), time(16, 55))
    assert cash_auction_window(date(2026, 1, 15)) == (time(17, 25), time(17, 55))
    assert cash_auction_window(date(2012, 2, 15)) == (time(17, 25), time(17, 55))


def test_equity_session_uses_the_cash_calendar():
    assert session_bounds("EQUITY", date(2026, 9, 30)) == (time(10, 0), time(17, 0))
    assert session_bounds("EQUITY", date(2026, 1, 15)) == (time(10, 0), time(18, 0))
    assert session_bounds("EQUITY", date(2012, 2, 15)) == (time(11, 0), time(18, 0))
