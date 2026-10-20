"""Backtest segment → seeded live genesis: same decision code, no look-ahead, segments never mixed."""
import datetime as dt
import json

import pandas as pd
import pytest

import funded_account as fa
import funded_backtest as fb
from test_funded_account import at, benchmark, frame, gold, lake_tmp, run  # noqa: F401

START = dt.date(2026, 9, 1)


def reports(extra=()):
    rows = [("005930", "KR", dt.date(2026, 9, 3)), ("AAPL", "US", dt.date(2026, 9, 15)), *extra]
    return [{"id": fa.report_id(d, t, f"{d}_{t}.md"), "ticker": t, "market": m, "name": t, "source": f"{d}_{t}.md",
             "clubs": 1, "report_date": d, "first_seen": "2026-10-10"} for t, m, d in rows]


def prices(overrides=None):
    return {"005930": frame("KR", 10_000.0, overrides, start="2026-08-01"), "AAPL": frame("US", 100.0, start="2026-08-01"),
            "000660": frame("KR", 50_000.0, start="2026-08-01"), "035420": frame("KR", 20_000.0, start="2026-08-01")}


def backtest(end_at, *, price_overrides=None, availability="publication_date", extra=()):
    fx = pd.Series(1400.0, index=pd.bdate_range("2026-08-01", "2026-12-31"))
    result = fb.simulate(reports=reports(extra), prices=prices(price_overrides),
                         benchmarks={"KR": frame("KR", 100.0, start="2026-08-01")["close"],
                                     "US": frame("US", 100.0, start="2026-08-01")["close"]},
                         fx_series=fx, end_at=end_at, availability=availability, start=START)
    return fb.artifact(result, end_at=end_at, availability=availability, inputs={"synthetic": True})


def test_backtest_uses_live_decision_rules_and_never_sees_the_future(lake_tmp):  # noqa: F811
    end = at("2026-10-02")
    doc = backtest(end)
    assert doc["label"] == "백테스트 구간 (simulated)" and doc["segment"] == "backtest"
    assert doc["summary"]["principal_krw"] == 54_000_000  # 50M + installment 1 (09-01) + installment 2 (10-01)
    assert doc["config"]["contribution_day"]["status"] == "user_confirmed"
    first = next(f for f in doc["fills"] if f["ticker"] == "005930")
    assert (first["trading_day"], first["status"]) == ("2026-09-04", "filled")  # next real open after the report date
    # A crash after the end date cannot change anything inside the backtest window.
    crash = {d: {"open": 1_000.0, "close": 1_000.0} for d in ("2026-10-06", "2026-10-07", "2026-10-08")}  # 10-05, 10-09 closed
    assert backtest(end, price_overrides=crash) == doc
    later = backtest(at("2026-10-15"), price_overrides=crash)
    assert [f for f in later["fills"] if f["trading_day"] < "2026-10-06"] == [f for f in doc["fills"] if f["trading_day"] < "2026-10-06"]
    # first_seen availability: nothing is tradable before the pipeline held the reports.
    strict = backtest(at("2026-10-15"), availability="first_seen")
    assert all(f["trading_day"] > "2026-10-10" for f in strict["fills"])


def seeded(lake_tmp, epoch, extra=()):
    doc = backtest(at("2026-10-19"), extra=extra)
    path = lake_tmp / "backtest.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    seed = fa.seed_from_backtest(path)
    rule = seed.pop("contribution_day")
    fa.start_account(epoch=epoch, contribution_day=rule, as_of=epoch.astimezone(fa.KST).date().isoformat(),
                     positions={}, reports=[], policy={"atr_period": 42, "atr_mult": 5.75, "max_positions": 30}, seed=seed)
    return doc, path


