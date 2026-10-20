"""Predeclared W sensitivity diagnostics: exclude top 1/3/5 tickers; delay entry 1/3/5 sessions.

No tuning or strategy promotion. Winner exclusions use full-sample hindsight and are diagnostic,
not a deployable selection rule or an untouched holdout. Every variant replays the entire engine.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path

import lake
from backtest.accounting import build_pending_entries
from backtest.parking_engine import ENGINE_VERSION
from backtest.research_inputs import load
from backtest.strategies import run_parking_core_chandelier
from lake_prices import replace_atomic


def ticker_contributions(result):
    values = {}; names = {}
    for fill in result["execution"]["fills"]:
        key = fill["ticker"]
        values[key] = values.get(key, 0.0) + fill["cash_flow_krw"]
    for ticker, pos in result["open_positions"].items():
        values[ticker] = values.get(ticker, 0.0) + pos["shares"] * pos["last_close"] * pos.get("fx", 1)
        names[ticker] = pos["display_name"]
    for trade in result["trades"]:
        names[trade["ticker"]] = trade.get("display_name", trade["ticker"])
    return [{"ticker": t, "name": names.get(t, t), "pnl_krw": round(v, 2)} for t, v in sorted(values.items(), key=lambda x: (-x[1], x[0]))]


def delayed_schedule(reports, calendar, prices, extra_sessions):
    base = build_pending_entries(reports, calendar, False, prices)
    result = {}
    calendar_set = set(calendar)
    days = {t: sorted(i.date() for i in f.index if i.date() in calendar_set) for t, f in prices.items()}
    for day, entries in base.items():
        for ticker, source, clubs in entries:
            actual = days[ticker]; index = actual.index(day) + extra_sessions
            if index < len(actual):
                result.setdefault(actual[index], []).append((ticker, source, clubs))
    return result


def run(inputs, *, reference_before=None):
    policy = inputs["policy"]
    def simulate(reports, delay=0):
        schedule = delayed_schedule(reports, inputs["calendar"], inputs["prices"], delay) if delay else None
        return run_parking_core_chandelier(inputs["prices"], reports, inputs["calendar"], "W_allweather_chandelier",
            inputs["parking"], **policy, ticker_reports=inputs["metadata"], parking_name="올웨더", entry_schedule=schedule)
    baseline = simulate(inputs["reports"])
    ranking = ticker_contributions(baseline)
    winners = [r["ticker"] for r in ranking if r["pnl_krw"] > 0]
    rows = []
    for count in (0, 1, 3, 5):
        removed = winners[:count]
        result = baseline if count == 0 else simulate([r for r in inputs["reports"] if r[1] not in removed])
        rows.append({"id": "baseline" if count == 0 else f"exclude_top_{count}", "kind": "baseline" if count == 0 else "winner_exclusion",
                     "excluded_tickers": removed, "extra_entry_sessions": 0, "metrics": result["metrics"],
                     "in_sample": result["in_sample"], "out_of_sample": result["out_of_sample"],
                     "fees_krw": result["execution"]["fees_krw"]})
    for delay in (1, 3, 5):
        result = simulate(inputs["reports"], delay)
        rows.append({"id": f"delay_{delay}", "kind": "entry_delay", "excluded_tickers": [], "extra_entry_sessions": delay,
                     "metrics": result["metrics"], "in_sample": result["in_sample"], "out_of_sample": result["out_of_sample"],
                     "fees_krw": result["execution"]["fees_krw"]})
    positive = sum(max(0, r["pnl_krw"]) for r in ranking)
    return {"schema_version": "1.0", "as_of": inputs["as_of"], "strategy": "W_allweather_chandelier", "engine_version": ENGINE_VERSION,
            "policy": policy, "input_fingerprint": inputs["fingerprint"], "reoptimized": False,
            "method": "동일 입력·고정 파라미터로 전 구간 재실행. 최고 수익 종목 제외는 사후 진단이며 실전 종목선택 규칙이 아님. "
                      "진입 지연은 기본 익일 시가에 해당 종목 거래일 1/3/5일을 추가. 기존 OOS는 이미 검토된 역사 구간임.",
            "ranking": ranking, "top5_positive_pnl_share_pct": sum(max(0, r["pnl_krw"]) for r in ranking[:5]) / positive * 100 if positive else None,
            "scenarios": rows, "historical_reference": reference_before}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--as-of"); p.add_argument("--reference-before", type=Path)
    p.add_argument("--check", action="store_true"); args = p.parse_args()
    if args.check:
        doc = json.loads(lake.STRATEGY_ROBUSTNESS.read_text(encoding="utf-8"))
        if doc["schema_version"] != "1.0" or doc["engine_version"] != ENGINE_VERSION or doc["reoptimized"] or len(doc["scenarios"]) != 7:
            raise ValueError("invalid/stale strategy sensitivity artifact")
        model = json.loads((lake.WEB_DATA / "strategy-backtest.json").read_text(encoding="utf-8"))
        if doc["as_of"] != model["metrics"]["end"]:
            raise ValueError("research artifact date drift")
        expected_ids = {"baseline", "exclude_top_1", "exclude_top_3", "exclude_top_5", "delay_1", "delay_3", "delay_5"}
        if {r["id"] for r in doc["scenarios"]} != expected_ids:
            raise ValueError("research scenarios drift")
        w = next(s for s in model["multi_strategy"]["strategies"] if s["key"] == doc["strategy"])
        baseline = next(s for s in doc["scenarios"] if s["id"] == "baseline")
        if baseline["metrics"] != w["metrics"]:
            raise ValueError("research baseline differs from published W")
        selected = model.get("optuna_chandelier", {})
        if selected.get("adopted") and doc["policy"] != selected["best_params"]:
            raise ValueError("research mandate drift")
        return 0  # Full recompute requires the private, git-ignored frozen price inputs.
    model = json.loads((lake.WEB_DATA / "strategy-backtest.json").read_text(encoding="utf-8"))
    as_of = args.as_of or model["metrics"]["end"]
    reference = None
    if args.reference_before:
        content = args.reference_before.read_bytes(); old = json.loads(content)
        old_w = next(s for s in old["multi_strategy"]["strategies"] if s["key"] == "W_allweather_chandelier")
        reference = {"sha256": hashlib.sha256(content).hexdigest(), "as_of": old["metrics"]["end"], "metrics": old_w["metrics"]}
    if reference is None and lake.STRATEGY_ROBUSTNESS.exists():
        reference = json.loads(lake.STRATEGY_ROBUSTNESS.read_text(encoding="utf-8")).get("historical_reference")
    result = run(load(as_of), reference_before=reference)
    replace_atomic(lake.STRATEGY_ROBUSTNESS, lambda fh: json.dump(result, fh, ensure_ascii=False, indent=1, allow_nan=False))
    print(json.dumps({"as_of": as_of, "scenarios": [{"id": r["id"], "cagr_pct": r["metrics"]["cagr_pct"], "mdd_pct": r["metrics"]["mdd_pct"]} for r in result["scenarios"]]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
