"""KR/US holiday tables used to pick each market's next real trading day for order plans."""
import datetime as dt

import pytest

import funded_account as fa
import market_calendars as cal

D = dt.date.fromisoformat


@pytest.mark.parametrize("market, day, expected", [
    ("KR", "2026-09-23", "2026-09-28"),  # 추석 9/24-25 + weekend
    ("KR", "2026-10-02", "2026-10-06"),  # 개천절 대체휴일 10/5
    ("KR", "2026-12-30", "2027-01-04"),  # 연말 휴장일 12/31 + 신정
    ("US", "2026-11-25", "2026-11-27"),  # Thanksgiving; KR trades that day
    ("US", "2026-07-02", "2026-07-06"),  # Independence Day observed 7/3
])
def test_next_trading_day_skips_each_markets_own_holidays(market, day, expected):
    assert cal.next_trading_day(D(day), market) == D(expected)


def test_markets_do_not_share_holidays_and_uncovered_years_fail_closed():
    assert cal.is_trading_day(D("2026-11-26"), "KR") and not cal.is_trading_day(D("2026-11-26"), "US")
    assert cal.is_trading_day(D("2026-10-09"), "US") and not cal.is_trading_day(D("2026-10-09"), "KR")
    with pytest.raises(ValueError, match="does not cover"):
        cal.is_trading_day(D("2028-01-03"), "KR")
    assert cal.skipped_closures(D("2026-12-30"), D("2027-01-04"), "KR") == ["2026-12-31", "2027-01-01"]


def test_expected_session_uses_market_specific_cutoffs():
    evening_kst = dt.datetime(2026, 10, 20, 11, 30, tzinfo=dt.timezone.utc)  # 20:30 KST / 07:30 New York
    assert fa.expected_session("KR", evening_kst) == D("2026-10-20")
    assert fa.expected_session("US", evening_kst) == D("2026-10-19")  # never the unopened NY session
    before_close = dt.datetime(2026, 10, 20, 6, 0, tzinfo=dt.timezone.utc)  # 15:00 KST
    assert fa.expected_session("KR", before_close) == D("2026-10-19")
    holiday_evening = dt.datetime(2026, 10, 9, 11, 30, tzinfo=dt.timezone.utc)  # 한글날
    assert fa.expected_session("KR", holiday_evening) == D("2026-10-08")


# Verified 2026-10-20: NYSE = nyse.com/markets/hours-calendars (equals exchange_calendars 4.13.2 XNYS);
# KRX = exchange_calendars 4.13.2 XKRX plus the KRX 2026-05-20 announcement (06-03 election, 07-17 제헌절),
# which that library does not list yet. 2027 KR substitute days for 5/1 and 7/17 are not final: not pinned.
VERIFIED_CLOSURES = {
    "KR": {"2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-03-02", "2026-05-01", "2026-05-05",
           "2026-05-25", "2026-06-03", "2026-07-17", "2026-08-17", "2026-09-24", "2026-09-25", "2026-10-05",
           "2026-10-09", "2026-12-25", "2026-12-31", "2027-01-01", "2027-02-08", "2027-02-09", "2027-03-01",
           "2027-05-05", "2027-05-13", "2027-08-16", "2027-09-14", "2027-09-15", "2027-09-16", "2027-10-04",
           "2027-10-11", "2027-12-27", "2027-12-31"},
    "US": {"2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
           "2026-09-07", "2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26",
           "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"},
}


@pytest.mark.parametrize("market", ["KR", "US"])
def test_weekday_closures_match_the_verified_exchange_calendars(market):
    import pandas as pd
    closed = {d.date().isoformat() for d in pd.bdate_range("2026-01-01", "2027-12-31") if not cal.is_trading_day(d.date(), market)}
    assert closed == VERIFIED_CLOSURES[market]


def test_special_hours_cutoffs():
    utc = dt.timezone.utc
    # NYSE 13:00 early close: the regular 16:15 NY cutoff already waits past it.
    assert cal.session_close(D("2026-11-27"), "US").hour == 13
    assert fa.expected_session("US", dt.datetime(2026, 11, 27, 19, 0, tzinfo=utc)) == D("2026-11-25")  # 14:00 NY
    assert fa.expected_session("US", dt.datetime(2026, 11, 27, 21, 30, tzinfo=utc)) == D("2026-11-27")
    # KRX CSAT day closes 16:30: 16:00 KST is past the regular 15:45 cutoff but the session is still open.
    assert fa.expected_session("KR", dt.datetime(2026, 11, 19, 7, 0, tzinfo=utc)) == D("2026-11-18")
    assert fa.expected_session("KR", dt.datetime(2026, 11, 19, 7, 50, tzinfo=utc)) == D("2026-11-19")
    # Late 10:00 first-session opens: plans freeze before 09:00, which is earlier, so no rule changes.
    assert cal.SPECIAL_HOURS[("KR", D("2027-01-04"))][0] == dt.time(10)


@pytest.mark.parametrize("market, instant, expected", [
    ("KR", "2026-11-19T07:44:00+00:00", "2026-11-18"),  # CSAT day 16:44 KST: closes 16:30, not yet settled
    ("KR", "2026-11-19T07:45:00+00:00", "2026-11-19"),  # 16:45 KST = close + 15 minutes
    ("KR", "2026-11-18T06:45:00+00:00", "2026-11-18"),  # regular day unchanged: 15:45 KST
    ("US", "2026-11-27T18:15:00+00:00", "2026-11-26"),  # 13:15 NY early close + 15: still held to 16:15 NY
    # (market_clock leaves holidays to the vendor, so Thanksgiving 11-26 is the calendar ceiling here)
    ("US", "2026-11-27T21:15:00+00:00", "2026-11-27"),
])
def test_shared_market_clock_waits_for_late_special_closes_only(market, instant, expected):
    from market_clock import closed_bar_end
    assert closed_bar_end(market, now=dt.datetime.fromisoformat(instant)).isoformat() == expected
