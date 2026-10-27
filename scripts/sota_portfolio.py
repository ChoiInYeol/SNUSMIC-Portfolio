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


SCHEMA_VERSION = "2.0"
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


def json_schema() -> dict:
    """Public structural contract; accounting identities are checked by validate()."""
    text, num, date = {"type": "string"}, {"type": "number"}, {"type": "string", "format": "date"}
    nullable = {"type": ["number", "null"]}
    def obj(fields): return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}
    def fields(strings="", numbers="", dates="", nulls=""):
        return {**{k: text for k in strings.split()}, **{k: num for k in numbers.split()},
                **{k: date for k in dates.split()}, **{k: nullable for k in nulls.split()}}
    holding = obj({**fields("ticker market name", "shares entry_price current_price fx cost_krw value_krw weight_pct pnl_krw return_pct", "entry_date quote_date", "stop_level dist_to_stop_pct"), "exit_pending": {"type": "boolean"}})
    fill = obj({**fields("ticker market name reason", "shares price fx gross_krw cash_flow_krw fees_krw entry_price", "date entry_date", "pnl_krw return_pct"), "side": {"type": "string", "enum": ["buy", "sell"]}})
    return obj({**fields("strategy source_sha256 generated_at parking_name", "initial_capital_krw nav_krw return_pct parked_krw parked_weight_pct cash_krw cash_weight_pct recent_closed_count", "as_of simulation_start recent_window_start"),
                "schema_version": {"type": "string", "const": SCHEMA_VERSION}, "mode": {"type": "string", "const": "sota_backtest"},
                "quantity_basis": {"type": "string", "const": "fractional_model_shares"},
                "positions": {"type": "array", "items": holding}, "fills": {"type": "array", "items": fill},
                "regime": {"anyOf": [obj({**fields("state note", "kospi_close kospi_ma200"), "applies": {"type": "boolean"}}), {"type": "null"}]},
                "slots": obj(fields(numbers="max_positions open available")),
                "buy_candidates": {"type": "array", "items": obj({**fields("ticker market display_name entry_reason"), "entry_pending": {"type": "boolean", "const": True}, "entry_basis_date": {"type": ["string", "null"], "format": "date"}})}})


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
    # Keep actual fills, including buys. Never infer a sold quantity from rounded P&L.
    fills = []
    inventory = {}
    names = {(t["market"], t["ticker"]): t.get("display_name", t["ticker"]) for t in result["trades"]}
    names.update({(p["market"], p["ticker"]): p["name"] for p in rows})
    if "fills" not in execution:
        raise ValueError("headline engine must expose actual execution fills")
    for raw in execution["fills"]:
        item = dict(raw)
        key = (item["market"], item["ticker"])
        if item["side"] == "buy":
            if key in inventory:
                raise ValueError("duplicate open position in SOTA fills")
            inventory[key] = item
        entry = inventory.get(key)
        if entry is None:
            raise ValueError("SOTA sale without an entry")
        item.update(name=names.get(key, item["ticker"]), entry_date=entry["date"], entry_price=entry["price"],
                    pnl_krw=None, return_pct=None)
        if item["side"] == "sell":
            if abs(item["shares"] - entry["shares"]) > 1e-7:
                raise ValueError("SOTA entry/exit quantities differ")
            item["pnl_krw"] = round(item["cash_flow_krw"] + entry["cash_flow_krw"], 2)
            item["return_pct"] = round(item["pnl_krw"] / -entry["cash_flow_krw"] * 100, 2)
            trade = next((t for t in result["trades"] if t["ticker"] == item["ticker"] and t["entry_date"] == item["entry_date"] and t["exit_date"] == item["date"]), None)
            if trade is None or abs(trade["pnl_krw"] - item["pnl_krw"]) > .02 or abs(trade["return_pct"] - item["return_pct"]) > .011:
                raise ValueError("SOTA fill P&L differs from the published engine trade")
            del inventory[key]
        fills.append(item)
    if set(inventory) != {(p["market"], p["ticker"]) for p in rows}:
        raise ValueError("SOTA fill inventory differs from holdings")
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
           "recent_window_start": cutoff, "recent_closed_count": len(closed), "fills": fills}
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
    inventory = {}
    previous = ""
    for fill in doc["fills"]:
        if not doc["simulation_start"] <= fill["date"] <= doc["as_of"] or fill["date"] < previous:
            raise ValueError("future/unordered SOTA fill")
        previous = fill["date"]
        key = (fill["market"], fill["ticker"])
        for field in ("shares", "price", "fx", "gross_krw"):
            number(fill[field], field, positive=True)
        fees = number(fill["fees_krw"], "fees")
        cash_flow = number(fill["cash_flow_krw"], "cash flow")
        if fill["market"] not in ("KR", "US") or fees < 0 or abs(fill["gross_krw"] - fill["shares"] * fill["price"] * fill["fx"]) > .02:
            raise ValueError("SOTA fill gross does not reconcile")
        expected = -fill["gross_krw"] - fees if fill["side"] == "buy" else fill["gross_krw"] - fees
        if abs(cash_flow - expected) > .02:
            raise ValueError("SOTA fill cash flow does not reconcile")
        if fill["side"] == "buy":
            if key in inventory or fill["date"] != fill["entry_date"] or fill["price"] != fill["entry_price"] or fill["pnl_krw"] is not None or fill["return_pct"] is not None:
                raise ValueError("invalid SOTA entry")
            inventory[key] = fill
        elif fill["side"] == "sell":
            entry = inventory.pop(key, None)
            if entry is None or entry["date"] != fill["entry_date"] or entry["price"] != fill["entry_price"] or abs(entry["shares"] - fill["shares"]) > 1e-7:
                raise ValueError("unmatched SOTA exit")
            pnl = cash_flow + entry["cash_flow_krw"]
            if abs(number(fill["pnl_krw"], "realized P&L") - pnl) > .02 or abs(number(fill["return_pct"], "realized return") - pnl / -entry["cash_flow_krw"] * 100) > .011:
                raise ValueError("SOTA realized amounts do not reconcile")
        else:
            raise ValueError("invalid SOTA fill side")
    if set(inventory) != {(p["market"], p["ticker"]) for p in doc["positions"]}:
        raise ValueError("SOTA fill inventory differs from holdings")
    for p in doc["positions"]:
        entry = inventory[(p["market"], p["ticker"])]
        if abs(entry["shares"] - p["shares"]) > 1e-7 or entry["date"] != p["entry_date"] or abs(-entry["cash_flow_krw"] - p["cost_krw"]) > .02:
            raise ValueError("SOTA holding differs from entry fill")
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
