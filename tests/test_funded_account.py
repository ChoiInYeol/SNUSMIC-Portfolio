"""Funded account (funded-w50-v1): contribution oracles, plan/fill separation, decision rules, dedupe."""
import datetime as dt
import json

import pandas as pd
import pytest

import funded_account as fa
import market_calendars as cal
from backtest import fx

UTC = dt.timezone.utc
EVENING = dt.time(11, 30)  # 20:30 KST: KR session closed, US session of the previous NY date closed


def at(day: str, time=EVENING) -> dt.datetime:
    return dt.datetime.combine(dt.date.fromisoformat(day), time, UTC)


def frame(market, base, overrides=None, start="2026-10-01", end="2026-12-31"):
    days = [d for d in pd.bdate_range(start, end) if cal.is_trading_day(d.date(), market)]
    rows = {d: {"open": base, "high": base * 1.01, "low": base * 0.99, "close": base, "volume": 1000.0, "atr": base * 0.02}
            for d in days}
    for day, change in (overrides or {}).items():
        if change is None:
            rows.pop(pd.Timestamp(day), None)
        else:
            rows[pd.Timestamp(day)].update(change)
    return pd.DataFrame.from_dict(rows, orient="index").sort_index()


def benchmark(market, missing=()):
    df = frame(market, 100.0)
    return df["close"].drop([pd.Timestamp(d) for d in missing], errors="ignore")


@pytest.fixture
def lake_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(fa.lake, "BRONZE_FUNDED", tmp_path / "bronze")
    monkeypatch.setattr(fa.lake, "SILVER_FUNDED_LEDGER", tmp_path / "silver" / "ledger.json")
    monkeypatch.setattr(fa.lake, "FUNDED_ACCOUNT", tmp_path / "gold" / "funded-account.json")
    fx.set_usdkrw(pd.Series(1400.0, index=pd.bdate_range("2026-09-01", "2026-12-31")))
    return tmp_path


POLICY = {"atr_period": 42, "atr_mult": 5.75, "max_positions": 30}
HELD = {"005930": {"market": "KR", "display_name": "삼성전자", "n_clubs": 2},
        "AAPL": {"market": "US", "display_name": "Apple", "n_clubs": 1}}


def run(now, *, prices=None, positions=None, reports=(), kospi=None, sp500=None, start=True):
    """One pipeline evening. The first call starts the account exactly as `init` would (explicit rule)."""
    prices = prices or {"005930": frame("KR", 10_000.0), "AAPL": frame("US", 100.0)}
    if start and not (fa.lake.BRONZE_FUNDED / "account.json").exists():
        fa.start_account(epoch=now, contribution_day=fa.contribution_day_rule("first-trading-day", "proposed"),
                         as_of=now.astimezone(fa.KST).date().isoformat(), positions=HELD if positions is None else positions,
                         reports=list(reports), policy=POLICY)
    return fa.capture(as_of=now.astimezone(fa.KST).date().isoformat(), positions=HELD if positions is None else positions,
                      prices=prices, core={"INDEX:KOSPI": kospi if kospi is not None else benchmark("KR"),
                                           "INDEX:SP500": sp500 if sp500 is not None else benchmark("US")},
                      reports=list(reports), ticker_reports={}, policy=POLICY, now=now)


def gold():
    return json.loads(fa.lake.FUNDED_ACCOUNT.read_text(encoding="utf-8"))


# ── contribution oracles (no return, no cost) ───────────────────────────────

@pytest.mark.parametrize("installments, principal", [(24, 98_000_000), (48, 170_000_000), (120, 530_000_000)])
def test_principal_oracle(installments, principal):
    assert fa.principal_after(installments) == principal


def test_contribution_steps_every_24_installments():
    assert [fa.contribution_amount(n) for n in (1, 24, 25, 48, 49, 72, 73)] == \
        [2_000_000, 2_000_000, 3_000_000, 3_000_000, 4_000_000, 4_000_000, 5_000_000]


def test_contribution_day_is_first_kr_trading_day_after_epoch_month_rolling_holidays():
    epoch = dt.date(2026, 10, 20)
    assert fa.contribution_date(1, epoch) == dt.date(2026, 11, 2)   # Nov 1 is a Sunday
    assert fa.contribution_date(3, epoch) == dt.date(2027, 1, 4)    # Jan 1 holiday + weekend
    assert fa.CONFIG["contribution_day"]["label"] == "제안값(미확정)"
    assert fa.CONFIG["contribution_day"]["status"] == "proposed_unconfirmed"


