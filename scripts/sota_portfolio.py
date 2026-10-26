"""Selected research strategy's current model book, separate from forward-paper accounts.

The backtest calls write() with its raw result: quantities, FX marks and parking balances are
the engine's actual state, never reconstructed from the published default 5% entry weights.
The CLI can bootstrap the artifact by replaying a published parking-family winner from cached
inputs only; it refuses to publish if the replay differs from the published backtest.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math

import pandas as pd

import lake
from lake_prices import replace_atomic


SCHEMA_VERSION = "1.0"
RECENT_SESSIONS = 20


def fingerprint(model: dict) -> str:
    return hashlib.sha256(json.dumps(model, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"invalid {name}")
    if positive and value <= 0:
        raise ValueError(f"invalid {name}")
    return value


def build(model: dict, result: dict, *, prices: dict, calendar: list) -> dict:
    strategy = model["multi_strategy"]["headline_key"]
    signals = model["signals"]
    as_of = signals["as_of"]
    if result["label"] != strategy or result["metrics"] != model["metrics"] or as_of != result["metrics"]["end"]:
        raise ValueError("SOTA result differs from the published headline")
    if calendar[-1].isoformat() != as_of or signals["headline_strategy"] != strategy:
        raise ValueError("SOTA signal date/strategy drift")
    nav = number(result["metrics"]["final_nav_krw"], "NAV", positive=True)
    execution = result.get("execution", {})
    pending_exits = set(execution.get("pending_exits", []))
    signal_positions = {p["ticker"]: p for p in signals["open_positions"]}
    rows = []
    for ticker, pos in result["open_positions"].items():
        signal = signal_positions[ticker]
        shares = number(float(pos["shares"]), "shares", positive=True)
        price = number(float(pos["last_close"]), "close", positive=True)
        rate = number(float(pos.get("fx", 1)), "FX", positive=True)
        cost = number(float(pos["cost"]), "cost", positive=True)
        value = shares * price * rate
        stop = pos.get("stop")
        distance = (price - stop) / price * 100 if stop else None
        frame = prices[ticker].loc[:pd.Timestamp(as_of), "close"].dropna()
        if frame.empty or float(frame.iloc[-1]) != price:
            raise ValueError(f"SOTA quote differs from the engine mark: {ticker}")
        rows.append({"ticker": ticker, "market": pos["market"], "name": pos.get("display_name", ticker),
                     "shares": shares, "entry_date": signal["entry_date"], "entry_price": pos["entry_price"],
                     "current_price": price, "quote_date": frame.index[-1].date().isoformat(), "fx": rate,
                     "cost_krw": cost, "value_krw": value, "weight_pct": value / nav * 100,
                     "pnl_krw": value - cost, "return_pct": (value / cost - 1) * 100,
                     "stop_level": stop, "dist_to_stop_pct": distance,
                     "exit_pending": ticker in pending_exits or bool(stop and price < stop)})
    if set(signal_positions) != {p["ticker"] for p in rows}:
        raise ValueError("SOTA holdings differ from signal holdings")
    equity_value = sum(p["value_krw"] for p in rows)
    if execution:
        parked = execution["parked_krw"]
        cash = execution["cash_krw"]
    else:
        # Non-parking engines keep the residual in cash. A negative residual is rejected below.
        parked, cash = 0.0, nav - equity_value
    cutoff = calendar[max(0, len(calendar) - RECENT_SESSIONS)].isoformat()
    closed = sorted((t for t in result["trades"] if not t.get("exit_reason", "").endswith("미청산")
                     and cutoff <= t["exit_date"] <= as_of), key=lambda t: (t["exit_date"], t["ticker"]), reverse=True)
    recent = [{"ticker": t["ticker"], "market": t["market"], "name": t.get("display_name", t["ticker"]),
               "entry_date": t["entry_date"], "exit_date": t["exit_date"], "entry_price": t["entry"],
               "exit_price": t["exit"], "pnl_krw": t["pnl_krw"], "return_pct": t["return_pct"],
               "exit_reason": t["exit_reason"]} for t in closed[:10]]
    doc = {"schema_version": SCHEMA_VERSION, "mode": "sota_backtest", "strategy": strategy,
           "as_of": as_of, "generated_at": model["generated_at"], "source_sha256": fingerprint(model),
           "initial_capital_krw": result["metrics"]["initial_capital_krw"], "nav_krw": nav,
           "return_pct": result["metrics"]["total_return_pct"], "simulation_start": result["metrics"]["start"],
           "quantity_basis": "fractional_model_shares", "positions": sorted(rows, key=lambda p: -p["weight_pct"]),
           "parked_krw": parked, "parked_weight_pct": parked / nav * 100,
           "parking_name": "올웨더 합성지수" if strategy == "W_allweather_chandelier" else "KOSPI",
           "cash_krw": cash, "cash_weight_pct": cash / nav * 100,
           "regime": signals["regime"], "slots": signals["slots"],
           "buy_candidates": [b for b in signals["imminent_buys"] if b["entry_pending"]],
           "recent_window_start": cutoff, "recent_closed_count": len(closed), "recent_exits": recent}
    validate(doc, model)
    return doc


def validate(doc: dict, model: dict | None = None) -> None:
    if (doc.get("schema_version"), doc.get("mode"), doc.get("quantity_basis")) != \
            (SCHEMA_VERSION, "sota_backtest", "fractional_model_shares"):
        raise ValueError("expected SOTA model portfolio")
    nav = number(doc["nav_krw"], "NAV", positive=True)
    number(doc["initial_capital_krw"], "initial capital", positive=True)
    number(doc["return_pct"], "return")
    dt.date.fromisoformat(doc["as_of"])
    dt.datetime.fromisoformat(doc["generated_at"])
    symbols = set()
    for p in doc["positions"]:
        if p["ticker"] in symbols or p["market"] not in ("KR", "US") or p["quote_date"] > doc["as_of"]:
            raise ValueError("invalid SOTA holding")
        symbols.add(p["ticker"])
        for key in ("shares", "current_price", "fx", "cost_krw", "value_krw"):
            number(p[key], key, positive=True)
        for key in ("weight_pct", "pnl_krw", "return_pct"):
            number(p[key], key)
        if abs(p["shares"] * p["current_price"] * p["fx"] - p["value_krw"]) > 0.02 or \
                abs(p["value_krw"] - p["cost_krw"] - p["pnl_krw"]) > 0.02 or \
                abs(p["weight_pct"] - p["value_krw"] / nav * 100) > 0.001 or \
                abs(p["return_pct"] - (p["value_krw"] / p["cost_krw"] - 1) * 100) > 0.001:
            raise ValueError("SOTA holding amounts do not reconcile")
        if p["stop_level"] is not None:
            number(p["stop_level"], "stop")
            number(p["dist_to_stop_pct"], "stop distance")
    for kind in ("cash", "parked"):
        amount = number(doc[f"{kind}_krw"], kind)
        weight = number(doc[f"{kind}_weight_pct"], f"{kind} weight")
        if amount < -0.02 or abs(weight - amount / nav * 100) > 0.001:
            raise ValueError("invalid SOTA residual allocation")
    if abs(sum(p["value_krw"] for p in doc["positions"]) + doc["parked_krw"] + doc["cash_krw"] - nav) > 0.05:
        raise ValueError("SOTA allocations do not reconcile to NAV")
    if doc["slots"]["open"] != len(symbols):
        raise ValueError("SOTA slot count drift")
    if doc["slots"]["max_positions"] < len(symbols) or \
            doc["slots"]["available"] != doc["slots"]["max_positions"] - len(symbols):
        raise ValueError("invalid SOTA available slots")
    regime = doc["regime"]
    if regime is not None:
        close = number(regime["kospi_close"], "KOSPI", positive=True)
        ma = number(regime["kospi_ma200"], "200MA", positive=True)
        if regime["state"] != ("ON" if close >= ma else "OFF") or not isinstance(regime["applies"], bool):
            raise ValueError("invalid SOTA regime")
    for trade in doc["recent_exits"]:
        if not doc["recent_window_start"] <= trade["exit_date"] <= doc["as_of"]:
            raise ValueError("future/out-of-window SOTA exit")
        number(trade["pnl_krw"], "realized P&L")
        number(trade["return_pct"], "realized return")
    for candidate in doc["buy_candidates"]:
        if candidate["ticker"] in symbols or candidate["entry_pending"] is not True:
            raise ValueError("executed/held SOTA buy candidate")
    if model is not None and (doc["source_sha256"] != fingerprint(model) or
                              doc["strategy"] != model["multi_strategy"]["headline_key"] or
                              doc["as_of"] != model["metrics"]["end"] or
                              doc["nav_krw"] != model["metrics"]["final_nav_krw"]):
        raise ValueError("SOTA portfolio differs from current backtest; regenerate it")


def write(model: dict, result: dict, *, prices: dict, calendar: list) -> dict:
    doc = build(model, result, prices=prices, calendar=calendar)
    replace_atomic(lake.GOLD_SOTA_PORTFOLIO, lambda fh: json.dump(doc, fh, ensure_ascii=False, indent=1, allow_nan=False))
    return doc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    model = json.loads((lake.WEB_DATA / "strategy-backtest.json").read_text(encoding="utf-8"))
    if args.check:
        validate(json.loads(lake.GOLD_SOTA_PORTFOLIO.read_text(encoding="utf-8")), model)
        return 0
    from backtest.research_inputs import load
    from backtest.strategies import run_parking_core_chandelier
    key = model["multi_strategy"]["headline_key"]
    if key not in ("W_allweather_chandelier", "T_kospi_core_chandelier", "T-_kospi_core_regime"):
        raise ValueError("bootstrap replay supports parking-family headlines; run backtest_momentum.py for other winners")
    inputs = load(model["metrics"]["end"])
    result = run_parking_core_chandelier(inputs["prices"], inputs["reports"], inputs["calendar"], key,
        inputs["parking"] if key == "W_allweather_chandelier" else inputs["kospi"], **inputs["policy"],
        ticker_reports=inputs["metadata"], record_full_trades=True,
        regime_aware=key == "T-_kospi_core_regime", regime_index=inputs["kospi"],
        parking_name="올웨더" if key == "W_allweather_chandelier" else "KOSPI")
    write(model, result, prices=inputs["prices"], calendar=inputs["calendar"])
    print(f"SOTA portfolio: {key}, {model['metrics']['end']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
