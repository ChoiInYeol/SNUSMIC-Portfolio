"""Funded forward-paper account: 50M KRW start, stepped monthly contributions, W policy, next-open plans.

A separate account id and epoch (`funded-w50-v1`). The 100M execution-v2 account and its Bronze are
never read or reinterpreted here. Bronze (append-only, `lake.BRONZE_FUNDED`):

  account.json                              genesis: epoch, frozen config, bootstrap candidates, known report ids
  contributions/{installment:04d}.json      one cash-flow event per installment (the file name is the dedupe key)
  signals/{report_id[:24]}.json             buy reports first observed after the epoch
  sessions/{KR|US}/{date}.json              completed session bars (open/close/ATR) and observed USDKRW
  plans/{KR|US}/{date}/{policy}/rNN.json    next-open order plans frozen after the close; revisions explicit

Silver and Gold replay only those files. A plan is frozen from the last completed close of its own
market and never sees the open it targets; fills exist only once a later session record carries that
real open. Plan and fill are separate models. Quantities computed from the close are estimates: the plan
freezes a KRW budget and the fill recomputes integer shares at the real open within it.
Contributions are external cash flows: they raise principal, never returns (TWR excludes them).
"""
from __future__ import annotations

import argparse
import bisect
import copy
import datetime as dt
import hashlib
import json
import logging
import math
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import lake
from backtest.config import COST_PER_SIDE, POSITION_WEIGHT
from backtest.parking_engine import market_open
from lake_prices import publish_new, replace_atomic
from market_calendars import (first_trading_day_of_month, is_trading_day, next_trading_day,
                              previous_trading_day, session_close, skipped_closures)
from market_clock import closed_bar_end
from paper_execution import report_id

KST = ZoneInfo("Asia/Seoul")
SCHEMA_VERSION = "1.0"
ACCOUNT_ID = "funded-w50-v1"
POLICY_VERSION = "w-open-plan-v1"
PROPOSED_LABEL = "제안값(미확정)"
BACKTEST_LABEL = "백테스트 구간 (simulated)"
LIVE_LABEL = "실전 구간"
CONFIRMED_LABEL = "사용자 확정"
MARKETS = ("KR", "US")
BENCHMARKS = {"KR": "INDEX:KOSPI", "US": "INDEX:SP500"}  # presence of the session bar = market data ready
CONTRIBUTION_LEAD = dt.timedelta(minutes=30)              # the deposit lands 30 minutes before the KR open
ATR_COLUMN = {20: "atr20", 42: "atr", 63: "atr63"}

CONFIG = {
    "account_id": ACCOUNT_ID,
    "policy_version": POLICY_VERSION,
    "strategy": "W_allweather_chandelier",
    "initial_capital_krw": 50_000_000,
    "contribution": {"base_krw": 2_000_000, "step_krw": 1_000_000, "step_every_installments": 24},
    # Proposal only. A real genesis takes the rule from `init --contribution-day/--contribution-day-status`.
    "contribution_day": {
        "rule": "first_kr_trading_day_of_month_before_open", "day_of_month": None, "first_installment": "month_after_epoch",
        "holiday": "roll_forward", "status": "proposed_unconfirmed", "label": PROPOSED_LABEL,
        "description": "개시 다음 달부터 매월 첫 KRX 거래일 시가 30분 전(08:30 KST) 입금, 휴장일은 다음 거래일로 순연",
    },
    "atr_period": 42, "atr_mult": 5.75, "max_positions": 30,
    "position_weight": POSITION_WEIGHT, "min_fill_fraction": 0.5, "cost_per_side": COST_PER_SIDE,
    "missing_open_retry_sessions": 5,
    "cash_policy": "미투자 잔액은 무이자 원화 현금. W 백테스트의 올웨더 합성지수 파킹은 주문 불가라 적용하지 않음",
}
PARKING_EXCLUSION = {"ticker": "INDEX:AllWeather", "reason_code": "not_orderable",
                     "reason": "올웨더 합성지수는 거래 가능한 상품·시가가 없어 주문 대상이 아님. 미투자 잔액은 무이자 원화 현금으로 보유"}
METHOD = ("W 정책(신규 학회 매수 리포트 → 다음 시가 진입, ATR42×5.75 샹들리에 종가 스탑 → 다음 시가 청산, 최대 30종목, "
          "신규 진입 NAV 5%)을 정수 주식으로 따라가는 납입식 모의 계정. 계획은 시장별 종가·입력 검증 뒤 동결하고 "
          "실제 다음 시가가 기록된 뒤에만 체결한다. 주식 편도 0.3%. 납입은 원금이며 수익률(TWR)에서 제외. 실계좌 주문 아님.")


# ── contributions ───────────────────────────────────────────────────────────