def test_replayed_contributions_raise_principal_but_never_returns(lake_tmp):
    run(at("2026-10-20"), positions={})
    run(at("2026-12-01"), positions={})  # prices move; the account holds only cash
    doc = gold()
    perf = doc["performance"]
    assert [c["installment"] for c in doc["contributions"]] == [1, 2]
    assert perf["principal_krw"] == fa.principal_after(2) == 54_000_000
    assert perf["nav_krw"] == perf["principal_krw"] and perf["investment_pnl_krw"] == 0
    assert perf["twr_pct"] == 0 and perf["fees_krw"] == 0
    assert doc["contributions"][0]["late"] is True  # recorded after its scheduled deposit: effective when recorded
    assert len(list((lake_tmp / "bronze" / "contributions").glob("*.json"))) == 2
    run(at("2026-12-01", dt.time(12, 0)), positions={})
    assert len(list((lake_tmp / "bronze" / "contributions").glob("*.json"))) == 2  # account+installment dedupe


# ── plans and fills ─────────────────────────────────────────────────────────

def test_plan_is_frozen_without_the_future_open_and_fills_only_after_it_exists(lake_tmp):
    readiness = run(at("2026-10-20"))
    assert readiness["KR"]["expected_session"] == "2026-10-20" and readiness["US"]["expected_session"] == "2026-10-19"
    kr = gold()["latest_plans"]["KR"]
    assert (kr["basis_session"], kr["trading_day"], kr["outcome"], gold()["fills"]) == ("2026-10-20", "2026-10-21", "awaiting_open", [])
    order = kr["orders"][0]
    assert order["instrument"]["symbol"] == "KRX:005930" and order["budget_krw"] == 2_500_000
    assert order["est_shares"] == 249 and kr["quantity_basis"] == "estimate_from_close"
    # A different future open in the input frame cannot change the frozen plan.
    other = lake_tmp / "other"
    for name, value in (("BRONZE_FUNDED", other / "bronze"), ("SILVER_FUNDED_LEDGER", other / "l.json"), ("FUNDED_ACCOUNT", other / "g.json")):
        setattr(fa.lake, name, value)
    run(at("2026-10-20"), prices={"005930": frame("KR", 10_000.0, {"2026-10-21": {"open": 20_000.0}}), "AAPL": frame("US", 100.0)})
    future_blind = gold()["latest_plans"]["KR"]
    assert future_blind["orders"] == kr["orders"]
    for name, value in (("BRONZE_FUNDED", lake_tmp / "bronze"), ("SILVER_FUNDED_LEDGER", lake_tmp / "silver" / "ledger.json"), ("FUNDED_ACCOUNT", lake_tmp / "gold" / "funded-account.json")):
        setattr(fa.lake, name, value)
    run(at("2026-10-21"), prices={"005930": frame("KR", 10_000.0, {"2026-10-21": {"open": 10_200.0}}), "AAPL": frame("US", 100.0)})
    fill = next(f for f in gold()["fills"] if f["ticker"] == "005930")
    assert fill["status"] == "filled" and fill["price"] == 10_200.0
    assert fill["shares"] == 244 and isinstance(fill["shares"], int)  # gap-up: shares shrink inside the frozen budget
    assert -fill["cash_flow_krw"] <= 2_500_000


def test_us_plan_uses_its_own_cutoff_and_never_a_future_us_close(lake_tmp):
    us_today = frame("US", 100.0, {"2026-10-20": {"close": 1.0}})  # the NY session of 10-20 has not even opened
    run(at("2026-10-20"), prices={"005930": frame("KR", 10_000.0), "AAPL": us_today})
    session = json.loads((lake_tmp / "bronze" / "sessions" / "US" / "2026-10-19.json").read_text(encoding="utf-8"))
    assert not (lake_tmp / "bronze" / "sessions" / "US" / "2026-10-20.json").exists()
    assert session["bars"]["AAPL"]["close"] == 100.0
    us = gold()["latest_plans"]["US"]
    assert (us["basis_session"], us["trading_day"]) == ("2026-10-19", "2026-10-20")
    assert us["orders"] == []  # the bootstrap report is dated the epoch day; entries need a later session
    run(at("2026-10-21"))
    us = gold()["latest_plans"]["US"]
    buy = us["orders"][0]
    assert buy["instrument"]["symbol"] == "US:AAPL" and buy["est_fx"] == 1400.0
    assert us["account_snapshot"]["reserved_for_earlier_plans_krw"] == 0


