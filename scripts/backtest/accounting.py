# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import pandas as pd

from .config import POSITION_WEIGHT, COST_PER_SIDE
from .fx import _fx
from .warehouse import _px, _has_day, asof_value



# ──────────────────────────────────────────────────────────────────────────────
# Consensus trigger resolution
# ──────────────────────────────────────────────────────────────────────────────

def build_ticker_reports(perf: pd.DataFrame) -> dict[str, list[dict]]:
    """
    Returns ticker_key -> sorted list of report dicts.
    ticker_key = "KR_{6-digit}" or "US_{TICKER}"
    """
    result: dict[str, list[dict]] = {}
    for _, row in perf.iterrows():
        market = str(row.get("market", "KR"))
        raw_ticker = str(row["ticker"])
        if market == "KR":
            ticker_key = raw_ticker.zfill(6)
        else:
            ticker_key = raw_ticker  # US tickers as-is
        try:
            rdate = dt.date.fromisoformat(str(row["report_date"]))
        except ValueError:
            continue
        result.setdefault(ticker_key, []).append({
            "report_date": rdate,
            "school": str(row.get("school", "")),
            "source_file": str(row.get("source_file", "")),
            "target_price": float(row["target_price"]) if pd.notna(row.get("target_price")) else None,
            "display_name": str(row.get("display_name", ticker_key)),
            "stated_upside_pct": float(row["stated_upside_pct"]) if pd.notna(row.get("stated_upside_pct")) else None,
            "market": market,
        })
    for t in result:
        result[t].sort(key=lambda x: x["report_date"])
    return result


def find_trigger_reports(
    ticker_key: str,
    entry_date: dt.date,
    ticker_reports: dict[str, list[dict]],
    consensus_window: int | None,
) -> list[dict]:
    reports = ticker_reports.get(ticker_key, [])
    past = [r for r in reports if r["report_date"] < entry_date]
    if not past:
        return []
    if consensus_window is not None:
        cutoff = entry_date - dt.timedelta(days=consensus_window)
        past = [r for r in past if r["report_date"] >= cutoff]
    by_school: dict[str, dict] = {}
    for r in past:
        school = r["school"]
        if school not in by_school or r["report_date"] > by_school[school]["report_date"]:
            by_school[school] = r
    return list(by_school.values())


def _report_trigger_reason(
    ticker: str,
    entry_date: dt.date,
    ticker_reports: dict[str, list[dict]] | None,
    prefix: str = "학회 매수리포트 발간 → 익일 시가 진입",
) -> str:
    """v18: 진입 사유 텍스트 — 트리거 리포트(학회·발간일)를 붙인 표준 문구."""
    if not ticker_reports:
        return prefix
    triggers = find_trigger_reports(ticker, entry_date, ticker_reports, None)
    if not triggers:
        return prefix
    parts = ", ".join(
        f"{t['school']} {t['report_date'].isoformat()}"
        for t in sorted(triggers, key=lambda x: x["report_date"], reverse=True)
    )
    return f"{prefix} (트리거: {parts})"


# ──────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────────────

def round_to_tick(price: float, market: str) -> float:
    """Round a DISPLAY-ONLY price level down to the nearest exchange tick size.

    Conservative floor rounding (math.floor) is used for stop levels: a stop
    slightly lower than the raw float never overstates protection to the user.
    Entry/close prices that come from actual fills are left untouched — only
    synthetic derived levels (chandelier stop, etc.) pass through here at the
    serialization boundary.

    KRX 2023 호가 단위 (tick table):
      < 2,000       →  1원
      < 5,000       →  5원
      < 20,000      → 10원
      < 50,000      → 50원
      < 200,000     → 100원
      < 500,000     → 500원
      ≥ 500,000     → 1,000원

    US: round to $0.01 (standard cent tick).
    """
    if market == "US":
        return math.floor(price * 100) / 100
    # KR / default: KRX tick table
    if price < 2_000:
        tick = 1
    elif price < 5_000:
        tick = 5
    elif price < 20_000:
        tick = 10
    elif price < 50_000:
        tick = 50
    elif price < 200_000:
        tick = 100
    elif price < 500_000:
        tick = 500
    else:
        tick = 1_000
    return math.floor(price / tick) * tick


