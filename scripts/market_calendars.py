"""Explicit KRX / US equity full-day holiday tables for order planning.

Order plans need the NEXT real trading day before any bar exists, so the vendor calendar
(which only reveals holidays after the fact) is not enough. The tables below are the exchanges'
published full-day closures. Outside the covered years every lookup raises: extend the table
(and cite the source) instead of guessing. A bar on a listed holiday also raises upstream, so a
wrong table fails loudly instead of silently shifting a plan.

Verified 2026-10-20/21 (tests/test_funded_account_calendar.py pins these):
- 2023-2025 KRX/NYSE: exchange_calendars 4.13.2 XKRX/XNYS (past sessions, incl. the 2024-04-10 and 2025-06-03
  elections, temporary holidays 2023-10-02/2024-10-01/2025-01-27, and the NYSE 2025-01-09 mourning day).
- NYSE 2026-2027 holidays and 13:00 early closes: https://www.nyse.com/markets/hours-calendars (identical to
  exchange_calendars 4.13.2 XNYS).
- KRX: exchange_calendars 4.13.2 XKRX agrees except two 2026 closures it does not list yet, both from the KRX
  announcement of 2026-05-20 (https://news.bizwatch.co.kr/article/market/2026/05/20/0023): 2026-06-03 local
  election day and 2026-07-17 Constitution Day (restored as a statutory holiday in 2026). The exchange wins.
- Not yet final, so NOT listed: Saturday 2027-05-01 Labor Day and 2027-07-17 Constitution Day would move to
  Monday 2027-05-03 / 2027-07-19 under the government's 2026-04-17 substitute-holiday proposal (mpm.go.kr).
  KRX publishes its 2027 calendar in December; update this table then. A wrong entry fails loudly either way
  (a missing benchmark bar stalls the account; a bar on a listed holiday raises).
"""
from __future__ import annotations

import datetime as dt
from typing import Literal
from zoneinfo import ZoneInfo

Market = Literal["KR", "US"]

COVERED_YEARS = range(2023, 2028)  # 2023-2025 back the funded-account backtest segment

_HOLIDAYS: dict[str, frozenset[dt.date]] = {
    "KR": frozenset(dt.date.fromisoformat(d) for d in (
        # 2023-2025 from exchange_calendars XKRX (past closures, incl. elections and temporary holidays)
        "2023-01-23", "2023-01-24", "2023-03-01", "2023-05-01", "2023-05-05", "2023-05-29", "2023-06-06",
        "2023-08-15", "2023-09-28", "2023-09-29", "2023-10-02", "2023-10-03", "2023-10-09", "2023-12-25",
        "2023-12-29", "2024-01-01", "2024-02-09", "2024-02-12", "2024-03-01", "2024-04-10", "2024-05-01",
        "2024-05-06", "2024-05-15", "2024-06-06", "2024-08-15", "2024-09-16", "2024-09-17", "2024-09-18",
        "2024-10-01", "2024-10-03", "2024-10-09", "2024-12-25", "2024-12-31", "2025-01-01", "2025-01-27",
        "2025-01-28", "2025-01-29", "2025-01-30", "2025-03-03", "2025-05-01", "2025-05-05", "2025-05-06",
        "2025-06-03", "2025-06-06", "2025-08-15", "2025-10-03", "2025-10-06", "2025-10-07", "2025-10-08",
        "2025-10-09", "2025-12-25", "2025-12-31",
        # 2026: 신정, 설날 연휴, 삼일절 대체, 근로자의 날, 어린이날, 부처님오신날 대체, 지방선거, 제헌절,
        # 광복절 대체, 추석 연휴, 개천절 대체, 한글날, 성탄절, 연말 휴장일
        "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-03-02", "2026-05-01",
        "2026-05-05", "2026-05-25", "2026-06-03", "2026-07-17", "2026-08-17", "2026-09-24", "2026-09-25",
        "2026-10-05", "2026-10-09", "2026-12-25", "2026-12-31",
        # 2027: 신정, 설날 대체, 삼일절, 어린이날, 부처님오신날, 광복절 대체, 추석 연휴,
        # 개천절 대체, 한글날 대체, 성탄절 대체, 연말 휴장일
        "2027-01-01", "2027-02-08", "2027-02-09", "2027-03-01", "2027-05-05", "2027-05-13",
        "2027-08-16", "2027-09-14", "2027-09-15", "2027-09-16", "2027-10-04", "2027-10-11",
        "2027-12-27", "2027-12-31",
    )),
    "US": frozenset(dt.date.fromisoformat(d) for d in (
        "2023-01-02", "2023-01-16", "2023-02-20", "2023-04-07", "2023-05-29", "2023-06-19", "2023-07-04",
        "2023-09-04", "2023-11-23", "2023-12-25", "2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29",
        "2024-05-27", "2024-06-19", "2024-07-04", "2024-09-02", "2024-11-28", "2024-12-25", "2025-01-01",
        "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26", "2025-06-19", "2025-07-04",
        "2025-09-01", "2025-11-27", "2025-12-25",
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19",
        "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
        "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
        "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
    )),
}