def test_stop_triggers_a_next_open_sell_and_missing_open_keeps_it_pending(lake_tmp):
    crash = {"2026-10-23": {"close": 8_000.0, "low": 7_900.0},
             "2026-10-26": {"open": 0.0, "close": 8_000.0, "volume": 0.0},   # halted: no executable open
             "2026-10-27": {"open": 7_900.0, "close": 7_900.0}}
    prices = lambda: {"005930": frame("KR", 10_000.0, crash), "AAPL": frame("US", 100.0)}
    for day in ("2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"):
        run(at(day), prices=prices())
    sell = gold()["latest_plans"]["KR"]["orders"][0]
    assert (sell["side"], sell["reason_code"], sell["planned_weight_pct"]) == ("sell", "chandelier_stop", 0.0)
    run(at("2026-10-26"), prices=prices())
    halted = [f for f in gold()["fills"] if f["side"] == "sell"]
    assert [(f["status"], f["reason_code"]) for f in halted] == [("unfilled", "halt_or_missing_open")]
    assert gold()["latest_plans"]["KR"]["orders"][0]["side"] == "sell"  # retried in the next plan
    run(at("2026-10-27"), prices=prices())
    sold = [f for f in gold()["fills"] if f["side"] == "sell" and f["status"] == "filled"]
    assert sold[0]["price"] == 7_900.0 and not any(p["ticker"] == "005930" for p in gold()["positions"])


def test_missing_open_retries_then_expires_and_one_share_rule(lake_tmp):
    halted = {d: {"open": None} for d in ("2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26", "2026-10-27")}
    prices = lambda: {"005930": frame("KR", 10_000.0, halted), "AAPL": frame("US", 4_000.0)}  # 1 AAPL ≈ 5.6M KRW > 2.5M budget
    for day in ("2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26", "2026-10-27"):
        run(at(day), prices=prices())
    doc = gold()
    kr_fills = [f["reason_code"] for f in doc["fills"] if f["ticker"] == "005930"]
    assert kr_fills == ["halt_or_missing_open"] * 5
    signal = {s["ticker"]: s for s in doc["signals"]}
    assert signal["005930"]["status"] == "expired"
    assert signal["AAPL"]["status"] == "dropped" and "1주" in signal["AAPL"]["outcome"]
    assert all(o["ticker"] != "005930" for o in doc["latest_plans"]["KR"]["orders"])


def test_insufficient_cash_and_simultaneous_orders_follow_funding_order(lake_tmp):
    many = {f"{i:06d}": {"market": "KR", "display_name": f"종목{i}", "n_clubs": 1} for i in range(1, 25)}
    prices = {t: frame("KR", 10_000.0) for t in many}
    run(at("2026-10-20"), prices=prices, positions=many)
    plan = gold()["latest_plans"]["KR"]
    assert len(plan["orders"]) == 20  # 20 x 5% of 50M; the rest cannot get half a budget
    cash = [e for e in plan["excluded"] if e["reason_code"] == "insufficient_cash"]
    assert len(cash) == 4 and all(e["final"] for e in cash)
    assert plan["account_snapshot"]["cash_after_plan_est_krw"] == pytest.approx(0, abs=1)
    assert [o["seq"] for o in plan["orders"]] == list(range(1, 21))


def test_index_allweather_is_never_an_orderable_ticker(lake_tmp):
    assert fa.instrument("INDEX:AllWeather", "KR") == (None, "지수·합성 상품은 주문 불가")
    run(at("2026-10-20"), positions={"INDEX:AllWeather": {"market": "KR", "display_name": "올웨더"}, **HELD})
    doc = gold()
    plan = doc["latest_plans"]["KR"]
    assert all(o["ticker"] != "INDEX:AllWeather" for o in plan["orders"])
    assert {"ticker": "INDEX:AllWeather", "reason_code": "not_orderable", "final": True}.items() <= \
        next(e for e in plan["excluded"] if e["ticker"] == "INDEX:AllWeather").items()
    assert doc["excluded_instruments"][0]["ticker"] == "INDEX:AllWeather"


# ── dedupe, revision, cancellation, readiness ───────────────────────────────

