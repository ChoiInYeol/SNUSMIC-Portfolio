"""Version 2 paper account: frozen inputs, fixed mandate, the actual W backtest engine.

Only globally completed KR/US/FX sessions are evaluated, so reporting can lag the KR date.
Old v1 weight-following observations are archived and never reinterpreted as opening fills.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import lake
from backtest import fx
from backtest.parking_engine import ENGINE_VERSION, market_open
from backtest.config import POSITION_WEIGHT, COST_PER_SIDE, KOSPI_PARK_COST
from backtest.strategies import run_parking_core_chandelier
from lake_prices import publish_new
from market_clock import closed_bar_end

INITIAL = 100_000_000.0


def report_id(day, ticker, source):
    return hashlib.sha256(f"{day}|{ticker}|{source}".encode()).hexdigest()


def capture(*, strategy, as_of, positions, prices, core, reports, ticker_reports, policy, now=None, **_):
    instant = now or dt.datetime.now(dt.timezone.utc)
    paths = sorted(lake.BRONZE_PAPER_EXECUTION.glob("*.json"))
    old = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    if old and as_of < old[-1]["as_of"]:
        return
    known = {i for doc in old for i in doc["known_report_ids"]}
    policy = old[0]["policy"] if old else {**policy, "strategy": strategy, "engine_version": ENGINE_VERSION,
        "position_weight": POSITION_WEIGHT, "cost_per_side": COST_PER_SIDE, "parking_cost": KOSPI_PARK_COST}
    if policy["strategy"] != "W_allweather_chandelier":
        raise ValueError("execution-v2 currently supports the fixed W mandate only")
    if policy["engine_version"] != ENGINE_VERSION:
        raise ValueError("a new execution engine requires an explicit account epoch")
    events = []; ids = []
    for day, ticker, source, clubs in reports:
        if day.isoformat() > as_of:
            continue
        key = report_id(day, ticker, source); ids.append(key)
        if not old or key in known:
            continue  # Existing reports are not retrospectively turned into new signals.
        items = (ticker_reports or {}).get(ticker, [])
        info = next((r for r in items if Path(r["source_file"]).name == source), {})
        events.append({"id": key, "ticker": ticker, "source": source, "clubs": clubs,
                       "report_date": day.isoformat(), "observed_at": instant.isoformat(),
                       "market": info.get("market", "KR" if ticker.isdigit() else "US"),
                       "name": info.get("display_name", ticker)})
    bootstrap = old[0]["bootstrap"] if old else [{"ticker": t, "market": p.get("market", "KR"),
        "name": p.get("display_name", t), "source": "bootstrap-published-holding", "clubs": p.get("n_clubs", 1),
        "report_date": as_of, "observed_at": instant.isoformat()} for t, p in positions.items()]
    wanted = {r["ticker"] for r in bootstrap + events}
    wanted.update(r["ticker"] for doc in old for r in doc["report_events"])
    start = (dt.date.fromisoformat(old[-1]["as_of"]) - dt.timedelta(days=10)) if old else None
    bars = {}
    for ticker in sorted(wanted):
        if ticker not in prices:
            raise ValueError(f"missing held/queued paper input: {ticker}")
        market = next((r["market"] for r in bootstrap + events if r["ticker"] == ticker), "KR" if ticker.isdigit() else "US")
        df = prices[ticker].loc[:pd.Timestamp(closed_bar_end(market, dt.date.fromisoformat(as_of), now=instant))]
        df = df.loc[pd.Timestamp(start):] if start else df.tail(2)
        bars[ticker] = [{"date": index.date().isoformat(), **{k: float(row[k]) if pd.notna(row[k]) and math.isfinite(float(row[k])) else None
                        for k in ("open", "high", "low", "close", "volume", "atr", "atr20", "atr63") if k in row}}
                        for index, row in df.iterrows()]
    def rows(series):
        cap = min(dt.date.fromisoformat(as_of), closed_bar_end("FX", now=instant))
        series = series.loc[:pd.Timestamp(cap)]
        series = series.loc[pd.Timestamp(start):] if start else series.tail(2)
        return [{"date": index.date().isoformat(), "value": float(value)} for index, value in series.items()]
    if fx._USDKRW is None:
        raise ValueError("paper execution requires observed FX, not a constant fallback")
    complete = min(dt.date.fromisoformat(as_of), *(closed_bar_end(m, now=instant) for m in ("KR", "US", "FX")))
    doc = {"schema_version": "2.0", "as_of": as_of, "complete_through": complete.isoformat(),
           "observed_at": instant.isoformat(), "source": "pipeline execution inputs", "policy": policy,
           "known_report_ids": ids, "report_events": events, "bootstrap": bootstrap,
           "bars": bars, "parking": rows(core["INDEX:AllWeather"]), "fx": rows(fx._USDKRW)}
    name = instant.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
    target = lake.BRONZE_PAPER_EXECUTION / f"{name}.json"
    if not target.exists():
        publish_new(target, json.dumps(doc, ensure_ascii=False, allow_nan=False).encode())


def replay(observations):
    docs = sorted(observations, key=lambda d: dt.datetime.fromisoformat(d["observed_at"]))
    if not docs:
        raise ValueError("no execution observations")
    first = docs[0]; policy = first["policy"]
    if policy["engine_version"] != ENGINE_VERSION or policy["strategy"] != "W_allweather_chandelier":
        raise ValueError("unsupported execution mandate")
    if (policy["position_weight"], policy["cost_per_side"], policy["parking_cost"]) != (POSITION_WEIGHT, COST_PER_SIDE, KOSPI_PARK_COST):
        raise ValueError("execution costs/weight changed inside frozen mandate")
    activation = dt.datetime.fromisoformat(first["observed_at"])
    if activation.tzinfo is None or any(e["observed_at"] != first["observed_at"] or e["report_date"] > first["as_of"] or e["market"] not in ("KR", "US") for e in first["bootstrap"]):
        raise ValueError("invalid bootstrap availability")
    complete = docs[-1]["complete_through"]
    bars = {}; parking = {}; rates = {}; seen_events = {}; issues = []; sealed = None
    for doc in docs:
        if doc["schema_version"] != "2.0" or doc["policy"] != policy:
            raise ValueError("execution policy changed inside a frozen account")
        observed = dt.datetime.fromisoformat(doc["observed_at"])
        if observed.tzinfo is None:
            raise ValueError("observation requires a timezone")
        ceiling = min(doc["as_of"], *(closed_bar_end(m, now=observed).isoformat() for m in ("KR", "US", "FX")))
        if doc["complete_through"] > ceiling or (sealed and doc["complete_through"] < sealed):
            raise ValueError("invalid completed-session seal")
        if doc["bootstrap"] != first["bootstrap"]:
            raise ValueError("bootstrap changed inside a frozen account")
        markets = {e["ticker"]: e["market"] for e in first["bootstrap"] + list(seen_events.values()) + doc["report_events"]}
        for ticker, rows in doc["bars"].items():
            target = bars.setdefault(ticker, {})
            for row in rows:
                date = row["date"]
                if ticker not in markets or date > min(doc["as_of"], closed_bar_end(markets[ticker], now=observed).isoformat()):
                    raise ValueError("future paper bar")
                if any(not isinstance(row.get(k), (int, float)) or not math.isfinite(row[k]) or row[k] < 0 for k in ("open", "close")) or row["close"] == 0:
                    raise ValueError("invalid paper stock quote")
                if sealed and date <= sealed and date not in target:
                    if date >= activation.date().isoformat():
                        issues.append(f"late bar ignored after session seal: {ticker} {date}")
                    continue
                target.setdefault(date, row)
        for field, target in (("parking", parking), ("fx", rates)):
            for row in doc[field]:
                if row["date"] > min(doc["as_of"], closed_bar_end("FX", now=observed).isoformat()) or not math.isfinite(row["value"]) or row["value"] <= 0:
                    raise ValueError("invalid paper index/FX quote")
                if sealed and row["date"] <= sealed and row["date"] not in target:
                    if row["date"] >= activation.date().isoformat():
                        issues.append(f"late {field} ignored after session seal: {row['date']}")
                    continue
                target.setdefault(row["date"], row["value"])
        for event in doc["report_events"]:
            available = dt.datetime.fromisoformat(event["observed_at"])
            if available.tzinfo is None or available != observed or event["report_date"] > doc["as_of"]:
                raise ValueError("invalid report availability timestamp")
            seen_events.setdefault(event["id"], event)
        sealed = doc["complete_through"]
    frames = {t: pd.DataFrame(rows.values()).set_index("date").sort_index() for t, rows in bars.items() if rows}
    for frame in frames.values():
        frame.index = pd.to_datetime(frame.index)
        for column in ("open", "high", "low", "close", "atr", "atr20", "atr63"):
            if column in frame:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
    park = pd.Series(parking, dtype=float).sort_index(); park.index = pd.to_datetime(park.index)
    fx_series = pd.Series(rates, dtype=float).sort_index(); fx_series.index = pd.to_datetime(fx_series.index)
    events = first["bootstrap"] + list(seen_events.values())
    metadata = {}; schedule = {}; pending = []
    for event in events:
        ticker = event["ticker"]; market = event["market"]
        metadata.setdefault(ticker, []).append({"report_date": dt.date.fromisoformat(event["report_date"]),
            "market": market, "display_name": event["name"], "source_file": event["source"], "school": "published",
            "target_price": None, "available_at": event["observed_at"]})
        available = dt.datetime.fromisoformat(event["observed_at"])
        dates = [index.date() for index in frames.get(ticker, pd.DataFrame()).index if
                 index.date().isoformat() <= complete and index.date().isoformat() > event["report_date"] and market_open(index.date(), market) > available]
        if dates:
            schedule.setdefault(min(dates), []).append((ticker, event["source"], event["clubs"]))
        else:
            pending.append(event)
    calendar = sorted({index.date() for frame in [*frames.values(), park] for index in frame.index if
        index.date().isoformat() <= complete and (market_open(index.date(), "KR") > activation or market_open(index.date(), "US") > activation)})
    result = None
    original = fx._USDKRW
    try:
        fx.set_usdkrw(fx_series)
        if calendar:
            result = run_parking_core_chandelier(frames, [], calendar, policy["strategy"], park,
                int(policy["atr_period"]), float(policy["atr_mult"]), int(policy["max_positions"]),
                ticker_reports=metadata, parking_name="올웨더", entry_schedule=schedule, collect_history=True)
    finally:
        fx._USDKRW = original
    nav = float(result["nav_df"].iloc[-1]) if result else INITIAL
    holdings = result["open_positions"] if result else {}
    positions = [{"id": f"{p['market']}:{t}", "name": p["display_name"],
        "quote_date": frames[t].loc[:pd.Timestamp(complete)].index[-1].date().isoformat(),
        "value_krw": p["shares"] * p["last_close"] * p.get("fx", 1),
        "weight_pct": p["shares"] * p["last_close"] * p.get("fx", 1) / nav * 100,
        "return_pct": (p["shares"] * p["last_close"] * p.get("fx", 1) / p["cost"] - 1) * 100} for t, p in holdings.items()]
    if result:
        parked = result["execution"]["parked_krw"]
        positions.append({"id": "INDEX:AllWeather", "name": "올웨더 합성지수", "quote_date": complete,
                          "value_krw": parked, "weight_pct": parked / nav * 100, "return_pct": None})
    history = [{"as_of": first["complete_through"], "nav_krw": INITIAL, "daily_return_pct": 0.0, "return_pct": 0.0, "rebalanced": False}]
    if result:
        previous = INITIAL
        fill_dates = {f["date"] for f in result["execution"]["fills"]}
        for index, value in result["nav_df"].items():
            history.append({"as_of": index.date().isoformat(), "nav_krw": float(value),
                            "daily_return_pct": (value / previous - 1) * 100, "return_pct": (value / INITIAL - 1) * 100,
                            "rebalanced": index.date().isoformat() in fill_dates}); previous = value
    return {"schema_version": "2.0", "engine_version": ENGINE_VERSION, "policy": policy,
            "as_of": complete, "source_as_of": docs[-1]["as_of"], "started_at": first["observed_at"],
            "strategy": policy["strategy"], "mode": "forward_paper", "initial_capital_krw": INITIAL,
            "nav_krw": nav, "cash_krw": 0.0 if result else INITIAL, "cash_weight_pct": 0.0 if result else 100.0,
            "return_pct": (nav / INITIAL - 1) * 100, "daily_return_pct": history[-1]["daily_return_pct"],
            "fees_krw": result["execution"]["fees_krw"] if result else 0.0,
            "positions": positions, "history": history, "fills": result["execution"]["fills"] if result else [],
            "data_issues": sorted(set(issues)),
            "target_positions": list({f"{e['market']}:{e['ticker']}": {"id": f"{e['market']}:{e['ticker']}", "name": e["name"], "weight_pct": POSITION_WEIGHT * 100} for e in pending}.values()),
            "status": "tracking" if result else "awaiting_next_open",
            "method": "W 백테스트와 동일한 parking-open-v2 엔진. 관측 이후 시장별 다음 시가 진입·종가 스탑 신호 후 다음 시가 청산. "
                      "전일 ATR·환율·잔고, 주식 0.3%·파킹 0.05%. KR/US/FX 전체 완료 일봉까지만 평가하므로 국내 날짜보다 늦을 수 있음. "
                      "올웨더는 시가 없는 합성지수 프록시. 새 계정은 과거 수익을 승계하지 않으며 실계좌 주문이 아님."}
