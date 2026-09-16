from datetime import date, datetime

from src.nse_calendar import is_nse_fo_holiday, is_nse_fo_session


def test_ganesh_chaturthi_2026_is_an_fo_holiday():
    assert is_nse_fo_holiday(date(2026, 9, 14))
    assert not is_nse_fo_session(date(2026, 9, 14))


def test_ordinary_tuesday_is_a_session():
    assert is_nse_fo_session(date(2026, 9, 16))
    assert not is_nse_fo_holiday(date(2026, 9, 16))


def test_weekend_is_not_a_session_even_if_not_listed():
    assert not is_nse_fo_session(date(2026, 9, 13))  # Sunday
    assert not is_nse_fo_holiday(date(2026, 9, 13))


def test_datetime_is_accepted():
    assert is_nse_fo_holiday(datetime(2026, 10, 2, 10, 0))