def test_plans_dedupe_and_revisions_are_explicit_and_frozen_after_open(lake_tmp):
    run(at("2026-10-20"))
    run(at("2026-10-20", dt.time(12, 0)))
    folder = lake_tmp / "bronze" / "plans" / "KR" / "2026-10-21" / fa.POLICY_VERSION
    assert [p.name for p in folder.glob("r*.json")] == ["r01.json"]
    report = [(dt.date(2026, 10, 20), "000660", "2026-10-20_hynix.md", 3)]
    prices = {"005930": frame("KR", 10_000.0), "AAPL": frame("US", 100.0), "000660": frame("KR", 50_000.0)}
    run(at("2026-10-20", dt.time(13, 0)), prices=prices, reports=report)
    revised = gold()["latest_plans"]["KR"]
    assert revised["revision"] == 2 and revised["supersedes"] == json.loads((folder / "r01.json").read_text(encoding="utf-8"))["plan_id"]
    assert {o["ticker"] for o in revised["orders"]} == {"005930", "000660"}
    assert [p["status"] for p in gold()["plans"] if p["market"] == "KR"] == ["superseded", "active"]
    with pytest.raises(ValueError, match="immutable"):
        fa.cancel_plan("KR", "2026-10-21", "late", at("2026-10-21", dt.time(0, 30)))


def test_cancelled_plan_never_fills(lake_tmp):
    run(at("2026-10-20"))
    fa.cancel_plan("KR", "2026-10-21", "operator test", at("2026-10-20", dt.time(12, 0)))
    run(at("2026-10-21"))
    doc = gold()
    assert [p["outcome"] for p in doc["plans"] if p["trading_day"] == "2026-10-21" and p["market"] == "KR"] == ["superseded", "cancelled"]
    assert not any(f["trading_day"] == "2026-10-21" and f["market"] == "KR" for f in doc["fills"])


def test_not_ready_data_records_no_session_and_no_plan(lake_tmp):
    run(at("2026-10-20"))
    run(at("2026-10-21"), kospi=benchmark("KR", missing=["2026-10-21"]))
    assert not (lake_tmp / "bronze" / "sessions" / "KR" / "2026-10-21.json").exists()
    assert gold()["latest_plans"]["KR"]["trading_day"] == "2026-10-21"  # no new KR plan without today's close


def test_benchmark_bar_on_a_calendar_holiday_fails_loudly(lake_tmp):
    run(at("2026-12-23"))
    bad = pd.concat([benchmark("KR"), pd.Series([100.0], index=[pd.Timestamp("2026-12-25")])]).sort_index()
    with pytest.raises(ValueError, match="holiday"):
        run(at("2026-12-28"), kospi=bad)


def test_account_does_not_start_without_explicit_init(lake_tmp, monkeypatch):
    assert run(at("2026-10-20"), start=False) is None  # the pipeline hook never creates a genesis
    assert not (lake_tmp / "bronze").exists() and fa.write() is None
    now = at("2026-10-20")
    with pytest.raises(ValueError, match="24 hours"):
        fa.init(epoch=now + dt.timedelta(minutes=1), contribution_day="first-trading-day", status="proposed", now=now)
    with pytest.raises(ValueError, match="24 hours"):
        fa.init(epoch=now - dt.timedelta(days=2), contribution_day="first-trading-day", status="proposed", now=now)
    with pytest.raises(ValueError, match="day of month"):
        fa.contribution_day_rule("31", "proposed")
    with pytest.raises(ValueError, match="status"):
        fa.contribution_day_rule("first-trading-day", "")
    monkeypatch.setattr("sys.argv", ["funded_account.py", "init", "--epoch", now.isoformat()])
    import runpy
    with pytest.raises(SystemExit):  # argparse: --contribution-day and its status are required
        runpy.run_path(fa.__file__, run_name="__main__")
    assert not (lake_tmp / "bronze").exists()


def test_init_writes_genesis_from_published_holdings_with_explicit_rule(lake_tmp, monkeypatch):
    web = lake_tmp / "web"; web.mkdir()
    (web / "strategy-backtest.json").write_text(json.dumps({"generated_at": "2026-10-20T11:00:00+00:00", "signals": {"open_positions": [
        {"ticker": "005930", "market": "KR", "display_name": "삼성전자", "trigger_schools": ["smic", "kuvic"]}]}}), encoding="utf-8")
    perf = lake_tmp / "performance.csv"
    perf.write_text("source_file,report_date,market,ticker,rating_class\na/2026-10-01_x.md,2026-10-01,KR,660,buy\n", encoding="utf-8")
    monkeypatch.setattr(fa.lake, "WEB_DATA", web); monkeypatch.setattr(fa.lake, "GOLD_REPORT_PERFORMANCE", perf)
    with pytest.raises(ValueError, match="newer than the epoch"):
        fa.init(epoch=at("2026-10-20", dt.time(10, 0)), contribution_day="first-trading-day", status="proposed", now=at("2026-10-20"))
    genesis = fa.init(epoch=at("2026-10-20"), contribution_day="first-trading-day", status="proposed", now=at("2026-10-20"))
    assert genesis["config"]["contribution_day"]["label"] == "제안값(미확정)"
    assert [b["ticker"] for b in genesis["bootstrap"]] == ["005930"] and genesis["bootstrap"][0]["clubs"] == 2
    assert genesis["known_report_ids"] == [fa.report_id(dt.date(2026, 10, 1), "000660", "2026-10-01_x.md")]
    with pytest.raises(FileExistsError):  # the genesis is append-only: a second start is refused
        fa.init(epoch=at("2026-10-20"), contribution_day="25", status="confirmed", now=at("2026-10-20"))