def first_trading_day_after(target: dt.date, calendar: list[dt.date]) -> dt.date | None:
    for d in calendar:
        if d > target:
            return d
    return None


def first_trading_day_on_or_after(target: dt.date, calendar: list[dt.date]) -> dt.date | None:
    for d in calendar:
        if d >= target:
            return d
    return None


def months_later(base: dt.date, n: int) -> dt.date:
    m = base.month - 1 + n
    return dt.date(base.year + m // 12, m % 12 + 1, min(base.day, 28))


def _get_quote(prices: dict[str, pd.DataFrame], ticker: str, day: dt.date) -> dict | None:
    """Open/close quote at an exact day (None if no bar). Same values as df.loc[ts]."""
    df = prices.get(ticker)
    if df is None:
        return None
    ts = pd.Timestamp(day)
    o = _px(df, ts, "open")
    if o is None:
        return None
    return {"open": o, "close": _px(df, ts, "close")}


def _last_month_open(day: dt.date, calendar: list[dt.date]) -> dt.date | None:
    """Return the first trading day of the current month (for monthly MA check)."""
    target = dt.date(day.year, day.month, 1)
    return first_trading_day_on_or_after(target, calendar)


# ──────────────────────────────────────────────────────────────────────────────
# Common entry-queue builder for consensus strategies
# ──────────────────────────────────────────────────────────────────────────────

def build_pending_entries(
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
    consensus_only: bool,
    prices: dict[str, pd.DataFrame],
) -> dict[dt.date, list[tuple[str, str, int]]]:
    """Queue reports on each ticker's next actual bar, not another market's day."""
    pending: dict[dt.date, list[tuple[str, str, int]]] = {}
    calendar_days = set(calendar)
    market_calendars = {
        ticker: sorted({ts.date() for ts in frame.index if ts.date() in calendar_days})
        for ticker, frame in prices.items()
    }
    for rdate, ticker, source, n_clubs in reports:
        if consensus_only and n_clubs < 2:
            continue
        entry_day = first_trading_day_after(rdate, market_calendars.get(ticker, []))
        if entry_day is not None:
            pending.setdefault(entry_day, []).append((ticker, source, n_clubs))
    return pending


def _try_enter(
    ticker: str,
    source: str,
    n_clubs: int,
    day: dt.date,
    prices: dict[str, pd.DataFrame],
    positions: dict[str, dict],
    cash: float,
    nav_now: float,
    ticker_reports: dict[str, list[dict]] | None,
    weight: float = POSITION_WEIGHT,
    momentum_filter: bool = False,
    entry_reason: str | None = None,
) -> tuple[dict | None, float]:
    """
    Try to enter a position. Returns (position_dict, new_cash) or (None, cash).
    momentum_filter=True: compare entry open with the previous available 200MA.
    entry_reason: v18 — 진입 사유 텍스트. None이면 표준 리포트 트리거 문구 자동 생성.
    """
    if ticker in positions:
        return None, cash
    df = prices.get(ticker)
    if df is None:
        return None, cash
    day_ts = pd.Timestamp(day)
    entry_price = _px(df, day_ts, "open")
    if entry_price is None:
        return None, cash
    if not math.isfinite(entry_price) or entry_price <= 0:
        return None, cash

    # The opening fill cannot depend on an MA containing this day's close.
    if momentum_filter:
        ma200_val = asof_value(df["ma200"], day - dt.timedelta(days=1))
        if not math.isfinite(ma200_val) or ma200_val <= 0 or entry_price < ma200_val:
            return None, cash

    # Resolve market and display metadata before budget/shares (needed for FX)
    display_name = ticker
    tp = None
    market = "KR"
    if ticker_reports is not None:
        tr_list = ticker_reports.get(ticker, [])
        if tr_list:
            market = tr_list[0].get("market", "KR")
        past_tr = [x for x in tr_list if x["report_date"] < day]
        if past_tr:
            latest = max(past_tr, key=lambda x: x["report_date"])
            display_name = latest["display_name"]
            tps = [x["target_price"] for x in past_tr if x["target_price"]]
            tp = max(tps) if tps else None

    budget = min(nav_now * weight, cash)
    if budget < nav_now * POSITION_WEIGHT * 0.5:
        return None, cash

    # v19 FX: shares = KRW_budget / (local_price × USDKRW). KR: fx=1 → unchanged.
    entry_fx = _fx(market, day)
    shares = budget * (1 - COST_PER_SIDE) / (entry_price * entry_fx)
    cash -= budget

    pos = {
        "shares": shares,
        "entry_price": entry_price,
        "entry_date": day,
        "cost": budget,
        "last_close": entry_price,
        "highest": entry_price,
        "source": source,
        "n_clubs": n_clubs,
        "display_name": display_name,
        "market": market,
        "target_price": tp,
        # For chandelier
        "stop": None,
        # For narrative hold (C rule): track if below_ma200_and_entry last month
        "ma200_exit_triggered": False,
        # For half-exit (E): whether half already sold
        "half_sold": False,
        "half_sell_price": None,
        # v18: 진입 사유 (왜 샀는가)
        "entry_reason": entry_reason or _report_trigger_reason(ticker, day, ticker_reports),
    }
    return pos, cash


def _close_trade(
    ticker: str,
    pos: dict,
    exit_date: dt.date,
    exit_price: float,
    exit_reason: str,
    ticker_reports: dict[str, list[dict]] | None,
    record_full_trades: bool,
    consensus_window: int | None,
    shares_override: float | None = None,
    cost_override: float | None = None,
    fx_rate: float | None = None,
) -> dict:
    shares = shares_override if shares_override is not None else pos["shares"]
    cost = cost_override if cost_override is not None else pos["cost"]
    # v19 FX: auto-derive USDKRW from pos market + exit_date if not supplied.
    if fx_rate is None:
        fx_rate = _fx(pos.get("market", "KR"), exit_date)
    exit_price_krw = exit_price * fx_rate
    proceeds = shares * exit_price_krw * (1 - COST_PER_SIDE)
    trade: dict = {
        "ticker": ticker,
        "market": pos.get("market", "KR"),
        "display_name": pos.get("display_name", ticker),
        "source": pos["source"],
        "n_clubs": pos["n_clubs"],
        "entry_date": pos["entry_date"].isoformat(),
        "exit_date": exit_date.isoformat(),
        "entry": round(pos["entry_price"], 4),
        "exit": round(exit_price, 4),   # local currency (USD for US)
        "return_pct": round((proceeds / cost - 1) * 100, 2),
        "pnl_krw": round(proceeds - cost, 2),
        "days": (exit_date - pos["entry_date"]).days,
        "exit_reason": exit_reason,
        "entry_reason": pos.get("entry_reason", ""),
    }
    if record_full_trades and ticker_reports is not None:
        triggers = find_trigger_reports(ticker, pos["entry_date"], ticker_reports, consensus_window)
        trade["trigger_reports"] = [
            {
                "school": tr["school"],
                "report_date": tr["report_date"].isoformat(),
                "source_file": Path(tr["source_file"]).name,
                "target_price": tr["target_price"],
                "stated_upside_pct": tr["stated_upside_pct"],
            }
            for tr in triggers
        ]
        trade["trigger_schools"] = sorted({tr["school"] for tr in triggers})
        trade["trigger_target_prices"] = [tr["target_price"] for tr in triggers if tr["target_price"]]
    return trade
