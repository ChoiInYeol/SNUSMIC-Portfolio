"""Forward-only paper portfolio following published strategy weights at subsequent daily closes.

No historical backtest returns are counted. The first observation starts at cash. Rebalances require
new quotes whose market close is strictly AFTER the preceding observation's actual timestamp.
AllWeather/KOSPI exposures are explicitly synthetic index proxies; this is not broker execution.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

import lake
from lake_prices import publish_new, replace_atomic

INITIAL_CAPITAL = 100_000_000.0


def close_at(quote: dict) -> dt.datetime:
    day = dt.date.fromisoformat(quote["date"])
    market = quote["market"]
    return dt.datetime.combine(day, dt.time(16 if market == "US" else 15, 0 if market == "US" else 30),
                               ZoneInfo("America/New_York" if market == "US" else "Asia/Seoul")).astimezone(dt.timezone.utc)


def validate(obs: dict) -> None:
    if obs.get("schema_version") != "1.0": raise ValueError("invalid portfolio observation")
    observed = dt.datetime.fromisoformat(obs["observed_at"])
    if observed.tzinfo is None:
        raise ValueError("observation timestamp must include timezone")
    weights = obs["weights"]
    if any(not math.isfinite(w) or w < 0 for w in weights.values()) or sum(weights.values()) > 1.000001:
        raise ValueError("invalid portfolio weights")
    for q in obs["quotes"].values():
        if not math.isfinite(q["price_krw"]) or q["price_krw"] <= 0 or q["date"] > obs["as_of"]:
            raise ValueError("invalid portfolio quote")
        if close_at(q) > observed:
            raise ValueError("portfolio quote was unavailable at observation time")


def replay(observations: list[dict]) -> dict:
    units: dict[str, float] = {}; basis: dict[str, float] = {}; cash = INITIAL_CAPITAL
    history = []; previous = None; prices = {}; total_fees = 0.0
    ordered = sorted(observations, key=lambda d: dt.datetime.fromisoformat(d["observed_at"]))
    for obs in ordered:
        validate(obs)
        prices.update(obs["quotes"])
        before = cash + sum(n * prices[k]["price_krw"] for k, n in units.items())
        traded = False
        if previous:
            keys = set(units) | set(previous["weights"])
            observed = dt.datetime.fromisoformat(previous["observed_at"])
            ready = bool(keys) and all(k in obs["quotes"] and close_at(obs["quotes"][k]) > observed for k in keys)
            if ready:
                weights = previous["weights"]
                old = {k: units.get(k, 0) * prices[k]["price_krw"] for k in keys}
                rate = lambda k: 0.0005 if k.startswith("INDEX:") else 0.003
                lo, hi = 0.0, before
                for _ in range(64):
                    nav = (lo + hi) / 2
                    fee = sum(abs(weights.get(k, 0) * nav - old[k]) * rate(k) for k in keys)
                    if nav + fee > before: hi = nav
                    else: lo = nav
                net = lo
                for k in keys:
                    value = weights.get(k, 0) * net
                    old_units = units.get(k, 0); new_units = value / prices[k]["price_krw"]
                    if new_units > old_units:
                        basis[k] = basis.get(k, 0) + (new_units - old_units) * prices[k]["price_krw"]
                    elif old_units:
                        basis[k] = basis.get(k, 0) * new_units / old_units
                    units[k] = new_units
                cash = net - sum(units[k] * prices[k]["price_krw"] for k in keys)
                total_fees += before - net; traded = True
        nav = cash + sum(n * prices[k]["price_krw"] for k, n in units.items())
        prev_nav = history[-1]["nav_krw"] if history else INITIAL_CAPITAL
        history.append({"as_of": obs["as_of"], "nav_krw": round(nav, 2), "daily_return_pct": (nav / prev_nav - 1) * 100,
                        "return_pct": (nav / INITIAL_CAPITAL - 1) * 100, "rebalanced": traded})
        previous = obs
    if previous is None: raise ValueError("no portfolio observations")
    positions = []
    for key, n in units.items():
        if n <= 1e-12: continue
        q = prices[key]; value = n * q["price_krw"]
        positions.append({"id": key, "name": q["name"], "quote_date": q["date"], "value_krw": round(value, 2),
                          "weight_pct": value / nav * 100, "return_pct": (value / basis[key] - 1) * 100 if basis.get(key) else None})
    return {"schema_version": "1.0", "as_of": previous["as_of"], "started_at": ordered[0]["observed_at"],
            "strategy": previous["strategy"], "mode": "forward_paper", "initial_capital_krw": INITIAL_CAPITAL,
            "nav_krw": round(nav, 2), "cash_krw": round(cash, 2), "cash_weight_pct": cash / nav * 100,
            "return_pct": history[-1]["return_pct"], "daily_return_pct": history[-1]["daily_return_pct"],
            "fees_krw": round(total_fees, 2), "positions": positions, "history": history,
            "target_positions": [{"id": k, "name": previous["quotes"][k]["name"], "weight_pct": w * 100} for k, w in previous["weights"].items() if w > 0],
            "status": "tracking" if positions else "awaiting_next_close",
            "method": "실제 기록 시작 후 공표 비중을 다음 가용 일봉 종가에서 추종. 국내외 환율 반영, 주식 편도 0.3%·지수 프록시 0.05%. 실계좌·실시간 호가·과거 백테스트 수익률 아님. 미체결 현금은 이자 없음."}


def capture(*, strategy: str, as_of: str, nav: float, positions: dict, prices: dict, fx_rate, core) -> None:
    """Called only after successful backtest: observe current allocation, never seed past dates."""
    import pandas as pd
    weights = {}; quotes = {}
    prior_keys = set()
    existing = list(lake.BRONZE_PAPER_TARGETS.glob("*.json"))
    if existing and as_of < max(p.stem for p in existing):
        return  # Historical backtest reruns cannot backfill a forward record.
    for path in existing:
        prior_keys.update(json.loads(path.read_text(encoding="utf-8"))["quotes"])
    for ticker, df in prices.items():
        p = positions.get(ticker)
        market = p.get("market", "KR") if p else ("US" if not ticker.isdigit() else "KR")
        key = f"{market}:{ticker}"
        if not p and key not in prior_keys: continue
        available = df[df.index <= pd.Timestamp(as_of)]
        if available.empty: raise ValueError(f"missing paper quote: {key}")
        row = available.iloc[-1]; date = available.index[-1].date()
        price = float(row["close"]) * fx_rate(market, dt.date.fromisoformat(as_of))
        quotes[key] = {"market": market, "name": p.get("display_name", ticker) if p else ticker,
                       "price_krw": price, "date": date.isoformat()}
        if p: weights[key] = p["shares"] * price / nav
    residual = 1 - sum(weights.values())
    if residual < -0.000001: raise ValueError("leveraged headline requires a separate paper mandate")
    for key, values in core.items():
        series = values[values.index <= pd.Timestamp(as_of)]
        quotes[key] = {"market": "KR", "name": "올웨더 합성지수" if key == "INDEX:AllWeather" else "KOSPI 합성지수",
                       "price_krw": float(series.iloc[-1]), "date": series.index[-1].date().isoformat()}
    if strategy.startswith(("W_", "T_", "T-_", "U_")):
        key = "INDEX:AllWeather" if strategy.startswith("W_") else "INDEX:KOSPI"
        weights[key] = max(0, residual)
    observation = {"schema_version": "1.0", "source": "published strategy allocation", "as_of": as_of,
                   "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(), "strategy": strategy,
                   "weights": weights, "quotes": quotes}
    validate(observation)
    target = lake.BRONZE_PAPER_TARGETS / f"{as_of}.json"
    if not target.exists(): publish_new(target, json.dumps(observation, ensure_ascii=False, allow_nan=False).encode())
    write()


def write(check: bool = False) -> dict:
    docs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(lake.BRONZE_PAPER_TARGETS.glob("*.json"))]
    result = replay(docs)
    if check:
        for target in (lake.SILVER_PAPER_LEDGER, lake.PAPER_PORTFOLIO):
            if not target.exists() or json.loads(target.read_text(encoding="utf-8")) != result:
                raise ValueError(f"paper portfolio drift: {target.name}")
        return result
    for target in (lake.SILVER_PAPER_LEDGER, lake.PAPER_PORTFOLIO):
        replace_atomic(target, lambda fh: json.dump(result, fh, ensure_ascii=False, indent=1, allow_nan=False))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    write(check=parser.parse_args().check)