def test_confirmed_day_of_month_rule_changes_schedule_and_label():
    rule = fa.contribution_day_rule("25", "confirmed")
    config = {**fa.CONFIG, "contribution_day": rule}
    assert rule["label"] == "사용자 확정" and rule["status"] == "user_confirmed"
    assert fa.contribution_date(1, dt.date(2026, 10, 20), config) == dt.date(2026, 11, 25)
    assert fa.contribution_date(2, dt.date(2026, 10, 20), config) == dt.date(2026, 12, 28)  # 12/25 holiday, weekend


def test_tampered_bronze_and_drift_are_rejected(lake_tmp):
    run(at("2026-10-20"), positions={})
    run(at("2026-12-01"), positions={})
    fa.write(check=True)
    path = lake_tmp / "bronze" / "contributions" / "0001.json"
    doc = json.loads(path.read_text(encoding="utf-8")); doc["amount_krw"] = 9_000_000
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="frozen schedule"):
        fa.load_bronze()


# ── telegram ────────────────────────────────────────────────────────────────

def test_order_message_and_stale_plan_gate(lake_tmp, monkeypatch, capsys):
    import send_telegram_orders as orders
    import send_telegram_portfolio as shared
    run(at("2026-10-20"))
    doc = gold()
    plans, problems = orders.todays_plans(doc, at("2026-10-20", dt.time(12, 0)))
    assert [p["market"] for p in plans] == ["KR", "US"] and problems == []
    text = "\n".join(orders.messages(doc, plans[0]))
    for needle in ("KR", "기준일(종가) 2026-10-20", "주문일 2026-10-21", "[매수] 삼성전자 KRX:005930", "249주(추정)",
                   "0.00% → 4.98%", "계정 개시 시점 W 공표 보유 종목", "추정비용", "제안값(미확정)", "체결 조건", "실계좌 주문이 아닙니다"):
        assert needle in text
    assert "[매수] 올웨더" not in text and "INDEX:AllWeather:" in text
    stale, problems = orders.todays_plans(doc, at("2026-10-21"))
    assert stale == [] and len(problems) == 2  # yesterday's plans are never re-sent as today's

    monkeypatch.setattr(orders.lake, "FUNDED_ACCOUNT", fa.lake.FUNDED_ACCOUNT)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-test-token"); monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    class Response:
        status_code = 200
        def json(self): return {"ok": True}
    calls = []
    monkeypatch.setattr(shared.requests, "post", lambda *a, **kw: calls.append(kw) or Response())
    monkeypatch.setattr(orders, "_now", lambda: at("2026-10-20", dt.time(12, 0)))
    receipt = lake_tmp / "orders.json"
    assert orders.main(["--receipt", str(receipt)]) == 0
    sent = len(calls)
    assert sent >= 2 and orders.main(["--receipt", str(receipt)]) == 0 and len(calls) == sent
    assert "chat" not in receipt.read_text() and "secret-test-token" not in capsys.readouterr().out


# ── review fixes ────────────────────────────────────────────────────────────

