# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from . import fx
from .config import *  # noqa: F401,F403
from .config import (ROOT, ATR_PERIOD, MAX_POSITIONS, POSITION_WEIGHT,
    COST_PER_SIDE, REGIME_MA, CHANDELIER_ATR_MULT, MA200_MONTHLY_CHECK,
    CASH_YIELD_DAILY, BORROW_RATE_DAILY, IS_END, OOS_START, PRICE_DIR, GOLD_CACHE)
from .fx import _fx, set_usdkrw
from .warehouse import (load_prices, load_kospi, _fast_asof_raw, asof_value,
    _px, _has_day, _fast_frame)


def _atr_column(df: pd.DataFrame, period: int) -> pd.Series:
    """Return the exact precomputed ATR series; missing periods are invalid."""
    column = {20: "atr20", 42: "atr", 63: "atr63"}.get(period)
    if column is None or column not in df.columns:
        raise ValueError(f"ATR({period}) is unavailable; refusing period fallback")
    return df[column]


def _regime_risk_on(index: pd.Series, ma200: pd.Series, day: dt.date) -> bool:
    """Regime gate for day's parking return, decided with the prior close only.

    The return for ``day`` runs from the previous close to ``day``'s close, so the
    decision may not read ``day``'s close or its 200MA (same-day look-ahead).
    """
    prior = day - dt.timedelta(days=1)
    ma200_val = asof_value(ma200, prior)
    return ma200_val <= 0 or asof_value(index, prior) >= ma200_val
from .accounting import (build_ticker_reports, find_trigger_reports,
    _report_trigger_reason, round_to_tick, first_trading_day_after,
    first_trading_day_on_or_after, months_later, _get_quote, _last_month_open,
    build_pending_entries, _try_enter, _close_trade)
from .metrics import _compute_result, compute_extension



# ──────────────────────────────────────────────────────────────────────────────
# Strategy A/B: Immediate entry, fixed hold period
# ──────────────────────────────────────────────────────────────────────────────

def run_fixed_hold(
    prices: dict[str, pd.DataFrame],
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
    hold_months: int,
    label: str,
    ticker_reports: dict[str, list[dict]] | None = None,
    record_full_trades: bool = False,
) -> dict:
    """Immediate entry, sell after hold_months. All single reports OK (v8: no consensus gate)."""
    START_CAPITAL = 100_000_000
    cash = float(START_CAPITAL)
    positions: dict[str, dict] = {}
    nav_series: list[tuple[str, float]] = []
    trades: list[dict] = []
    scheduled_exits: dict[str, dt.date] = {}

    pending_entries = build_pending_entries(reports, calendar, consensus_only=False, prices=prices)

    for day in calendar:
        day_ts = pd.Timestamp(day)
        cash *= (1 + CASH_YIELD_DAILY)  # v18: 유휴 현금 일복리 이자 (연 3% MMF 프록시)

        # Execute scheduled exits
        to_exit = [t for t, d in list(scheduled_exits.items()) if d <= day and t in positions]
        for ticker in to_exit:
            pos = positions[ticker]
            q = _get_quote(prices, ticker, day)
            if q is None or float(q["open"]) <= 0:
                continue
            exit_price = float(q["open"])
            cash += pos["shares"] * exit_price * _fx(pos.get("market", "KR"), day) * (1 - COST_PER_SIDE)
            trades.append(_close_trade(ticker, pos, day, exit_price, f"{hold_months}개월_만기",
                                       ticker_reports, record_full_trades, None))
            del positions[ticker]
            del scheduled_exits[ticker]

        # Execute pending entries
        if day in pending_entries:
            nav_now = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
            slots = MAX_POSITIONS - len(positions)
            candidates = list({t: (t, s, nc) for t, s, nc in pending_entries[day] if t not in positions}.values())
            for ticker, source, n_clubs in candidates[:slots]:
                pos, cash = _try_enter(ticker, source, n_clubs, day, prices, positions, cash, nav_now, ticker_reports)
                if pos is not None:
                    pos["entry_reason"] += f" · {hold_months}개월 고정 보유"
                    positions[ticker] = pos
                    exit_target = months_later(day, hold_months)
                    exit_day = first_trading_day_on_or_after(exit_target, calendar)
                    if exit_day:
                        scheduled_exits[ticker] = exit_day

        # Update last_close
        for ticker, pos in positions.items():
            df = prices.get(ticker)
            if df is not None and _has_day(df, day_ts):
                pos["last_close"] = _px(df, day_ts, "close")
                pos["fx"] = _fx(pos.get("market", "KR"), day)

        nav = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
        nav_series.append((day.isoformat(), nav))

    # Force-close remaining
    last_day = calendar[-1]
    for ticker, pos in list(positions.items()):
        trades.append(_close_trade(ticker, pos, last_day, pos["last_close"], "데이터_종료_미청산",
                                   ticker_reports, record_full_trades, None))

    return _compute_result(nav_series, trades, START_CAPITAL, label, open_positions=positions)


# ──────────────────────────────────────────────────────────────────────────────
# Strategy C: Narrative Hold — exit on 200MA + below entry (Faber 2007)
# Checked monthly. Hold indefinitely if thesis intact.
# ──────────────────────────────────────────────────────────────────────────────

def run_narrative_hold(
    prices: dict[str, pd.DataFrame],
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
    label: str,
    ticker_reports: dict[str, list[dict]] | None = None,
    record_full_trades: bool = False,
    momentum_filter_entry: bool = False,
) -> dict:
    """
    진입: 단독 커버 포함 즉시 진입 (v8: 컨센서스 게이트 제거)
    청산: 월말 체크 — close < 200MA AND close < entry_price → 다음 거래일 시가 청산
    (Faber 2007 10-month SMA rule 정신: 추세 아래로 돌아오면 EXIT)
    momentum_filter_entry=True: 진입 시 close > 200MA 조건 추가 (Strategy F)
    """
    START_CAPITAL = 100_000_000
    cash = float(START_CAPITAL)
    positions: dict[str, dict] = {}
    nav_series: list[tuple[str, float]] = []
    trades: list[dict] = []
    pending_exits: set[str] = set()  # flagged at month-end, executed next open

    pending_entries = build_pending_entries(reports, calendar, consensus_only=False, prices=prices)

    # Build set of month-end dates
    cal_s = pd.Series(calendar)
    month_ends: set[dt.date] = set(
        cal_s.groupby(cal_s.apply(lambda d: (d.year, d.month))).last().values
    )

    for day in calendar:
        day_ts = pd.Timestamp(day)
        cash *= (1 + CASH_YIELD_DAILY)  # v18: 유휴 현금 일복리 이자 (연 3% MMF 프록시)

        # Execute pending exits (flagged previous month-end)
        to_exit = [t for t in sorted(pending_exits) if t in positions]
        for ticker in to_exit:
            pos = positions[ticker]
            q = _get_quote(prices, ticker, day)
            if q is None or float(q["open"]) <= 0:
                continue
            exit_price = float(q["open"])
            cash += pos["shares"] * exit_price * _fx(pos.get("market", "KR"), day) * (1 - COST_PER_SIDE)
            trades.append(_close_trade(ticker, pos, day, exit_price, "thesis_break_200MA",
                                       ticker_reports, record_full_trades, None))
            del positions[ticker]
            pending_exits.discard(ticker)

        # Execute pending entries
        if day in pending_entries:
            nav_now = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
            slots = MAX_POSITIONS - len(positions)
            candidates = list({t: (t, s, nc) for t, s, nc in pending_entries[day] if t not in positions}.values())
            for ticker, source, n_clubs in candidates[:slots]:
                pos, cash = _try_enter(ticker, source, n_clubs, day, prices, positions, cash, nav_now,
                                       ticker_reports, momentum_filter=momentum_filter_entry)
                if pos is not None:
                    if momentum_filter_entry:
                        pos["entry_reason"] += " · 진입 조건: 시가 > 200MA (모멘텀 필터 통과)"
                    positions[ticker] = pos

        # Update last_close and MA
        for ticker, pos in positions.items():
            df = prices.get(ticker)
            if df is not None and _has_day(df, day_ts):
                pos["last_close"] = _px(df, day_ts, "close")
                pos["fx"] = _fx(pos.get("market", "KR"), day)

        # Month-end thesis-break check (Faber rule: monthly check)
        if day in month_ends:
            for ticker, pos in list(positions.items()):
                df = prices.get(ticker)
                if df is None:
                    continue
                close = pos["last_close"]
                entry_p = pos["entry_price"]
                ma200_val = asof_value(df["ma200"], day)
                # Thesis break: close below 200MA AND below entry price
                if ma200_val > 0 and close < ma200_val and close < entry_p:
                    pending_exits.add(ticker)

        nav = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
        nav_series.append((day.isoformat(), nav))

    # Force-close remaining
    last_day = calendar[-1]
    for ticker, pos in list(positions.items()):
        trades.append(_close_trade(ticker, pos, last_day, pos["last_close"], "데이터_종료_미청산",
                                   ticker_reports, record_full_trades, None))

    return _compute_result(nav_series, trades, START_CAPITAL, label, open_positions=positions)


# ──────────────────────────────────────────────────────────────────────────────
# Strategy D: Chandelier Ratchet — ATR(42)×5 trailing from highest-high
# Wide enough to let multibaggers breathe.
# ──────────────────────────────────────────────────────────────────────────────

def run_chandelier(
    prices: dict[str, pd.DataFrame],
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
    label: str,
    ticker_reports: dict[str, list[dict]] | None = None,
    record_full_trades: bool = False,
) -> dict:
    """
    진입: 단독 커버 포함 즉시 진입 (v8: 컨센서스 게이트 제거)
    청산: close < (highest_high_since_entry - ATR(42) × 5)
    Chandelier Exit (Le Beau) — 문헌 표준값 ATR×5
    """
    START_CAPITAL = 100_000_000
    cash = float(START_CAPITAL)
    positions: dict[str, dict] = {}
    nav_series: list[tuple[str, float]] = []
    trades: list[dict] = []
    pending_exits: set[str] = set()

    pending_entries = build_pending_entries(reports, calendar, consensus_only=False, prices=prices)

    for day in calendar:
        day_ts = pd.Timestamp(day)
        cash *= (1 + CASH_YIELD_DAILY)  # v18: 유휴 현금 일복리 이자 (연 3% MMF 프록시)

        # Execute pending exits
        to_exit = [t for t in sorted(pending_exits) if t in positions]
        for ticker in to_exit:
            pos = positions[ticker]
            q = _get_quote(prices, ticker, day)
            if q is None or float(q["open"]) <= 0:
                continue
            exit_price = float(q["open"])
            cash += pos["shares"] * exit_price * _fx(pos.get("market", "KR"), day) * (1 - COST_PER_SIDE)
            trades.append(_close_trade(ticker, pos, day, exit_price, "chandelier_ATR5",
                                       ticker_reports, record_full_trades, None))
            del positions[ticker]
            pending_exits.discard(ticker)

        # Execute pending entries
        if day in pending_entries:
            nav_now = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
            slots = MAX_POSITIONS - len(positions)
            candidates = list({t: (t, s, nc) for t, s, nc in pending_entries[day] if t not in positions}.values())
            for ticker, source, n_clubs in candidates[:slots]:
                pos, cash = _try_enter(ticker, source, n_clubs, day, prices, positions, cash, nav_now, ticker_reports)
                if pos is not None:
                    atr_val = asof_value(prices[ticker]["atr"], day - dt.timedelta(days=1))
                    stop = pos["entry_price"] - CHANDELIER_ATR_MULT * atr_val if atr_val else pos["entry_price"] * 0.75
                    pos["stop"] = stop
                    positions[ticker] = pos

        # Update positions and check chandelier stop
        for ticker, pos in list(positions.items()):
            df = prices.get(ticker)
            if df is None or not _has_day(df, day_ts):
                continue
            close = _px(df, day_ts, "close")
            pos["last_close"] = close
            pos["fx"] = _fx(pos.get("market", "KR"), day)
            pos["highest"] = max(pos.get("highest", close), close)
            atr_val = asof_value(df["atr"], day)
            if atr_val:
                new_stop = pos["highest"] - CHANDELIER_ATR_MULT * atr_val
                pos["stop"] = max(pos.get("stop", 0.0), new_stop)
            if pos.get("stop") and close < pos["stop"] and ticker not in pending_exits:
                pending_exits.add(ticker)

        nav = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
        nav_series.append((day.isoformat(), nav))

    last_day = calendar[-1]
    for ticker, pos in list(positions.items()):
        trades.append(_close_trade(ticker, pos, last_day, pos["last_close"], "데이터_종료_미청산",
                                   ticker_reports, record_full_trades, None))

    return _compute_result(nav_series, trades, START_CAPITAL, label, open_positions=positions)


# ──────────────────────────────────────────────────────────────────────────────
# Strategy E: Half-exit at target + runner with C rule
# Sell half at club target price; trail rest with 200MA+entry thesis break (monthly)
# ──────────────────────────────────────────────────────────────────────────────

def run_half_exit_runner(
    prices: dict[str, pd.DataFrame],
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
    label: str,
    ticker_reports: dict[str, list[dict]] | None = None,
    record_full_trades: bool = False,
) -> dict:
    """
    진입: 단독 커버 포함 즉시 진입 (v8: 컨센서스 게이트 제거)
    절반 청산: 목표가(클럽 최고 목표가) 도달 시 → 보유 주수 50% 매도 (당일 종가)
    나머지 러너: C 규칙 (200MA + 진입가 하방, 월 1회 체크)
    목표가 없으면 전량 C 규칙만.
    """
    START_CAPITAL = 100_000_000
    cash = float(START_CAPITAL)
    positions: dict[str, dict] = {}
    nav_series: list[tuple[str, float]] = []
    trades: list[dict] = []
    runner_exits: set[str] = set()  # flagged for C-rule exit next open

    pending_entries = build_pending_entries(reports, calendar, consensus_only=False, prices=prices)

    cal_s = pd.Series(calendar)
    month_ends: set[dt.date] = set(
        cal_s.groupby(cal_s.apply(lambda d: (d.year, d.month))).last().values
    )

    for day in calendar:
        day_ts = pd.Timestamp(day)
        cash *= (1 + CASH_YIELD_DAILY)  # v18: 유휴 현금 일복리 이자 (연 3% MMF 프록시)

        # Execute runner exits (C-rule triggered previous month-end)
        to_exit = [t for t in sorted(runner_exits) if t in positions]
        for ticker in to_exit:
            pos = positions[ticker]
            q = _get_quote(prices, ticker, day)
            if q is None or float(q["open"]) <= 0:
                continue
            exit_price = float(q["open"])
            # Only the runner shares remain
            runner_shares = pos["shares"]
            runner_cost = pos.get("runner_cost", pos["cost"] * 0.5)
            cash += runner_shares * exit_price * _fx(pos.get("market", "KR"), day) * (1 - COST_PER_SIDE)
            trade = _close_trade(ticker, pos, day, exit_price, "runner_thesis_break_200MA",
                                 ticker_reports, record_full_trades, None,
                                 shares_override=runner_shares, cost_override=runner_cost)
            trades.append(trade)
            del positions[ticker]
            runner_exits.discard(ticker)

        # Execute pending entries
        if day in pending_entries:
            nav_now = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
            slots = MAX_POSITIONS - len(positions)
            candidates = list({t: (t, s, nc) for t, s, nc in pending_entries[day] if t not in positions}.values())
            for ticker, source, n_clubs in candidates[:slots]:
                pos, cash = _try_enter(ticker, source, n_clubs, day, prices, positions, cash, nav_now, ticker_reports)
                if pos is not None:
                    positions[ticker] = pos

        # Check target-price half exits (intraday high)
        for ticker, pos in list(positions.items()):
            if pos.get("half_sold"):
                continue
            tp = pos.get("target_price")
            if tp is None:
                continue
            df = prices.get(ticker)
            if df is None or not _has_day(df, day_ts):
                continue
            high_today = _px(df, day_ts, "high")
            close_today = _px(df, day_ts, "close")
            if high_today >= tp:
                # Sell half at target price (capped by close if needed)
                half_exit_price = min(tp, close_today) if close_today < tp else tp
                half_shares = pos["shares"] * 0.5
                half_cost = pos["cost"] * 0.5
                cash += half_shares * half_exit_price * _fx(pos.get("market", "KR"), day) * (1 - COST_PER_SIDE)
                trade = _close_trade(ticker, pos, day, half_exit_price, "목표가_절반익절",
                                     ticker_reports, record_full_trades, None,
                                     shares_override=half_shares, cost_override=half_cost)
                trades.append(trade)
                # Update position to runner only
                pos["shares"] = pos["shares"] * 0.5
                pos["runner_cost"] = half_cost
                pos["half_sold"] = True
                pos["half_sell_price"] = half_exit_price

        # Update last_close
        for ticker, pos in positions.items():
            df = prices.get(ticker)
            if df is not None and _has_day(df, day_ts):
                pos["last_close"] = _px(df, day_ts, "close")
                pos["fx"] = _fx(pos.get("market", "KR"), day)

        # Month-end C-rule check for runners
        if day in month_ends:
            for ticker, pos in list(positions.items()):
                df = prices.get(ticker)
                if df is None:
                    continue
                close = pos["last_close"]
                entry_p = pos["entry_price"]
                ma200_val = asof_value(df["ma200"], day)
                if ma200_val > 0 and close < ma200_val and close < entry_p:
                    runner_exits.add(ticker)

        nav = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
        nav_series.append((day.isoformat(), nav))

    last_day = calendar[-1]
    for ticker, pos in list(positions.items()):
        trades.append(_close_trade(ticker, pos, last_day, pos["last_close"], "데이터_종료_미청산",
                                   ticker_reports, record_full_trades, None))

    return _compute_result(nav_series, trades, START_CAPITAL, label, open_positions=positions)


# ──────────────────────────────────────────────────────────────────────────────
# Strategy G: 딥바이 — dip-buy on ≥20% pullback after report
# Single-club OK (dip itself is the filter).
# Entry: price falls ≥20% below publication-day close within 6 months.
# Exit: club target price OR +50% OR 12mo OR ATR×3 trailing stop (whichever first).
# Reference: mean-reversion after analyst catalyst (Jegadeesh & Kim 2006 framing).
# ──────────────────────────────────────────────────────────────────────────────

DIP_THRESHOLD = 0.20        # 20% below report-day close
DIP_WINDOW_DAYS = 180       # watch window
DIP_EXIT_PCT = 0.50         # +50% profit target
DIP_HOLD_MONTHS = 12        # max hold
DIP_ATR_MULT = 3.0          # trailing stop multiplier

