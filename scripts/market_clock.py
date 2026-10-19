"""Conservative calendar-date ceilings for completed daily bars.

Regular close plus 15 minutes for equity feeds. Early-close days are held until
the regular cutoff; holidays are left to the vendor's trading calendar.
FX is held until its entire UTC calendar day has elapsed.
"""
from __future__ import annotations

import datetime as dt
from typing import Literal
from zoneinfo import ZoneInfo


def closed_bar_end(market: Literal["KR", "US", "FX"], requested: dt.date | None = None,
                   *, now: dt.datetime | None = None) -> dt.date:
    instant = now or dt.datetime.now(dt.timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("market clock requires a timezone-aware instant")
    if market == "FX":
        ceiling = instant.astimezone(dt.timezone.utc).date() - dt.timedelta(days=1)
    else:
        zone, cutoff = {"KR": ("Asia/Seoul", dt.time(15, 45)),
                        "US": ("America/New_York", dt.time(16, 15))}[market]
        local = instant.astimezone(ZoneInfo(zone))
        ceiling = local.date() - dt.timedelta(days=int(local.time() < cutoff))
    ceiling = min(requested, ceiling) if requested else ceiling
    while ceiling.weekday() >= 5:
        ceiling -= dt.timedelta(days=1)
    return ceiling