# Sessions with non-standard hours (local open, local close). Regular: KR 09:00-15:30, US 09:30-16:00.
# KRX first sessions open at 10:00 (exchange_calendars XKRX). CSAT day 2026-11-19 (exam date per the
# Ministry of Education): KRX shifted 2024 and 2025 CSAT sessions to 10:00-16:30; the 2026 notice is not out
# yet, so the same shift is assumed. It only makes the bar cutoff later, never earlier. NYSE early closes
# are 13:00 per nyse.com.
SPECIAL_HOURS: dict[tuple[str, dt.date], tuple[dt.time, dt.time]] = {
    # 2023-2025: first sessions and NYSE early closes from exchange_calendars; CSAT days 10:00-16:30 per KRX
    # notices (2024/2025 reported; 2023 followed the same practice).
    ("KR", dt.date(2023, 1, 2)): (dt.time(10), dt.time(15, 30)),
    ("KR", dt.date(2023, 11, 16)): (dt.time(10), dt.time(16, 30)),
    ("KR", dt.date(2024, 1, 2)): (dt.time(10), dt.time(15, 30)),
    ("KR", dt.date(2024, 11, 14)): (dt.time(10), dt.time(16, 30)),
    ("KR", dt.date(2025, 1, 2)): (dt.time(10), dt.time(15, 30)),
    ("KR", dt.date(2025, 11, 13)): (dt.time(10), dt.time(16, 30)),
    ("US", dt.date(2023, 7, 3)): (dt.time(9, 30), dt.time(13)),
    ("US", dt.date(2023, 11, 24)): (dt.time(9, 30), dt.time(13)),
    ("US", dt.date(2024, 7, 3)): (dt.time(9, 30), dt.time(13)),
    ("US", dt.date(2024, 11, 29)): (dt.time(9, 30), dt.time(13)),
    ("US", dt.date(2024, 12, 24)): (dt.time(9, 30), dt.time(13)),
    ("US", dt.date(2025, 7, 3)): (dt.time(9, 30), dt.time(13)),
    ("US", dt.date(2025, 11, 28)): (dt.time(9, 30), dt.time(13)),
    ("US", dt.date(2025, 12, 24)): (dt.time(9, 30), dt.time(13)),
    ("KR", dt.date(2026, 1, 2)): (dt.time(10), dt.time(15, 30)),
    ("KR", dt.date(2026, 11, 19)): (dt.time(10), dt.time(16, 30)),
    ("KR", dt.date(2027, 1, 4)): (dt.time(10), dt.time(15, 30)),
    ("US", dt.date(2026, 11, 27)): (dt.time(9, 30), dt.time(13)),
    ("US", dt.date(2026, 12, 24)): (dt.time(9, 30), dt.time(13)),
    ("US", dt.date(2027, 11, 26)): (dt.time(9, 30), dt.time(13)),
}
_ZONES = {"KR": ZoneInfo("Asia/Seoul"), "US": ZoneInfo("America/New_York")}
BAR_SETTLE = dt.timedelta(minutes=15)  # market_clock waits for close + this before a bar counts as complete


def session_close(day: dt.date, market: Market) -> dt.datetime:
    regular = dt.time(15, 30) if market == "KR" else dt.time(16)
    close = SPECIAL_HOURS.get((market, day), (None, regular))[1]
    return dt.datetime.combine(day, close, _ZONES[market])


def is_trading_day(day: dt.date, market: Market) -> bool:
    if market not in _HOLIDAYS:
        raise ValueError(f"no trading calendar for market {market!r}")
    if day.year not in COVERED_YEARS:
        raise ValueError(f"{market} holiday table does not cover {day.year}; extend market_calendars.py")
    return day.weekday() < 5 and day not in _HOLIDAYS[market]


def next_trading_day(day: dt.date, market: Market) -> dt.date:
    """First trading day strictly after `day`."""
    day += dt.timedelta(days=1)
    while not is_trading_day(day, market):
        day += dt.timedelta(days=1)
    return day


def previous_trading_day(day: dt.date, market: Market, *, inclusive: bool = False) -> dt.date:
    """Last trading day before `day` (or on it when `inclusive`)."""
    if not inclusive:
        day -= dt.timedelta(days=1)
    while not is_trading_day(day, market):
        day -= dt.timedelta(days=1)
    return day


def first_trading_day_of_month(year: int, month: int, market: Market) -> dt.date:
    """The month's first trading day; holidays roll forward."""
    return next_trading_day(dt.date(year, month, 1) - dt.timedelta(days=1), market)


def skipped_closures(after: dt.date, until: dt.date, market: Market) -> list[str]:
    """Calendar days strictly between two sessions that are weekday exchange holidays."""
    out = []
    day = after + dt.timedelta(days=1)
    while day < until:
        if day.weekday() < 5 and not is_trading_day(day, market):
            out.append(day.isoformat())
        day += dt.timedelta(days=1)
    return out