def run_dip_buy(
    prices: dict[str, pd.DataFrame],
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
    label: str,
    ticker_reports: dict[str, list[dict]] | None = None,
    record_full_trades: bool = False,
) -> dict:
    """
    단독 커버 OK (딥이 필터).
    진입: 발간일 종가 대비 ≥20% 하락 시점 (6개월 내), 다음 거래일 시가 매수.
    청산: 목표가 / +50% / 12개월 / ATR×3 트레일 중 선착.
    """
    START_CAPITAL = 100_000_000
    cash = float(START_CAPITAL)
    positions: dict[str, dict] = {}
    nav_series: list[tuple[str, float]] = []
    trades: list[dict] = []
    pending_exits: set[str] = set()

    # Build per-ticker dip-watch queue: {ticker: [(report_date, pub_day_close, target_price, display_name, n_clubs, source)]}
    dip_watch: dict[str, list[dict]] = {}
    cal_set = set(calendar)
    for rdate, ticker, source, n_clubs in reports:
        if rdate < SIM_START - dt.timedelta(days=DIP_WINDOW_DAYS):
            continue
        df = prices.get(ticker)
        if df is None:
            continue
        # publication-day close (asof)
        pub_close = asof_value(df["close"], rdate)
        if pub_close <= 0:
            continue
        tr_list = (ticker_reports or {}).get(ticker, [])
        past_tr = [x for x in tr_list if x["report_date"] <= rdate]
        tp = max((x["target_price"] for x in past_tr if x["target_price"]), default=None)
        dn = past_tr[-1]["display_name"] if past_tr else ticker
        market = past_tr[0].get("market", "KR") if past_tr else "KR"
        dip_watch.setdefault(ticker, []).append({
            "report_date": rdate,
            "pub_close": pub_close,
            "expire_date": rdate + dt.timedelta(days=DIP_WINDOW_DAYS),
            "target_price": tp,
            "display_name": dn,
            "n_clubs": n_clubs,
            "source": source,
            "market": market,
        })

    # Flag set: ticker -> next-day entry queued
    dip_entry_queue: list[tuple[str, dict]] = []

    for day in calendar:
        day_ts = pd.Timestamp(day)
        cash *= (1 + CASH_YIELD_DAILY)  # v18: 유휴 현금 일복리 이자 (연 3% MMF 프록시)

        # Execute pending exits (trailing stop or other)
        to_exit = [t for t in sorted(pending_exits) if t in positions]
        for ticker in to_exit:
            pos = positions[ticker]
            q = _get_quote(prices, ticker, day)
            if q is None or float(q["open"]) <= 0:
                continue
            exit_price = float(q["open"])
            cash += pos["shares"] * exit_price * _fx(pos.get("market", "KR"), day) * (1 - COST_PER_SIDE)
            trades.append(_close_trade(ticker, pos, day, exit_price, pos.get("_exit_reason", "dip_atr3_stop"),
                                       ticker_reports, record_full_trades, None))
            del positions[ticker]
            pending_exits.discard(ticker)

        # Execute dip entries queued from previous day's check
        if dip_entry_queue:
            nav_now = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
            slots = MAX_POSITIONS - len(positions)
            for ticker, watch in dip_entry_queue[:slots]:
                if ticker in positions:
                    continue
                df = prices.get(ticker)
                if df is None:
                    continue
                entry_price = _px(df, day_ts, "open")
                if entry_price is None or entry_price <= 0:
                    continue
                budget = min(nav_now * POSITION_WEIGHT, cash)
                if budget < nav_now * POSITION_WEIGHT * 0.5:
                    continue
                shares = budget * (1 - COST_PER_SIDE) / (entry_price * _fx(watch["market"], day))
                cash -= budget
                atr_val = asof_value(df["atr"], day - dt.timedelta(days=1))
                stop = entry_price - DIP_ATR_MULT * atr_val if atr_val else entry_price * 0.80
                pos = {
                    "shares": shares, "entry_price": entry_price, "entry_date": day,
                    "cost": budget, "last_close": entry_price, "highest": entry_price,
                    "source": watch["source"], "n_clubs": watch["n_clubs"],
                    "display_name": watch["display_name"], "market": watch["market"],
                    "target_price": watch["target_price"], "stop": stop,
                    "max_hold_date": months_later(day, DIP_HOLD_MONTHS),
                    "half_sold": False, "half_sell_price": None,
                    "entry_reason": (
                        f"발간일({watch['report_date'].isoformat()}) 종가 대비 "
                        f"−{int(DIP_THRESHOLD * 100)}% 하락 도달 → 익일 시가 진입 (딥바이)"
                    ),
                }
                positions[ticker] = pos
            dip_entry_queue = []

        # Scan dip-watch for new triggers
        for ticker, watches in dip_watch.items():
            if ticker in positions:
                continue
            df = prices.get(ticker)
            if df is None or not _has_day(df, day_ts):
                continue
            close_today = _px(df, day_ts, "close")
            for watch in watches:
                if day < watch["report_date"] or day > watch["expire_date"]:
                    continue
                dip_level = watch["pub_close"] * (1 - DIP_THRESHOLD)
                if close_today <= dip_level:
                    dip_entry_queue.append((ticker, watch))
                    break  # one entry per ticker per day

        # Update positions + check exits
        for ticker, pos in list(positions.items()):
            df = prices.get(ticker)
            if df is None or not _has_day(df, day_ts):
                continue
            close = _px(df, day_ts, "close")
            high_today = _px(df, day_ts, "high")
            pos["last_close"] = close
            pos["fx"] = _fx(pos.get("market", "KR"), day)
            pos["highest"] = max(pos.get("highest", close), close)

            # Trailing stop ratchet
            atr_val = asof_value(df["atr"], day)
            if atr_val:
                new_stop = pos["highest"] - DIP_ATR_MULT * atr_val
                pos["stop"] = max(pos.get("stop", 0.0), new_stop)

            exit_reason = None
            exit_price_override = None

            # ATR trailing stop
            if pos.get("stop") and close < pos["stop"]:
                exit_reason = "dip_atr3_stop"

            # +50% profit target
            elif high_today >= pos["entry_price"] * (1 + DIP_EXIT_PCT):
                exit_reason = "dip_+50pct"
                exit_price_override = pos["entry_price"] * (1 + DIP_EXIT_PCT)

            # Club target price
            elif pos.get("target_price") and high_today >= pos["target_price"]:
                exit_reason = "dip_목표가"
                exit_price_override = pos["target_price"]

            # Max hold
            elif day >= pos["max_hold_date"]:
                exit_reason = "dip_12mo_만기"

            if exit_reason and ticker not in pending_exits:
                if exit_price_override:
                    # Immediate same-day close at override price
                    ep = min(exit_price_override, close)
                    cash += pos["shares"] * ep * (1 - COST_PER_SIDE)
                    trades.append(_close_trade(ticker, pos, day, ep, exit_reason,
                                               ticker_reports, record_full_trades, None))
                    del positions[ticker]
                else:
                    pos["_exit_reason"] = exit_reason
                    pending_exits.add(ticker)

        nav = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
        nav_series.append((day.isoformat(), nav))

    last_day = calendar[-1]
    for ticker, pos in list(positions.items()):
        trades.append(_close_trade(ticker, pos, last_day, pos["last_close"], "데이터_종료_미청산",
                                   ticker_reports, record_full_trades, None))

    return _compute_result(nav_series, trades, START_CAPITAL, label, open_positions=positions)


# ──────────────────────────────────────────────────────────────────────────────
# Strategy H: 미너비니 트렌드 템플릿
# Entry conditions use the last available bar before the opening fill:
#   close > 50MA > 150MA > 200MA
#   200MA rising vs 1 month ago
#   close ≥ 70% of 52w high
#   RS(6mo) vs KOSPI > 0
# Exit: close < 50MA on weekly check (Friday close).
# Reference: Minervini (2013) "Trade Like a Stock Market Wizard"
# ──────────────────────────────────────────────────────────────────────────────

def run_minervini(
    prices: dict[str, pd.DataFrame],
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
    label: str,
    ticker_reports: dict[str, list[dict]] | None = None,
    record_full_trades: bool = False,
    kospi: pd.Series | None = None,
) -> dict:
    """
    미너비니 트렌드 템플릿. 진입: 단독 커버 포함 + 5-point template (v8: 컨센서스 게이트 제거).
    청산: 주간(금요일) 체크 시 close < 50MA.
    """
    START_CAPITAL = 100_000_000
    cash = float(START_CAPITAL)
    positions: dict[str, dict] = {}
    nav_series: list[tuple[str, float]] = []
    trades: list[dict] = []
    pending_exits: set[str] = set()

    pending_entries = build_pending_entries(reports, calendar, consensus_only=False, prices=prices)

    # Weekly check days (Fridays, or last day of week in calendar)
    cal_s = pd.Series(calendar)
    week_ends: set[dt.date] = set(
        cal_s.groupby(cal_s.apply(lambda d: (d.isocalendar()[0], d.isocalendar()[1]))).last().values
    )

    for day in calendar:
        day_ts = pd.Timestamp(day)
        cash *= (1 + CASH_YIELD_DAILY)  # v18: 유휴 현금 일복리 이자 (연 3% MMF 프록시)

        # Execute pending exits
        to_exit = [t for t in sorted(pending_exits) if t in positions]
        for ticker in to_exit:
            pos = positions[ticker]
            q = _get_quote(prices, ticker, day)
            if q is None or float(q["open"]) <= 0:
                continue
            exit_price = float(q["open"])
            cash += pos["shares"] * exit_price * _fx(pos.get("market", "KR"), day) * (1 - COST_PER_SIDE)
            trades.append(_close_trade(ticker, pos, day, exit_price, "minervini_close<50MA",
                                       ticker_reports, record_full_trades, None))
            del positions[ticker]
            pending_exits.discard(ticker)

        # Execute pending entries — check Minervini template
        if day in pending_entries:
            nav_now = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
            slots = MAX_POSITIONS - len(positions)
            candidates = list({t: (t, s, nc) for t, s, nc in pending_entries[day] if t not in positions}.values())
            for ticker, source, n_clubs in candidates[:slots]:
                df = prices.get(ticker)
                if df is None:
                    continue
                signal_day = day - dt.timedelta(days=1)
                close = asof_value(df["close"], signal_day)
                if close <= 0:
                    continue
                ma50  = asof_value(df["ma50"],  signal_day)
                ma150 = asof_value(df["ma150"], signal_day)
                ma200 = asof_value(df["ma200"], signal_day)
                hi52w = asof_value(df["hi52w"], signal_day)
                if any(v <= 0 for v in [ma50, ma150, ma200, hi52w]):
                    continue
                # Template: prior close > prior indicators; same-day close cannot affect open fill.
                if not (close > ma50 > ma150 > ma200):
                    continue
                ma200_1mo = asof_value(df["ma200"], signal_day - dt.timedelta(days=30))
                if ma200_1mo <= 0 or ma200 <= ma200_1mo:
                    continue
                if close < 0.70 * hi52w:
                    continue
                if kospi is not None:
                    price_6mo_ago = asof_value(df["close"], signal_day - dt.timedelta(days=182))
                    kospi_6mo_ago = asof_value(kospi, signal_day - dt.timedelta(days=182))
                    kospi_now = asof_value(kospi, signal_day)
                    if price_6mo_ago > 0 and kospi_6mo_ago > 0 and kospi_now > 0:
                        stock_rs = close / price_6mo_ago - 1
                        kospi_rs = kospi_now / kospi_6mo_ago - 1
                        if stock_rs <= kospi_rs:
                            continue
                pos, cash = _try_enter(
                    ticker, source, n_clubs, day, prices, positions, cash, nav_now, ticker_reports,
                    entry_reason=_report_trigger_reason(
                        ticker, day, ticker_reports,
                        prefix="미너비니 트렌드 템플릿 통과 (close>50MA>150MA>200MA, 200MA 상승, "
                               "52주고점 70%↑, RS>KOSPI) → 진입",
                    ),
                )
                if pos is not None:
                    positions[ticker] = pos

        # Update last_close
        for ticker, pos in positions.items():
            df = prices.get(ticker)
            if df is not None and _has_day(df, day_ts):
                pos["last_close"] = _px(df, day_ts, "close")
                pos["fx"] = _fx(pos.get("market", "KR"), day)

        # Weekly check: exit if close < 50MA
        if day in week_ends:
            for ticker, pos in list(positions.items()):
                df = prices.get(ticker)
                if df is None:
                    continue
                close = pos["last_close"]
                ma50_val = asof_value(df["ma50"], day)
                if ma50_val > 0 and close < ma50_val:
                    pending_exits.add(ticker)

        nav = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
        nav_series.append((day.isoformat(), nav))

    last_day = calendar[-1]
    for ticker, pos in list(positions.items()):
        trades.append(_close_trade(ticker, pos, last_day, pos["last_close"], "데이터_종료_미청산",
                                   ticker_reports, record_full_trades, None))

    return _compute_result(nav_series, trades, START_CAPITAL, label, open_positions=positions)


# ──────────────────────────────────────────────────────────────────────────────
# Strategy I: 슈퍼트렌드(10, 3)
# Consensus ≥2 required.
# Entry: Supertrend is bullish on report day OR first bullish flip within 3mo.
# Exit: Supertrend flips bearish.
# Reference: Supertrend indicator (Olivier Seban popularised; standard (10, 3) params).
# ──────────────────────────────────────────────────────────────────────────────

SUPERTREND_WINDOW_DAYS = 90   # 3mo window to wait for bullish flip after report

def run_supertrend(
    prices: dict[str, pd.DataFrame],
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
    label: str,
    ticker_reports: dict[str, list[dict]] | None = None,
    record_full_trades: bool = False,
) -> dict:
    """
    Supertrend(10, 3). 진입: 발간 시 불리시 OR 3개월 내 첫 상향 전환.
    청산: 하향 전환 다음 거래일 시가.
    """
    START_CAPITAL = 100_000_000
    cash = float(START_CAPITAL)
    positions: dict[str, dict] = {}
    nav_series: list[tuple[str, float]] = []
    trades: list[dict] = []
    pending_exits: set[str] = set()

    # Build per-day ST-watch: tickers waiting for a bullish flip after report
    # st_watch: ticker -> (report_date, expire_date, n_clubs, source)
    st_watch: dict[str, dict] = {}
    # v18: 4th element = entry_reason (직접 진입 vs 상방 전환 구분)
    pending_entries_direct: dict[dt.date, list[tuple[str, str, int, str]]] = {}

    for rdate, ticker, source, n_clubs in reports:
        # v8: no consensus gate — single-club OK
        df = prices.get(ticker)
        if df is None:
            continue
        # Check if supertrend is already bullish on report day
        st_val = asof_value(df["supertrend_bull"].astype(float), rdate)
        entry_day = first_trading_day_after(rdate, calendar)
        if st_val >= 0.5:
            # Already bullish → enter immediately
            if entry_day:
                pending_entries_direct.setdefault(entry_day, []).append(
                    (ticker, source, n_clubs, "리포트 발간 시점 슈퍼트렌드(10,3) 상방 → 익일 시가 진입"))
        else:
            # Wait for first bullish flip within 3 months
            expire = rdate + dt.timedelta(days=SUPERTREND_WINDOW_DAYS)
            if ticker not in st_watch or st_watch[ticker]["report_date"] < rdate:
                st_watch[ticker] = {
                    "report_date": rdate, "expire_date": expire,
                    "n_clubs": n_clubs, "source": source,
                }

    for day in calendar:
        day_ts = pd.Timestamp(day)
        cash *= (1 + CASH_YIELD_DAILY)  # v18: 유휴 현금 일복리 이자 (연 3% MMF 프록시)

        # Execute pending exits
        to_exit = [t for t in sorted(pending_exits) if t in positions]
        for ticker in to_exit:
            pos = positions[ticker]
            q = _get_quote(prices, ticker, day)
            if q is None or float(q["open"]) <= 0:
                continue
            exit_price = float(q["open"])
            cash += pos["shares"] * exit_price * _fx(pos.get("market", "KR"), day) * (1 - COST_PER_SIDE)
            trades.append(_close_trade(ticker, pos, day, exit_price, "supertrend_bearish_flip",
                                       ticker_reports, record_full_trades, None))
            del positions[ticker]
            pending_exits.discard(ticker)

        # Execute direct entries (supertrend bullish at report)
        if day in pending_entries_direct:
            nav_now = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
            slots = MAX_POSITIONS - len(positions)
            candidates = list({t: (t, s, nc, rsn) for t, s, nc, rsn in pending_entries_direct[day] if t not in positions}.values())
            for ticker, source, n_clubs, st_reason in candidates[:slots]:
                pos, cash = _try_enter(ticker, source, n_clubs, day, prices, positions, cash, nav_now,
                                       ticker_reports,
                                       entry_reason=_report_trigger_reason(ticker, day, ticker_reports, prefix=st_reason))
                if pos is not None:
                    positions[ticker] = pos

        # Scan st_watch for bullish flips
        new_direct: list[tuple[str, str, int, str]] = []
        for ticker, watch in list(st_watch.items()):
            if ticker in positions:
                continue
            if day > watch["expire_date"]:
                del st_watch[ticker]
                continue
            if day < watch["report_date"]:
                continue
            df = prices.get(ticker)
            if df is None or not _has_day(df, day_ts):
                continue
            st_now = bool(_px(df, day_ts, "supertrend_bull"))
            if st_now:
                new_direct.append((ticker, watch["source"], watch["n_clubs"],
                                   "리포트 발간 후 3개월 내 슈퍼트렌드(10,3) 상방 전환 → 익일 시가 진입"))
                del st_watch[ticker]

        if new_direct:
            nav_now = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
            slots = MAX_POSITIONS - len(positions)
            entry_day = first_trading_day_after(day, calendar)
            if entry_day:
                pending_entries_direct.setdefault(entry_day, []).extend(new_direct)

        # Update last_close + check supertrend exit
        for ticker, pos in list(positions.items()):
            df = prices.get(ticker)
            if df is None or not _has_day(df, day_ts):
                continue
            pos["last_close"] = _px(df, day_ts, "close")
            st_now = bool(_px(df, day_ts, "supertrend_bull"))
            if not st_now and ticker not in pending_exits:
                pending_exits.add(ticker)

        nav = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
        nav_series.append((day.isoformat(), nav))

    last_day = calendar[-1]
    for ticker, pos in list(positions.items()):
        trades.append(_close_trade(ticker, pos, last_day, pos["last_close"], "데이터_종료_미청산",
                                   ticker_reports, record_full_trades, None))

    return _compute_result(nav_series, trades, START_CAPITAL, label, open_positions=positions)


# ──────────────────────────────────────────────────────────────────────────────
# Strategy J: 코어-새틀라이트 80/20 + 폭락 레버리지 overlay
# Overlay on D (Chandelier) NAV series.
# Allocation: 80% in strategy D, 20% cash buffer.
# When KOSPI drawdown from 52w high ≥ 15%: deploy cash + borrow to 120% equity.
# Borrow cost: 6%/yr accrued daily on borrowed amount.
# Deleverage back to 80/20 when drawdown recovers to < 5%.
# ──────────────────────────────────────────────────────────────────────────────

LEVERAGE_DEPLOY_DD = 0.15             # KOSPI -15% from 52w high triggers deploy
LEVERAGE_RECOVER_DD = 0.05            # KOSPI -5% (from 52w high) → deleverage
LEVERAGE_TARGET = 1.20                # 120% of equity at leverage peak
CORE_ALLOCATION = 0.80                # 80% core, 20% cash

def run_core_satellite_leverage(
    chandelier_nav: pd.Series,
    kospi: pd.Series,
    label: str = "J_core_satellite",
) -> dict:
    """
    D 샹들리에 NAV 오버레이.
    80% 코어(D), 20% 현금. KOSPI 52w 고점 대비 -15% 시 레버리지 120% 전개.
    차입비용 6%/년 일 단위 적립. 복구 시(-5% 미만) 디레버.
    """
    START_CAPITAL = 100_000_000
    # Normalise chandelier NAV to returns
    chan_ret = chandelier_nav.pct_change().fillna(0)

    idx = chandelier_nav.index
    kospi_aligned = kospi.reindex(idx).ffill().bfill()
    kospi_hi52 = kospi_aligned.rolling(252, min_periods=1).max()

    equity = float(START_CAPITAL)
    # core_units: how many "shares" of the chandelier strategy we hold
    core_units = equity * CORE_ALLOCATION  # notional
    cash_buffer = equity * (1 - CORE_ALLOCATION)
    borrowed = 0.0
    is_leveraged = False

    nav_series: list[tuple[str, float]] = []

    for i, ts in enumerate(idx):
        day_ts = ts
        day = ts.date()

        # v18: 현금 버퍼 일복리 이자 (연 3% MMF 프록시) — 양(+) 잔액에만
        if cash_buffer > 0:
            cash_buffer *= (1 + CASH_YIELD_DAILY)

        # Compute KOSPI drawdown
        kp = float(kospi_aligned.loc[day_ts])
        kp_hi = float(kospi_hi52.loc[day_ts])
        kospi_dd = (kp / kp_hi - 1) if kp_hi > 0 else 0.0

        cr = float(chan_ret.iloc[i])

        if not is_leveraged:
            # Normal 80/20: core grows with chandelier return
            core_units *= (1 + cr)
            # Check if we should leverage
            if kospi_dd <= -LEVERAGE_DEPLOY_DD and cash_buffer > 0:
                # Deploy cash + borrow to get to 120% of current equity
                total_equity = core_units + cash_buffer
                target_core = total_equity * LEVERAGE_TARGET
                additional = target_core - core_units
                # First use cash, then borrow rest
                from_cash = min(cash_buffer, additional)
                from_borrow = additional - from_cash
                core_units += additional
                cash_buffer -= from_cash
                borrowed = from_borrow
                is_leveraged = True
        else:
            # Leveraged: core grows, borrow cost accrues
            # v18: 일복리 (1+6%)^(1/252)−1 — 단리 6%/365에서 통일
            core_units *= (1 + cr)
            borrow_cost_daily = borrowed * BORROW_RATE_DAILY
            # Capitalize interest into debt exactly once; NAV subtracts debt below.
            borrowed += borrow_cost_daily

            # Check if we should deleverage
            if kospi_dd > -LEVERAGE_RECOVER_DD:
                # Sell down core to 80% of net equity and repay borrow
                net_equity = core_units + cash_buffer - borrowed
                target_core = net_equity * CORE_ALLOCATION
                excess = core_units - target_core
                cash_freed = max(0.0, excess)
                core_units = target_core
                cash_buffer += cash_freed
                # Repay borrow with cash
                repay = min(borrowed, cash_buffer)
                borrowed -= repay
                cash_buffer -= repay
                if borrowed < 0:
                    borrowed = 0.0
                is_leveraged = False

        nav = core_units + cash_buffer - borrowed
        nav_series.append((day.isoformat(), nav))

    # Build dummy trades list (overlay has no discrete trades)
    trades: list[dict] = []

    return _compute_result(nav_series, trades, START_CAPITAL, label, open_positions={})


# ──────────────────────────────────────────────────────────────────────────────
# Strategy K: R:R 2.5 추세추종 (risk-defined fast trading)
# Consensus ≥2.
# Entry: open next day after report signal.
# Stop: entry − 1×ATR(20) = 1R.
# Take half at +2.5R.
# Trail remainder with Chandelier ATR×3.
# Max 10 concurrent positions (concentration).
# Reference: Van Tharp "Trade Your Way to Financial Freedom" R-multiple framework.
# ──────────────────────────────────────────────────────────────────────────────

RR_STOP_MULT = 1.0          # 1R stop = 1×ATR(20)
RR_TARGET_MULT = 2.5        # half off at +2.5R
RR_TRAIL_ATR_MULT = 3.0     # trail rest with ATR(42)×3 chandelier
RR_MAX_POSITIONS = 10       # max 10 concurrent positions