def test_seeded_live_segment_continues_the_backtest_without_mixing_results(lake_tmp):  # noqa: F811
    doc, path = seeded(lake_tmp, at("2026-10-20", dt.time(11, 0)))
    for day in ("2026-10-20", "2026-10-30", "2026-11-02"):
        run(at(day))
    g = gold()
    seg = g["segments"]
    assert g["performance"]["scope"] == "combined_backtest_and_live"
    assert seg["backtest"]["twr_pct"] == doc["summary"]["twr_pct"] and seg["backtest"]["run_id"] == doc["run_id"]
    assert seg["live"]["start_nav_krw"] == doc["end_state"]["nav_krw"]
    assert [c["installment"] for c in g["contributions"]] == [3]  # numbering continues: 09 → #1, 10 → #2, 11 → #3
    assert g["performance"]["principal_krw"] == doc["end_state"]["principal_krw"] + 2_000_000
    live = seg["live"]
    assert live["investment_pnl_krw"] == pytest.approx(live["nav_krw"] - live["start_nav_krw"] - live["contributions_krw"], abs=0.02)
    assert seg["combined"]["twr_pct"] == pytest.approx(((1 + seg["backtest"]["twr_pct"] / 100) * (1 + live["twr_pct"] / 100) - 1) * 100, abs=1e-5)
    segments = [row["segment"] for row in g["history"]]
    assert segments == sorted(segments) and set(segments) == {"backtest", "live"}  # backtest rows first, then live
    assert all(p["frozen_at"] >= g["started_at"] for p in g["plans"])  # only live plans are frozen Bronze plans
    fa.write(check=True)
    tampered = json.loads(path.read_text(encoding="utf-8")); tampered["summary"]["twr_pct"] += 1
    path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        fa.write(check=True)


def test_seed_must_end_before_the_epoch_and_telegram_labels_segments(lake_tmp, monkeypatch, capsys):  # noqa: F811
    import send_telegram_orders as orders
    with pytest.raises(ValueError, match="before the live epoch"):
        seeded(lake_tmp, at("2026-10-19", dt.time(11, 0)))  # backtest ends 2026-10-19 20:30 KST = 11:30 UTC
    seeded(lake_tmp, at("2026-10-20", dt.time(11, 0)))
    run(at("2026-10-20"))
    doc = gold()
    plan = orders.todays_plans(doc, at("2026-10-20", dt.time(12, 0)))[0][0]
    text = "\n".join(orders.messages(doc, plan))
    assert "실전 구간(2026-10-20~)" in text and "백테스트 구간 (simulated)" in text and "합산(백테스트 포함)" in text
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False); monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(orders, "_now", lambda: at("2026-10-20", dt.time(12, 0)))
    monkeypatch.setattr(orders.requests if hasattr(orders, "requests") else __import__("requests"), "post",
                        lambda *a, **kw: pytest.fail("must not send without secrets"))
    assert orders.main(["--receipt", str(lake_tmp / "r.json")]) == 0
    assert "skipping delivery" in capsys.readouterr().out


def test_seam_reports_are_neither_dropped_nor_entered_twice(lake_tmp):  # noqa: F811
    """Seam rule: the backtest owns exactly the reports in its input snapshot that were public by its last
    simulated decision; it fills them, drops them, or hands them over as queued bootstrap signals. Every other
    report — published later, or published earlier but first collected after the backtest ran — becomes a live
    signal at the live pipeline's first observation. Known ids = backtest known + backtest signals."""
    seam = ("000660", "KR", dt.date(2026, 10, 19))   # public at the backtest's last decision; its open is live
    late = ("035420", "KR", dt.date(2026, 10, 14))   # published before the epoch, absent from the backtest input
    doc, _ = seeded(lake_tmp, at("2026-10-20", dt.time(11, 0)), extra=[seam])
    assert [q["ticker"] for q in doc["end_state"]["queued_signals"]] == ["000660"]
    assert not any(f["ticker"] == "000660" for f in doc["fills"])
    live_reports = [(r["report_date"], r["ticker"], r["source"], 1) for r in reports([seam, late])]
    every = {**prices()}
    run(at("2026-10-20"), prices=every, reports=live_reports)
    run(at("2026-10-21"), prices=every, reports=live_reports)
    g = gold()
    fills = [f for f in doc["fills"] + g["fills"] if f["status"] == "filled" and f["side"] == "buy"]
    assert [f["ticker"] for f in fills].count("000660") == 1 and [f["ticker"] for f in fills].count("035420") == 1
    assert [f["ticker"] for f in fills].count("005930") == 1  # backtest-owned report is not re-signalled live
    live_signals = {s["ticker"]: s for s in g["signals"]}
    assert live_signals["035420"]["observed_at"] == at("2026-10-20").isoformat()  # strict first live observation
    assert dt.datetime.fromisoformat(live_signals["000660"]["observed_at"]) == at("2026-10-19")  # handed over, not re-observed
    assert "005930" not in live_signals and "AAPL" not in live_signals
