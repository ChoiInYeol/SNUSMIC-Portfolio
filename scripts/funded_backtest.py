"""Backtest segment (백테스트 구간, simulated) of funded-w50-v1 — the seed of the live account.

Replays the SAME decision code as the live account over history, in memory, one simulated pipeline
evening (20:30 KST) at a time: `funded_account.build_plan` freezes each market's next-open plan from its
last completed close, and `funded_account._run` fills it at the real next open (integer shares, 0.3% per
side, contributions as principal, TWR excluding contributions). Sessions enter the simulation only after
their own market cutoff and signals only after they are available, so a plan never sees its open.

Signal availability (AVAILABILITY):
  publication_date — a buy report is public on its report date (20:30 KST that evening), the same rule as
                     the published W backtest. It is NOT what this pipeline observed: the archive was
                     bulk-collected in 2026-06, so pipeline first_seen would leave 2023-2026 empty.
  first_seen       — the pipeline's first download date (UTC day end); kept for comparison.

Start 2023-07-03 (2023-07-01 is a Saturday): 50,000,000 KRW initial capital and installment 1 land at
08:30 KST, before that open. Installments on the first KRX trading day of each month (user-confirmed).
Output: the Silver artifact lake.SILVER_FUNDED_BACKTEST, produced only by this writer. The live genesis
records its content hash (`funded_account.py init --seed-backtest`), so the seeded state is traceable.

    uv run python scripts/funded_backtest.py --end-at 2026-10-21T00:30:00+09:00
    uv run python scripts/funded_backtest.py --check     # recompute from local prices and compare
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import json
import math
from pathlib import Path

import pandas as pd

import funded_account as fa
import lake
from backtest.parking_engine import market_open
from lake_prices import replace_atomic
from market_calendars import is_trading_day, next_trading_day
from market_clock import closed_bar_end
from paper_execution import report_id

START = dt.date(2023, 7, 3)
EVENING = dt.time(20, 30)
AVAILABILITY = ("publication_date", "first_seen")
METHOD = {
    "publication_date": "공표일 기준: 학회 매수 리포트는 발간일 저녁에 공개된 것으로 보고 다음 실제 시가에 진입(W 백테스트와 같은 가정). "
                        "이 파이프라인의 최초 관측 시각이 아님.",
    "first_seen": "파이프라인 최초 수집일 기준(UTC 하루 종료 후 사용).",
}


def available_at(report: dict, availability: str) -> dt.datetime | None:
    if availability == "publication_date":
        return dt.datetime.combine(report["report_date"], EVENING, fa.KST)
    if report.get("first_seen") is None:
        return None
    return dt.datetime.combine(dt.date.fromisoformat(report["first_seen"]) + dt.timedelta(days=1), dt.time(0), dt.timezone.utc)


def _bar_table(frame: pd.DataFrame | None, since: dt.date, atr_column: str) -> dict[str, dict]:
    if frame is None:
        return {}
    out = {}
    for ts, row in frame.loc[pd.Timestamp(since):].iterrows():
        def num(key):
            v = row.get(key)
            return float(v) if v is not None and pd.notna(v) and math.isfinite(float(v)) else None
        close = num("close")
        if close is not None and close > 0:
            out[ts.date().isoformat()] = {"open": num("open"), "high": num("high"), "low": num("low"), "close": close,
                                          "volume": num("volume"), "atr": num(atr_column)}
    return out


def simulate(*, reports: list[dict], prices: dict, benchmarks: dict[str, pd.Series], fx_series: pd.Series,
             end_at: dt.datetime, availability: str, start: dt.date = START) -> dict:
    if availability not in AVAILABILITY:
        raise ValueError(f"availability must be one of {AVAILABILITY}")
    rule = fa.contribution_day_rule("first-trading-day", "confirmed", "anchor_month")
    config = {**fa.CONFIG, "contribution_day": rule}
    epoch = dt.datetime.combine(start, dt.time(8, 30), fa.KST)  # installment 1's deposit time, before the open
    if end_at <= epoch:
        raise ValueError("end_at must be after the backtest start")
    timed = sorted(((available_at(r, availability), r) for r in reports), key=lambda x: (x[0] is None, x[0] or epoch, x[1]["id"]))
    known = sorted(r["id"] for at, r in timed if at is not None and at < epoch)
    queue = [(at, r) for at, r in timed if at is not None and at >= epoch and at <= end_at]
    genesis = {"schema_version": fa.SCHEMA_VERSION, "account_id": fa.ACCOUNT_ID, "epoch_at": epoch.isoformat(),
               "as_of": start.isoformat(), "config": config, "bootstrap": [], "known_report_ids": known,
               "schedule_anchor": start.isoformat(), "seed": None}
    bronze = {"genesis": genesis, "contributions": [], "sessions": {}, "signals": [], "plans": []}
    atr_column = fa.ATR_COLUMN[config["atr_period"]]
    since = start - dt.timedelta(days=10)
    bars = {t: _bar_table(frame, since, atr_column) for t, frame in prices.items()}
    bench = {m: {ts.date().isoformat() for ts, v in s.dropna().items() if ts.date() >= since} for m, s in benchmarks.items()}
    last_bench = {m: max(days) for m, days in bench.items()}
    fx_clean = fx_series.dropna()
    fx_days = [ts.date().isoformat() for ts in fx_clean.index]; fx_vals = [float(v) for v in fx_clean.to_numpy()]
    issues: list[str] = []
    for m, days in bench.items():
        for day in days:
            if not is_trading_day(dt.date.fromisoformat(day), m):
                raise ValueError(f"{m} benchmark has a bar on calendar holiday {day}: fix market_calendars.py")
    universe = {"KR": set(), "US": set()}
    instants = [epoch]
    day = start
    while (evening := dt.datetime.combine(day, EVENING, fa.KST)) <= end_at:
        instants.append(evening); day += dt.timedelta(days=1)
    plan_index: dict[tuple[str, str], dict] = {}
    pointer = 0
    for now in instants:
        while pointer < len(queue) and queue[pointer][0] <= now:
            at, r = queue[pointer]; pointer += 1
            bronze["signals"].append({"id": r["id"], "ticker": r["ticker"], "source": r["source"], "clubs": r["clubs"],
                                      "report_date": r["report_date"].isoformat(), "observed_at": at.isoformat(),
                                      "market": r["market"], "name": r["name"]})
            universe[r["market"]].add(r["ticker"])
        fx_cap = closed_bar_end("FX", now=now).isoformat()
        for m in fa.MARKETS:
            cap = fa.expected_session(m, now)
            recorded = [d for mm, d in bronze["sessions"] if mm == m]
            last = max(recorded) if recorded else None
            days, cursor = ([cap] if last is None else []), (dt.date.fromisoformat(last) if last else None)
            while cursor is not None and (cursor := next_trading_day(cursor, m)) <= cap:
                days.append(cursor)
            for d in days:
                iso = d.isoformat()
                if iso > last_bench[m]:
                    break  # not ingested yet (end of local data): later sessions wait, as in live
                if iso not in bench[m]:
                    issues.append(f"{m} {iso}: 거래일인데 벤치마크 시세 없음 — 세션 생략")
                    continue
                i = bisect.bisect_right(fx_days, min(iso, fx_cap))
                bronze["sessions"][(m, iso)] = {
                    "schema_version": fa.SCHEMA_VERSION, "account_id": fa.ACCOUNT_ID, "market": m, "session_date": iso,
                    "observed_at": now.isoformat(),
                    "bars": {t: bars.get(t, {}).get(iso) for t in sorted(universe[m])},
                    "fx_usdkrw": [{"date": fx_days[j], "value": fx_vals[j]} for j in range(max(0, i - 5), i)]}
            own = [d for mm, d in bronze["sessions"] if mm == m]
            if own:  # addendum: a ticker first signalled after its basis session was recorded
                latest = bronze["sessions"][(m, max(own))]
                for t in universe[m] - set(latest["bars"]):
                    latest["bars"][t] = bars.get(t, {}).get(latest["session_date"])
        targets = {}
        for m in fa.MARKETS:
            recorded = [d for mm, d in bronze["sessions"] if mm == m]
            if recorded:
                targets[m] = market_open(next_trading_day(dt.date.fromisoformat(max(recorded)), m), m)
        if targets:
            n = len(bronze["contributions"]) + 1
            while market_open(d := fa.contribution_date(n, start, config), "KR") - fa.CONTRIBUTION_LEAD <= max(targets.values()):
                bronze["contributions"].append(fa.contribution_record(genesis, n, d, now)); n += 1
        for m in sorted(targets, key=targets.get):
            body = fa.build_plan(bronze, m, now)
            if body is None:
                continue
            key = (m, body["trading_day"]); latest = plan_index.get(key)
            candidate = {**body, "status": "active"}
            if latest and fa._core(latest) == fa._core(candidate):
                continue
            plan = fa.finalize_plan(candidate, latest, now)
            plan_index[key] = plan; bronze["plans"].append(plan)
    st = fa._run(bronze, until=end_at)
    return {"bronze": bronze, "state": st, "issues": issues, "epoch": epoch}


def artifact(result: dict, *, end_at: dt.datetime, availability: str, inputs: dict) -> dict:
    bronze, st, epoch = result["bronze"], result["state"], result["epoch"]
    cfg = bronze["genesis"]["config"]
    flows = [(epoch, -float(cfg["initial_capital_krw"]))] + \
        [(dt.datetime.fromisoformat(c["effective_at"]), -float(c["amount_krw"])) for c in st["applied"] if c["installment"]]
    flows.append((end_at, st["nav"]))
    signal_ids = {s["id"] for s in bronze["signals"]}
    queued = [{k: s[k] for k in ("id", "ticker", "market", "name", "source", "clubs", "report_date", "observed_at")}
              for s in st["signals"].values() if s["status"] == "queued"]
    positions = [{**p} for p in sorted(st["positions"].values(), key=lambda p: p["ticker"])]
    doc = {
        "schema_version": "1.0", "account_id": fa.ACCOUNT_ID, "segment": "backtest", "label": fa.BACKTEST_LABEL,
        "availability": availability, "method": METHOD[availability],
        "start_at": epoch.isoformat(), "end_at": end_at.isoformat(), "schedule_anchor": bronze["genesis"]["schedule_anchor"],
        "config": cfg, "inputs": inputs, "marks": st["marks"],
        "summary": {"principal_krw": round(st["principal"], 2), "nav_krw": round(st["nav"], 2),
                    "cash_krw": round(st["cash"], 2), "investment_pnl_krw": round(st["nav"] - st["principal"], 2),
                    "twr_pct": round((st["twr_factor"] - 1) * 100, 6), "installments_applied": len(st["applied"]),
                    "fees_krw": round(st["fees"], 2), "realized_pnl_krw": round(st["realized"], 2),
                    "xirr_pct": fa._xirr(flows), "plans": len(bronze["plans"]),
                    "fills": sum(f["status"] == "filled" for f in st["fills"]), "signals": len(bronze["signals"])},
        "history": st["history"], "fills": st["fills"], "contributions": st["applied"],
        "end_state": {"cash_krw": round(st["cash"], 2), "principal_krw": round(st["principal"], 2),
                      "nav_krw": round(st["nav"], 2), "installments_applied": len(st["applied"]),
                      "marks": st["marks"], "positions": positions, "queued_signals": queued,
                      "known_report_ids": sorted(set(bronze["genesis"]["known_report_ids"]) | signal_ids),
                      "twr_factor": st["twr_factor"]},
        "data_issues": result["issues"],
    }
    doc["run_id"] = fa.canonical_sha256(doc)[:16]
    return doc


def load_inputs(availability: str) -> tuple[list[dict], dict, dict, pd.Series, dict]:
    """Local inputs only (no network): gold report performance, silver prices, KOSPI/S&P500/USDKRW."""
    from backtest.warehouse import load_prices
    from backtest_momentum import point_in_time_school_counts
    perf = pd.read_csv(lake.GOLD_REPORT_PERFORMANCE, encoding="utf-8-sig", dtype={"ticker": str})
    perf = perf[perf["ticker"].notna() & perf["report_date"].notna() & (perf["rating_class"] == "buy")].copy()
    perf["ticker_key"] = [str(t).zfill(6) if str(m) == "KR" else str(t) for t, m in zip(perf["ticker"], perf["market"])]
    clubs = point_in_time_school_counts(perf)
    first_seen = json.loads((lake.WEB_DATA / "report-first-seen.json").read_text(encoding="utf-8"))["first_seen"]
    reports, prices = [], {}
    for row in perf.itertuples():
        try:
            day = dt.date.fromisoformat(str(row.report_date))
        except ValueError:
            continue
        if day < START - dt.timedelta(days=400):
            continue
        ticker, market = row.ticker_key, str(row.market)
        if ticker not in prices:
            frame = load_prices(str(row.ticker), market)
            if frame is None:
                continue
            prices[ticker] = frame
        source = Path(str(row.source_file)).name
        reports.append({"id": report_id(day, ticker, source), "ticker": ticker, "market": market,
                        "name": str(row.display_name) if pd.notna(row.display_name) else ticker, "source": source,
                        "clubs": clubs.get((ticker, day), 1), "report_date": day,
                        "first_seen": first_seen.get(str(row.source_file))})
    reports = list({r["id"]: r for r in reports}.values())
    idx = lambda name: pd.read_csv(lake.SILVER_PRICES / f"IDX_{name}.csv", index_col=0, parse_dates=True)["close"].sort_index()
    benchmarks = {"KR": idx("KOSPI"), "US": idx("US")}
    fx_series = idx("USDKRW")
    inputs = {"reports": len(reports), "tickers_with_prices": len(prices),
              "benchmark_last": {m: s.dropna().index[-1].date().isoformat() for m, s in benchmarks.items()},
              "fx_last": fx_series.dropna().index[-1].date().isoformat(),
              "report_performance_rows": int(len(perf))}
    return reports, prices, benchmarks, fx_series, inputs


def build(end_at: dt.datetime, availability: str) -> dict:
    reports, prices, benchmarks, fx_series, inputs = load_inputs(availability)
    result = simulate(reports=reports, prices=prices, benchmarks=benchmarks, fx_series=fx_series,
                      end_at=end_at, availability=availability)
    return artifact(result, end_at=end_at, availability=availability, inputs=inputs)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--end-at", help="ISO instant the backtest ends (must precede the live epoch)")
    p.add_argument("--availability", choices=AVAILABILITY, default="publication_date")
    p.add_argument("--check", action="store_true", help="recompute with the stored end_at/availability and compare")
    args = p.parse_args(argv)
    target = lake.SILVER_FUNDED_BACKTEST
    if args.check:
        current = json.loads(target.read_text(encoding="utf-8"))
        fresh = build(dt.datetime.fromisoformat(current["end_at"]), current["availability"])
        if fresh != current:
            print(f"{lake.rel(target)} drifted from a recompute on local inputs"); return 1
        return 0
    if not args.end_at:
        p.error("--end-at is required when writing")
    end_at = dt.datetime.fromisoformat(args.end_at)
    if end_at.tzinfo is None:
        p.error("--end-at needs a timezone")
    doc = build(end_at, args.availability)
    replace_atomic(target, lambda fh: json.dump(doc, fh, ensure_ascii=False, indent=1, allow_nan=False))
    s = doc["summary"]
    print(f"backtest {doc['start_at']} → {doc['end_at']} ({doc['availability']}): principal {s['principal_krw']:,.0f} "
          f"NAV {s['nav_krw']:,.0f} TWR {s['twr_pct']:+.2f}% fills {s['fills']} issues {len(doc['data_issues'])} "
          f"run {doc['run_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