def run_rr_trend(
    prices: dict[str, pd.DataFrame],
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
    label: str,
    ticker_reports: dict[str, list[dict]] | None = None,
    record_full_trades: bool = False,
) -> dict:
    """
    R:R 2.5 추세추종. Stop = 1×ATR(20). 반절 +2.5R. 나머지 Chandelier ATR×3 트레일.
    동시 최대 10종목. v8: 단독 커버 포함.
    """
    START_CAPITAL = 100_000_000
    cash = float(START_CAPITAL)
    positions: dict[str, dict] = {}
    nav_series: list[tuple[str, float]] = []
    trades: list[dict] = []
    pending_exits: dict[str, str] = {}  # ticker -> reason

    pending_entries = build_pending_entries(reports, calendar, consensus_only=False, prices=prices)

    for day in calendar:
        day_ts = pd.Timestamp(day)
        cash *= (1 + CASH_YIELD_DAILY)  # v18: 유휴 현금 일복리 이자 (연 3% MMF 프록시)

        # Execute pending exits
        to_exit = [t for t in list(pending_exits) if t in positions]
        for ticker in to_exit:
            pos = positions[ticker]
            reason = pending_exits.pop(ticker)
            q = _get_quote(prices, ticker, day)
            if q is None or float(q["open"]) <= 0:
                pending_exits[ticker] = reason  # defer
                continue
            exit_price = float(q["open"])
            # How many shares remain?
            shares = pos["shares"]
            cost = pos["cost"] * (shares / pos.get("original_shares", shares))
            cash += shares * exit_price * _fx(pos.get("market", "KR"), day) * (1 - COST_PER_SIDE)
            trades.append(_close_trade(ticker, pos, day, exit_price, reason,
                                       ticker_reports, record_full_trades, None,
                                       shares_override=shares, cost_override=cost))
            del positions[ticker]

        # Execute pending entries (max 10 positions)
        if day in pending_entries:
            nav_now = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
            slots = RR_MAX_POSITIONS - len(positions)
            candidates = list({t: (t, s, nc) for t, s, nc in pending_entries[day] if t not in positions}.values())
            for ticker, source, n_clubs in candidates[:slots]:
                df = prices.get(ticker)
                if df is None:
                    continue
                entry_price = _px(df, day_ts, "open")
                if entry_price is None or entry_price <= 0:
                    continue
                atr20_val = asof_value(df["atr20"], day - dt.timedelta(days=1))
                if atr20_val <= 0:
                    continue
                one_r = RR_STOP_MULT * atr20_val
                stop = entry_price - one_r
                take_profit = entry_price + RR_TARGET_MULT * one_r

                display_name = ticker
                tp = None
                market = "KR"
                if ticker_reports is not None:
                    tr_list = ticker_reports.get(ticker, [])
                    past_tr = [x for x in tr_list if x["report_date"] < day]
                    if past_tr:
                        latest = max(past_tr, key=lambda x: x["report_date"])
                        display_name = latest["display_name"]
                        tps = [x["target_price"] for x in past_tr if x["target_price"]]
                        tp = max(tps) if tps else None
                        market = past_tr[0].get("market", "KR")

                budget = min(nav_now * POSITION_WEIGHT, cash)
                if budget < nav_now * POSITION_WEIGHT * 0.5:
                    continue
                shares = budget * (1 - COST_PER_SIDE) / (entry_price * _fx(market, day))
                cash -= budget

                positions[ticker] = {
                    "shares": shares, "original_shares": shares,
                    "entry_price": entry_price, "entry_date": day,
                    "cost": budget, "last_close": entry_price,
                    "highest": entry_price, "stop": stop,
                    "take_profit_price": take_profit,
                    "one_r": one_r, "half_sold": False,
                    "source": source, "n_clubs": n_clubs,
                    "display_name": display_name, "market": market,
                    "target_price": tp,
                    "entry_reason": _report_trigger_reason(
                        ticker, day, ticker_reports,
                        prefix="학회 매수리포트 발간 → 익일 시가 진입, R:R 세팅 (스탑 1×ATR20, 목표 +2.5R)",
                    ),
                }

        # Update positions and check exit conditions
        for ticker, pos in list(positions.items()):
            df = prices.get(ticker)
            if df is None or not _has_day(df, day_ts):
                continue
            close = _px(df, day_ts, "close")
            high_today = _px(df, day_ts, "high")
            low_today = _px(df, day_ts, "low")
            pos["last_close"] = close
            pos["fx"] = _fx(pos.get("market", "KR"), day)
            pos["highest"] = max(pos.get("highest", close), close)

            # Ratchet trail stop (Chandelier ATR×3) for remaining runner
            atr_val = asof_value(df["atr"], day)
            if atr_val:
                trail_stop = pos["highest"] - RR_TRAIL_ATR_MULT * atr_val
                pos["stop"] = max(pos.get("stop", 0.0), trail_stop)

            if ticker in pending_exits:
                continue  # already queued

            # Half-exit at +2.5R
            if not pos.get("half_sold") and high_today >= pos["take_profit_price"]:
                half_price = pos["take_profit_price"]
                half_shares = pos["original_shares"] * 0.5
                half_cost = pos["cost"] * 0.5
                cash += half_shares * half_price * _fx(pos.get("market", "KR"), day) * (1 - COST_PER_SIDE)
                trade = _close_trade(
                    ticker, pos, day, half_price, "rr_half_+2.5R",
                    ticker_reports, record_full_trades, None,
                    shares_override=half_shares, cost_override=half_cost,
                )
                trades.append(trade)
                # Keep only runner half
                pos["shares"] = pos["original_shares"] * 0.5
                pos["cost"] = pos["cost"] * 0.5
                pos["half_sold"] = True

            # Stop: low today touched stop
            elif low_today <= pos["stop"]:
                pending_exits[ticker] = "rr_stop"

        nav = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
        nav_series.append((day.isoformat(), nav))

    last_day = calendar[-1]
    for ticker, pos in list(positions.items()):
        trades.append(_close_trade(ticker, pos, last_day, pos["last_close"], "데이터_종료_미청산",
                                   ticker_reports, record_full_trades, None))

    return _compute_result(nav_series, trades, START_CAPITAL, label, open_positions=positions)


# ──────────────────────────────────────────────────────────────────────────────
# Chandelier parametric runner (for Optuna tuning)
# ──────────────────────────────────────────────────────────────────────────────

def run_chandelier_parametric(
    prices: dict[str, pd.DataFrame],
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
    label: str,
    atr_period: int,
    atr_mult: float,
    max_positions: int,
    ticker_reports: dict[str, list[dict]] | None = None,
    record_full_trades: bool = False,
) -> dict:
    """Chandelier with configurable ATR period, multiplier, and max positions."""
    START_CAPITAL = 100_000_000
    cash = float(START_CAPITAL)
    positions: dict[str, dict] = {}
    nav_series: list[tuple[str, float]] = []
    trades: list[dict] = []
    pending_exits: set[str] = set()

    pending_entries = build_pending_entries(reports, calendar, consensus_only=False, prices=prices)

    for day in calendar:
        day_ts = pd.Timestamp(day)
        cash *= (1 + CASH_YIELD_DAILY)  # v18: 유휴 현금 일복리 이자 (연 3% MMF 프록시)

        to_exit = [t for t in sorted(pending_exits) if t in positions]
        for ticker in to_exit:
            pos = positions[ticker]
            q = _get_quote(prices, ticker, day)
            if q is None or float(q["open"]) <= 0:
                continue
            exit_price = float(q["open"])
            cash += pos["shares"] * exit_price * _fx(pos.get("market", "KR"), day) * (1 - COST_PER_SIDE)
            trades.append(_close_trade(ticker, pos, day, exit_price, f"chandelier_ATR{atr_mult}",
                                       ticker_reports, record_full_trades, None))
            del positions[ticker]
            pending_exits.discard(ticker)

        if day in pending_entries:
            nav_now = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
            slots = max_positions - len(positions)
            candidates = list({t: (t, s, nc) for t, s, nc in pending_entries[day] if t not in positions}.values())
            for ticker, source, n_clubs in candidates[:slots]:
                pos, cash = _try_enter(ticker, source, n_clubs, day, prices, positions, cash, nav_now, ticker_reports)
                if pos is not None:
                    df = prices[ticker]
                    atr_val = asof_value(_atr_column(df, atr_period), day - dt.timedelta(days=1))

                    stop = pos["entry_price"] - atr_mult * atr_val if atr_val else pos["entry_price"] * 0.75
                    pos["stop"] = stop
                    positions[ticker] = pos

        for ticker, pos in list(positions.items()):
            df = prices.get(ticker)
            if df is None or not _has_day(df, day_ts):
                continue
            close = _px(df, day_ts, "close")
            pos["last_close"] = close
            pos["fx"] = _fx(pos.get("market", "KR"), day)
            pos["highest"] = max(pos.get("highest", close), close)
            atr_val = asof_value(_atr_column(df, atr_period), day)

            if atr_val:
                new_stop = pos["highest"] - atr_mult * atr_val
                pos["stop"] = max(pos.get("stop", 0.0), new_stop)
            if pos.get("stop") and close < pos["stop"] and ticker not in pending_exits:
                pending_exits.add(ticker)

        nav = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
        nav_series.append((day.isoformat(), nav))

    last_day = calendar[-1]
    for ticker, pos in list(positions.items()):
        trades.append(_close_trade(ticker, pos, last_day, pos["last_close"], "데이터_종료_미청산",
                                   ticker_reports, record_full_trades, None))

    return _compute_result(nav_series, trades, START_CAPITAL, label, open_positions=positions)


# ──────────────────────────────────────────────────────────────────────────────
# Optuna robust optimization for chandelier family
# Search space: ATR period {20,42,63}, ATR mult [2.5,7], max_positions {10,20,30}
# Objective: evaluated on IS only, 2-fold (2020-21, 2022-23)
#   = min(fold1_sharpe, fold2_sharpe) − 0.1 × |fold1_sharpe − fold2_sharpe|
# ~120 trials, TPE, fixed seed.
# OOS evaluated ONCE after best params selected.
# ──────────────────────────────────────────────────────────────────────────────

OPTUNA_N_TRIALS = 120
OPTUNA_SEED = 42
# IS folds
IS_FOLD1_START = dt.date(2020, 1, 1)
IS_FOLD1_END   = dt.date(2021, 12, 31)
IS_FOLD2_START = dt.date(2022, 1, 1)
IS_FOLD2_END   = dt.date(2023, 12, 31)

# ── Expensive-stage caches (Optuna / SPO) ─────────────────────────────────────
# Both stages are fully deterministic given (prices, reports, calendar, FX,
# seeds). We fingerprint those inputs; on an exact match the previously found
# result is reused — identical to re-running. `--retune` forces a re-run.
OPTUNA_CACHE_PATH = GOLD_CACHE / "optuna_cache.json"
SPO_CACHE_PATH = GOLD_CACHE / "spo_cache.pkl"
S_WEIGHTS_CACHE_PATH = GOLD_CACHE / "s_weights_cache.pkl"
# Bump when _hrp_weights/_msharpe_weights/_mincvar_weights logic changes
S_WEIGHTS_CODE_TAG = "s_v1"
# Bump when load_prices indicator math or chandelier objective logic changes —
# the dataset fingerprint hashes raw OHLCV only, so indicator-logic edits would
# otherwise replay a stale Optuna cache silently (S-weights has the same guard).
OPTUNA_CODE_TAG = "optuna_v1"
FORCE_RETUNE = False  # set by --retune CLI flag


def _dataset_fingerprint(
    prices: dict[str, pd.DataFrame],
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
) -> str:
    """SHA-256 over every input that can influence backtest results:
    report stream, calendar span, raw OHLCV of every ticker, and USDKRW."""
    h = hashlib.sha256()
    # Strategy accounting changes invalidate tuning/rebalance caches even on identical market data.
    h.update(b"execution-v2-prior-close-entry-atr\n")
    for rdate, ticker, source, n_clubs in reports:
        h.update(f"{rdate.isoformat()}|{ticker}|{source}|{n_clubs}\n".encode())
    h.update(f"cal|{calendar[0].isoformat()}|{calendar[-1].isoformat()}|{len(calendar)}\n".encode())
    for tk in sorted(prices):
        df = prices[tk]
        h.update(tk.encode())
        h.update(df.index.values.tobytes())
        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                h.update(np.ascontiguousarray(df[col].to_numpy()).tobytes())
    if fx._USDKRW is not None:
        h.update(fx._USDKRW.index.values.tobytes())
        h.update(np.ascontiguousarray(fx._USDKRW.to_numpy()).tobytes())
    return h.hexdigest()

def _chandelier_fold_sharpe(
    nav_df: pd.Series,
    fold_start: dt.date,
    fold_end: dt.date,
) -> float:
    mask = (nav_df.index.date >= fold_start) & (nav_df.index.date <= fold_end)
    sub = nav_df[mask]
    if len(sub) < 20:
        return -9.0
    ret = sub.pct_change().dropna()
    sd = float(ret.std())
    # 정확히 0뿐 아니라 1e-18 같은 수치적 0도 차단 — Inf 샤프가 목적함수를 오염시키지 않게
    if not math.isfinite(sd) or sd < 1e-12:
        return -9.0
    return float(ret.mean() / sd * math.sqrt(252))