def test_cancel_is_final_until_explicit_uncancel(lake_tmp, monkeypatch):
    import send_telegram_orders as orders
    import send_telegram_portfolio as shared
    run(at("2026-10-20"))
    fa.cancel_plan("KR", "2026-10-21", "operator", at("2026-10-20", dt.time(12, 0)))
    folder = lake_tmp / "bronze" / "plans" / "KR" / "2026-10-21" / fa.POLICY_VERSION
    run(at("2026-10-20", dt.time(12, 30)))  # routine re-capture before the open
    assert [p.name for p in sorted(folder.glob("r*.json"))] == ["r01.json", "r02.json"]
    assert gold()["latest_plans"]["KR"]["status"] == "cancelled"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t"); monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    class Response:
        status_code = 200
        def json(self): return {"ok": True}
    calls = []
    monkeypatch.setattr(shared.requests, "post", lambda *a, **kw: calls.append(kw["json"]["text"]) or Response())
    monkeypatch.setattr(orders, "_now", lambda: at("2026-10-20", dt.time(12, 45)))  # before the 13:30 UTC US open
    receipt = lake_tmp / "orders.json"
    assert orders.main(["--receipt", str(receipt)]) == 0
    kr = [t for t in calls if " KR " in t.splitlines()[0]]
    assert kr and all("취소된 주문 계획" in t for t in kr) and not any("[매수]" in t for t in kr)
    sent = len(calls)
    run(at("2026-10-20", dt.time(13, 0)))
    assert orders.main(["--receipt", str(receipt)]) == 0 and len(calls) == sent  # nothing new to send
    revived = fa.uncancel_plan("KR", "2026-10-21", "mistake", at("2026-10-20", dt.time(13, 10)))
    assert (revived["revision"], revived["status"]) == (3, "active") and revived["orders"]


def test_contribution_landing_tomorrow_is_scheduled_not_applied(lake_tmp):
    run(at("2026-10-20"), positions={})
    run(at("2026-10-30"), positions={})  # Friday plan for Monday 11-02 records installment 1 (lands 08:30 Mon)
    doc = gold()
    assert doc["contributions"] == [] and [c["installment"] for c in doc["scheduled_contributions"]] == [1]
    assert doc["performance"]["principal_krw"] == doc["performance"]["nav_krw"] == 50_000_000
    assert doc["latest_plans"]["KR"]["account_snapshot"]["incoming_contributions_krw"] == 2_000_000
    run(at("2026-11-02"), positions={})
    doc = gold()
    assert [c["installment"] for c in doc["contributions"]] == [1] and doc["scheduled_contributions"] == []
    assert doc["performance"]["principal_krw"] == 52_000_000


def test_float_budget_drift_does_not_revise_or_resend(lake_tmp):
    run(at("2026-10-20"))
    folder = lake_tmp / "bronze" / "plans" / "KR" / "2026-10-21" / fa.POLICY_VERSION
    plan = json.loads((folder / "r01.json").read_text(encoding="utf-8"))
    drifted = {k: v for k, v in plan.items() if k not in ("plan_id", "revision", "frozen_at", "supersedes", "revision_reason", "status")}
    drifted["orders"] = [{**o, "budget_krw": o["budget_krw"] + 3.7} for o in plan["orders"]]
    assert fa.freeze_plan(drifted, at("2026-10-20", dt.time(12, 0)))["plan_id"] == plan["plan_id"]
    drifted["orders"] = [{**o, "est_shares": o["est_shares"] - 1} for o in plan["orders"]]
    assert fa.freeze_plan(drifted, at("2026-10-20", dt.time(12, 0)))["revision"] == 2


def test_delivery_workflow_gates_and_receipt_persistence():
    root = fa.lake.ROOT
    try:
        import yaml
    except ImportError:
        yaml = None
    text = (root / ".github/workflows/funded-orders.yml").read_text(encoding="utf-8")
    if yaml:
        flow = yaml.safe_load(text)
        trigger = flow[True]["workflow_run"]  # PyYAML reads the `on` key as True
        assert trigger["branches"] == ["main"] and set(trigger["workflows"]) == {"refresh-daily", "full-sweep-manual"}
        steps = flow["jobs"]["notify"]["steps"]
        save = next(s for s in steps if s.get("uses", "").startswith("actions/cache/save"))
        assert save["if"] == "always()" and any(s.get("uses", "").startswith("actions/cache/restore") for s in steps)
    else:
        assert "branches: [main]" in text and "full-sweep-manual" in text and "if: always()" in text
    assert "actions/cache@" not in text  # the combined action skips saving after a failed step
    for workflow in ("refresh-daily.yml", "refresh-reports.yml"):  # every workflow that freezes plans gates them
        assert "scripts/funded_account.py --check" in (root / ".github/workflows" / workflow).read_text(encoding="utf-8")
    assert "FUNDED_ACCOUNT_START" not in (root / "src/lib/funded-account-data.ts").read_text(encoding="utf-8")
    assert "신호 유효기간: 제한 없음(사용자 결정" in (root / "docs/FUNDED-ACCOUNT.md").read_text(encoding="utf-8")
