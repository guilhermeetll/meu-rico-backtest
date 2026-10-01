from datetime import date, time

from engine.sessions import (
    front_win_contract,
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


def test_equity_session_is_a_fixed_window():
    assert session_bounds("EQUITY", date(2026, 9, 30)) == (time(10, 0), time(17, 0))