def run_optuna_chandelier(
    prices: dict[str, pd.DataFrame],
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
    ticker_reports: dict[str, list[dict]] | None = None,
    dataset_fingerprint: str | None = None,
) -> dict:
    """Run Optuna optimization on chandelier family. Returns best params + IS/OOS metrics.

    The 120-trial search is skipped when (dataset fingerprint, search space,
    seed, trial count) match a cached run — TPE with a fixed seed on identical
    inputs reproduces the same best params, so reusing them is result-identical.
    """
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
    except ImportError:
        print("  WARNING: optuna not installed — skipping D+ optimization", flush=True)
        return {"skipped": True, "reason": "optuna not installed"}

    # IS calendar only
    is_calendar = [d for d in calendar if IS_FOLD1_START <= d <= IS_FOLD2_END]

    search_space_tag = "atr_period[20,42,63]|atr_mult[2.5,7.0,0.25]|max_positions[10,20,30]"
    cache_key = None
    if dataset_fingerprint is not None:
        cache_key = hashlib.sha256(
            f"{dataset_fingerprint}|{search_space_tag}|seed={OPTUNA_SEED}|trials={OPTUNA_N_TRIALS}"
            f"|code={OPTUNA_CODE_TAG}".encode()
        ).hexdigest()

    best: dict | None = None
    best_val: float | None = None
    if cache_key is not None and not FORCE_RETUNE and OPTUNA_CACHE_PATH.exists():
        try:
            cache = json.loads(OPTUNA_CACHE_PATH.read_text(encoding="utf-8"))
            entry = cache.get(cache_key)
            if entry:
                best = entry["best_params"]
                best_val = float(entry["best_objective_raw"])
                print(f"  Optuna cache HIT (dataset unchanged) — best params {best}  "
                      f"obj={best_val:.3f}  [--retune to force re-search]", flush=True)
        except Exception:
            best = None

    if best is None:
        def objective(trial: "optuna.Trial") -> float:
            atr_period = trial.suggest_categorical("atr_period", [20, 42, 63])
            # Discretised grid: step=0.25 → values land on {2.50, 2.75, 3.00, …, 7.00}
            atr_mult   = trial.suggest_float("atr_mult", 2.5, 7.0, step=0.25)
            max_pos    = trial.suggest_categorical("max_positions", [10, 20, 30])

            # Need ATR for non-standard periods — compute on the fly if needed
            # atr_period=20 uses atr20, atr_period=42 uses atr (default), 63 we reuse atr (closest)
            result = run_chandelier_parametric(
                prices, reports, is_calendar, "optuna_trial",
                atr_period=atr_period, atr_mult=atr_mult, max_positions=max_pos,
                ticker_reports=ticker_reports, record_full_trades=False,
            )
            nav_df = result["nav_df"]
            s1 = _chandelier_fold_sharpe(nav_df, IS_FOLD1_START, IS_FOLD1_END)
            s2 = _chandelier_fold_sharpe(nav_df, IS_FOLD2_START, IS_FOLD2_END)
            # Objective: worst-fold sharpe with instability penalty
            return min(s1, s2) - 0.1 * abs(s1 - s2)

        sampler = optuna.samplers.TPESampler(seed=OPTUNA_SEED)
        study = optuna.create_study(direction="maximize", sampler=sampler)
        print(f"  Running Optuna ({OPTUNA_N_TRIALS} trials)...", flush=True)
        study.optimize(objective, n_trials=OPTUNA_N_TRIALS, show_progress_bar=False)

        best = study.best_params
        # Round floats to 2 decimal places for deterministic reporting
        best = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in best.items()}
        best_val = study.best_value
        print(f"  Best params (discretised): {best}  obj={best_val:.3f}", flush=True)

        if cache_key is not None:
            try:
                cache = {}
                if OPTUNA_CACHE_PATH.exists():
                    cache = json.loads(OPTUNA_CACHE_PATH.read_text(encoding="utf-8"))
                cache[cache_key] = {
                    "best_params": best,
                    "best_objective_raw": best_val,
                    "search_space": search_space_tag,
                    "seed": OPTUNA_SEED,
                    "n_trials": OPTUNA_N_TRIALS,
                    "cached_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                }
                OPTUNA_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
                OPTUNA_CACHE_PATH.write_text(
                    json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
            except Exception as e:
                print(f"  WARNING: optuna cache write failed: {e}", flush=True)

    # Evaluate best config on IS (both folds together) for reporting
    is_result = run_chandelier_parametric(
        prices, reports, is_calendar, "D+_optuna_IS",
        atr_period=best["atr_period"], atr_mult=best["atr_mult"],
        max_positions=best["max_positions"],
        ticker_reports=ticker_reports, record_full_trades=False,
    )
    is_nav = is_result["nav_df"]
    fold1_sharpe = _chandelier_fold_sharpe(is_nav, IS_FOLD1_START, IS_FOLD1_END)
    fold2_sharpe = _chandelier_fold_sharpe(is_nav, IS_FOLD2_START, IS_FOLD2_END)

    # Evaluate ONCE on OOS (untouched)
    oos_calendar = [d for d in calendar if d >= OOS_START]
    # Need to run full sim from start to get correct positions for OOS equity
    full_result = run_chandelier_parametric(
        prices, reports, calendar, "D+_chandelier_optuna",
        atr_period=best["atr_period"], atr_mult=best["atr_mult"],
        max_positions=best["max_positions"],
        ticker_reports=ticker_reports, record_full_trades=True,
    )
    oos_sharpe_val = full_result.get("out_of_sample", {}).get("sharpe")
    is_sharpe_val  = full_result.get("in_sample", {}).get("sharpe")

    print(f"  D+ Optuna: IS sharpe={is_sharpe_val}  fold1={fold1_sharpe:.2f}  fold2={fold2_sharpe:.2f}  OOS sharpe={oos_sharpe_val}", flush=True)

    full_result["optuna_meta"] = {
        "best_params": best,
        "best_objective": round(best_val, 4),
        "fold1_sharpe": round(fold1_sharpe, 3),
        "fold2_sharpe": round(fold2_sharpe, 3),
        "n_trials": OPTUNA_N_TRIALS,
        "search_space": {
            "atr_period": [20, 42, 63],
            "atr_mult": {"min": 2.5, "max": 7.0, "step": 0.25},
            "max_positions": [10, 20, 30],
        },
        "methodology": (
            "IS 2-폴드 (2020-21, 2022-23), 목적함수 = min(fold1, fold2) − 0.1×|fold1−fold2|. "
            f"TPE sampler, seed={OPTUNA_SEED}, {OPTUNA_N_TRIALS} trials. OOS는 1회만 평가. "
            "탐색공간 이산화: atr_mult step=0.25 (2-decimal grid). 파라미터 소수점 2자리 반올림."
        ),
    }
    return full_result


# ──────────────────────────────────────────────────────────────────────────────
# Strategy L: 민리버전 (Connors RSI-2 mean reversion)
# Universe: report-validated within last 18 months.
# Entry: RSI(2) < 10 AND close > 200MA (checked daily).
# Exit: RSI(2) > 70 OR 10 trading days.
# Reference: Connors & Alvarez "Short-Term Trading Strategies That Work" (2009).
# ──────────────────────────────────────────────────────────────────────────────

RSI2_ENTRY_THRESHOLD = 10.0   # RSI(2) < 10 to enter
RSI2_EXIT_THRESHOLD  = 70.0   # RSI(2) > 70 to exit
RSI2_MAX_HOLD_DAYS   = 10     # trading days
RSI2_UNIVERSE_MONTHS = 18     # report valid for 18 months

def run_rsi2_mean_reversion(
    prices: dict[str, pd.DataFrame],
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
    label: str,
    ticker_reports: dict[str, list[dict]] | None = None,
    record_full_trades: bool = False,
) -> dict:
    """
    Connors RSI-2 평균회귀. 유니버스: 최근 18개월 내 매수 리포트.
    진입: RSI(2) < 10 AND close > 200MA.
    청산: RSI(2) > 70 OR 10 거래일.
    """
    START_CAPITAL = 100_000_000
    cash = float(START_CAPITAL)
    positions: dict[str, dict] = {}
    nav_series: list[tuple[str, float]] = []
    trades: list[dict] = []
    pending_exits: dict[str, str] = {}

    # Build universe: per-day set of valid tickers (report within 18mo)
    # For efficiency: precompute per ticker the valid date range
    ticker_valid: dict[str, list[tuple[dt.date, dt.date]]] = {}
    for rdate, ticker, source, n_clubs in reports:
        expire = rdate + dt.timedelta(days=int(RSI2_UNIVERSE_MONTHS * 30.44))
        ticker_valid.setdefault(ticker, []).append((rdate, expire))

    # RSI-2 entry queue: signals detected at close of prev day, filled at next open.
    rsi2_entry_queue: list[tuple[str, str, int]] = []  # (ticker, source, n_clubs)

    for day in calendar:
        day_ts = pd.Timestamp(day)
        cash *= (1 + CASH_YIELD_DAILY)  # v18: 유휴 현금 일복리 이자 (연 3% MMF 프록시)

        # Execute pending exits at open
        to_exit = [t for t in list(pending_exits) if t in positions]
        for ticker in to_exit:
            pos = positions[ticker]
            reason = pending_exits.pop(ticker)
            q = _get_quote(prices, ticker, day)
            if q is None or float(q["open"]) <= 0:
                pending_exits[ticker] = reason
                continue
            exit_price = float(q["open"])
            cash += pos["shares"] * exit_price * _fx(pos.get("market", "KR"), day) * (1 - COST_PER_SIDE)
            trades.append(_close_trade(ticker, pos, day, exit_price, reason,
                                       ticker_reports, record_full_trades, None))
            del positions[ticker]

        # Execute queued RSI-2 entries at today's open (signal detected yesterday)
        if rsi2_entry_queue:
            nav_now = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
            slots = MAX_POSITIONS - len(positions)
            for ticker, source, n_clubs in rsi2_entry_queue:
                if slots <= 0:
                    break
                if ticker in positions:
                    continue
                pos, cash = _try_enter(ticker, source, n_clubs, day, prices, positions, cash, nav_now, ticker_reports)
                if pos is not None:
                    pos["hold_days_remaining"] = RSI2_MAX_HOLD_DAYS
                    positions[ticker] = pos
                    slots -= 1
            rsi2_entry_queue = []

        # Update positions + check exit conditions (end-of-day)
        for ticker, pos in list(positions.items()):
            df = prices.get(ticker)
            if df is None or not _has_day(df, day_ts):
                continue
            close = _px(df, day_ts, "close")
            pos["last_close"] = close
            pos["fx"] = _fx(pos.get("market", "KR"), day)
            pos["hold_days_remaining"] = pos.get("hold_days_remaining", RSI2_MAX_HOLD_DAYS) - 1

            if ticker in pending_exits:
                continue
            rsi2_val = float(df["rsi2"].asof(day_ts)) if "rsi2" in df.columns else 50.0
            if rsi2_val > RSI2_EXIT_THRESHOLD:
                pending_exits[ticker] = "rsi2_exit_>70"
            elif pos["hold_days_remaining"] <= 0:
                pending_exits[ticker] = "rsi2_10day_만기"

        # End-of-day entry SIGNAL scan — deferred to next bar's open
        new_rsi2_entries: list[tuple[str, str, int]] = []
        nav_now_eod = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
        for ticker, ranges in ticker_valid.items():
            if ticker in positions:
                continue
            # Check if any report range covers today
            valid = any(start <= day <= end for start, end in ranges)
            if not valid:
                continue
            df = prices.get(ticker)
            if df is None or not _has_day(df, day_ts):
                continue
            close = _px(df, day_ts, "close")
            rsi2_val = float(df["rsi2"].asof(day_ts)) if "rsi2" in df.columns else 50.0
            ma200_val = asof_value(df["ma200"], day)
            if rsi2_val < RSI2_ENTRY_THRESHOLD and ma200_val > 0 and close > ma200_val:
                tr_list = (ticker_reports or {}).get(ticker, [])
                past_tr = [r for r in tr_list if r["report_date"] <= day]
                n_clubs = len({r["school"] for r in past_tr}) if past_tr else 1
                source = past_tr[-1]["source_file"] if past_tr else ""
                source = Path(source).name if source else ""
                new_rsi2_entries.append((ticker, source, n_clubs))

        rsi2_entry_queue = new_rsi2_entries

        nav = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
        nav_series.append((day.isoformat(), nav))

    last_day = calendar[-1]
    for ticker, pos in list(positions.items()):
        trades.append(_close_trade(ticker, pos, last_day, pos["last_close"], "데이터_종료_미청산",
                                   ticker_reports, record_full_trades, None))

    return _compute_result(nav_series, trades, START_CAPITAL, label, open_positions=positions)


# ──────────────────────────────────────────────────────────────────────────────
# Strategy M: 단기 리버설 (Short-Term Reversal)
# Universe: report-validated stocks (buy report within last 18mo).
# Monthly: buy bottom quintile by trailing 1-month return, hold 1 month.
# Equal weight. Factor-zoo short-term reversal.
# Reference: Jegadeesh (1990), Lehmann (1990), Debondt & Thaler (1985).
# ──────────────────────────────────────────────────────────────────────────────

def run_short_term_reversal(
    prices: dict[str, pd.DataFrame],
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
    label: str,
    ticker_reports: dict[str, list[dict]] | None = None,
    record_full_trades: bool = False,
) -> dict:
    """
    단기 리버설. 월초 리밸런싱: 유니버스 중 직전 1개월 수익률 하위 20% 매수, 1개월 보유.
    """
    START_CAPITAL = 100_000_000
    cash = float(START_CAPITAL)
    positions: dict[str, dict] = {}
    nav_series: list[tuple[str, float]] = []
    trades: list[dict] = []

    REVERSAL_UNIVERSE_MONTHS = 18
    ticker_valid: dict[str, list[tuple[dt.date, dt.date]]] = {}
    for rdate, ticker, source, n_clubs in reports:
        expire = rdate + dt.timedelta(days=int(REVERSAL_UNIVERSE_MONTHS * 30.44))
        ticker_valid.setdefault(ticker, []).append((rdate, expire))

    # Build month-first and month-end days
    cal_s = pd.Series(calendar)
    month_firsts: set[dt.date] = set(
        cal_s.groupby(cal_s.apply(lambda d: (d.year, d.month))).first().values
    )
    month_ends: set[dt.date] = set(
        cal_s.groupby(cal_s.apply(lambda d: (d.year, d.month))).last().values
    )

    # Reversal rebalance queue: bottom-quintile tickers computed at PREVIOUS month-end
    # close, bought at month-first open (eliminates same-bar close→open lookahead).
    # Format: list of (ticker, source, n_clubs)
    reversal_entry_queue: list[tuple[str, str, int]] = []

    for day in calendar:
        day_ts = pd.Timestamp(day)
        cash *= (1 + CASH_YIELD_DAILY)  # v18: 유휴 현금 일복리 이자 (연 3% MMF 프록시)

        if day in month_firsts:
            # Step 1: Close all existing positions at today's open (month-first open)
            # Only clear positions that are successfully exited to avoid cash leakage.
            exited: set[str] = set()
            for ticker in list(positions.keys()):
                pos = positions[ticker]
                q = _get_quote(prices, ticker, day)
                if q is None or float(q["open"]) <= 0:
                    # No valid open price — carry position forward, exit at close
                    close_val = pos.get("last_close", pos["entry_price"])
                    if close_val > 0:
                        cash += pos["shares"] * close_val * (1 - COST_PER_SIDE)
                        trades.append(_close_trade(ticker, pos, day, close_val, "reversal_1mo_만기_no_open",
                                                   ticker_reports, record_full_trades, None))
                        exited.add(ticker)
                    continue
                exit_price = float(q["open"])
                cash += pos["shares"] * exit_price * _fx(pos.get("market", "KR"), day) * (1 - COST_PER_SIDE)
                trades.append(_close_trade(ticker, pos, day, exit_price, "reversal_1mo_만기",
                                           ticker_reports, record_full_trades, None))
                exited.add(ticker)
            for t in exited:
                del positions[t]

            # Step 2: Execute the bottom-quintile queue computed at previous month-end
            if reversal_entry_queue:
                nav_now = cash
                if nav_now <= 0:
                    nav_now = float(START_CAPITAL)
                slots = MAX_POSITIONS
                for ticker, source, n_clubs in reversal_entry_queue[:slots]:
                    if ticker in positions:
                        continue
                    pos, cash = _try_enter(ticker, source, n_clubs, day, prices, positions, cash, nav_now, ticker_reports)
                    if pos is not None and pos.get("cost", 0) > 0:
                        positions[ticker] = pos
                reversal_entry_queue = []

        # Update last_close
        for ticker, pos in positions.items():
            df = prices.get(ticker)
            if df is not None and _has_day(df, day_ts):
                pos["last_close"] = _px(df, day_ts, "close")
                pos["fx"] = _fx(pos.get("market", "KR"), day)

        # End-of-month: compute bottom-quintile ranking from today's close for next
        # month-first execution (point-in-time: signal at month-end close, fill next open).
        if day in month_ends:
            one_mo_ago = day - dt.timedelta(days=30)
            candidates_m: list[tuple[float, str, str, int]] = []
            for ticker, ranges in ticker_valid.items():
                if ticker in positions:
                    continue
                valid = any(start <= day <= end for start, end in ranges)
                if not valid:
                    continue
                df = prices.get(ticker)
                if df is None or not _has_day(df, day_ts):
                    continue
                close_now = _px(df, day_ts, "close")
                close_1mo = asof_value(df["close"], one_mo_ago)
                if close_1mo <= 0:
                    continue
                ret_1mo = close_now / close_1mo - 1
                tr_list = (ticker_reports or {}).get(ticker, [])
                past_tr = [r for r in tr_list if r["report_date"] <= day]
                n_clubs_val = len({r["school"] for r in past_tr}) if past_tr else 1
                source_val = Path(past_tr[-1]["source_file"]).name if past_tr and past_tr[-1].get("source_file") else ""
                candidates_m.append((ret_1mo, ticker, source_val, n_clubs_val))

            if len(candidates_m) >= 5:
                candidates_m.sort(key=lambda x: x[0])
                n_quintile = max(1, len(candidates_m) // 5)
                reversal_entry_queue = [
                    (ticker, source, nc)
                    for _, ticker, source, nc in candidates_m[:n_quintile]
                ]

        nav = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
        nav_series.append((day.isoformat(), nav))

    last_day = calendar[-1]
    for ticker, pos in list(positions.items()):
        trades.append(_close_trade(ticker, pos, last_day, pos["last_close"], "데이터_종료_미청산",
                                   ticker_reports, record_full_trades, None))

    return _compute_result(nav_series, trades, START_CAPITAL, label, open_positions=positions)


# ──────────────────────────────────────────────────────────────────────────────
# Strategy N: 52주 고가 근접 (George & Hwang 2004)
# Enter on report if price ≥ 85% of 52w high.
# Exit when price < 70% of 52w high (monthly check).
# Reference: George & Hwang (2004) "The 52-Week High and Momentum Investing".
# ──────────────────────────────────────────────────────────────────────────────

N52W_ENTRY_PCT  = 0.85   # enter if price ≥ 85% of 52w high
N52W_EXIT_PCT   = 0.70   # exit if price < 70% of 52w high

def run_52w_high_proximity(
    prices: dict[str, pd.DataFrame],
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
    label: str,
    ticker_reports: dict[str, list[dict]] | None = None,
    record_full_trades: bool = False,
) -> dict:
    """
    52주 고가 근접 (George & Hwang 2004).
    진입: 리포트 당일 close ≥ 52w high × 85%.
    청산: 월말 체크 — close < 52w high × 70% → 다음 거래일 시가 청산.
    """
    START_CAPITAL = 100_000_000
    cash = float(START_CAPITAL)
    positions: dict[str, dict] = {}
    nav_series: list[tuple[str, float]] = []
    trades: list[dict] = []
    pending_exits: set[str] = set()

    # Filter reports: only those where entry condition met on report day
    n52_pending: dict[dt.date, list[tuple[str, str, int]]] = {}
    for rdate, ticker, source, n_clubs in reports:
        df = prices.get(ticker)
        if df is None:
            continue
        close_on_report = asof_value(df["close"], rdate)
        hi52w_on_report  = asof_value(df["hi52w"], rdate)
        if hi52w_on_report <= 0 or close_on_report <= 0:
            continue
        if close_on_report >= N52W_ENTRY_PCT * hi52w_on_report:
            entry_day = first_trading_day_after(rdate, calendar)
            if entry_day:
                n52_pending.setdefault(entry_day, []).append((ticker, source, n_clubs))

    cal_s = pd.Series(calendar)
    month_ends: set[dt.date] = set(
        cal_s.groupby(cal_s.apply(lambda d: (d.year, d.month))).last().values
    )

    for day in calendar:
        day_ts = pd.Timestamp(day)
        cash *= (1 + CASH_YIELD_DAILY)  # v18: 유휴 현금 일복리 이자 (연 3% MMF 프록시)

        # Execute pending exits
        to_exit = [t for t in sorted(pending_exits) if t in positions]
        for ticker in to_exit:
            pos = positions[ticker]
            q = _get_quote(prices, ticker, day)
            if q is None or float(q["open"]) <= 0:
                continue
            exit_price = float(q["open"])
            cash += pos["shares"] * exit_price * _fx(pos.get("market", "KR"), day) * (1 - COST_PER_SIDE)
            trades.append(_close_trade(ticker, pos, day, exit_price, "52w_hi_exit",
                                       ticker_reports, record_full_trades, None))
            del positions[ticker]
            pending_exits.discard(ticker)

        # Execute entries
        if day in n52_pending:
            nav_now = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
            slots = MAX_POSITIONS - len(positions)
            candidates = list({t: (t, s, nc) for t, s, nc in n52_pending[day] if t not in positions}.values())
            for ticker, source, n_clubs in candidates[:slots]:
                pos, cash = _try_enter(ticker, source, n_clubs, day, prices, positions, cash, nav_now, ticker_reports)
                if pos is not None:
                    positions[ticker] = pos

        # Update last_close
        for ticker, pos in positions.items():
            df = prices.get(ticker)
            if df is not None and _has_day(df, day_ts):
                pos["last_close"] = _px(df, day_ts, "close")
                pos["fx"] = _fx(pos.get("market", "KR"), day)

        # Monthly exit check: close < 70% of 52w high
        if day in month_ends:
            for ticker, pos in list(positions.items()):
                df = prices.get(ticker)
                if df is None:
                    continue
                close = pos["last_close"]
                hi52w_val = asof_value(df["hi52w"], day)
                if hi52w_val > 0 and close < N52W_EXIT_PCT * hi52w_val:
                    pending_exits.add(ticker)

        nav = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
        nav_series.append((day.isoformat(), nav))

    last_day = calendar[-1]
    for ticker, pos in list(positions.items()):
        trades.append(_close_trade(ticker, pos, last_day, pos["last_close"], "데이터_종료_미청산",
                                   ticker_reports, record_full_trades, None))

    return _compute_result(nav_series, trades, START_CAPITAL, label, open_positions=positions)


# ──────────────────────────────────────────────────────────────────────────────
# Strategy O: MTT (alpha16 이식) — Minervini Trend Template
# Universe: report-validated stocks (18-month window, same as L/M).
# RS computation: cross-sectional percentile across OUR price-warehouse universe
#   weighted_rs = 3m×0.5 + 6m×0.3 + 12m×0.2  (alpha16 RobustOpt KRX params)
# MTT filter (all must hold):
#   close > 50MA > 150MA > 200MA
#   200MA rising vs 1-month ago
#   close ≥ 1.9 × 52w low  (alpha16 KRX param)
#   close ≥ 0.95 × 52w high  (alpha16 KRX param)
#   RS ≥ 80
# Buy: RS ≥ 79 (+ MTT active)
# Exits (R-multiple chain, alpha16 KRX params):
#   Initial stop: −8% from entry (1R)
#   Breakeven stop: move to entry at +1R gain
#   Trailing 6% from highest: activated at +1.5R
#   Take profit: +3.5R
#   RS < 82 exit after min 8 holding days
#   Max 115 holding days
# Position sizing: 5%/20-slot equal-weight (skip Kelly for comparability).
#
# PROVENANCE DISCLOSURE: alpha16 RobustOpt KRX params were tuned on the full
# KRX universe, NOT on our report-validated data. These params are used as-is.
# Position sizing kept at our 5%/20-slot convention for comparability; Kelly
# sizing noted as future work.
# ──────────────────────────────────────────────────────────────────────────────

# alpha16 RobustOpt KRX parameters (from config.py / optimize.py)
MTT_STOP_PCT              = 0.08    # initial stop = −8% (1R)
MTT_BE_AT_R               = 1.0    # move stop to breakeven at +1R
MTT_TRAIL_PCT             = 0.06   # 6% trailing from highest
MTT_TRAIL_ACTIVATE_R      = 1.5    # trailing activates at +1.5R
MTT_TAKE_PROFIT_R         = 3.5    # take profit at +3.5R
MTT_RS_BUY_THRESHOLD      = 79     # buy when RS ≥ 79
MTT_RS_MTT_THRESHOLD      = 80     # MTT requires RS ≥ 80
MTT_RS_EXIT_THRESHOLD     = 82     # RS < 82 → exit (post min hold days)
MTT_RS_EXIT_MIN_HOLD_DAYS = 8      # min holding days before RS exit triggers
MTT_MAX_HOLD_DAYS         = 115    # max hold days
MTT_PRICE_FROM_LOW_MULT   = 1.90   # price ≥ 1.9× 52w low (alpha16 KRX)
MTT_PRICE_FROM_HIGH_MULT  = 0.95   # price ≥ 0.95× 52w high (alpha16 KRX)
MTT_UNIVERSE_MONTHS       = 18     # report valid 18 months (same as L/M)

# RS lookback in trading days (alpha16 defaults)
MTT_RS_3M  = 63
MTT_RS_6M  = 126
MTT_RS_12M = 252
MTT_RS_W3  = 0.5
MTT_RS_W6  = 0.3
MTT_RS_W12 = 0.2


# Shared per-day RS cache — _compute_rs_percentiles is a pure function of
# (prices, day) and the same `prices` dict is used for the entire run, so the
# O(MTT) and Q(깡토) strategies can share one computation per day.
_RS_DAY_CACHE: dict[dt.date, dict[str, float]] = {}


def _compute_rs_percentiles(
    prices: dict[str, pd.DataFrame],
    day: dt.date,
) -> dict[str, float]:
    """
    Cross-sectional RS percentile for all tickers in prices on a given day.
    weighted_rs = rank_pct(ret_3m)×0.5 + rank_pct(ret_6m)×0.3 + rank_pct(ret_12m)×0.2
    Returns {ticker: rs_score 0..99} — empty dict if insufficient data.
    """
    cached = _RS_DAY_CACHE.get(day)
    if cached is not None:
        return cached

    day_ts = pd.Timestamp(day)
    day_63  = day - dt.timedelta(days=int(MTT_RS_3M  * 1.45))   # ~91 cal days
    day_126 = day - dt.timedelta(days=int(MTT_RS_6M  * 1.45))   # ~183 cal days
    day_252 = day - dt.timedelta(days=int(MTT_RS_12M * 1.45))   # ~365 cal days

    rets: dict[str, tuple[float, float, float]] = {}
    for ticker, df in prices.items():
        close_now = _px(df, day_ts, "close")
        if close_now is None or close_now <= 0:
            continue
        c3  = asof_value(df["close"], day_63)
        c6  = asof_value(df["close"], day_126)
        c12 = asof_value(df["close"], day_252)
        if c3 <= 0 or c6 <= 0 or c12 <= 0:
            continue
        rets[ticker] = (close_now / c3 - 1, close_now / c6 - 1, close_now / c12 - 1)

    if len(rets) < 5:
        _RS_DAY_CACHE[day] = {}
        return {}

    tickers = list(rets.keys())
    r3  = [rets[t][0] for t in tickers]
    r6  = [rets[t][1] for t in tickers]
    r12 = [rets[t][2] for t in tickers]
    n = len(tickers)

    def rank_pct(vals: list[float]) -> list[float]:
        sorted_v = sorted(vals)
        return [sorted_v.index(v) / max(n - 1, 1) * 99 for v in vals]

    p3  = rank_pct(r3)
    p6  = rank_pct(r6)
    p12 = rank_pct(r12)

    result = {
        tickers[i]: round(p3[i] * MTT_RS_W3 + p6[i] * MTT_RS_W6 + p12[i] * MTT_RS_W12, 2)
        for i in range(n)
    }
    _RS_DAY_CACHE[day] = result
    return result


def run_mtt_alpha16(
    prices: dict[str, pd.DataFrame],
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
    label: str,
    ticker_reports: dict[str, list[dict]] | None = None,
    record_full_trades: bool = False,
) -> dict:
    """
    MTT (alpha16 이식) — Minervini Trend Template on report-validated universe.
    Universe: any ticker with a buy report within the past 18 months.
    RS: cross-sectional percentile across the full price-warehouse (our tickers).
    Exit: R-multiple chain (initial −8%, BE at +1R, trail-6% at +1.5R, TP +3.5R,
          RS<82 post 8d, max 115d).
    Position sizing: 5%/20-slot equal-weight (Kelly: future work).

    PROVENANCE: alpha16 RobustOpt KRX params tuned on full KRX universe,
    not on our report-validated data.
    """
    START_CAPITAL = 100_000_000
    cash = float(START_CAPITAL)
    positions: dict[str, dict] = {}
    nav_series: list[tuple[str, float]] = []
    trades: list[dict] = []
    pending_exits: dict[str, str] = {}

    # Build eligible pool: per-ticker the date ranges it is valid
    ticker_valid: dict[str, list[tuple[dt.date, dt.date]]] = {}
    for rdate, ticker, source, n_clubs in reports:
        expire = rdate + dt.timedelta(days=int(MTT_UNIVERSE_MONTHS * 30.44))
        ticker_valid.setdefault(ticker, []).append((rdate, expire))

    # Cache of daily RS scores — recomputed once per day lazily
    _rs_cache: dict[dt.date, dict[str, float]] = {}

    def get_rs(day: dt.date) -> dict[str, float]:
        if day not in _rs_cache:
            _rs_cache[day] = _compute_rs_percentiles(prices, day)
        return _rs_cache[day]

    # MTT entry queue: tickers whose signal was detected at close of prev day,
    # to be filled at open of the current day (eliminates same-bar lookahead).
    # Format: list of (ticker, source_val, n_clubs_val, rs_val_at_signal)
    mtt_entry_queue: list[tuple[str, str, int, float]] = []

    for day in calendar:
        day_ts = pd.Timestamp(day)
        cash *= (1 + CASH_YIELD_DAILY)  # v18: 유휴 현금 일복리 이자 (연 3% MMF 프록시)

        # Execute pending exits at open
        to_exit = [t for t in list(pending_exits) if t in positions]
        for ticker in to_exit:
            pos = positions[ticker]
            reason = pending_exits.pop(ticker)
            q = _get_quote(prices, ticker, day)
            if q is None or float(q["open"]) <= 0:
                pending_exits[ticker] = reason   # defer
                continue
            exit_price = float(q["open"])
            cash += pos["shares"] * exit_price * _fx(pos.get("market", "KR"), day) * (1 - COST_PER_SIDE)
            trades.append(_close_trade(ticker, pos, day, exit_price, reason,
                                       ticker_reports, record_full_trades, None))
            del positions[ticker]

        # Execute queued MTT entries at today's open (signal was detected yesterday)
        if mtt_entry_queue:
            nav_now = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
            slots = MAX_POSITIONS - len(positions)
            for ticker, source_val, n_clubs_val, rs_val in mtt_entry_queue:
                if slots <= 0:
                    break
                if ticker in positions:
                    continue
                pos, cash = _try_enter(ticker, source_val, n_clubs_val, day, prices, positions,
                                       cash, nav_now, ticker_reports)
                if pos is not None:
                    entry_p = pos["entry_price"]
                    one_r = entry_p * MTT_STOP_PCT
                    pos["stop"] = entry_p - one_r
                    pos["one_r"] = one_r
                    pos["trail_activated"] = False
                    pos["rs_val"] = rs_val
                    pos["hold_days"] = 0
                    pos["entry_reason"] = (
                        f"MTT 템플릿 통과 (close>50MA>150MA>200MA, 200MA 상승, "
                        f"52주저점×1.9↑, 52주고점×0.95↑, RS {rs_val:.0f}≥80) → 익일 시가 진입"
                    )
                    positions[ticker] = pos
                    slots -= 1
            mtt_entry_queue = []

        # RS scores for today (end-of-day close signal generation)
        rs_scores = get_rs(day)

        # End-of-day entry SIGNAL scan — conditions checked at today's close,
        # execution deferred to next bar's open (point-in-time, no same-bar lookahead).
        new_mtt_entries: list[tuple[str, str, int, float]] = []
        for ticker, ranges in ticker_valid.items():
            if ticker in positions:
                continue
            # 18-month validity window
            valid = any(start <= day <= end for start, end in ranges)
            if not valid:
                continue
            df = prices.get(ticker)
            if df is None or not _has_day(df, day_ts):
                continue

            rs_val = rs_scores.get(ticker, 0.0)
            if rs_val < MTT_RS_BUY_THRESHOLD:
                continue

            close = _px(df, day_ts, "close")
            ma50  = asof_value(df["ma50"],  day)
            ma150 = asof_value(df["ma150"], day)
            ma200 = asof_value(df["ma200"], day)
            hi52w = asof_value(df["hi52w"], day)
            lo52w = asof_value(df["lo52w"] if "lo52w" in df.columns else df["close"].rolling(252, min_periods=126).min(), day)

            if any(v <= 0 for v in [ma50, ma150, ma200, hi52w]):
                continue

            # MTT filter (checked at close — point-in-time)
            if not (close > ma50 > ma150 > ma200):
                continue
            # 200MA rising vs 1 month ago
            ma200_1mo = asof_value(df["ma200"], day - dt.timedelta(days=30))
            if ma200_1mo <= 0 or ma200 <= ma200_1mo:
                continue
            # Price ≥ 1.9× 52w low
            if lo52w > 0 and close < MTT_PRICE_FROM_LOW_MULT * lo52w:
                continue
            # Price ≥ 0.95× 52w high
            if close < MTT_PRICE_FROM_HIGH_MULT * hi52w:
                continue
            # RS ≥ 80 (MTT RS gate)
            if rs_val < MTT_RS_MTT_THRESHOLD:
                continue

            # Get source/n_clubs from most recent report
            tr_list = (ticker_reports or {}).get(ticker, [])
            past_tr = [r for r in tr_list if r["report_date"] <= day]
            n_clubs_val = len({r["school"] for r in past_tr}) if past_tr else 1
            source_val = Path(past_tr[-1]["source_file"]).name if past_tr and past_tr[-1].get("source_file") else ""

            new_mtt_entries.append((ticker, source_val, n_clubs_val, rs_val))

        # Queue for execution at next day's open
        mtt_entry_queue = new_mtt_entries

        # Update positions + check exit conditions
        for ticker, pos in list(positions.items()):
            df = prices.get(ticker)
            if df is None or not _has_day(df, day_ts):
                continue
            close = _px(df, day_ts, "close")
            pos["last_close"] = close
            pos["fx"] = _fx(pos.get("market", "KR"), day)
            pos["highest"] = max(pos.get("highest", close), close)
            pos["hold_days"] = pos.get("hold_days", 0) + 1

            if ticker in pending_exits:
                continue

            entry_p = pos["entry_price"]
            one_r   = pos["one_r"]
            highest = pos["highest"]
            hold_days = pos["hold_days"]

            # --- Stop management ---
            gain = close - entry_p
            gain_r = gain / one_r if one_r > 0 else 0.0

            # Breakeven stop: move to entry at +1R
            if gain_r >= MTT_BE_AT_R:
                pos["stop"] = max(pos.get("stop", 0.0), entry_p)

            # Trailing 6%: activated at +1.5R
            if gain_r >= MTT_TRAIL_ACTIVATE_R:
                pos["trail_activated"] = True

            if pos.get("trail_activated"):
                trail_stop = highest * (1 - MTT_TRAIL_PCT)
                pos["stop"] = max(pos.get("stop", 0.0), trail_stop)

            # --- Exit checks ---
            # Initial stop hit
            if close < pos["stop"]:
                pending_exits[ticker] = "mtt_stop"
                continue

            # Take profit +3.5R
            if gain_r >= MTT_TAKE_PROFIT_R:
                pending_exits[ticker] = "mtt_take_profit_3.5R"
                continue

            # RS < 82 exit after min 8 holding days
            rs_val_today = rs_scores.get(ticker, 0.0)
            pos["rs_val"] = rs_val_today
            if hold_days >= MTT_RS_EXIT_MIN_HOLD_DAYS and rs_val_today < MTT_RS_EXIT_THRESHOLD:
                pending_exits[ticker] = "mtt_rs_exit_<82"
                continue

            # Max 115 days
            if hold_days >= MTT_MAX_HOLD_DAYS:
                pending_exits[ticker] = "mtt_max_115d"
                continue

        nav = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
        nav_series.append((day.isoformat(), nav))

    last_day = calendar[-1]
    for ticker, pos in list(positions.items()):
        trades.append(_close_trade(ticker, pos, last_day, pos["last_close"], "데이터_종료_미청산",
                                   ticker_reports, record_full_trades, None))

    return _compute_result(nav_series, trades, START_CAPITAL, label, open_positions=positions)


# ──────────────────────────────────────────────────────────────────────────────
# Strategy P: 딥바이 샹들리에 하이브리드
# Entry = G 딥바이 (price falls ≥20% below publication-day close within 6mo).
# Scale-in = ONE add-on buy (same 5% slot size) if price falls another 10% below
#   the first entry price WHILE the 6-month thesis window is still open.
#   Combined into a single position with averaged cost; stop tracked from combined
#   highest-high.
# Exit = Optuna-tuned chandelier ATR trailing stop only (no profit cap).
#   Uses D+ Optuna best params if available at runtime; otherwise falls back to
#   ATR(42)×5 (D default).
# Reference: 딥바이 진입은 좋았으나 청산이 큰 winner를 못 먹었다 → trailing stop
#   only, no target cap.
# ──────────────────────────────────────────────────────────────────────────────

# P strategy params
P_DIP_THRESHOLD      = 0.20    # ≥20% below pub-day close → first entry
P_ADDON_DROP         = 0.10    # additional 10% below first entry → scale-in
P_DIP_WINDOW_DAYS    = 180     # 6-month watch window from report date
P_ATR_PERIOD         = 42      # ATR period (same as D default; overridable)
P_ATR_MULT_DEFAULT   = 5.0     # fallback ATR mult if Optuna result not available


def run_deepbuy_chandelier(
    prices: dict[str, pd.DataFrame],
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
    label: str,
    ticker_reports: dict[str, list[dict]] | None = None,
    record_full_trades: bool = False,
    atr_mult: float = P_ATR_MULT_DEFAULT,
    max_positions: int = MAX_POSITIONS,
) -> dict:
    """
    P 딥바이 샹들리에 하이브리드.

    진입: 발간일 종가 대비 ≥20% 하락 (6개월 내), 익일 시가 매수 (5% 슬롯).
    추가매수: 최초 진입가 대비 추가 10% 하락이 발생하면 동일 슬롯에 5% 1회 추가.
      → 평균 단가 재계산, 포지션 합산. 6개월 thesis 창 내에서만 허용.
    청산: 최고점 기준 ATR 트레일링 스탑 (D+ Optuna 파라미터, 기본 ATR42×5).
      타겟가 캡 없음 — winner를 충분히 보유.
    생존 편향 주석: 프라이스 파일이 존재하는 종목만 유니버스에 포함됨.
    """
    START_CAPITAL = 100_000_000
    cash = float(START_CAPITAL)
    positions: dict[str, dict] = {}
    nav_series: list[tuple[str, float]] = []
    trades: list[dict] = []
    pending_exits: set[str] = set()

    # Build per-ticker dip-watch queue (same structure as G)
    dip_watch: dict[str, list[dict]] = {}
    for rdate, ticker, source, n_clubs in reports:
        if rdate < SIM_START - dt.timedelta(days=P_DIP_WINDOW_DAYS):
            continue
        df = prices.get(ticker)
        if df is None:
            continue
        pub_close = asof_value(df["close"], rdate)
        if pub_close <= 0:
            continue
        tr_list = (ticker_reports or {}).get(ticker, [])
        past_tr = [x for x in tr_list if x["report_date"] <= rdate]
        dn = past_tr[-1]["display_name"] if past_tr else ticker
        market = past_tr[0].get("market", "KR") if past_tr else "KR"
        dip_watch.setdefault(ticker, []).append({
            "report_date": rdate,
            "pub_close": pub_close,
            "expire_date": rdate + dt.timedelta(days=P_DIP_WINDOW_DAYS),
            "display_name": dn,
            "n_clubs": n_clubs,
            "source": source,
            "market": market,
        })

    # dip_entry_queue: (ticker, watch) pairs detected at close, filled at next open
    dip_entry_queue: list[tuple[str, dict]] = []
    # addon_queue: tickers where scale-in was triggered at close, filled at next open
    addon_queue: list[str] = []

    for day in calendar:
        day_ts = pd.Timestamp(day)
        cash *= (1 + CASH_YIELD_DAILY)  # v18: 유휴 현금 일복리 이자 (연 3% MMF 프록시)

        # ── Open-of-day: execute exits ─────────────────────────────────────
        to_exit = [t for t in sorted(pending_exits) if t in positions]
        for ticker in to_exit:
            pos = positions[ticker]
            q = _get_quote(prices, ticker, day)
            if q is None or float(q["open"]) <= 0:
                continue
            exit_price = float(q["open"])
            cash += pos["shares"] * exit_price * _fx(pos.get("market", "KR"), day) * (1 - COST_PER_SIDE)
            trades.append(_close_trade(ticker, pos, day, exit_price,
                                       f"p_chandelier_ATR{atr_mult}",
                                       ticker_reports, record_full_trades, None))
            del positions[ticker]
            pending_exits.discard(ticker)

        # ── Open-of-day: execute deferred scale-in add-ons ─────────────────
        if addon_queue:
            nav_now = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
            for ticker in addon_queue:
                pos = positions.get(ticker)
                if pos is None:
                    continue
                df = prices.get(ticker)
                if df is None or not _has_day(df, day_ts):
                    continue
                addon_price = _px(df, day_ts, "open")
                if addon_price <= 0:
                    continue
                addon_budget = min(nav_now * POSITION_WEIGHT, cash)
                if addon_budget < nav_now * POSITION_WEIGHT * 0.5:
                    continue
                # v19 FX: KRW budget / (USD price × USDKRW) for US positions
                addon_fx = _fx(pos.get("market", "KR"), day)
                addon_shares = addon_budget * (1 - COST_PER_SIDE) / (addon_price * addon_fx)
                cash -= addon_budget
                # Merge into existing position: weighted avg entry, combined shares/cost
                old_shares = pos["shares"]
                old_cost   = pos["cost"]
                new_shares = old_shares + addon_shares
                new_cost   = old_cost + addon_budget
                avg_entry  = (old_shares * pos["entry_price"] + addon_shares * addon_price) / new_shares
                pos["shares"]      = new_shares
                pos["cost"]        = new_cost
                pos["entry_price"] = avg_entry   # blended avg for P&L tracking
                # Stop is reset from combined highest-high (already tracked)
                atr_val = asof_value(df["atr"], day - dt.timedelta(days=1))
                if atr_val:
                    new_stop = pos["highest"] - atr_mult * atr_val
                    pos["stop"] = max(pos.get("stop", 0.0), new_stop)
            addon_queue = []

        # ── Open-of-day: execute new dip entries queued from previous close ─
        if dip_entry_queue:
            nav_now = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
            slots = max_positions - len(positions)
            for ticker, watch in dip_entry_queue[:slots]:
                if ticker in positions:
                    continue
                df = prices.get(ticker)
                if df is None or not _has_day(df, day_ts):
                    continue
                entry_price = _px(df, day_ts, "open")
                if entry_price <= 0:
                    continue
                budget = min(nav_now * POSITION_WEIGHT, cash)
                if budget < nav_now * POSITION_WEIGHT * 0.5:
                    continue
                shares = budget * (1 - COST_PER_SIDE) / (entry_price * _fx(watch["market"], day))
                cash -= budget
                atr_val = asof_value(df["atr"], day - dt.timedelta(days=1))
                stop = entry_price - atr_mult * atr_val if atr_val else entry_price * 0.75
                positions[ticker] = {
                    "shares": shares,
                    "entry_price": entry_price,
                    "entry_date": day,
                    "cost": budget,
                    "last_close": entry_price,
                    "highest": entry_price,
                    "stop": stop,
                    "source": watch["source"],
                    "n_clubs": watch["n_clubs"],
                    "display_name": watch["display_name"],
                    "market": watch["market"],
                    "target_price": None,
                    "addon_done": False,
                    "addon_trigger": entry_price * (1 - P_ADDON_DROP),
                    "thesis_expire": watch["expire_date"],
                    "first_entry_price": entry_price,
                    "entry_reason": (
                        f"발간일({watch['report_date'].isoformat()}) 종가 대비 "
                        f"−{int(P_DIP_THRESHOLD * 100)}% 하락 도달 → 익일 시가 진입 "
                        f"(딥바이; 추가 −{int(P_ADDON_DROP * 100)}% 시 1회 스케일인)"
                    ),
                }
            dip_entry_queue = []

        # ── End-of-day: scan dip-watch for new first-entry triggers ────────
        new_dip_entries: list[tuple[str, dict]] = []
        for ticker, watches in dip_watch.items():
            if ticker in positions:
                continue
            df = prices.get(ticker)
            if df is None or not _has_day(df, day_ts):
                continue
            close_today = _px(df, day_ts, "close")
            for watch in watches:
                if day < watch["report_date"] or day > watch["expire_date"]:
                    continue
                dip_level = watch["pub_close"] * (1 - P_DIP_THRESHOLD)
                if close_today <= dip_level:
                    new_dip_entries.append((ticker, watch))
                    break
        dip_entry_queue = new_dip_entries

        # ── End-of-day: update positions, check chandelier stop + scale-in ─
        new_addon: list[str] = []
        for ticker, pos in list(positions.items()):
            df = prices.get(ticker)
            if df is None or not _has_day(df, day_ts):
                continue
            close = _px(df, day_ts, "close")
            pos["last_close"] = close
            pos["fx"] = _fx(pos.get("market", "KR"), day)
            pos["highest"] = max(pos.get("highest", close), close)

            # Ratchet chandelier stop from highest-high
            atr_val = asof_value(df["atr"], day)
            if atr_val:
                new_stop = pos["highest"] - atr_mult * atr_val
                pos["stop"] = max(pos.get("stop", 0.0), new_stop)

            # Chandelier stop breach → exit at next open
            if pos.get("stop") and close < pos["stop"] and ticker not in pending_exits:
                pending_exits.add(ticker)
                continue

            # Scale-in trigger: price drops P_ADDON_DROP below first entry,
            # thesis window still open, add-on not yet done.
            if (not pos.get("addon_done")
                    and close <= pos.get("addon_trigger", 0.0)
                    and day <= pos.get("thesis_expire", day - dt.timedelta(days=1))):
                pos["addon_done"] = True   # mark immediately to prevent re-trigger
                new_addon.append(ticker)

        addon_queue = new_addon

        nav = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
        nav_series.append((day.isoformat(), nav))

    # Force-close remaining at last bar
    last_day = calendar[-1]
    for ticker, pos in list(positions.items()):
        trades.append(_close_trade(ticker, pos, last_day, pos["last_close"], "데이터_종료_미청산",
                                   ticker_reports, record_full_trades, None))

    return _compute_result(nav_series, trades, START_CAPITAL, label, open_positions=positions)


# ──────────────────────────────────────────────────────────────────────────────
# Legacy Strategy (kept for sensitivity table): Immediate entry, fixed hold
# (supports all v5 flags for variant research)
# ──────────────────────────────────────────────────────────────────────────────

def run_immediate_hold(
    prices: dict[str, pd.DataFrame],
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
    hold_months: int,
    consensus_only: bool = False,
    label: str = "immediate",
    ticker_reports: dict[str, list[dict]] | None = None,
    consensus_window: int | None = None,
    upside_weighted: bool = False,
    target_exit: bool = False,
    record_full_trades: bool = False,
) -> dict:
    """v5 compatible fixed-hold runner (consensus window variants, upside weighting, target exit)."""
    START_CAPITAL = 100_000_000
    cash = float(START_CAPITAL)
    positions: dict[str, dict] = {}
    nav_series: list[tuple[str, float]] = []
    trades: list[dict] = []

    by_report_date: dict[dt.date, list[tuple[str, str, int]]] = {}
    if consensus_window is not None and ticker_reports is not None:
        all_report_dates = sorted({r[0] for r in reports})
        for rdate in all_report_dates:
            for r_rdate, r_ticker, r_source, _ in reports:
                if r_rdate != rdate:
                    continue
                tr = ticker_reports.get(r_ticker, [])
                past = [x for x in tr if x["report_date"] <= rdate]
                window_start = rdate - dt.timedelta(days=consensus_window)
                in_window = [x for x in past if x["report_date"] >= window_start]
                schools_in_window = {x["school"] for x in in_window}
                n_clubs_window = len(schools_in_window)
                if consensus_only and n_clubs_window < 2:
                    continue
                by_report_date.setdefault(rdate, []).append((r_ticker, r_source, n_clubs_window))
    else:
        for rdate, ticker, source, n_clubs in reports:
            if consensus_only and n_clubs < 2:
                continue
            by_report_date.setdefault(rdate, []).append((ticker, source, n_clubs))

    pending_entries: dict[dt.date, list[tuple[str, str, int]]] = {}
    scheduled_exits: dict[str, dt.date] = {}
    target_prices: dict[str, float] = {}

    for rdate, items in by_report_date.items():
        entry_day = first_trading_day_after(rdate, calendar)
        if entry_day:
            pending_entries.setdefault(entry_day, []).extend(items)

    for day in calendar:
        day_ts = pd.Timestamp(day)
        cash *= (1 + CASH_YIELD_DAILY)  # v18: 유휴 현금 일복리 이자 (연 3% MMF 프록시)

        if target_exit:
            for ticker, pos in list(positions.items()):
                tp = target_prices.get(ticker)
                if tp is None:
                    continue
                df = prices.get(ticker)
                if df is None:
                    continue
                if day_ts in df.index:
                    high_today = float(df.loc[day_ts].get("high", df.loc[day_ts]["close"]))
                    if high_today >= tp and ticker not in scheduled_exits:
                        scheduled_exits[ticker] = day

        to_exit = [t for t, exit_d in list(scheduled_exits.items()) if exit_d <= day and t in positions]
        for ticker in to_exit:
            pos = positions[ticker]
            df = prices.get(ticker)
            if df is None:
                continue
            q = df.loc[day_ts] if day_ts in df.index else None
            if q is None or float(q["open"]) <= 0:
                continue
            price = float(q["open"])
            proceeds = pos["shares"] * price * _fx(pos.get("market", "KR"), day) * (1 - COST_PER_SIDE)
            cash += proceeds
            exit_reason = "목표가_도달" if target_exit and price >= (target_prices.get(ticker, 0)) else f"{hold_months}개월_만기"
            if day == calendar[-1]:
                exit_reason = "데이터_종료"
            trade: dict = {
                "ticker": ticker,
                "market": pos.get("market", "KR"),
                "display_name": pos.get("display_name", ticker),
                "source": pos["source"],
                "n_clubs": pos["n_clubs"],
                "entry_date": pos["entry_date"].isoformat(),
                "exit_date": day.isoformat(),
                "entry": round(pos["entry_price"], 4),
                "exit": round(price, 4),
                "return_pct": round((proceeds / pos["cost"] - 1) * 100, 2),
                "days": (day - pos["entry_date"]).days,
                "exit_reason": exit_reason,
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
            trades.append(trade)
            del positions[ticker]
            if ticker in scheduled_exits:
                del scheduled_exits[ticker]
            if ticker in target_prices:
                del target_prices[ticker]

        if day in pending_entries:
            nav_now = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
            slots = MAX_POSITIONS - len(positions)
            candidates = [(t, s, nc) for t, s, nc in pending_entries[day] if t not in positions]
            seen: set[str] = set()
            deduped = []
            for t, s, nc in candidates:
                if t not in seen:
                    seen.add(t)
                    deduped.append((t, s, nc))

            for ticker, source, n_clubs in deduped[:slots]:
                if ticker not in prices:
                    continue
                df = prices[ticker]
                if day_ts not in df.index:
                    continue
                q = df.loc[day_ts]
                if float(q["open"]) <= 0:
                    continue

                weight = POSITION_WEIGHT
                if upside_weighted and ticker_reports is not None:
                    tr_list = ticker_reports.get(ticker, [])
                    past = [x for x in tr_list if x["report_date"] < day and x["stated_upside_pct"] is not None]
                    if past:
                        avg_upside = sum(x["stated_upside_pct"] for x in past) / len(past)
                        scale = max(0.5, min(2.0, avg_upside / 30.0))
                        weight = POSITION_WEIGHT * scale

                budget = min(nav_now * weight, cash)
                if budget < nav_now * POSITION_WEIGHT * 0.5:
                    continue
                price = float(q["open"])
                shares = budget * (1 - COST_PER_SIDE) / price
                cash -= budget

                display_name = ticker
                tp = None
                market = "KR"
                if ticker_reports is not None:
                    tr_list = ticker_reports.get(ticker, [])
                    past_tr = [x for x in tr_list if x["report_date"] < day]
                    if past_tr:
                        latest = max(past_tr, key=lambda x: x["report_date"])
                        display_name = latest["display_name"]
                        tps = [x["target_price"] for x in past_tr if x["target_price"]]
                        tp = max(tps) if tps else None
                        market = past_tr[0].get("market", "KR")

                positions[ticker] = {
                    "shares": shares,
                    "entry_price": price,
                    "entry_date": day,
                    "cost": budget,
                    "last_close": price,
                    "source": source,
                    "n_clubs": n_clubs,
                    "display_name": display_name,
                    "market": market,
                }
                if tp:
                    target_prices[ticker] = tp

                exit_target = months_later(day, hold_months)
                exit_day = first_trading_day_on_or_after(exit_target, calendar)
                if exit_day:
                    scheduled_exits[ticker] = exit_day

        for ticker, pos in positions.items():
            df = prices.get(ticker)
            if df is not None and _has_day(df, day_ts):
                pos["last_close"] = _px(df, day_ts, "close")
                pos["fx"] = _fx(pos.get("market", "KR"), day)

        nav = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
        nav_series.append((day.isoformat(), nav))

    last_day = calendar[-1]
    for ticker, pos in list(positions.items()):
        trade = {
            "ticker": ticker,
            "market": pos.get("market", "KR"),
            "display_name": pos.get("display_name", ticker),
            "source": pos["source"],
            "n_clubs": pos["n_clubs"],
            "entry_date": pos["entry_date"].isoformat() if hasattr(pos["entry_date"], "isoformat") else str(pos["entry_date"]),
            "exit_date": last_day.isoformat(),
            "entry": round(pos["entry_price"], 4),
            "exit": round(pos["last_close"], 4),
            "return_pct": round((pos["shares"] * pos["last_close"] / pos["cost"] - 1) * 100, 2),
            "days": (last_day - pos["entry_date"]).days,
            "exit_reason": "데이터_종료_미청산",
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
        trades.append(trade)

    return _compute_result(nav_series, trades, START_CAPITAL, label, open_positions=positions)


# ──────────────────────────────────────────────────────────────────────────────
# Legacy Strategy E: v3 breakout + ATR ratchet (kept for sensitivity table)
# ──────────────────────────────────────────────────────────────────────────────

def find_signal(df: pd.DataFrame, report_date: dt.date) -> dt.date | None:
    window = df[df.index >= pd.Timestamp(report_date)]
    if len(window) <= MIN_DAYS_BEFORE_SIGNAL:
        return None
    closes = window["close"]
    running_max = closes.cummax().shift(1)
    cutoff = pd.Timestamp(report_date) + pd.Timedelta(days=SIGNAL_WINDOW_DAYS)
    for ts in closes.index[MIN_DAYS_BEFORE_SIGNAL:]:
        if ts > cutoff:
            return None
        if closes.loc[ts] > running_max.loc[ts]:
            return ts.date()
    return None


def load_regime() -> pd.Series | None:
    path = PRICE_DIR / "IDX_KOSPI.csv"
    if not path.exists():
        return None
    idx = pd.read_csv(path, index_col=0, parse_dates=True).sort_index()
    if idx.empty or "close" not in idx:
        return None
    ma = idx["close"].rolling(REGIME_MA, min_periods=REGIME_MA // 2).mean()
    return idx["close"] > ma


def run_breakout_backtest(
    prices: dict[str, pd.DataFrame],
    by_signal_date: dict[dt.date, list[tuple[str, str, int]]],
    calendar: list[dt.date],
    atr_mult: float,
    regime: pd.Series | None,
    use_ratchet: bool = True,
    label: str = "breakout",
) -> dict:
    START_CAPITAL = 100_000_000
    cash = float(START_CAPITAL)
    positions: dict[str, dict] = {}
    nav_series: list[tuple[str, float]] = []
    trades: list[dict] = []
    pending_buys: list[tuple[str, str, int]] = []
    pending_sells: list[str] = []

    def quote(ticker: str, day: dt.date) -> pd.Series | None:
        df = prices.get(ticker)
        if df is None:
            return None
        ts = pd.Timestamp(day)
        return df.loc[ts] if ts in df.index else None

    def effective_atr_mult(pos: dict, atr_mult: float) -> float:
        if not use_ratchet:
            return atr_mult
        gain = pos["highest"] / pos["entry_price"] - 1
        if gain >= RATCHET_THRESHOLD_2:
            return atr_mult + 2
        if gain >= RATCHET_THRESHOLD_1:
            return atr_mult + 1
        return atr_mult

    for day in calendar:
        deferred_sells: list[str] = []
        for ticker in pending_sells:
            pos = positions.get(ticker)
            if not pos:
                continue
            q = quote(ticker, day)
            if q is None or q["open"] <= 0:
                deferred_sells.append(ticker)
                continue
            positions.pop(ticker)
            price = float(q["open"])
            proceeds = pos["shares"] * price * _fx(pos.get("market", "KR"), day) * (1 - COST_PER_SIDE)
            cash += proceeds
            trades.append({
                "ticker": ticker,
                "market": pos.get("market", "KR"),
                "display_name": ticker,
                "source": pos["source"],
                "n_clubs": pos["n_clubs"],
                "entry_date": pos["entry_date"].isoformat(),
                "exit_date": day.isoformat(),
                "entry": round(pos["entry_price"], 4),
                "exit": round(price, 4),
                "return_pct": round((proceeds / pos["cost"] - 1) * 100, 2),
                "days": (day - pos["entry_date"]).days,
                "exit_reason": "ATR_트레일링_스탑",
            })
        pending_sells = deferred_sells

        if pending_buys:
            signal_cutoff = day - dt.timedelta(days=1)
            regime_ok = True
            if regime is not None:
                value = regime.asof(pd.Timestamp(signal_cutoff))
                regime_ok = bool(value) if pd.notna(value) else False
            nav_now = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
            slots = MAX_POSITIONS - len(positions)
            candidates = [(t, s, nc) for t, s, nc in pending_buys if t not in positions]
            if regime_ok and slots > 0 and candidates:
                ranked = sorted(
                    candidates,
                    key=lambda item: (
                        item[2] >= 2,
                        asof_value(prices[item[0]]["sharpe90"], signal_cutoff),
                    ),
                    reverse=True,
                )
                for ticker, source, n_clubs in ranked[:slots]:
                    q = quote(ticker, day)
                    if q is None or q["open"] <= 0:
                        continue
                    budget = min(nav_now * POSITION_WEIGHT, cash)
                    if budget < nav_now * POSITION_WEIGHT * 0.5:
                        continue
                    price = float(q["open"])
                    shares = budget * (1 - COST_PER_SIDE) / price
                    cash -= budget
                    atr = asof_value(prices[ticker]["atr"], signal_cutoff)
                    positions[ticker] = {
                        "shares": shares,
                        "entry_price": price,
                        "entry_date": day,
                        "cost": budget,
                        "highest": price,
                        "stop": price - atr_mult * atr if atr else price * 0.85,
                        "last_close": price,
                        "source": source,
                        "n_clubs": n_clubs,
                        "market": "KR",
                    }
        pending_buys = []

        for ticker, pos in positions.items():
            q = quote(ticker, day)
            if q is None:
                continue
            close = float(q["close"])
            pos["last_close"] = close
            pos["fx"] = _fx(pos.get("market", "KR"), day)
            pos["highest"] = max(pos["highest"], close)
            atr = asof_value(prices[ticker]["atr"], day)
            if atr:
                eff_mult = effective_atr_mult(pos, atr_mult)
                pos["stop"] = max(pos["stop"], pos["highest"] - eff_mult * atr)
            if close < pos["stop"] and ticker not in pending_sells:
                pending_sells.append(ticker)

        pending_buys = by_signal_date.get(day, [])

        nav = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
        nav_series.append((day.isoformat(), nav))

    return _compute_result(nav_series, trades, START_CAPITAL, label, open_positions=positions)


# ──────────────────────────────────────────────────────────────────────────────
# Strategy Q: 깡토 추세추종 (Korean trend-following blogger system)
#
# 시장 신호등:
#   초록(시장유닛 2) = KOSPI close > 200MA AND 50MA rising (vs 20일 전)
#   빨강(시장유닛 1) = 그 외
# 종목 유닛 = 1.  총 유닛 = 종목유닛 × 시장유닛 (1 or 2).
# 점진적 베팅: 포지션 +3R 도달 후 같은 티커 패밀리 다음 진입에 +1 종목유닛 (최대 3유닛).
# 유닛 사이즈 = 총자본 / 20.  Max 2% Rule: 단일 포지션 리스크 ≤ equity × 2%.
#
# 진입 (단독 커버 포함, 18mo 유효 유니버스):
#   RS 퍼센타일(MTT 방식) ≥ KOSPI RS AND
#   close = 60d high AND volume ≥ 1.5 × 20d avg volume.
#   체결: 익일 시가.
#
# 스탑/청산 (1R = entry × 8%):
#   초기 스탑: entry − 1R (−8%)
#   +1R 시 스탑 → breakeven
#   +1.5R 시 트레일 고점 − 8% 활성화
#   +3R 시 절반 익절 (나머지는 트레일 지속)
#   편도 비용 0.3%
# ──────────────────────────────────────────────────────────────────────────────

Q_STOP_PCT          = 0.08   # 1R = 8%
Q_BE_R              = 1.0    # move stop to BE at +1R
Q_TRAIL_ACTIVATE_R  = 1.5    # trail high−8% activates at +1.5R
Q_HALF_EXIT_R       = 3.0    # take half at +3R
Q_MAX_UNIT_ADD      = 3      # max 3 total units after progressive betting
Q_UNIVERSE_MONTHS   = 18
Q_VOL_MULT          = 1.5    # volume ≥ 1.5× 20d avg
Q_BREAKOUT_DAYS     = 60     # 60d high breakout


def _q_market_units(kospi: pd.Series, day: dt.date) -> int:
    """시장 신호등: 초록=2유닛, 빨강=1유닛."""
    kospi_close = asof_value(kospi, day)
    if kospi_close <= 0:
        return 1
    # 200MA of KOSPI — compute on the fly using rolling
    idx = kospi.index
    day_ts = pd.Timestamp(day)
    sub = kospi[idx <= day_ts]
    if len(sub) < 100:
        return 1
    ma200 = float(sub.iloc[-200:].mean()) if len(sub) >= 200 else float(sub.mean())
    # 50MA — current vs 20 days ago
    ma50_now = float(sub.iloc[-50:].mean()) if len(sub) >= 50 else float(sub.mean())
    sub_20ago = kospi[idx <= day_ts - pd.Timedelta(days=20)]
    if len(sub_20ago) < 50:
        return 1
    ma50_20ago = float(sub_20ago.iloc[-50:].mean()) if len(sub_20ago) >= 50 else float(sub_20ago.mean())
    if kospi_close > ma200 and ma50_now > ma50_20ago:
        return 2
    return 1


def run_kangto_trend(
    prices: dict[str, pd.DataFrame],
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
    label: str,
    ticker_reports: dict[str, list[dict]] | None = None,
    record_full_trades: bool = False,
    kospi: pd.Series | None = None,
) -> dict:
    """
    Q 깡토 추세추종.
    진입: RS ≥ KOSPI RS AND close = 60d high AND volume ≥ 1.5× 20d avg.
    스탑: −8% 초기 / BE at +1R / 트레일 고점−8% at +1.5R / 절반 +3R.
    유닛 사이징: capital/20, max 2% risk rule.
    """
    START_CAPITAL = 100_000_000
    cash = float(START_CAPITAL)
    equity = float(START_CAPITAL)
    positions: dict[str, dict] = {}
    nav_series: list[tuple[str, float]] = []
    trades: list[dict] = []
    pending_exits: dict[str, str] = {}

    # Track closed trade returns per ticker family (for progressive betting)
    # ticker -> list of return_r (profit/1R multiple)
    ticker_family_profit: dict[str, list[float]] = {}

    # Build eligible pool
    ticker_valid: dict[str, list[tuple[dt.date, dt.date]]] = {}
    for rdate, ticker, source, n_clubs in reports:
        expire = rdate + dt.timedelta(days=int(Q_UNIVERSE_MONTHS * 30.44))
        ticker_valid.setdefault(ticker, []).append((rdate, expire))

    # Entry signal queue: detected at close, filled at next open
    q_entry_queue: list[tuple[str, str, int, float]] = []  # ticker, source, n_clubs, rs_val

    for day in calendar:
        day_ts = pd.Timestamp(day)
        cash *= (1 + CASH_YIELD_DAILY)  # v18: 유휴 현금 일복리 이자 (연 3% MMF 프록시)
        equity = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())

        # Execute pending exits at open
        to_exit = [t for t in list(pending_exits) if t in positions]
        for ticker in to_exit:
            pos = positions[ticker]
            reason = pending_exits.pop(ticker)
            q = _get_quote(prices, ticker, day)
            if q is None or float(q["open"]) <= 0:
                pending_exits[ticker] = reason
                continue
            exit_price = float(q["open"])
            proceeds = pos["shares"] * exit_price * _fx(pos.get("market", "KR"), day) * (1 - COST_PER_SIDE)
            cash += proceeds
            one_r = pos.get("one_r", pos["entry_price"] * Q_STOP_PCT)
            ret_r = (exit_price / pos["entry_price"] - 1) * pos["entry_price"] / one_r if one_r > 0 else 0.0
            ticker_family_profit.setdefault(ticker, []).append(ret_r)
            trades.append(_close_trade(ticker, pos, day, exit_price, reason,
                                       ticker_reports, record_full_trades, None))
            del positions[ticker]

        # Execute entry queue at open
        if q_entry_queue:
            nav_now = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
            market_units = _q_market_units(kospi, day - dt.timedelta(days=1)) if kospi is not None else 1  # open fill: prior close only
            slots = MAX_POSITIONS - len(positions)
            for ticker, source_val, n_clubs_val, rs_val in q_entry_queue:
                if slots <= 0:
                    break
                if ticker in positions:
                    continue
                df = prices.get(ticker)
                if df is None or not _has_day(df, day_ts):
                    continue
                entry_price = _px(df, day_ts, "open")
                if entry_price <= 0:
                    continue

                # Resolve market first (needed for FX-correct risk sizing)
                display_name = ticker
                tp = None
                market = "KR"
                if ticker_reports is not None:
                    tr_list = ticker_reports.get(ticker, [])
                    past_tr = [r for r in tr_list if r["report_date"] < day]
                    if past_tr:
                        latest = max(past_tr, key=lambda x: x["report_date"])
                        display_name = latest["display_name"]
                        tps = [x["target_price"] for x in past_tr if x["target_price"]]
                        tp = max(tps) if tps else None
                        market = past_tr[0].get("market", "KR")

                # Progressive betting: +1 종목유닛 if last trade on this ticker was profitable ≥+3R
                family_hist = ticker_family_profit.get(ticker, [])
                extra_unit = 1 if family_hist and family_hist[-1] >= Q_HALF_EXIT_R else 0
                stock_units = min(1 + extra_unit, Q_MAX_UNIT_ADD)
                total_units = stock_units * market_units

                unit_size = nav_now / 20.0
                _entry_krw = entry_price * _fx(market, day)
                one_r = entry_price * Q_STOP_PCT
                # Max 2% risk rule: shrink if needed (risk in KRW terms)
                raw_budget = unit_size * total_units
                shares_raw = raw_budget * (1 - COST_PER_SIDE) / _entry_krw
                actual_risk = shares_raw * one_r * _fx(market, day)
                max_risk = nav_now * 0.02
                if actual_risk > max_risk and actual_risk > 0:
                    scale = max_risk / actual_risk
                    raw_budget *= scale

                budget = min(raw_budget, cash)
                if budget < nav_now * POSITION_WEIGHT * 0.5:
                    continue
                shares = budget * (1 - COST_PER_SIDE) / _entry_krw
                cash -= budget
                stop = entry_price - one_r

                positions[ticker] = {
                    "shares": shares,
                    "original_shares": shares,
                    "entry_price": entry_price,
                    "entry_date": day,
                    "cost": budget,
                    "last_close": entry_price,
                    "highest": entry_price,
                    "stop": stop,
                    "one_r": one_r,
                    "trail_activated": False,
                    "half_sold": False,
                    "source": source_val,
                    "n_clubs": n_clubs_val,
                    "display_name": display_name,
                    "market": market,
                    "target_price": tp,
                    "total_units": total_units,
                    "entry_reason": (
                        f"깡토 추세추종: RS {rs_val:.0f} ≥ KOSPI RS + 60일 신고가 돌파 + "
                        f"거래량 ≥ 1.5×20일평균 → 익일 시가 진입 ({total_units}유닛)"
                    ),
                }
                slots -= 1
            q_entry_queue = []

        # Compute cross-sectional RS for entry signals
        rs_scores = _compute_rs_percentiles(prices, day)
        # KOSPI RS percentile benchmark
        kospi_rs_pct = 50.0  # default
        if kospi is not None and rs_scores:
            # Use _compute_rs_percentiles result for a synthetic KOSPI entry
            # Proxy: KOSPI percentile in the cross-section via direct RS calc
            kospi_close_now = asof_value(kospi, day)
            if kospi_close_now > 0:
                day_63  = day - dt.timedelta(days=91)
                day_126 = day - dt.timedelta(days=183)
                day_252 = day - dt.timedelta(days=365)
                c3  = asof_value(kospi, day_63)
                c6  = asof_value(kospi, day_126)
                c12 = asof_value(kospi, day_252)
                if c3 > 0 and c6 > 0 and c12 > 0:
                    ret3  = kospi_close_now / c3 - 1
                    ret6  = kospi_close_now / c6 - 1
                    ret12 = kospi_close_now / c12 - 1
                    # rank this against all tickers in prices
                    all_scores = list(rs_scores.values())
                    if len(all_scores) >= 5:
                        # Recompute raw rets for each ticker and compare
                        raw_r3:  list[float] = []
                        raw_r6:  list[float] = []
                        raw_r12: list[float] = []
                        tickers_list = list(rs_scores.keys())
                        for t in tickers_list:
                            df = prices.get(t)
                            if df is None:
                                raw_r3.append(0.0); raw_r6.append(0.0); raw_r12.append(0.0)
                                continue
                            day_ts2 = pd.Timestamp(day)
                            if not _has_day(df, day_ts2):
                                raw_r3.append(0.0); raw_r6.append(0.0); raw_r12.append(0.0)
                                continue
                            cn = _px(df, day_ts2, "close")
                            _c3  = asof_value(df["close"], day_63)
                            _c6  = asof_value(df["close"], day_126)
                            _c12 = asof_value(df["close"], day_252)
                            raw_r3.append(cn / _c3 - 1 if _c3 > 0 else 0.0)
                            raw_r6.append(cn / _c6 - 1 if _c6 > 0 else 0.0)
                            raw_r12.append(cn / _c12 - 1 if _c12 > 0 else 0.0)
                        n = len(tickers_list) + 1  # include KOSPI
                        raw_r3.append(ret3); raw_r6.append(ret6); raw_r12.append(ret12)
                        rank3  = sorted(raw_r3).index(ret3)  / max(n - 1, 1) * 99
                        rank6  = sorted(raw_r6).index(ret6)  / max(n - 1, 1) * 99
                        rank12 = sorted(raw_r12).index(ret12) / max(n - 1, 1) * 99
                        kospi_rs_pct = rank3 * MTT_RS_W3 + rank6 * MTT_RS_W6 + rank12 * MTT_RS_W12

        # End-of-day: scan for entry signals
        new_entries: list[tuple[str, str, int, float]] = []
        for ticker, ranges in ticker_valid.items():
            if ticker in positions:
                continue
            valid = any(start <= day <= end for start, end in ranges)
            if not valid:
                continue
            df = prices.get(ticker)
            if df is None or not _has_day(df, day_ts):
                continue

            rs_val = rs_scores.get(ticker, 0.0)
            if rs_val < kospi_rs_pct:
                continue

            close = _px(df, day_ts, "close")
            # 60d high breakout: close == 60d high (close >= rolling 60d high)
            # hi60 precomputed in load_prices — identical rolling op on same data
            hi60 = _fast_asof_raw(df["hi60"], day_ts) if "hi60" in df.columns else 0.0
            if hi60 <= 0 or close < hi60 * 0.999:  # allow tiny float tolerance
                continue
            # Volume ≥ 1.5× 20d avg (vol20avg precomputed in load_prices)
            if "vol20avg" in df.columns and _has_day(df, day_ts):
                vol_now = _px(df, day_ts, "volume")
                vol_20avg = _fast_asof_raw(df["vol20avg"], day_ts)
                if vol_20avg <= 0 or vol_now < Q_VOL_MULT * vol_20avg:
                    continue

            tr_list = (ticker_reports or {}).get(ticker, [])
            past_tr = [r for r in tr_list if r["report_date"] <= day]
            n_clubs_val = len({r["school"] for r in past_tr}) if past_tr else 1
            source_val = Path(past_tr[-1]["source_file"]).name if past_tr and past_tr[-1].get("source_file") else ""
            new_entries.append((ticker, source_val, n_clubs_val, rs_val))

        q_entry_queue = new_entries

        # Update positions + check exits
        for ticker, pos in list(positions.items()):
            df = prices.get(ticker)
            if df is None or not _has_day(df, day_ts):
                continue
            close = _px(df, day_ts, "close")
            high_today = _px(df, day_ts, "high")
            pos["last_close"] = close
            pos["fx"] = _fx(pos.get("market", "KR"), day)
            pos["highest"] = max(pos.get("highest", close), close)

            if ticker in pending_exits:
                continue

            entry_p = pos["entry_price"]
            one_r   = pos["one_r"]
            highest = pos["highest"]
            gain_r  = (close - entry_p) / one_r if one_r > 0 else 0.0
            gain_r_high = (high_today - entry_p) / one_r if one_r > 0 else 0.0

            # Stop management
            # BE at +1R
            if gain_r >= Q_BE_R:
                pos["stop"] = max(pos.get("stop", 0.0), entry_p)
            # Trail high−8% at +1.5R
            if gain_r >= Q_TRAIL_ACTIVATE_R:
                pos["trail_activated"] = True
            if pos.get("trail_activated"):
                trail_stop = highest * (1 - Q_STOP_PCT)
                pos["stop"] = max(pos.get("stop", 0.0), trail_stop)

            # Half-exit at +3R (intraday high check)
            if not pos.get("half_sold") and gain_r_high >= Q_HALF_EXIT_R:
                half_price = entry_p + Q_HALF_EXIT_R * one_r
                half_price = min(half_price, high_today)
                half_shares = pos["original_shares"] * 0.5
                half_cost = pos["cost"] * 0.5
                cash += half_shares * half_price * _fx(pos.get("market", "KR"), day) * (1 - COST_PER_SIDE)
                trade = _close_trade(ticker, pos, day, half_price, "q_half_+3R",
                                     ticker_reports, record_full_trades, None,
                                     shares_override=half_shares, cost_override=half_cost)
                trades.append(trade)
                pos["shares"] = pos["original_shares"] * 0.5
                pos["cost"] = pos["cost"] * 0.5
                pos["half_sold"] = True

            # Stop breach
            if close < pos["stop"]:
                pending_exits[ticker] = "q_stop"

        nav = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
        nav_series.append((day.isoformat(), nav))

    last_day = calendar[-1]
    for ticker, pos in list(positions.items()):
        trades.append(_close_trade(ticker, pos, last_day, pos["last_close"], "데이터_종료_미청산",
                                   ticker_reports, record_full_trades, None))

    return _compute_result(nav_series, trades, START_CAPITAL, label, open_positions=positions)


# ──────────────────────────────────────────────────────────────────────────────
# Strategy R: Kelly 샹들리에 (D+ chandelier rules + Kelly position sizing)
#
# 규칙: D+ Chandelier (Optuna 파라미터) 진입/청산 로직 동일.
# 포지션 사이즈: rolling 최근 40 거래 win_rate + payoff → fractional Kelly.
#   kelly_raw = win_rate − (1−win_rate) / (avg_win/avg_loss)
#   kelly_frac = kelly_raw × safety(0.5), cap 0.25, floor 1%/trade (= equity/100).
# 충분한 거래 이력 없으면 flat 5% fallback.
# 오버레이 그룹.
# ──────────────────────────────────────────────────────────────────────────────

R_KELLY_LOOKBACK = 40
R_KELLY_CAP      = 0.25
R_KELLY_SAFETY   = 0.5
R_KELLY_FLOOR    = 0.01   # 1% of equity floor
R_KELLY_FALLBACK = 0.05   # flat 5% if insufficient history


def _kelly_fraction(closed_returns: list[float]) -> float:
    """
    Fractional Kelly from rolling trade returns (in %).
    Returns fraction of equity to risk (0..R_KELLY_CAP).
    """
    recent = closed_returns[-R_KELLY_LOOKBACK:]
    if len(recent) < 10:
        return R_KELLY_FALLBACK
    wins   = [r for r in recent if r > 0]
    losses = [r for r in recent if r < 0]
    if not wins or not losses:
        return R_KELLY_FALLBACK
    p = len(wins) / len(recent)
    avg_win  = sum(wins)  / len(wins)
    avg_loss = abs(sum(losses) / len(losses))
    if avg_win <= 0 or avg_loss <= 0:
        return R_KELLY_FALLBACK
    b = avg_win / avg_loss
    kelly_raw = p - (1 - p) / b
    kelly_frac = max(0.0, kelly_raw) * R_KELLY_SAFETY
    return max(R_KELLY_FLOOR, min(kelly_frac, R_KELLY_CAP))


def run_kelly_chandelier(
    prices: dict[str, pd.DataFrame],
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
    label: str,
    ticker_reports: dict[str, list[dict]] | None = None,
    record_full_trades: bool = False,
    atr_period: int = ATR_PERIOD,
    atr_mult: float = CHANDELIER_ATR_MULT,
    max_positions: int = MAX_POSITIONS,
) -> dict:
    """
    R Kelly 샹들리에.
    진입/청산: D+ Chandelier 규칙 동일.
    포지션 사이즈: Kelly (rolling 40 trades), cap 0.25, safety 0.5, floor 1%.
    """
    START_CAPITAL = 100_000_000
    cash = float(START_CAPITAL)
    positions: dict[str, dict] = {}
    nav_series: list[tuple[str, float]] = []
    trades: list[dict] = []
    pending_exits: set[str] = set()
    closed_returns: list[float] = []   # running history of closed trade returns (%)

    pending_entries = build_pending_entries(reports, calendar, consensus_only=False, prices=prices)

    for day in calendar:
        day_ts = pd.Timestamp(day)
        cash *= (1 + CASH_YIELD_DAILY)  # v18: 유휴 현금 일복리 이자 (연 3% MMF 프록시)

        # Execute pending exits at open
        to_exit = [t for t in sorted(pending_exits) if t in positions]
        for ticker in to_exit:
            pos = positions[ticker]
            q = _get_quote(prices, ticker, day)
            if q is None or float(q["open"]) <= 0:
                continue
            exit_price = float(q["open"])
            proceeds = pos["shares"] * exit_price * _fx(pos.get("market", "KR"), day) * (1 - COST_PER_SIDE)
            ret_pct = (proceeds / pos["cost"] - 1) * 100
            closed_returns.append(ret_pct)
            cash += proceeds
            trades.append(_close_trade(ticker, pos, day, exit_price,
                                       f"r_chandelier_ATR{atr_mult}",
                                       ticker_reports, record_full_trades, None))
            del positions[ticker]
            pending_exits.discard(ticker)

        # Execute pending entries with Kelly sizing
        if day in pending_entries:
            nav_now = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
            kelly_frac = _kelly_fraction(closed_returns)
            slots = max_positions - len(positions)
            candidates = list({t: (t, s, nc) for t, s, nc in pending_entries[day]
                                if t not in positions}.values())
            for ticker, source, n_clubs in candidates[:slots]:
                df = prices.get(ticker)
                if df is None or not _has_day(df, day_ts):
                    continue
                entry_price = _px(df, day_ts, "open")
                if entry_price <= 0:
                    continue

                display_name = ticker
                tp = None
                market = "KR"
                if ticker_reports is not None:
                    tr_list = ticker_reports.get(ticker, [])
                    past_tr = [r for r in tr_list if r["report_date"] < day]
                    if past_tr:
                        latest = max(past_tr, key=lambda x: x["report_date"])
                        display_name = latest["display_name"]
                        tps = [x["target_price"] for x in past_tr if x["target_price"]]
                        tp = max(tps) if tps else None
                        market = past_tr[0].get("market", "KR")

                budget = min(nav_now * kelly_frac, cash)
                if budget < nav_now * R_KELLY_FLOOR * 0.5:
                    continue
                shares = budget * (1 - COST_PER_SIDE) / (entry_price * _fx(market, day))
                cash -= budget

                atr_val = asof_value(_atr_column(df, atr_period), day - dt.timedelta(days=1))

                stop = entry_price - atr_mult * atr_val if atr_val else entry_price * 0.75

                positions[ticker] = {
                    "shares": shares,
                    "entry_price": entry_price,
                    "entry_date": day,
                    "cost": budget,
                    "last_close": entry_price,
                    "highest": entry_price,
                    "stop": stop,
                    "source": source,
                    "n_clubs": n_clubs,
                    "display_name": display_name,
                    "market": market,
                    "target_price": tp,
                    "entry_reason": _report_trigger_reason(
                        ticker, day, ticker_reports,
                        prefix=f"학회 매수리포트 발간 → 익일 시가 진입 (Kelly 사이징 {kelly_frac * 100:.1f}%)",
                    ),
                }

        # Update positions + check chandelier stop
        for ticker, pos in list(positions.items()):
            df = prices.get(ticker)
            if df is None or not _has_day(df, day_ts):
                continue
            close = _px(df, day_ts, "close")
            pos["last_close"] = close
            pos["fx"] = _fx(pos.get("market", "KR"), day)
            pos["highest"] = max(pos.get("highest", close), close)
            atr_val = asof_value(_atr_column(df, atr_period), day)

            if atr_val:
                new_stop = pos["highest"] - atr_mult * atr_val
                pos["stop"] = max(pos.get("stop", 0.0), new_stop)
            if pos.get("stop") and close < pos["stop"] and ticker not in pending_exits:
                pending_exits.add(ticker)

        nav = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
        nav_series.append((day.isoformat(), nav))

    last_day = calendar[-1]
    for ticker, pos in list(positions.items()):
        trades.append(_close_trade(ticker, pos, last_day, pos["last_close"], "데이터_종료_미청산",
                                   ticker_reports, record_full_trades, None))

    return _compute_result(nav_series, trades, START_CAPITAL, label, open_positions=positions)


# ──────────────────────────────────────────────────────────────────────────────
# Strategy S: 포트폴리오 최적화 (월간 리밸런스)
#
# 유니버스: 18개월 유효 활성 종목 (buy report within 18mo).
# 가격 데이터: trailing 252d daily returns (점-in-time).
# 세 변형:
#   S_hrp    — HRP (Hierarchical Risk Parity): 직접 구현
#              corr distance → single-linkage → quasi-diag reorder → iv-split
#   S_msharpe — max-Sharpe: mean-variance, LedoitWolf 수축
#              (sklearn if available, else λ=0.3 diagonal shrinkage), long-only w≤15%
#   S_mincvar — min-CVaR 95%: scipy.optimize.linprog LP, long-only w≤15%
#
# 월 리밸런스: 월말 종가로 가중치 계산, 다음 거래일 시가 체결.
# 비용: 전체 NAV × 총 턴오버 × 편도 비용.
# ──────────────────────────────────────────────────────────────────────────────

S_UNIVERSE_MONTHS   = 18
S_LOOKBACK_DAYS     = 252
S_MIN_STOCKS        = 3      # minimum stocks to run optimisation
S_MAX_WEIGHT        = 0.15   # max weight per stock
S_SHRINK_LAMBDA     = 0.3    # simple shrinkage fallback


def _hrp_weights(ret_df: pd.DataFrame) -> dict[str, float]:
    """
    Hierarchical Risk Parity weights.
    Hand-rolled: corr distance → single-linkage → quasi-diag → inverse-variance split.
    Returns {ticker: weight}, sums to 1.
    """
    import numpy as np

    tickers = list(ret_df.columns)
    n = len(tickers)
    if n < 2:
        return {t: 1.0 / n for t in tickers}

    corr = ret_df.corr().values
    # Distance matrix: sqrt(0.5 * (1 - corr))
    dist = np.sqrt(np.maximum(0.5 * (1 - corr), 0))

    # Single-linkage clustering (manual)

    def _min_dist_pair(clust: list[list[int]], d: "np.ndarray") -> tuple[int, int]:
        best = float("inf")
        bi, bj = 0, 1
        for ii in range(len(clust)):
            for jj in range(ii + 1, len(clust)):
                # single-linkage: min dist between elements
                d_ij = min(d[a][b] for a in clust[ii] for b in clust[jj])
                if d_ij < best:
                    best = d_ij
                    bi, bj = ii, jj
        return bi, bj

    # Build sorted leaf order via single-linkage
    active = [[i] for i in range(n)]
    while len(active) > 1:
        if len(active) > 50:
            # For large n: use average inter-cluster distance approximation
            best = float("inf")
            bi, bj = 0, 1
            for ii in range(len(active)):
                for jj in range(ii + 1, len(active)):
                    avg_d = float(np.mean([dist[a][b] for a in active[ii] for b in active[jj]]))
                    if avg_d < best:
                        best = avg_d; bi, bj = ii, jj
        else:
            bi, bj = _min_dist_pair(active, dist)
        active[bi] = active[bi] + active[bj]
        active.pop(bj)
    leaf_order: list[int] = active[0]

    # Quasi-diagonal reorder: just use the leaf_order from clustering
    ordered_tickers = [tickers[i] for i in leaf_order]

    # Inverse-variance weights via recursive bisection
    w = {t: 1.0 for t in ordered_tickers}

    def _recursive_bisect(items: list[str]) -> None:
        if len(items) <= 1:
            return
        mid = len(items) // 2
        left = items[:mid]
        right = items[mid:]

        # Cluster variance using current weights and covariance
        sub_l = ret_df[left]
        sub_r = ret_df[right]
        w_l = np.array([w[t] for t in left]); w_l /= w_l.sum()
        w_r = np.array([w[t] for t in right]); w_r /= w_r.sum()
        cov_l = sub_l.cov().values
        cov_r = sub_r.cov().values
        var_l = float(w_l @ cov_l @ w_l)
        var_r = float(w_r @ cov_r @ w_r)
        # NaN 분산(퇴화 공분산)은 `<= 0` 비교를 통과해 가중치를 오염시킨다 — 명시 차단
        if not math.isfinite(var_l + var_r) or var_l + var_r <= 0:
            return

        alpha = 1 - var_l / (var_l + var_r)  # proportion to left cluster
        for t in left:
            w[t] *= alpha
        for t in right:
            w[t] *= (1 - alpha)

        _recursive_bisect(left)
        _recursive_bisect(right)

    _recursive_bisect(ordered_tickers)

    total = sum(w.values())
    if total <= 0:
        return {t: 1.0 / n for t in tickers}
    return {t: w.get(t, 0.0) / total for t in tickers}


def _msharpe_weights(ret_df: pd.DataFrame) -> dict[str, float]:
    """
    Maximum Sharpe weights via mean-variance optimisation.
    Covariance: LedoitWolf (sklearn) or simple shrinkage (λ=0.3).
    Long-only, w ≤ 15%, solved with scipy.optimize.minimize.
    """
    import numpy as np
    from scipy.optimize import minimize

    tickers = list(ret_df.columns)
    n = len(tickers)
    mu = ret_df.mean().values * 252  # annualised

    try:
        from sklearn.covariance import LedoitWolf  # type: ignore
        lw = LedoitWolf().fit(ret_df.values)
        cov = lw.covariance_ * 252
    except Exception:
        raw_cov = ret_df.cov().values * 252
        cov = (1 - S_SHRINK_LAMBDA) * raw_cov + S_SHRINK_LAMBDA * np.diag(np.diag(raw_cov))

    w0 = np.ones(n) / n
    bounds = [(0.0, S_MAX_WEIGHT)] * n
    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]

    def neg_sharpe(w: "np.ndarray") -> float:
        port_ret = float(w @ mu)
        port_var = float(w @ cov @ w)
        if port_var <= 0:
            return 1e9
        return -port_ret / (port_var ** 0.5)

    try:
        res = minimize(neg_sharpe, w0, method="SLSQP", bounds=bounds, constraints=constraints,
                       options={"maxiter": 500, "ftol": 1e-9})
        if res.success:
            w_opt = np.maximum(res.x, 0.0)
            total = w_opt.sum()
            if total > 0:
                w_opt /= total
                return {t: float(w_opt[i]) for i, t in enumerate(tickers)}
    except Exception:
        pass
    return {t: 1.0 / n for t in tickers}


def _mincvar_weights(ret_df: pd.DataFrame) -> dict[str, float]:
    """
    Minimum CVaR (95%) via LP formulation.
    min_{w, z, u}  z + 1/(T*(1−α)) * sum(u_t)
    s.t.  u_t ≥ −(R_t @ w) − z  ∀t
          u_t ≥ 0  ∀t
          sum(w) = 1, 0 ≤ w_i ≤ 15%
    Solved with scipy.optimize.linprog.
    """
    import numpy as np
    from scipy.optimize import linprog

    tickers = list(ret_df.columns)
    n = len(tickers)
    R = ret_df.values  # shape (T, n)
    T = R.shape[0]
    alpha = 0.95

    # Variables: [w(n), z(1), u(T)]
    # Objective: min z + 1/(T*(1-alpha)) * sum(u)
    c = np.zeros(n + 1 + T)
    c[n] = 1.0  # z coefficient
    c[n + 1:] = 1.0 / (T * (1 - alpha))  # u coefficients

    # Inequality: u_t ≥ −(R_t @ w) − z  ↔  −R_t @ w − z − u_t ≤ 0
    # → for each t: -R[t,:] @ w - z - u_t ≤ 0
    A_ub = np.zeros((T, n + 1 + T))
    b_ub = np.zeros(T)
    for t in range(T):
        A_ub[t, :n] = -R[t, :]
        A_ub[t, n] = -1.0
        A_ub[t, n + 1 + t] = -1.0

    # Equality: sum(w) = 1
    A_eq = np.zeros((1, n + 1 + T))
    A_eq[0, :n] = 1.0
    b_eq = np.array([1.0])

    # Bounds: 0 ≤ w ≤ 0.15, z free, u ≥ 0
    bounds = [(0.0, S_MAX_WEIGHT)] * n + [(None, None)] + [(0.0, None)] * T

    try:
        res = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq, bounds=bounds,
                      method="highs")
        if res.success:
            w_opt = np.maximum(res.x[:n], 0.0)
            total = w_opt.sum()
            if total > 0:
                w_opt /= total
                return {t: float(w_opt[i]) for i, t in enumerate(tickers)}
    except Exception:
        pass
    return {t: 1.0 / n for t in tickers}


def run_portfolio_opt(
    prices: dict[str, pd.DataFrame],
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
    label: str,
    variant: str = "hrp",   # "hrp" | "msharpe" | "mincvar" | (외부 스케줄: "spo_plus"/"ls")
    ticker_reports: dict[str, list[dict]] | None = None,
    record_full_trades: bool = False,
    weight_schedule: dict[dt.date, dict[str, float]] | None = None,
    weights_memo: dict[str, dict[str, float] | None] | None = None,
) -> dict:
    """
    S 포트폴리오 최적화 (월간 리밸런스).
    variant: 'hrp', 'msharpe', 'mincvar'.
    유니버스: 18개월 내 buy report 종목.
    Trailing 252d 일별 수익률로 가중치 계산.
    월말 신호 → 다음 거래일 시가 체결.
    비용: 총 NAV × 턴오버 × 편도 비용.

    weight_schedule (v16): 외부에서 계산한 {월말일 → {티커: 비중}} 스케줄이 주어지면
    내부 가중치 계산을 건너뛰고 동일한 실행 메커니즘(월말 신호 → 익월 첫 거래일
    시가 체결, 턴오버 비용)으로 체결만 수행. V(SPO) 패밀리가 사용.

    weights_memo (perf cache): {month_end_iso → filtered target_weights | None}.
    가중치 최적화(hrp/msharpe/mincvar)는 (prices, reports, calendar)의 순수 함수 —
    동일 데이터셋에서는 캐시 재생이 재계산과 결과 동일. None = 해당 월말에 가중치
    미산출(유니버스 부족/실패) 기록. 캐시에 없는 월은 정상 계산 후 기록.
    """
    # ── v15 프리플라이트: 의존성 누락은 즉시 크게 실패 ──────────────────────
    # (과거 버그: scipy 미설치 시 월별 except가 ImportError를 삼켜
    #  NAV 1.0 평탄·거래 0건의 "유령 전략"이 조용히 출력되었다)
    if weight_schedule is None and variant in ("msharpe", "mincvar"):
        try:
            import scipy.optimize  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                f"S({variant}) 전략은 scipy가 필요합니다. "
                f"`pip install -r requirements.txt` 후 재실행하세요: {e}"
            ) from e

    START_CAPITAL = 100_000_000
    cash = float(START_CAPITAL)
    positions: dict[str, dict] = {}   # ticker -> {shares, entry_price, cost, last_close, entry_date, ...}
    nav_series: list[tuple[str, float]] = []
    trades: list[dict] = []

    # Build per-ticker valid date ranges
    ticker_valid: dict[str, list[tuple[dt.date, dt.date]]] = {}
    for rdate, ticker, source, n_clubs in reports:
        expire = rdate + dt.timedelta(days=int(S_UNIVERSE_MONTHS * 30.44))
        ticker_valid.setdefault(ticker, []).append((rdate, expire))

    # Calendar helpers
    cal_s = pd.Series(calendar)
    month_ends: set[dt.date] = set(
        cal_s.groupby(cal_s.apply(lambda d: (d.year, d.month))).last().values
    )
    month_firsts: set[dt.date] = set(
        cal_s.groupby(cal_s.apply(lambda d: (d.year, d.month))).first().values
    )

    # Target weights queue: computed at month-end, applied at next month-first open
    target_weights: dict[str, float] = {}   # ticker -> weight (from last month-end)

    for day in calendar:
        day_ts = pd.Timestamp(day)
        cash *= (1 + CASH_YIELD_DAILY)  # v18: 유휴 현금 일복리 이자 (연 3% MMF 프록시)

        # Month-first: execute rebalance
        if day in month_firsts and target_weights:
            nav_now = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
            if nav_now <= 0:
                nav_now = float(START_CAPITAL)

            new_positions: dict[str, dict] = {}
            new_cash = 0.0
            total_turnover = 0.0

            # Close positions not in new targets (or weight drops to 0)
            for ticker, pos in list(positions.items()):
                new_w = target_weights.get(ticker, 0.0)
                if new_w == 0.0:
                    q = _get_quote(prices, ticker, day)
                    exit_price = float(q["open"]) if (q is not None and float(q["open"]) > 0) else pos["last_close"]
                    _fx_s = _fx(pos.get("market", "KR"), day)
                    proceeds = pos["shares"] * exit_price * _fx_s * (1 - COST_PER_SIDE)
                    new_cash += proceeds
                    total_turnover += pos["shares"] * exit_price * _fx_s / nav_now
                    trades.append(_close_trade(ticker, pos, day, exit_price, "s_rebalance_exit",
                                               ticker_reports, record_full_trades, None))

            cash_after_close = cash + new_cash

            # Open / resize positions
            for ticker, w in target_weights.items():
                if w <= 0:
                    continue
                target_value = nav_now * w
                cur_pos = positions.get(ticker)
                # v20 FX fix: cur_value must be in KRW like target_value/nav_now.
                # 누락 시(v19 회귀) US 보유 포지션 가치가 ~1/1400로 과소평가되어
                # delta_value ≈ 전체 목표금액 → 매월 현금에서 목표금액을 재차 차감하면서
                # 기존 보유분 가치는 증발 → NAV가 0으로 수렴 (S 3종·V 패밀리 MDD −100%의 근본 원인).
                _cur_fx = _fx(cur_pos.get("market", "KR"), day) if cur_pos else 1.0
                cur_value = cur_pos["shares"] * cur_pos["last_close"] * _cur_fx if cur_pos else 0.0

                df = prices.get(ticker)
                if df is None or not _has_day(df, day_ts):
                    if cur_pos:
                        new_positions[ticker] = cur_pos
                    continue

                trade_price = _px(df, day_ts, "open")
                if trade_price <= 0:
                    if cur_pos:
                        new_positions[ticker] = cur_pos
                    continue

                delta_value = target_value - cur_value
                turnover_frac = abs(delta_value) / nav_now
                total_turnover += turnover_frac

                if cur_pos:
                    # v20: delta-only resize — 비용은 실제 체결된 델타에만 부과하고,
                    # 현금이 모자라면 그만큼만 산다 (현금 없이 주식이 생기는 유령 매수 금지).
                    fx_t = _fx(cur_pos.get("market", "KR"), day)
                    if delta_value < 0:
                        sell_value = min(-delta_value, cur_pos["shares"] * trade_price * fx_t)
                        sell_shares = sell_value / (trade_price * fx_t) if trade_price * fx_t > 0 else 0.0
                        if sell_shares > 0 and cur_pos["shares"] > 0:
                            proceeds = sell_shares * trade_price * fx_t * (1 - COST_PER_SIDE)
                            cash_after_close += proceeds
                            trades.append(_close_trade(ticker, cur_pos, day, trade_price,
                                                        "s_rebalance_trim",
                                                        ticker_reports, record_full_trades, None,
                                                        shares_override=sell_shares,
                                                        cost_override=cur_pos["cost"] * (sell_shares / cur_pos["shares"])))
                        new_shares = cur_pos["shares"] - sell_shares
                        _rem_frac = (new_shares / cur_pos["shares"]) if cur_pos["shares"] > 0 else 0.0
                        new_cost = cur_pos["cost"] * _rem_frac
                    else:
                        add_budget = min(delta_value, cash_after_close)
                        if add_budget < 0:
                            add_budget = 0.0
                        cash_after_close -= add_budget
                        add_shares = add_budget * (1 - COST_PER_SIDE) / (trade_price * fx_t)
                        new_shares = cur_pos["shares"] + add_shares
                        new_cost = cur_pos["cost"] + add_budget
                    if new_shares <= 0:
                        continue
                    new_positions[ticker] = {
                        "shares": new_shares,
                        "entry_price": cur_pos["entry_price"],
                        "entry_date": cur_pos["entry_date"],
                        "cost": new_cost,
                        "last_close": trade_price,
                        "fx": fx_t,
                        "source": cur_pos["source"],
                        "n_clubs": cur_pos["n_clubs"],
                        "display_name": cur_pos["display_name"],
                        "market": cur_pos["market"],
                        "target_price": cur_pos.get("target_price"),
                        "entry_reason": f"{variant} 월간 리밸런스 — 목표비중 {w * 100:.1f}% 재조정 보유",
                    }
                else:
                    buy_budget = min(target_value, cash_after_close)
                    if buy_budget < target_value * 0.5:
                        continue
                    cash_after_close -= buy_budget
                    dn = ticker
                    mkt = "KR"
                    if ticker_reports:
                        tr_l = ticker_reports.get(ticker, [])
                        past = [r for r in tr_l if r["report_date"] <= day]
                        if past:
                            dn = past[-1]["display_name"]
                            mkt = past[0].get("market", "KR")
                    act_shares = buy_budget * (1 - COST_PER_SIDE) / (trade_price * _fx(mkt, day))
                    new_positions[ticker] = {
                        "shares": act_shares,
                        "entry_price": trade_price,
                        "entry_date": day,
                        "cost": buy_budget,
                        "last_close": trade_price,
                        "fx": _fx(mkt, day),
                        "source": "",
                        "n_clubs": 1,
                        "display_name": dn,
                        "market": mkt,
                        "target_price": None,
                        "entry_reason": f"{variant} 월간 리밸런스 — 목표비중 {w * 100:.1f}% 신규 편입 (18개월 유효 리포트 유니버스)",
                    }

            positions = new_positions
            cash = cash_after_close
            target_weights = {}

        # Update last_close
        for ticker, pos in positions.items():
            df = prices.get(ticker)
            if df is not None and _has_day(df, day_ts):
                pos["last_close"] = _px(df, day_ts, "close")
                pos["fx"] = _fx(pos.get("market", "KR"), day)

        # Month-end: compute new target weights (point-in-time signal)
        if day in month_ends and weight_schedule is not None:
            # 외부 스케줄 모드 (V SPO 패밀리): 사전 계산된 비중 사용
            sched_w = weight_schedule.get(day)
            if sched_w:
                target_weights = {t: v for t, v in sched_w.items() if v > 0.001}
        elif day in month_ends:
            mkey = day.isoformat()
            if weights_memo is not None and mkey in weights_memo:
                # Cache replay — same filtered weights the optimiser produced on
                # this dataset → identical rebalance. None = no weights that month.
                cached_w = weights_memo[mkey]
                if cached_w is not None:
                    target_weights = dict(cached_w)
            else:
                computed: dict[str, float] | None = None
                lookback_start = day - dt.timedelta(days=S_LOOKBACK_DAYS + 30)
                # Build active universe
                active: list[str] = []
                for ticker, ranges in ticker_valid.items():
                    if not any(start <= day <= end for start, end in ranges):
                        continue
                    df = prices.get(ticker)
                    if df is None or not _has_day(df, day_ts):
                        continue
                    active.append(ticker)

                if len(active) >= S_MIN_STOCKS:
                    # Build return matrix
                    day_ts_start = pd.Timestamp(lookback_start)
                    ret_cols: dict[str, pd.Series] = {}
                    for ticker in active:
                        df = prices[ticker]
                        sub = df.loc[(df.index >= day_ts_start) & (df.index <= day_ts), "close"]
                        if len(sub) < 30:
                            continue
                        r = sub.pct_change().dropna()
                        ret_cols[ticker] = r

                    if len(ret_cols) >= S_MIN_STOCKS:
                        ret_df = pd.DataFrame(ret_cols).dropna(how="any")
                        if len(ret_df) >= 20 and len(ret_df.columns) >= S_MIN_STOCKS:
                            try:
                                if variant == "hrp":
                                    w_dict = _hrp_weights(ret_df)
                                elif variant == "msharpe":
                                    w_dict = _msharpe_weights(ret_df)
                                else:  # mincvar
                                    w_dict = _mincvar_weights(ret_df)
                                computed = {t: v for t, v in w_dict.items() if v > 0.001}
                            except Exception as e:
                                print(f"  S({variant}) weight computation failed on {day}: {e}", flush=True)
                if computed is not None:
                    target_weights = dict(computed)
                if weights_memo is not None:
                    weights_memo[mkey] = computed

        nav = cash + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
        nav_series.append((day.isoformat(), nav))

    # Force-close at end
    last_day = calendar[-1]
    for ticker, pos in list(positions.items()):
        trades.append(_close_trade(ticker, pos, last_day, pos["last_close"], "데이터_종료_미청산",
                                   ticker_reports, record_full_trades, None))

    # v15 sanity guard: a portfolio-opt run that never traded is a wiring bug, not a result
    if not trades:
        raise RuntimeError(
            f"S({variant}) 백테스트가 거래 0건으로 종료 — 가중치 계산이 매월 실패했을 가능성. "
            "로그의 'weight computation failed' 메시지를 확인하세요."
        )

    # v20 zero-NAV ghost guard: long-only(개별 비중 ≤15%, 무차입) 포트폴리오의 NAV는
    # 0 근처로 떨어질 수 없다 — 도달했다면 수익률이 아니라 회계 버그다
    # (예: v19 FX 누락 회귀 → S 3종 MDD −100% 유령). NaN NAV도 동일하게 즉시 실패.
    _nav_vals = [v for _, v in nav_series]
    if any((not math.isfinite(v)) or v <= 0 for v in _nav_vals):
        raise RuntimeError(
            f"S({variant}) NAV가 NaN/0 이하에 도달 — long-only 무차입 구조에서 불가능. "
            "리밸런스 회계(FX 환산·현금 차감)를 점검하세요."
        )
    _min_nav = min(_nav_vals)
    if _min_nav < START_CAPITAL * 0.02:
        raise RuntimeError(
            f"S({variant}) NAV 최저점이 시작자본의 {_min_nav / START_CAPITAL * 100:.2f}%까지 붕괴 — "
            "개별 비중 캡 15% long-only에서 −98%는 시장 손실이 아니라 회계 버그 신호입니다."
        )

    return _compute_result(nav_series, trades, START_CAPITAL, label, open_positions=positions)


# ──────────────────────────────────────────────────────────────────────────────
# Strategy T: 코어-KOSPI 샹들리에 (KOSPI-parked idle cash)
#
# 설계 원칙:
#   - D+ Chandelier Optuna 규칙(진입/청산/사이징)과 완전 동일.
#   - 유휴 현금(비어있는 슬롯 현금 + DCA 기여금) → 모두 KOSPI 지수 익스포저로 주차.
#   - NAV = 주식 포지션 + KOSPI 파킹 잔액.
#   - 일별: 파킹 잔액은 KOSPI close-to-close 수익률을 반영.
#   - 진입 시: 필요 금액만큼 KOSPI 익스포저 매도 (비용 0.05%/side 인덱스 ETF 가정)
#             → 해당 금액으로 주식 매수 (비용 0.3%/side 기존과 동일).
#   - 청산 시: 주식 매도 수익금(비용 0.3% 후) → KOSPI 익스포저 매수 (비용 0.05%).
#   - 이 설계에서 전략의 베이스라인 = KOSPI DCA.
#     주식 픽은 KOSPI 대비 순 알파를 더하거나 뺄 뿐.
#   - DCA 기여금: KOSPI 익스포저로 즉시 편입 (이 시뮬레이션은 NAV-only, DCA 없음,
#     같은 START_CAPITAL 100M 사용 — DCA 비교는 wealth_sim에서 처리).
#   - KOSPI 인덱스 ETF 편도 비용 0.05% 가정: 실제 KODEX200 기준 0.02~0.05% 스프레드.
#     이 가정을 method note에 명시.
#
# T  (always-KOSPI): 항상 KOSPI 파킹.
# T- (regime-aware): KOSPI < 200MA이면 파킹 이자율 0% (현금). Faber 레짐.
#
# CSV: 주식 거래만 기록 + 헤더 주석에 "KOSPI 파킹 거래 미포함" 명시.
# ──────────────────────────────────────────────────────────────────────────────



def run_parking_core_chandelier(
    prices: dict[str, pd.DataFrame],
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
    label: str,
    parking: pd.Series,
    atr_period: int,
    atr_mult: float,
    max_positions: int,
    ticker_reports: dict[str, list[dict]] | None = None,
    record_full_trades: bool = False,
    regime_aware: bool = False,
    regime_index: pd.Series | None = None,
    parking_name: str = "KOSPI",
    *, entry_schedule: dict | None = None, initial_capital: float = 100_000_000.0,
    collect_history: bool = False,
) -> dict:
    """Shared daily engine for T/T-/W backtests and timestamped forward paper accounts."""
    from .parking_engine import simulate
    return simulate(prices, reports, calendar, label, parking, atr_period, atr_mult, max_positions,
                    ticker_reports, record_full_trades, regime_aware, regime_index, parking_name,
                    entry_schedule=entry_schedule, initial_capital=initial_capital,
                    collect_history=collect_history)


# ──────────────────────────────────────────────────────────────────────────────
# Strategy U: 코어-KOSPI 샹들리에 + 과열 스케일아웃
#
# 설계: T- (regime-aware KOSPI 파킹) 와 완전 동일, 추가 규칙:
#   - 과열 게이지: extension = B/A  (ATR(14)%, 50SMA, Minervini circle)
#   - extension > 8× 시 → 보유 주수의 절반 매도 (1차 스케일아웃)
#     - 나머지 절반은 샹들리에 트레일 계속
#   - extension 나중에 > 12× 시 → 남은 포지션의 절반 다시 매도 (2차 스케일아웃)
#   - 스케일아웃은 포지션당 1회: 1차 완료 후 재발동 없음
#     (단, 진입 때 초기화 — 완전 청산 후 재진입 시 리셋)
#   - 스케일아웃 수익금 → KOSPI 파킹 (T- 규칙과 동일)
# ──────────────────────────────────────────────────────────────────────────────

U_SCALEOUT_EXT_1 = 8.0    # 1차 스케일아웃: extension > 8×
U_SCALEOUT_EXT_2 = 12.0   # 2차 스케일아웃: extension > 12×


def run_kospi_core_chandelier_scaleout(
    prices: dict[str, pd.DataFrame],
    reports: list[tuple[dt.date, str, str, int]],
    calendar: list[dt.date],
    label: str,
    kospi: pd.Series,
    atr_period: int,
    atr_mult: float,
    max_positions: int,
    ticker_reports: dict[str, list[dict]] | None = None,
    record_full_trades: bool = False,
) -> dict:
    """
    U 코어-KOSPI 샹들리에 + 과열 스케일아웃.

    T- (regime-aware) 규칙 완전 동일 +
    extension(ATR%×50SMA) > 8× → 절반 익절 → KOSPI 파킹.
    extension > 12× → 남은 절반 다시 익절 → KOSPI 파킹.
    트리거는 포지션당 1회씩만 발동 (오실레이션 재발동 없음).
    새 포지션 진입 시 카운터 초기화.
    """
    START_CAPITAL = 100_000_000
    cash = 0.0
    kospi_parked = START_CAPITAL * (1 - KOSPI_PARK_COST)
    positions: dict[str, dict] = {}
    nav_series: list[tuple[str, float]] = []
    trades: list[dict] = []
    pending_exits: set[str] = set()

    # Precompute KOSPI 200MA for regime filter
    kospi_ma200 = kospi.rolling(200, min_periods=100).mean()

    pending_entries = build_pending_entries(reports, calendar, consensus_only=False, prices=prices)
    prev_kospi_close: float | None = None

    for day in calendar:
        day_ts = pd.Timestamp(day)
        cash *= (1 + CASH_YIELD_DAILY)  # v18: 유휴 현금 일복리 이자 (연 3% MMF 프록시)

        # ── Daily KOSPI return on parked balance ──────────────────────────────
        kospi_close_today = asof_value(kospi, day)
        if kospi_close_today > 0:
            if prev_kospi_close is not None and prev_kospi_close > 0:
                use_kospi_return = _regime_risk_on(kospi, kospi_ma200, day)
                if kospi_parked > 0:
                    if use_kospi_return:
                        daily_kospi_ret = kospi_close_today / prev_kospi_close - 1
                        kospi_parked *= (1 + daily_kospi_ret)
                    else:
                        # v18: 레짐 OFF — 파킹 잔액은 현금 이자 연 3% 일복리
                        kospi_parked *= (1 + CASH_YIELD_DAILY)
            prev_kospi_close = kospi_close_today
        else:
            if prev_kospi_close is None:
                prev_kospi_close = asof_value(kospi, day) or None

        # ── Execute pending exits (chandelier stop — next open) ───────────────
        to_exit = [t for t in sorted(pending_exits) if t in positions]
        for ticker in to_exit:
            pos = positions[ticker]
            q = _get_quote(prices, ticker, day)
            if q is None or float(q["open"]) <= 0:
                continue
            exit_price = float(q["open"])
            stock_proceeds = pos["shares"] * exit_price * _fx(pos.get("market", "KR"), day) * (1 - COST_PER_SIDE)
            kospi_parked += stock_proceeds * (1 - KOSPI_PARK_COST)
            trades.append(_close_trade(ticker, pos, day, exit_price,
                                       f"u_chandelier_ATR{atr_mult}",
                                       ticker_reports, record_full_trades, None))
            del positions[ticker]
            pending_exits.discard(ticker)

        # ── Execute pending entries ───────────────────────────────────────────
        if day in pending_entries:
            nav_now = (
                kospi_parked
                + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
            )
            slots = max_positions - len(positions)
            candidates = list(
                {t: (t, s, nc) for t, s, nc in pending_entries[day]
                 if t not in positions}.values()
            )
            for ticker, source, n_clubs in candidates[:slots]:
                df = prices.get(ticker)
                if df is None or not _has_day(df, day_ts):
                    continue
                entry_price = _px(df, day_ts, "open")
                if entry_price <= 0:
                    continue

                budget = nav_now * POSITION_WEIGHT
                if budget > kospi_parked:
                    budget = kospi_parked
                if budget < nav_now * POSITION_WEIGHT * 0.5:
                    continue

                display_name = ticker
                tp = None
                market = "KR"
                if ticker_reports is not None:
                    tr_list = ticker_reports.get(ticker, [])
                    past_tr = [x for x in tr_list if x["report_date"] < day]
                    if past_tr:
                        latest = max(past_tr, key=lambda x: x["report_date"])
                        display_name = latest["display_name"]
                        tps = [x["target_price"] for x in past_tr if x["target_price"]]
                        tp = max(tps) if tps else None
                        market = past_tr[0].get("market", "KR")

                kospi_parked -= budget
                stock_budget = budget * (1 - KOSPI_PARK_COST)
                shares = stock_budget * (1 - COST_PER_SIDE) / (entry_price * _fx(market, day))
                total_spent = budget

                atr_val = asof_value(_atr_column(df, atr_period), day - dt.timedelta(days=1))

                stop = entry_price - atr_mult * atr_val if atr_val else entry_price * 0.75

                pos = {
                    "shares": shares,
                    "entry_price": entry_price,
                    "entry_date": day,
                    "cost": total_spent,
                    "last_close": entry_price,
                    "highest": entry_price,
                    "stop": stop,
                    "source": source,
                    "n_clubs": n_clubs,
                    "display_name": display_name,
                    "market": market,
                    "target_price": tp,
                    # U-specific: scale-out state (one-time triggers)
                    "scaleout1_done": False,   # extension > 8× triggered
                    "scaleout2_done": False,   # extension > 12× triggered
                    "entry_reason": _report_trigger_reason(
                        ticker, day, ticker_reports,
                        prefix="학회 매수리포트 발간 → 익일 시가 진입 (KOSPI 파킹 매도 후 전환, 과열 스케일아웃 규칙)",
                    ),
                }
                positions[ticker] = pos

        # ── Update positions + check chandelier stop + extension scale-out ────
        for ticker, pos in list(positions.items()):
            df = prices.get(ticker)
            if df is None or not _has_day(df, day_ts):
                continue
            close = _px(df, day_ts, "close")
            pos["last_close"] = close
            pos["fx"] = _fx(pos.get("market", "KR"), day)
            pos["highest"] = max(pos.get("highest", close), close)

            atr_val = asof_value(_atr_column(df, atr_period), day)
            if atr_val:
                new_stop = pos["highest"] - atr_mult * atr_val
                pos["stop"] = max(pos.get("stop", 0.0), new_stop)

            # Chandelier stop check
            if pos.get("stop") and close < pos["stop"] and ticker not in pending_exits:
                pending_exits.add(ticker)
                continue  # don't check scale-out on same day as stop trigger

            # Extension-based scale-out (one-time, FIFO check: 1st then 2nd tier)
            ext = compute_extension(df, day)
            if ext is not None:
                # 1차 스케일아웃: extension > 8×, only if not yet done
                if not pos["scaleout1_done"] and ext > U_SCALEOUT_EXT_1:
                    half_shares = pos["shares"] * 0.5
                    half_cost = pos["cost"] * 0.5
                    proceeds = half_shares * close * _fx(pos.get("market", "KR"), day) * (1 - COST_PER_SIDE)
                    kospi_parked += proceeds * (1 - KOSPI_PARK_COST)
                    trade = _close_trade(
                        ticker, pos, day, close,
                        f"u_scaleout1_ext{ext:.1f}x",
                        ticker_reports, record_full_trades, None,
                        shares_override=half_shares,
                        cost_override=half_cost,
                    )
                    trades.append(trade)
                    pos["shares"] -= half_shares
                    pos["cost"] -= half_cost
                    pos["scaleout1_done"] = True

                # 2차 스케일아웃: extension > 12×, only if 1st done and not yet 2nd
                elif pos["scaleout1_done"] and not pos["scaleout2_done"] and ext > U_SCALEOUT_EXT_2:
                    quarter_shares = pos["shares"] * 0.5
                    quarter_cost = pos["cost"] * 0.5
                    proceeds = quarter_shares * close * _fx(pos.get("market", "KR"), day) * (1 - COST_PER_SIDE)
                    kospi_parked += proceeds * (1 - KOSPI_PARK_COST)
                    trade = _close_trade(
                        ticker, pos, day, close,
                        f"u_scaleout2_ext{ext:.1f}x",
                        ticker_reports, record_full_trades, None,
                        shares_override=quarter_shares,
                        cost_override=quarter_cost,
                    )
                    trades.append(trade)
                    pos["shares"] -= quarter_shares
                    pos["cost"] -= quarter_cost
                    pos["scaleout2_done"] = True

        nav = kospi_parked + sum(p["shares"] * p["last_close"] * p.get("fx", 1.0) for p in positions.values())
        nav_series.append((day.isoformat(), nav))

    # Force-close remaining positions at end
    last_day = calendar[-1]
    for ticker, pos in list(positions.items()):
        trades.append(_close_trade(ticker, pos, last_day, pos["last_close"],
                                   "데이터_종료_미청산",
                                   ticker_reports, record_full_trades, None))

    result = _compute_result(nav_series, trades, START_CAPITAL, label,
                             open_positions=positions)
    result["kospi_parking_note"] = (
        "U 코어-KOSPI 샹들리에 + 과열 스케일아웃: T- 레짐 필터 동일 + "
        "ATR% Multiple from 50-MA (extension = B/A, A=ATR14/price, B=(price-50SMA)/50SMA). "
        "extension > 8× → 절반 익절 → KOSPI 파킹; extension > 12× → 나머지 절반 다시 익절. "
        "트리거는 포지션당 1회 (재발동 없음; 재진입 시 초기화). "
        "출처: Minervini 커뮤니티 관행, TradingView Fred6724."
    )
    return result
