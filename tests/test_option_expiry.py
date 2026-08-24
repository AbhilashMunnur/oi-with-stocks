from datetime import date

from src.data.option_expiry import last_tuesday, select_current_month_oi_expiry


def test_august_2026_nse_monthly_is_last_tuesday():
    assert last_tuesday(2026, 8) == date(2026, 8, 25)
    assert last_tuesday(2026, 9) == date(2026, 9, 29)


def test_current_month_oi_picks_last_tuesday_not_bse_thursday():
    chosen = select_current_month_oi_expiry(
        [
            date(2026, 8, 18),
            date(2026, 8, 25),
            date(2026, 8, 27),
            date(2026, 9, 29),
        ],
        today=date(2026, 8, 21),
    )
    assert chosen == date(2026, 8, 25)


def test_monday_before_stock_expiry_still_reads_this_month_tuesday():
    chosen = select_current_month_oi_expiry(
        [date(2026, 8, 25), date(2026, 8, 27), date(2026, 9, 29)],
        today=date(2026, 8, 24),
    )
    assert chosen == date(2026, 8, 25)


def test_after_monthly_expiry_rolls_to_next_month_tuesday():
    chosen = select_current_month_oi_expiry(
        [date(2026, 8, 27), date(2026, 9, 29), date(2026, 10, 27)],
        today=date(2026, 8, 26),
    )
    assert chosen == date(2026, 9, 29)


def test_nifty_and_stock_expiry_tuesday_reads_next_month_stock_oi():
    from src.data.option_expiry import select_scan_oi_expiry

    expiries = [
        date(2026, 8, 18),
        date(2026, 8, 25),
        date(2026, 8, 27),
        date(2026, 9, 29),
        date(2026, 10, 27),
    ]
    # 25 Aug 2026 is last Tuesday — NSE stock + Nifty monthly expiry.
    assert select_scan_oi_expiry(expiries, today=date(2026, 8, 25)) == date(2026, 9, 29)
    # Monday before expiry still uses August NSE monthly (25th), not BSE 27th.
    assert select_scan_oi_expiry(expiries, today=date(2026, 8, 24)) == date(2026, 8, 25)
    assert select_scan_oi_expiry(expiries, today=date(2026, 8, 21)) == date(2026, 8, 25)
