from datetime import date

from src.data.option_expiry import last_thursday, select_current_month_oi_expiry


def test_august_2026_monthly_is_last_thursday():
    assert last_thursday(2026, 8) == date(2026, 8, 27)


def test_current_month_oi_skips_weeklies_for_the_monthly():
    chosen = select_current_month_oi_expiry(
        [
            date(2026, 8, 21),
            date(2026, 8, 25),
            date(2026, 8, 27),
            date(2026, 9, 24),
        ],
        today=date(2026, 8, 21),
    )
    assert chosen == date(2026, 8, 27)


def test_after_monthly_expiry_rolls_to_next_month():
    chosen = select_current_month_oi_expiry(
        [date(2026, 8, 27), date(2026, 9, 24), date(2026, 10, 29)],
        today=date(2026, 8, 28),
    )
    assert chosen == date(2026, 9, 24)