def contribution_amount(installment: int, config: dict = CONFIG) -> int:
    if installment < 1:
        raise ValueError("installments start at 1")
    c = config["contribution"]
    return c["base_krw"] + c["step_krw"] * ((installment - 1) // c["step_every_installments"])


def principal_after(installments: int, config: dict = CONFIG) -> int:
    """Initial capital plus the first `installments` contributions (no return, no cost)."""
    return config["initial_capital_krw"] + sum(contribution_amount(n, config) for n in range(1, installments + 1))


def contribution_day_rule(choice: str, status: str, first_installment: str = "month_after_epoch") -> dict:
    """`first-trading-day` or a calendar day 1..28 (rolled forward to a KRX trading day); proposed or confirmed.

    `first_installment`: "month_after_epoch" (installment 1 in the month after the schedule anchor) or
    "anchor_month" (installment 1 in the anchor's own month, e.g. the 2023-07 backtest start).
    """
    if first_installment not in ("month_after_epoch", "anchor_month"):
        raise ValueError("first installment must be 'month_after_epoch' or 'anchor_month'")
    if status not in ("proposed", "confirmed"):
        raise ValueError("contribution-day status must be 'proposed' or 'confirmed'")
    if choice == "first-trading-day":
        rule, day, text = "first_kr_trading_day_of_month_before_open", None, "매월 첫 KRX 거래일"
    elif choice.isdigit() and 1 <= int(choice) <= 28:
        rule, day, text = "kr_trading_day_on_or_after_day_of_month_before_open", int(choice), f"매월 {int(choice)}일(휴장 시 다음 KRX 거래일)"
    else:
        raise ValueError("contribution day must be 'first-trading-day' or a day of month 1..28")
    start = "개시 다음 달부터" if first_installment == "month_after_epoch" else "시작 월부터"
    return {"rule": rule, "day_of_month": day, "first_installment": first_installment, "holiday": "roll_forward",
            "status": "proposed_unconfirmed" if status == "proposed" else "user_confirmed",
            "label": PROPOSED_LABEL if status == "proposed" else CONFIRMED_LABEL,
            "description": f"{start} {text} 시가 30분 전(08:30 KST) 입금, 휴장일은 다음 거래일로 순연"}


def contribution_date(installment: int, anchor: dt.date, config: dict = CONFIG) -> dt.date:
    """Installment date counted from the schedule anchor (the epoch, or the backtest start for a seeded account)."""
    offset = installment - (1 if config["contribution_day"]["first_installment"] == "anchor_month" else 0)
    month = anchor.month - 1 + offset
    year, month = anchor.year + month // 12, month % 12 + 1
    day = config["contribution_day"]["day_of_month"]
    if day is None:
        return first_trading_day_of_month(year, month, "KR")
    return next_trading_day(dt.date(year, month, day) - dt.timedelta(days=1), "KR")


def schedule_anchor(genesis: dict) -> dt.date:
    return dt.date.fromisoformat(genesis.get("schedule_anchor") or _instant(genesis["epoch_at"]).astimezone(KST).date().isoformat())


def first_installment(genesis: dict) -> int:
    """Seeded accounts continue the backtest's installment numbering."""
    return (genesis.get("seed") or {}).get("installments_applied", 0) + 1


def _contribution_effective(c: dict) -> dt.datetime:
    scheduled = market_open(dt.date.fromisoformat(c["scheduled_date"]), "KR") - CONTRIBUTION_LEAD
    return max(scheduled, dt.datetime.fromisoformat(c["recorded_at"]))


# ── instruments and calendars ───────────────────────────────────────────────

def instrument(ticker: str, market: str) -> tuple[dict | None, str | None]:
    """Tradable instrument for the follow-along list (integer shares), or None with a reason."""
    if ticker.startswith("INDEX:") or ":" in ticker:
        return None, "지수·합성 상품은 주문 불가"
    if market == "KR" and re.fullmatch(r"\d{6}", ticker):
        return {"symbol": f"KRX:{ticker}", "venue": "KRX", "currency": "KRW", "lot": 1}, None
    if market == "US" and re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", ticker):
        return {"symbol": f"US:{ticker}", "venue": "NYSE/NASDAQ", "currency": "USD", "lot": 1}, None
    return None, f"{market} 거래 가능 종목 코드 형식이 아님"


def close_at(day: dt.date, market: str) -> dt.datetime:
    return session_close(day, market).astimezone(dt.timezone.utc)


def expected_session(market: str, now: dt.datetime) -> dt.date:
    """Latest session whose bar is complete at `now` under this market's own cutoff, hours and holidays.

    market_clock applies the regular cutoff (KR 15:45, US 16:15 NY) and any later special close (KRX CSAT day).
    """
    return previous_trading_day(closed_bar_end(market, now=now), market, inclusive=True)


# ── bronze io ───────────────────────────────────────────────────────────────

def _base() -> Path:
    return lake.BRONZE_FUNDED


def _publish(path: Path, doc: dict) -> None:
    publish_new(path, json.dumps(doc, ensure_ascii=False, allow_nan=False, indent=1).encode())


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _instant(value: str) -> dt.datetime:
    instant = dt.datetime.fromisoformat(value)
    if instant.tzinfo is None:
        raise ValueError(f"timestamp requires a timezone: {value}")
    return instant


def canonical_sha256(doc: dict) -> str:
    """Line-ending independent content hash used for the backtest seed provenance."""
    return hashlib.sha256(json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def load_backtest(seed: dict) -> dict:
    """The Silver backtest artifact a seeded genesis points to, verified against the recorded hash."""
    doc = json.loads((lake.ROOT / seed["artifact"]).read_text(encoding="utf-8"))  # absolute paths pass through /
    if canonical_sha256(doc) != seed["artifact_sha256"] or doc["run_id"] != seed["run_id"]:
        raise ValueError("backtest artifact differs from the one recorded in the genesis seed")
    return doc


def load_bronze() -> dict | None:
    base = _base()
    if not (base / "account.json").exists():
        return None
    genesis = _read(base / "account.json")
    if genesis.get("schema_version") != SCHEMA_VERSION or genesis.get("account_id") != ACCOUNT_ID:
        raise ValueError("unexpected funded account genesis")
    epoch = _instant(genesis["epoch_at"])
    anchor = schedule_anchor(genesis)
    contributions = [_read(p) for p in sorted((base / "contributions").glob("*.json"))]
    for n, c in enumerate(contributions, first_installment(genesis)):
        if (c["account_id"], c["installment"]) != (ACCOUNT_ID, n):
            raise ValueError("contributions must be one record per installment, contiguous from the first live one")
        if c["amount_krw"] != contribution_amount(n, genesis["config"]) or \
                c["scheduled_date"] != contribution_date(n, anchor, genesis["config"]).isoformat():
            raise ValueError(f"contribution {n} does not match the frozen schedule")
        _instant(c["recorded_at"])
    sessions = {}
    for market in MARKETS:
        # {date}.json is the session record; {date}~NN.json addenda add bars for tickers first seen later.
        for path in sorted((base / "sessions" / market).glob("*.json")):
            doc = _read(path)
            day = dt.date.fromisoformat(doc["session_date"])
            observed = _instant(doc["observed_at"])
            key = (market, doc["session_date"])
            addendum = "~" in path.stem
            if doc["market"] != market or path.stem.split("~")[0] != doc["session_date"] or not is_trading_day(day, market) \
                    or addendum != (key in sessions):
                raise ValueError(f"invalid session record {path.name}")
            if closed_bar_end(market, now=observed) < day:
                raise ValueError(f"session {market} {day} recorded before its bar was complete")
            for ticker, bar in doc["bars"].items():
                if bar is not None and (not isinstance(bar.get("close"), (int, float)) or not math.isfinite(bar["close"]) or bar["close"] <= 0):
                    raise ValueError(f"invalid close {market} {ticker} {day}")
            if not addendum:
                sessions[key] = {**doc, "bars": dict(doc["bars"]), "fx_usdkrw": list(doc["fx_usdkrw"])}
                continue
            if set(doc["bars"]) & set(sessions[key]["bars"]):
                raise ValueError(f"session addendum may not restate recorded bars: {path.name}")
            sessions[key]["bars"].update(doc["bars"])
            sessions[key]["fx_usdkrw"] += doc["fx_usdkrw"]
    signals = [_read(p) for p in sorted((base / "signals").glob("*.json"))]
    for s in signals:
        if _instant(s["observed_at"]) < epoch or s["report_date"] > _instant(s["observed_at"]).astimezone(KST).date().isoformat():
            raise ValueError(f"invalid signal availability {s['id']}")
    plans = []
    for path in sorted((base / "plans").glob("*/*/*/r*.json")):
        plan = _read(path)
        frozen = _instant(plan["frozen_at"])
        day = dt.date.fromisoformat(plan["trading_day"])
        basis = sessions.get((plan["market"], plan["basis_session"]))
        if plan["policy_version"] != genesis["config"]["policy_version"] or basis is None:
            raise ValueError(f"plan {plan['plan_id']} has no recorded basis session or wrong policy")
        if frozen >= market_open(day, plan["market"]) or _instant(basis["observed_at"]) > frozen:
            raise ValueError(f"plan {plan['plan_id']} was not frozen from available data before its open")
        if day != next_trading_day(dt.date.fromisoformat(plan["basis_session"]), plan["market"]):
            raise ValueError(f"plan {plan['plan_id']} does not target the next real trading day")
        plans.append(plan)
    plans.sort(key=lambda p: (p["market"], p["trading_day"], p["revision"]))
    for previous, plan in zip([None, *plans], plans):
        same = previous and (previous["market"], previous["trading_day"]) == (plan["market"], plan["trading_day"])
        if plan["revision"] != (previous["revision"] + 1 if same else 1) or \
                plan.get("supersedes") != (previous["plan_id"] if same else None):
            raise ValueError(f"plan revisions must be explicit and contiguous: {plan['plan_id']}")
    return {"genesis": genesis, "contributions": contributions, "sessions": sessions, "signals": signals, "plans": plans}


# ── replay (silver) ─────────────────────────────────────────────────────────

def _fx_rows(bronze: dict) -> dict[str, float]:
    rates: dict[str, float] = {}
    for key in sorted(bronze["sessions"], key=lambda k: bronze["sessions"][k]["observed_at"]):
        for row in bronze["sessions"][key]["fx_usdkrw"]:
            if not math.isfinite(row["value"]) or row["value"] <= 0:
                raise ValueError("invalid observed USDKRW")
            rates.setdefault(row["date"], row["value"])
    return rates


def _fx(rates: dict[str, float], day: str, *, strict: bool, keys: list[str] | None = None) -> float | None:
    keys = keys if keys is not None else sorted(rates)
    i = (bisect.bisect_left if strict else bisect.bisect_right)(keys, day)
    return rates[keys[i - 1]] if i else None


def _signals(bronze: dict) -> dict[str, dict]:
    out = {}
    for s in bronze["genesis"]["bootstrap"] + bronze["signals"]:
        out[s["id"]] = {**s, "status": "queued", "attempts": 0, "outcome": None}
    return out


def _run(bronze: dict, until: dt.datetime | None = None) -> dict:
    g = bronze["genesis"]; cfg = g["config"]
    cost = cfg["cost_per_side"]; mult = cfg["atr_mult"]
    epoch = _instant(g["epoch_at"])
    active = {}
    for plan in bronze["plans"]:
        active[(plan["market"], plan["trading_day"])] = plan  # sorted by revision: latest wins
    seed = g.get("seed")
    events = [] if seed else [(epoch, 0, "cash", {"kind": "initial", "amount_krw": cfg["initial_capital_krw"]})]
    events += [(_contribution_effective(c), 0, "cash", {"kind": "contribution", **c}) for c in bronze["contributions"]]
    for market, day in bronze["sessions"]:
        d = dt.date.fromisoformat(day)
        if market_open(d, market) > epoch:
            events.append((market_open(d, market), 1, "open", (market, day)))
        events.append((max(close_at(d, market), epoch), 2, "close", (market, day)))
    events.sort(key=lambda e: (e[0], e[1], str(e[3].get("installment", "")) if isinstance(e[3], dict) else e[3]))
    if until is not None:
        events = [e for e in events if e[0] <= until]
    rates = _fx_rows(bronze); keys = sorted(rates)
    signals = _signals(bronze)
    st = {"cash": 0.0, "principal": 0.0, "fees": 0.0, "realized": 0.0, "positions": {}, "fills": [],
          "history": [], "applied": [], "marks": {"KR": None, "US": None}, "issues": [], "signals": signals,
          "executed_plans": set()}
    twr = {"factor": 1.0, "prev_nav": 0.0, "pending_cf": 0.0}
    if seed:  # the live segment starts from the backtest's end state; its P&L and TWR start at zero here
        st.update(cash=seed["cash_krw"], principal=seed["principal_krw"], marks=dict(seed["marks"]),
                  positions={p["ticker"]: copy.deepcopy(p) for p in seed["positions"]})
        twr["prev_nav"] = seed["nav_krw"]

    def nav():
        return st["cash"] + sum(p["shares"] * p["last_close"] * p["fx"] for p in st["positions"].values())

    def value(at, market, session):
        current = nav()
        base = twr["prev_nav"] + twr["pending_cf"]
        if base > 0:
            twr["factor"] *= current / base
        twr["prev_nav"] = current; twr["pending_cf"] = 0.0
        st["history"].append({"at": at.isoformat(), "market": market, "session": session,
                              "nav_krw": round(current, 2), "cash_krw": round(st["cash"], 2),
                              "principal_krw": round(st["principal"], 2),
                              "investment_pnl_krw": round(current - st["principal"], 2),
                              "twr_pct": round((twr["factor"] - 1) * 100, 6)})

    def retry(signal, reason):
        signal["attempts"] += 1
        expired = signal["attempts"] >= cfg["missing_open_retry_sessions"]
        signal["status"] = "expired" if expired else "queued"
        signal["outcome"] = f"{reason}{' → 신호 만료' if expired else ''}"

    def drop(signal, reason):
        signal["status"] = "dropped"; signal["outcome"] = reason

    for at, _, kind, payload in events:
        if kind == "cash":
            st["cash"] += payload["amount_krw"]; st["principal"] += payload["amount_krw"]
            twr["pending_cf"] += payload["amount_krw"]
            if payload["kind"] == "contribution":
                st["applied"].append({"installment": payload["installment"], "amount_krw": payload["amount_krw"],
                                      "scheduled_date": payload["scheduled_date"], "effective_at": at.isoformat(),
                                      "recorded_at": payload["recorded_at"],
                                      "late": at > market_open(dt.date.fromisoformat(payload["scheduled_date"]), "KR") - CONTRIBUTION_LEAD})
            else:
                value(at, None, None)
            continue
        market, day = payload
        session = bronze["sessions"][(market, day)]
        bars = session["bars"]
        if kind == "open":
            plan = active.get((market, day))
            if plan is None:
                continue
            st["executed_plans"].add(plan["plan_id"])
            if plan["status"] == "cancelled":
                continue
            fx_fill = 1.0 if market == "KR" else _fx(rates, day, strict=True, keys=keys)
            for order in plan["orders"]:
                ticker = order["ticker"]; bar = bars.get(ticker)
                opening = bar.get("open") if bar else None
                halted = not opening or opening <= 0 or (bar.get("volume") == 0)
                fill = {"fill_id": order["order_id"], "plan_id": plan["plan_id"], "order_id": order["order_id"],
                        "trading_day": day, "market": market, "ticker": ticker, "side": order["side"],
                        "status": "unfilled", "reason_code": None, "price": None, "fx": None, "shares": 0,
                        "gross_krw": 0.0, "fees_krw": 0.0, "cash_flow_krw": 0.0}
                signal = signals.get(order.get("signal_id"))
                if order["side"] == "sell":
                    pos = st["positions"].get(ticker)
                    if pos is None:
                        fill["reason_code"] = "no_position"
                    elif halted or fx_fill is None:
                        fill["reason_code"] = "halt_or_missing_open"  # the exit stays pending for the next plan
                    else:
                        gross = pos["shares"] * opening * fx_fill; fee = gross * cost
                        st["cash"] += gross - fee; st["fees"] += fee; st["realized"] += gross - fee - pos["cost_krw"]
                        fill.update(status="filled", price=opening, fx=fx_fill, shares=pos["shares"], gross_krw=round(gross, 2),
                                    fees_krw=round(fee, 2), cash_flow_krw=round(gross - fee, 2),
                                    realized_pnl_krw=round(gross - fee - pos["cost_krw"], 2))
                        del st["positions"][ticker]
                elif ticker in st["positions"]:
                    fill["reason_code"] = "already_held"; drop(signal, "이미 보유")
                elif len(st["positions"]) >= cfg["max_positions"]:
                    fill["reason_code"] = "slots_full"; drop(signal, "슬롯 부족")
                elif halted or fx_fill is None:
                    fill["reason_code"] = "halt_or_missing_open" if halted else "missing_fx"
                    retry(signal, "시가 없음·거래정지")
                else:
                    budget = min(order["budget_krw"], st["cash"])
                    shares = math.floor(budget / (opening * fx_fill * (1 + cost)) * (1 - 1e-12))
                    if budget < order["budget_krw"] * cfg["min_fill_fraction"]:
                        fill["reason_code"] = "insufficient_cash"; drop(signal, "현금 부족")
                    elif shares < 1:
                        fill["reason_code"] = "below_one_share_at_open"; drop(signal, "시가 기준 1주 미만(갭상승)")
                    else:
                        gross = shares * opening * fx_fill; fee = gross * cost
                        st["cash"] -= gross + fee; st["fees"] += fee
                        atr = order.get("atr_prior") or 0
                        st["positions"][ticker] = {
                            "ticker": ticker, "market": market, "name": order["name"], "instrument": order["instrument"],
                            "shares": shares, "cost_krw": gross + fee, "entry_price": opening, "entry_date": day,
                            "highest": opening, "stop": opening - mult * atr if atr > 0 else opening * 0.75,
                            "last_close": opening, "quote_date": day, "fx": fx_fill, "halted": False,
                            "pending_exit": False, "signal_id": order["signal_id"]}
                        fill.update(status="filled", price=opening, fx=fx_fill, shares=shares, gross_krw=round(gross, 2),
                                    fees_krw=round(fee, 2), cash_flow_krw=round(-(gross + fee), 2))
                        signal["status"] = "filled"; signal["outcome"] = f"{day} 시가 체결"
                st["fills"].append(fill)
            for item in plan["excluded"]:
                signal = signals.get(item.get("signal_id"))
                if signal is None or signal["status"] != "queued":
                    continue
                if item["final"]:
                    drop(signal, item["reason"])
                else:
                    retry(signal, item["reason"])
            continue
        # close: marks, stops, valuation
        fx_mark = 1.0 if market == "KR" else _fx(rates, day, strict=False, keys=keys)
        for pos in st["positions"].values():
            if pos["market"] != market:
                continue
            bar = bars.get(pos["ticker"])
            if bar is None or (market == "US" and fx_mark is None):
                pos["halted"] = True
                continue
            pos.update(halted=False, last_close=bar["close"], quote_date=day, fx=fx_mark,
                       highest=max(pos["highest"], bar["close"]))
            if bar.get("atr") and bar["atr"] > 0:
                pos["stop"] = max(pos["stop"], pos["highest"] - mult * bar["atr"])
            if bar["close"] < pos["stop"]:
                pos["pending_exit"] = True
        st["marks"][market] = day
        value(at, market, day)
    if seed and not st["history"]:
        value(epoch, None, None)
    st["nav"] = nav(); st["twr_factor"] = twr["factor"]
    return st


def _xirr(flows: list[tuple[dt.datetime, float]]) -> float | None:
    """Annualised money-weighted return; None when the span is too short to annualise meaningfully."""
    if len(flows) < 2 or (flows[-1][0] - flows[0][0]).days < 30:
        return None
    t0 = flows[0][0]
    npv = lambda r: sum(cf / (1 + r) ** ((t - t0).days / 365) for t, cf in flows)
    lo, hi = -0.99, 10.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0: hi = mid
        else: lo = mid
    return (lo + hi) / 2 * 100


def _horizon(bronze: dict) -> dt.datetime:
    """Latest instant any Bronze record was written: the replay never applies events after it."""
    stamps = [bronze["genesis"]["epoch_at"]] + [d["observed_at"] for d in bronze["sessions"].values()] + \
        [c["recorded_at"] for c in bronze["contributions"]] + [p["frozen_at"] for p in bronze["plans"]] + \
        [s["observed_at"] for s in bronze["signals"]]
    return max(_instant(t) for t in stamps)


def replay(bronze: dict) -> dict:
    g = bronze["genesis"]; cfg = g["config"]
    horizon = _horizon(bronze)
    st = _run(bronze, until=horizon)  # a deposit landing tomorrow 08:30 is scheduled, not applied
    nav = st["nav"]
    positions = sorted(({
        "id": f"{p['market']}:{p['ticker']}", "ticker": p["ticker"], "market": p["market"], "name": p["name"],
        "instrument": p["instrument"], "shares": p["shares"], "last_close": p["last_close"], "fx": p["fx"],
        "quote_date": p["quote_date"], "value_krw": round(p["shares"] * p["last_close"] * p["fx"], 2),
        "weight_pct": p["shares"] * p["last_close"] * p["fx"] / nav * 100, "cost_krw": round(p["cost_krw"], 2),
        "unrealized_pnl_krw": round(p["shares"] * p["last_close"] * p["fx"] - p["cost_krw"], 2),
        "entry_date": p["entry_date"], "entry_price": p["entry_price"], "stop": p["stop"],
        "halted": p["halted"], "pending_exit": p["pending_exit"]} for p in st["positions"].values()),
        key=lambda p: -p["value_krw"])
    epoch = _instant(g["epoch_at"]); epoch_day = epoch.astimezone(KST).date()
    seed = g.get("seed")
    upcoming = first_installment(g) + len(bronze["contributions"])
    try:
        next_day = contribution_date(upcoming, schedule_anchor(g), cfg).isoformat()
    except ValueError:
        next_day = None  # beyond the holiday table; capture fails loudly before it is due
    start_nav = seed["nav_krw"] if seed else float(cfg["initial_capital_krw"])
    start_principal = seed["principal_krw"] if seed else float(cfg["initial_capital_krw"])
    live_contrib = sum(c["amount_krw"] for c in st["applied"])
    flows = [(epoch, -start_nav)] + \
        [(dt.datetime.fromisoformat(c["effective_at"]), -float(c["amount_krw"])) for c in st["applied"]]
    if st["history"]:
        flows.append((dt.datetime.fromisoformat(st["history"][-1]["at"]), nav))
    live_twr = (st["twr_factor"] - 1) * 100
    live = {"label": LIVE_LABEL, "start_at": g["epoch_at"], "start_nav_krw": round(start_nav, 2),
            "start_principal_krw": round(start_principal, 2), "contributions_krw": live_contrib,
            "installments_applied": len(st["applied"]), "nav_krw": round(nav, 2),
            "investment_pnl_krw": round(nav - start_nav - live_contrib, 2) if seed else round(nav - st["principal"], 2),
            "twr_pct": round(live_twr, 6), "xirr_pct": _xirr(flows)}
    backtest = None; history = []
    if seed:
        bt = load_backtest(seed)
        summary = bt["summary"]
        backtest = {"label": BACKTEST_LABEL, "availability": bt["availability"], "start_at": bt["start_at"],
                    "end_at": bt["end_at"], "marks": bt["marks"], "run_id": bt["run_id"],
                    "artifact_sha256": seed["artifact_sha256"], **{k: summary[k] for k in (
                        "principal_krw", "nav_krw", "investment_pnl_krw", "twr_pct", "installments_applied",
                        "fees_krw", "xirr_pct")}}
        history = [{**row, "segment": "backtest"} for row in bt["history"]]
    bt_factor = 1 + backtest["twr_pct"] / 100 if backtest else 1.0
    combined = {"label": "백테스트+실전 합산" if seed else LIVE_LABEL, "principal_krw": round(st["principal"], 2),
                "nav_krw": round(nav, 2), "investment_pnl_krw": round(nav - st["principal"], 2),
                "twr_pct": round((bt_factor * st["twr_factor"] - 1) * 100, 6)}
    history += [{**row, "segment": "live"} for row in st["history"]]
    fills_by_plan: dict[str, list] = {}
    for fill in st["fills"]:
        fills_by_plan.setdefault(fill["plan_id"], []).append(fill)
    latest = {}
    for plan in bronze["plans"]:
        latest[plan["market"]] = plan  # sorted by market, day, revision
    def outcome(plan):
        if plan["status"] == "cancelled":
            return "cancelled"
        return "executed" if plan["plan_id"] in st["executed_plans"] else "awaiting_open"
    superseded = {p["supersedes"] for p in bronze["plans"] if p.get("supersedes")}
    return {
        "schema_version": SCHEMA_VERSION, "account_id": ACCOUNT_ID, "policy_version": cfg["policy_version"],
        "mode": "forward_paper_funded", "strategy": cfg["strategy"], "started_at": g["epoch_at"],
        "epoch_kst_date": epoch_day.isoformat(), "config": cfg, "marks": st["marks"],
        # Whole-account balances. With a backtest seed, principal/P&L/TWR here span both segments
        # (scope says so); the live-only track record is segments.live.
        "performance": {
            "scope": "combined_backtest_and_live" if seed else "live",
            "initial_capital_krw": cfg["initial_capital_krw"],
            "contributions_krw": round(st["principal"] - cfg["initial_capital_krw"], 2),
            "installments_applied": first_installment(g) - 1 + len(st["applied"]),
            "principal_krw": round(st["principal"], 2),
            "nav_krw": round(nav, 2), "cash_krw": round(st["cash"], 2),
            "cash_weight_pct": st["cash"] / nav * 100 if nav else 100.0,
            "investment_pnl_krw": round(nav - st["principal"], 2),
            # combined scope: the backtest's fees/realized P&L are part of the account totals too
            "realized_pnl_krw": round(st["realized"] + (bt["summary"]["realized_pnl_krw"] if seed else 0), 2),
            "fees_krw": round(st["fees"] + (bt["summary"]["fees_krw"] if seed else 0), 2),
            "twr_pct": combined["twr_pct"], "xirr_pct": live["xirr_pct"] if not seed else None},
        "segments": {"backtest": backtest, "live": live, "combined": combined},
        "seed": ({k: seed[k] for k in ("source", "artifact", "artifact_sha256", "run_id", "availability",
                                       "start_at", "end_at", "principal_krw", "nav_krw", "cash_krw",
                                       "installments_applied", "marks")} if seed else None),
        "positions": positions,
        "as_of": horizon.isoformat(),
        "contributions": st["applied"],
        "scheduled_contributions": [
            {"installment": c["installment"], "amount_krw": c["amount_krw"], "scheduled_date": c["scheduled_date"],
             "effective_at": _contribution_effective(c).isoformat(), "label": cfg["contribution_day"]["label"]}
            for c in bronze["contributions"][len(st["applied"]):]],
        "recorded_contributions": len(bronze["contributions"]),
        "next_contribution": {"installment": upcoming, "amount_krw": contribution_amount(upcoming, cfg),
                              "scheduled_date": next_day, "label": cfg["contribution_day"]["label"]},
        "latest_plans": {m: ({**latest[m], "outcome": outcome(latest[m]), "fills": fills_by_plan.get(latest[m]["plan_id"], [])}
                             if m in latest else None) for m in MARKETS},
        "plans": [{"plan_id": p["plan_id"], "market": p["market"], "trading_day": p["trading_day"],
                   "revision": p["revision"], "status": "superseded" if p["plan_id"] in superseded else p["status"],
                   "supersedes": p.get("supersedes"), "frozen_at": p["frozen_at"], "basis_session": p["basis_session"],
                   "orders": len(p["orders"]), "outcome": "superseded" if p["plan_id"] in superseded else outcome(p)}
                  for p in bronze["plans"]],
        "fills": st["fills"],
        "signals": [{"id": s["id"], "ticker": s["ticker"], "market": s["market"], "name": s["name"],
                     "report_date": s["report_date"], "observed_at": s["observed_at"], "status": s["status"],
                     "outcome": s["outcome"]} for s in st["signals"].values()],
        "history": history,
        "excluded_instruments": [PARKING_EXCLUSION],
        "data_issues": st["issues"],
        "method": METHOD,
    }


# ── plans ───────────────────────────────────────────────────────────────────

def _pending_plans(bronze: dict, now: dt.datetime, before: dt.datetime, skip: tuple[str, str]) -> list[dict]:
    """Active plans that execute after `now` and before `before`: their cash/slots are already committed."""
    latest = {}
    for plan in bronze["plans"]:
        latest[(plan["market"], plan["trading_day"])] = plan
    return [p for key, p in latest.items() if key != skip and p["status"] == "active" and key not in bronze["sessions"]
            and now < market_open(dt.date.fromisoformat(p["trading_day"]), p["market"]) < before]


def build_plan(bronze: dict, market: str, now: dt.datetime) -> dict | None:
    """Next-open plan for `market` from its last recorded close. Never reads the targeted open."""
    cfg = bronze["genesis"]["config"]
    recorded = sorted(d for m, d in bronze["sessions"] if m == market)
    if not recorded:
        return None
    basis = recorded[-1]
    target = next_trading_day(dt.date.fromisoformat(basis), market)
    opens_at = market_open(target, market)
    if opens_at <= now:
        return None  # cutoff passed: a late plan is never created for an open already in progress
    st = _run(bronze, until=now)
    cost = cfg["cost_per_side"]
    session = bronze["sessions"][(market, basis)]
    rates = _fx_rows(bronze)
    fx_est = 1.0 if market == "KR" else _fx(rates, target.isoformat(), strict=True)
    incoming = [c for c in bronze["contributions"] if now < _contribution_effective(c) <= opens_at]
    pending = _pending_plans(bronze, now, opens_at, (market, target.isoformat()))
    reserved = sum(o["budget_krw"] for p in pending for o in p["orders"] if o["side"] == "buy") - \
        sum(o["est_cash_flow_krw"] for p in pending for o in p["orders"] if o["side"] == "sell")
    slot_delta = sum((1 if o["side"] == "buy" else -1) for p in pending for o in p["orders"])
    nav_est = st["nav"] + sum(c["amount_krw"] for c in incoming)
    budget_target = nav_est * cfg["position_weight"]
    orders, excluded = [], []
    sells = [p for p in st["positions"].values() if p["market"] == market and p["pending_exit"]]
    for pos in sorted(sells, key=lambda p: p["ticker"]):
        gross = pos["shares"] * pos["last_close"] * pos["fx"]
        orders.append({"side": "sell", "ticker": pos["ticker"], "market": market, "name": pos["name"],
                       "instrument": pos["instrument"], "signal_id": None, "reason_code": "chandelier_stop",
                       "reason": f"샹들리에 스탑: {pos['quote_date']} 종가 {pos['last_close']:,.2f} < 스탑 {pos['stop']:,.2f}",
                       "budget_krw": None, "est_price": pos["last_close"], "est_fx": pos["fx"], "est_shares": pos["shares"],
                       "est_gross_krw": round(gross, 2), "est_cost_krw": round(gross * cost, 2),
                       "est_cash_flow_krw": round(gross * (1 - cost), 2),
                       "current_weight_pct": gross / nav_est * 100, "planned_weight_pct": 0.0, "atr_prior": None,
                       "conditions": ["보유 전량을 시가에 매도(수량 확정, 금액은 종가 기준 추정)",
                                      "시가 없음·거래정지 시 미체결, 다음 거래일 계획에서 재시도"]})
    sell_proceeds = sum(o["est_cash_flow_krw"] for o in orders)
    available = st["cash"] + sum(c["amount_krw"] for c in incoming) - reserved + sell_proceeds
    slots = cfg["max_positions"] - len(st["positions"]) + len(orders) - slot_delta
    eligible = [s for s in st["signals"].values() if s["status"] == "queued" and s["market"] == market
                and _instant(s["observed_at"]) <= now and s["report_date"] < target.isoformat()]
    for s in sorted(eligible, key=lambda s: (s["report_date"], -s.get("clubs", 1), s["ticker"])):
        inst, why = instrument(s["ticker"], market)
        skip = lambda code, reason, final: excluded.append(
            {"ticker": s["ticker"], "name": s["name"], "signal_id": s["id"], "reason_code": code, "reason": reason, "final": final})
        bar = session["bars"].get(s["ticker"])
        if inst is None:
            skip("not_orderable", why, True); continue
        if s["ticker"] in st["positions"]:
            skip("already_held", "이미 보유 중", True); continue
        if slots <= 0:
            skip("slots_full", f"최대 {cfg['max_positions']}종목 슬롯 부족", True); continue
        if bar is None:
            skip("no_close", f"{basis} 종가 없음(거래정지·미수집) — 다음 계획에서 재평가", False); continue
        if fx_est is None:
            skip("missing_fx", "관측 환율 없음 — 다음 계획에서 재평가", False); continue
        budget = min(budget_target, available)
        if budget < budget_target * cfg["min_fill_fraction"]:
            skip("insufficient_cash", f"가용 현금 {max(available, 0):,.0f}원 < 진입 예산의 50%", True); continue
        est_shares = math.floor(budget / (bar["close"] * fx_est * (1 + cost)) * (1 - 1e-12))
        if est_shares < 1:
            skip("below_one_share", f"1주 가격이 예산 {budget:,.0f}원 초과 (정수 주식만 주문)", True); continue
        gross = est_shares * bar["close"] * fx_est
        bootstrap = s["source"] == "bootstrap-published-holding"
        conditions = [f"수량은 {basis} 종가 기준 추정치. 실제 수량은 시가로 재계산(주문 예산 한도, 정수 내림)",
                      "갭상승으로 시가 기준 1주 미만이면 미체결(신호 소멸)",
                      f"시가 없음·거래정지 시 미체결, 최대 {cfg['missing_open_retry_sessions']}회 계획까지 재시도",
                      "실행 시 현금이 예산의 50% 미만이면 매수 생략"]
        if market == "US":
            conditions.append(f"환율 {fx_est:,.2f}원(직전 관측) 기준 추정, 실제 환전율로 정산")
        orders.append({"side": "buy", "ticker": s["ticker"], "market": market, "name": s["name"], "instrument": inst,
                       "signal_id": s["id"], "reason_code": "bootstrap_published_holding" if bootstrap else "new_report",
                       "reason": (f"계정 개시 시점 W 공표 보유 종목 ({s['report_date']})" if bootstrap else
                                  f"학회 매수 리포트 {s['report_date']} ({s.get('clubs', 1)}개 학회) · "
                                  f"최초 관측 {_instant(s['observed_at']).astimezone(KST):%Y-%m-%d %H:%M} KST"),
                       "budget_krw": round(budget, 2), "est_price": bar["close"], "est_fx": fx_est, "est_shares": est_shares,
                       "est_gross_krw": round(gross, 2), "est_cost_krw": round(gross * cost, 2),
                       "est_cash_flow_krw": round(-gross * (1 + cost), 2),
                       "current_weight_pct": 0.0, "planned_weight_pct": gross / nav_est * 100,
                       "atr_prior": bar.get("atr"), "conditions": conditions})
        available -= budget; slots -= 1
    for seq, order in enumerate(orders, 1):
        order["seq"] = seq
    reasons = []
    if not orders:
        reasons.append("스탑 매도 신호 없음" if not sells else "스탑 매도 대상 있음")
        reasons.append("신규 매수 신호 없음" if not eligible else f"매수 후보 {len(eligible)}건 모두 제외(사유 참조)")
    contributions = [{"installment": c["installment"], "amount_krw": c["amount_krw"], "scheduled_date": c["scheduled_date"],
                      "effective_at": _contribution_effective(c).isoformat(), "label": cfg["contribution_day"]["label"]} for c in incoming]
    return {
        "schema_version": SCHEMA_VERSION, "account_id": ACCOUNT_ID, "policy_version": cfg["policy_version"],
        "market": market, "trading_day": target.isoformat(), "open_at": opens_at.isoformat(),
        "basis_session": basis, "basis_observed_at": session["observed_at"],
        "skipped_closures": skipped_closures(dt.date.fromisoformat(basis), target, market),
        "quantity_basis": "estimate_from_close",
        "account_snapshot": {
            "nav_est_krw": round(nav_est, 2), "cash_krw": round(st["cash"], 2),
            "incoming_contributions_krw": sum(c["amount_krw"] for c in incoming),
            "reserved_for_earlier_plans_krw": round(reserved, 2), "est_sell_proceeds_krw": round(sell_proceeds, 2),
            "cash_after_plan_est_krw": round(available, 2), "target_budget_krw": round(budget_target, 2),
            "position_count": len(st["positions"]), "slots_free": slots, "marks": st["marks"],
            "principal_krw": round(st["principal"] + sum(c["amount_krw"] for c in incoming), 2)},
        "contributions": contributions, "orders": orders, "excluded": excluded,
        "no_trade_reason": " · ".join(reasons) if reasons else None,
        "nav_timing_note": "계정 평가액은 시장별 최근 종가(KR·US 기준일이 다를 수 있음)로 계산한 추정치",
    }


def _core(plan: dict) -> list:
    """The decision: a few won of NAV drift (float budgets) must not cause a revision or a re-send."""
    return [plan["status"], sorted((o["side"], o["ticker"], o["est_shares"]) for o in plan["orders"]),
            sorted(c["installment"] for c in plan["contributions"]),
            sorted((e["ticker"], e["reason_code"]) for e in plan["excluded"])]


def _plan_dir(market: str, day: str) -> Path:
    return _base() / "plans" / market / day / POLICY_VERSION


def freeze_plan(body: dict, now: dt.datetime, *, status: str = "active", reason: str | None = None,
                uncancel: bool = False) -> dict:
    """Write revision 1, or an explicit next revision when the decision changed before the open.

    A cancelled latest revision is final: a routine rebuild never reactivates it, only `uncancel=True`.
    """
    folder = _plan_dir(body["market"], body["trading_day"])
    existing = [_read(p) for p in sorted(folder.glob("r*.json"))]
    latest = existing[-1] if existing else None
    candidate = {**body, "status": status}
    if latest and latest["status"] == "cancelled" and status == "active" and not uncancel:
        return latest
    if uncancel and not (latest and latest["status"] == "cancelled"):
        raise ValueError("only a cancelled plan can be un-cancelled")
    if latest and _core(latest) == _core(candidate):
        return latest  # same decision: no new revision, no duplicate alert
    if now >= market_open(dt.date.fromisoformat(body["trading_day"]), body["market"]):
        raise ValueError("plans are immutable once the targeted open has passed")
    plan = finalize_plan(candidate, latest, now, reason)
    _publish(folder / f"r{plan['revision']:02d}.json", plan)
    return plan


def finalize_plan(candidate: dict, latest: dict | None, now: dt.datetime, reason: str | None = None) -> dict:
    """Assign revision, ids and freeze time (shared by live Bronze plans and the in-memory backtest)."""
    revision = latest["revision"] + 1 if latest else 1
    plan_id = hashlib.sha256(f"{ACCOUNT_ID}|{candidate['market']}|{candidate['trading_day']}|{POLICY_VERSION}|r{revision}".encode()).hexdigest()[:16]
    orders = [{**order, "order_id": f"{plan_id}-{seq:02d}"} for seq, order in enumerate(candidate["orders"], 1)]
    return {**candidate, "orders": orders, "plan_id": plan_id, "revision": revision, "frozen_at": now.isoformat(),
            "supersedes": latest["plan_id"] if latest else None,
            "revision_reason": reason or ("새 관측으로 결정이 바뀌어 개정" if latest else None)}


def cancel_plan(market: str, day: str, reason: str, now: dt.datetime) -> dict:
    folder = _plan_dir(market, day)
    existing = [_read(p) for p in sorted(folder.glob("r*.json"))]
    if not existing:
        raise ValueError("no plan to cancel")
    body = {k: v for k, v in existing[-1].items() if k not in ("plan_id", "revision", "frozen_at", "supersedes", "revision_reason", "status")}
    return freeze_plan({**body, "orders": [], "no_trade_reason": f"취소: {reason}"}, now, status="cancelled", reason=f"취소: {reason}")


def uncancel_plan(market: str, day: str, reason: str, now: dt.datetime) -> dict:
    """Explicitly reactivate a cancelled plan by rebuilding it from the data available now (before its open)."""
    body = build_plan(load_bronze(), market, now)
    if body is None or body["trading_day"] != day:
        raise ValueError("the cancelled plan's open has passed or its basis session changed; nothing to reactivate")
    return freeze_plan(body, now, reason=f"취소 해제: {reason}", uncancel=True)


# ── capture (pipeline hook) ─────────────────────────────────────────────────

def start_account(*, epoch: dt.datetime, contribution_day: dict, as_of: str, positions: dict, reports: list, policy: dict,
                  seed: dict | None = None) -> dict:
    """Write the genesis. Every caller passes the epoch and contribution-day rule explicitly.

    `seed` (from `seed_from_backtest`) starts the live segment from a backtest end state; its queued signals
    become the bootstrap and its known reports stay known, so nothing is re-signalled or lost at the seam.
    """
    now = epoch
    if now.tzinfo is None:
        raise ValueError("epoch requires a timezone")
    for key in ("atr_period", "atr_mult", "max_positions"):
        if policy.get(key) != CONFIG[key]:
            raise ValueError(f"published W {key} differs from the funded config; a new account epoch is required")
    bootstrap = [{"id": f"bootstrap:{t}", "ticker": t, "market": p.get("market", "KR"), "name": p.get("display_name", t),
                  "source": "bootstrap-published-holding", "clubs": p.get("n_clubs", 1), "report_date": as_of,
                  "observed_at": now.isoformat()} for t, p in sorted(positions.items())]
    known = sorted({report_id(d, t, s) for d, t, s, _ in reports if d.isoformat() <= as_of})
    anchor = None
    if seed:
        if _instant(seed["end_at"]) >= now:
            raise ValueError("the backtest seed must end before the live epoch")
        bootstrap, known, anchor = seed.pop("queued_signals"), seed.pop("known_report_ids"), seed["schedule_anchor"]
    genesis = {"schema_version": SCHEMA_VERSION, "account_id": ACCOUNT_ID, "epoch_at": now.isoformat(),
               "as_of": as_of, "config": {**CONFIG, "contribution_day": contribution_day}, "bootstrap": bootstrap,
               "known_report_ids": known, "schedule_anchor": anchor, "seed": seed,
               "note": "새 계정. execution-v2(1억원) 계정과 그 Bronze는 읽지도 재해석하지도 않음"}
    _publish(_base() / "account.json", genesis)
    return genesis


def record_signals(genesis: dict, *, as_of: str, reports: list, ticker_reports: dict, now: dt.datetime) -> int:
    known = set(genesis["known_report_ids"]); added = 0
    for day, ticker, source, clubs in reports:
        key = report_id(day, ticker, source)
        path = _base() / "signals" / f"{key[:24]}.json"
        if day.isoformat() > as_of or key in known or path.exists():
            continue
        info = next((r for r in (ticker_reports or {}).get(ticker, []) if Path(r["source_file"]).name == source), {})
        _publish(path, {"id": key, "ticker": ticker, "source": source, "clubs": clubs, "report_date": day.isoformat(),
                        "observed_at": now.isoformat(), "market": info.get("market", "KR" if ticker.isdigit() else "US"),
                        "name": info.get("display_name", ticker)})
        added += 1
    return added


def _bar(frame: pd.DataFrame | None, day: dt.date, atr_column: str) -> dict | None:
    ts = pd.Timestamp(day)
    if frame is None or ts not in frame.index:
        return None
    row = frame.loc[ts]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[-1]
    def num(key):
        v = row.get(key)
        return float(v) if v is not None and pd.notna(v) and math.isfinite(float(v)) else None
    close = num("close")
    if close is None or close <= 0:
        raise ValueError(f"invalid closing bar {day}")
    return {"open": num("open"), "high": num("high"), "low": num("low"), "close": close,
            "volume": num("volume"), "atr": num(atr_column)}


def record_sessions(market: str, *, universe: list[str], prices: dict, benchmark: pd.Series, fx_series: pd.Series,
                    atr_column: str, now: dt.datetime, after: str | None = None) -> dict:
    """Record every completed, data-ready session up to this market's own cutoff. Never a future bar."""
    folder = _base() / "sessions" / market
    recorded = sorted(p.stem for p in folder.glob("*.json"))
    cap = expected_session(market, now)
    bench = benchmark.dropna()
    # `after`: a seeded account's last backtest mark; sessions since then are recorded (closes only, no plans).
    last = recorded[-1] if recorded else after
    window_start = dt.date.fromisoformat(last) if last else cap - dt.timedelta(days=14)
    for ts in bench.index:
        if window_start < ts.date() <= cap and not is_trading_day(ts.date(), market):
            raise ValueError(f"{market} benchmark has a bar on calendar holiday {ts.date()}: fix market_calendars.py")
    days = [cap] if not last else []
    if last:
        day = dt.date.fromisoformat(last)
        while (day := next_trading_day(day, market)) <= cap:
            days.append(day)
    fx_cap = closed_bar_end("FX", now=now)
    written = []
    for day in days:
        if pd.Timestamp(day) not in bench.index:
            break  # data not ready: later sessions wait too, so fills stay in order
        rates = fx_series.dropna().loc[:pd.Timestamp(min(day, fx_cap))].tail(5)
        doc = {"schema_version": SCHEMA_VERSION, "account_id": ACCOUNT_ID, "market": market,
               "session_date": day.isoformat(), "observed_at": now.isoformat(),
               "bars": {t: _bar(prices.get(t), day, atr_column) for t in sorted(universe)},
               "fx_usdkrw": [{"date": i.date().isoformat(), "value": float(v)} for i, v in rates.items()]}
        _publish(folder / f"{day.isoformat()}.json", doc)
        written.append(day.isoformat())
    latest = (written or recorded or [None])[-1]
    if latest and not written:
        # A signal first observed after its basis session was recorded still needs that session's close.
        known = set()
        for path in sorted(folder.glob(f"{latest}*.json")):
            known |= set(_read(path)["bars"])
        missing = sorted(set(universe) - known)
        if missing:
            day = dt.date.fromisoformat(latest)
            seq = len(list(folder.glob(f"{latest}~*.json"))) + 1
            _publish(folder / f"{latest}~{seq:02d}.json", {
                "schema_version": SCHEMA_VERSION, "account_id": ACCOUNT_ID, "market": market, "session_date": latest,
                "observed_at": now.isoformat(), "bars": {t: _bar(prices.get(t), day, atr_column) for t in missing},
                "fx_usdkrw": []})
    return {"market": market, "expected_session": cap.isoformat(), "recorded_through": latest,
            "ready": latest == cap.isoformat(), "written": written}


def record_due_contributions(genesis: dict, *, until: dt.datetime, now: dt.datetime) -> list[int]:
    """Record installments whose deposit lands at or before `until` (the latest planned open)."""
    anchor = schedule_anchor(genesis)
    folder = _base() / "contributions"; added = []; n = first_installment(genesis)
    while True:
        day = contribution_date(n, anchor, genesis["config"])
        if market_open(day, "KR") - CONTRIBUTION_LEAD > until:
            return added
        path = folder / f"{n:04d}.json"
        if not path.exists():
            _publish(path, contribution_record(genesis, n, day, now))
            added.append(n)
        n += 1


def contribution_record(genesis: dict, n: int, day: dt.date, now: dt.datetime) -> dict:
    return {"schema_version": SCHEMA_VERSION, "account_id": ACCOUNT_ID, "installment": n,
            "amount_krw": contribution_amount(n, genesis["config"]), "scheduled_date": day.isoformat(),
            "recorded_at": now.isoformat(), "rule_status": genesis["config"]["contribution_day"]["status"],
            "rule_label": genesis["config"]["contribution_day"]["label"]}


def capture(*, as_of, positions, prices, core, reports, ticker_reports, policy, now=None, **_) -> dict | None:
    """Capture inputs under the genesis mandate, independently of research reoptimization.

    Does nothing until an operator ran `funded_account.py init` (no implicit start).
    `policy` describes today's research run; it never replaces the funded account's frozen config.
    """
    from backtest import fx
    instant = now or dt.datetime.now(dt.timezone.utc)
    if not (_base() / "account.json").exists():
        return None
    genesis = load_bronze()["genesis"]
    frozen_policy = {key: genesis["config"][key] for key in ("atr_period", "atr_mult", "max_positions")}
    if any(policy.get(key) != value for key, value in frozen_policy.items()):
        logging.getLogger(__name__).warning(
            "Research policy %s differs from funded account %s; continuing with frozen policy %s",
            policy, genesis["account_id"], frozen_policy,
        )
    if fx._USDKRW is None:
        raise ValueError("funded account requires observed USDKRW, not a constant fallback")
    record_signals(genesis, as_of=as_of, reports=reports, ticker_reports=ticker_reports, now=instant)
    bronze = load_bronze()
    atr_column = ATR_COLUMN[genesis["config"]["atr_period"]]
    readiness = {}
    for market in MARKETS:
        universe = [s["ticker"] for s in genesis["bootstrap"] + bronze["signals"] if s["market"] == market] + \
            [p["ticker"] for p in (genesis.get("seed") or {}).get("positions", []) if p["market"] == market]
        readiness[market] = record_sessions(market, universe=universe, prices=prices, benchmark=core[BENCHMARKS[market]],
                                            fx_series=fx._USDKRW, atr_column=atr_column, now=instant,
                                            after=(genesis.get("seed") or {}).get("marks", {}).get(market))
    bronze = load_bronze()
    targets = {}
    for market in MARKETS:
        recorded = sorted(d for m, d in bronze["sessions"] if m == market)
        if recorded:
            targets[market] = market_open(next_trading_day(dt.date.fromisoformat(recorded[-1]), market), market)
    if targets:
        record_due_contributions(genesis, until=max(targets.values()), now=instant)
    for market in sorted(targets, key=targets.get):  # earlier open first: later plans see its reservations
        body = build_plan(load_bronze(), market, instant)
        if body is not None:
            freeze_plan(body, instant)
    write()
    return readiness


def seed_from_backtest(path: Path) -> dict:
    """Explicit live seed: the backtest end state plus its provenance (artifact path, content hash, run id)."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("segment") != "backtest" or doc.get("account_id") != ACCOUNT_ID:
        raise ValueError("not a funded-account backtest artifact")
    end = doc["end_state"]
    path = Path(path).resolve()
    artifact = lake.rel(path) if path.is_relative_to(lake.ROOT) else path.as_posix()
    return {"source": "backtest", "artifact": artifact, "artifact_sha256": canonical_sha256(doc),
            "run_id": doc["run_id"], "availability": doc["availability"], "start_at": doc["start_at"],
            "end_at": doc["end_at"], "schedule_anchor": doc["schedule_anchor"],
            "contribution_day": doc["config"]["contribution_day"], **{k: end[k] for k in (
                "cash_krw", "principal_krw", "nav_krw", "installments_applied", "marks", "positions",
                "queued_signals", "known_report_ids")}}


def init(*, epoch: dt.datetime, contribution_day: str, status: str, now: dt.datetime | None = None,
         seed_path: Path | None = None) -> dict:
    """Operator start. Bootstrap = W holdings published before the epoch; known reports = buy reports so far.

    The epoch must be explicit, timezone-aware, not in the future and at most a day old: an old epoch would
    record months of contributions late and pretend the account existed before anyone decided to start it.
    Subsequent captures use the genesis's frozen ATR/slot policy even if research is reoptimized.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    if epoch.tzinfo is None or not now - dt.timedelta(days=1) <= epoch <= now:
        raise ValueError("--epoch must be a timezone-aware instant within the last 24 hours")
    if seed_path is not None:
        seed = seed_from_backtest(seed_path)
        rule = seed.pop("contribution_day")
        if (rule["day_of_month"], rule["status"]) != (contribution_day_rule(contribution_day, status)["day_of_month"],
                                                      contribution_day_rule(contribution_day, status)["status"]):
            raise ValueError("--contribution-day/--contribution-day-status disagree with the backtest's rule")
        return start_account(epoch=epoch, contribution_day=rule, as_of=epoch.astimezone(KST).date().isoformat(),
                             positions={}, reports=[], seed=seed,
                             policy={k: CONFIG[k] for k in ("atr_period", "atr_mult", "max_positions")})
    rule = contribution_day_rule(contribution_day, status)
    published = json.loads((lake.WEB_DATA / "strategy-backtest.json").read_text(encoding="utf-8"))
    if _instant(published["generated_at"]) > epoch:
        raise ValueError("published W holdings are newer than the epoch; choose a later epoch")
    positions = {p["ticker"]: {"market": p["market"], "display_name": p["display_name"],
                               "n_clubs": len(p.get("trigger_schools") or []) or 1}
                 for p in published["signals"]["open_positions"]}
    perf = pd.read_csv(lake.GOLD_REPORT_PERFORMANCE, encoding="utf-8-sig", dtype={"ticker": str})
    perf = perf[perf["ticker"].notna() & perf["report_date"].notna() & (perf["rating_class"] == "buy")]
    reports = []
    for row in perf.itertuples():
        try:
            day = dt.date.fromisoformat(str(row.report_date))
        except ValueError:
            continue
        ticker = str(row.ticker).zfill(6) if str(row.market) == "KR" else str(row.ticker)
        reports.append((day, ticker, Path(str(row.source_file)).name, 1))
    return start_account(epoch=epoch, contribution_day=rule, as_of=epoch.astimezone(KST).date().isoformat(),
                         positions=positions, reports=reports,
                         policy={k: CONFIG[k] for k in ("atr_period", "atr_mult", "max_positions")})


# ── silver / gold ───────────────────────────────────────────────────────────

def write(check: bool = False) -> dict | None:
    bronze = load_bronze()
    targets = (lake.SILVER_FUNDED_LEDGER, lake.FUNDED_ACCOUNT)
    if bronze is None:
        if any(t.exists() for t in targets):
            raise ValueError("funded account artifacts exist without a genesis record")
        return None
    result = replay(bronze)
    if check:
        for target in targets:
            if not target.exists() or json.loads(target.read_text(encoding="utf-8")) != result:
                raise ValueError(f"funded account drift: {target.name}")
        return result
    for target in targets:
        replace_atomic(target, lambda fh: json.dump(result, fh, ensure_ascii=False, indent=1, allow_nan=False))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    start = sub.add_parser("init", help="start the account (writes the append-only genesis; no defaults)")
    start.add_argument("--epoch", required=True, help="timezone-aware ISO instant within the last 24 hours")
    start.add_argument("--contribution-day", required=True, help="'first-trading-day' or a day of month 1..28")
    start.add_argument("--contribution-day-status", required=True, choices=("proposed", "confirmed"),
                       help="'proposed' keeps the 제안값(미확정) label everywhere")
    start.add_argument("--seed-backtest", type=Path, help="start the live segment from this backtest artifact's end state")
    sub.add_parser("write", help="replay Bronze into the Silver ledger and Gold artifact")
    sub.add_parser("check", help="fail if the Silver/Gold artifacts drift from a Bronze replay")
    cancel = sub.add_parser("cancel", help="explicitly cancel a frozen plan before its open")
    cancel.add_argument("--market", choices=MARKETS, required=True)
    cancel.add_argument("--day", required=True)
    cancel.add_argument("--reason", required=True)
    uncancel = sub.add_parser("uncancel", help="explicitly reactivate a cancelled plan before its open")
    uncancel.add_argument("--market", choices=MARKETS, required=True)
    uncancel.add_argument("--day", required=True)
    uncancel.add_argument("--reason", required=True)
    parser.add_argument("--check", action="store_true", help="alias of the check command")
    args = parser.parse_args()
    if args.check or args.command == "check":
        write(check=True)
    elif args.command == "init":
        init(epoch=dt.datetime.fromisoformat(args.epoch), contribution_day=args.contribution_day,
             status=args.contribution_day_status, seed_path=args.seed_backtest)
        write()
    elif args.command == "cancel":
        cancel_plan(args.market, args.day, args.reason, dt.datetime.now(dt.timezone.utc))
        write()
    elif args.command == "uncancel":
        uncancel_plan(args.market, args.day, args.reason, dt.datetime.now(dt.timezone.utc))
        write()
    else:
        write()
