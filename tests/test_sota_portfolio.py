"""SOTA Telegram book: engine quantities, FX accounting, signal timing and source isolation."""
import copy
import json

import pandas as pd
import pytest

import lake
import send_telegram_portfolio as sender
import sota_portfolio as sota


@pytest.fixture
def inputs():
    day = "2026-10-27"
    key = "W_allweather_chandelier"
    metrics = {"start": "2020-01-02", "end": day, "initial_capital_krw": 100_000,
               "final_nav_krw": 120_000, "total_return_pct": 20.0}
    positions = {
        "005930": {"market": "KR", "display_name": "삼성전자", "shares": 10.5, "last_close": 1000,
                   "entry_price": 950, "fx": 1, "cost": 10_000, "stop": 990},
        "MSFT": {"market": "US", "display_name": "Microsoft", "shares": 2.25, "last_close": 20,
                 "entry_price": 19, "fx": 1400, "cost": 60_000, "stop": 21},
    }
    def trade(ticker, date, pnl, reason="t_chandelier_ATR5.75"):
        return {"ticker": ticker, "market": "KR", "display_name": ticker,
                "entry_date": "2026-09-01", "exit_date": date, "entry": 100,
                "exit": 100 + pnl / 100, "pnl_krw": pnl, "return_pct": pnl / 100,
                "exit_reason": reason}
    result = {"label": key, "metrics": metrics, "open_positions": positions,
              "execution": {"parked_krw": 40_000, "cash_krw": 6500, "pending_exits": ["MSFT"]},
              "trades": [trade("PROFIT", "2026-10-23", 1000), trade("LOSS", "2026-10-26", -500),
                         trade("OLD", "2026-01-01", 500), trade("FUTURE", "2026-10-28", 1000),
                         trade("OPEN", day, 1000, "데이터_종료_미청산")]}
    def fill(date, ticker, side, price, shares, cost=None, market="KR", fx=1):
        gross = price * shares * fx
        cash = -(cost or gross) if side == "buy" else gross
        return dict(date=date,ticker=ticker,market=market,side=side,price=price,fx=fx,shares=shares,
                    gross_krw=gross,cash_flow_krw=cash,fees_krw=abs(cash)-gross if side=="buy" else 0,reason="test")
    result["execution"]["fills"] = [fill("2026-09-01",t,"buy",100,100) for t in ("PROFIT","LOSS")]
    result["execution"]["fills"] += [fill("2026-10-01",t,"buy",p["entry_price"],p["shares"],p["cost"],p["market"],p["fx"]) for t,p in positions.items()]
    result["execution"]["fills"] += [fill("2026-10-23","PROFIT","sell",110,100),fill("2026-10-26","LOSS","sell",95,100)]
    def buy(ticker, pending):
        return {"ticker": ticker, "market": "KR", "display_name": ticker, "entry_pending": pending,
                "entry_basis_date": None if pending else "2026-10-26", "entry_reason": "학회 매수리포트 → 익일 시가"}
    model = {"generated_at": "2026-10-27T10:00:00+00:00", "metrics": metrics,
             "multi_strategy": {"headline_key": key}, "signals": {
                 "as_of": day, "headline_strategy": key,
                 "regime": {"applies": False, "state": "OFF", "kospi_close": 2500,
                            "kospi_ma200": 2600, "note": "참고용 레짐"},
                 "slots": {"max_positions": 20, "open": 2, "available": 18},
                 "open_positions": [{"ticker": t, "entry_date": "2026-10-01"} for t in positions],
                 "imminent_buys": [buy("PENDING", True), buy("ALREADY_PASSED", False)]}}
    calendar = [d.date() for d in pd.bdate_range("2026-09-01", day)]
    prices = {t: pd.DataFrame({"close": [p["last_close"]]}, index=pd.to_datetime([day])) for t, p in positions.items()}
    return model, result, prices, calendar


def build(inputs):
    model, result, prices, calendar = inputs
    return sota.build(model, result, prices=prices, calendar=calendar)


def test_engine_quantities_fx_pnl_and_current_weights_reconcile(inputs):
    doc = build(inputs)
    kr, us = ({p["ticker"]: p for p in doc["positions"]}[t] for t in ("005930", "MSFT"))
    assert kr["shares"] == 10.5 and us["shares"] == 2.25
    assert (kr["value_krw"], kr["pnl_krw"], kr["weight_pct"]) == (10_500, 500, 8.75)
    assert (us["value_krw"], us["pnl_krw"], us["weight_pct"]) == (63_000, 3000, 52.5)
    assert kr["return_pct"] == pytest.approx(5) and us["return_pct"] == pytest.approx(5)
    assert sum(p["weight_pct"] for p in doc["positions"]) + doc["parked_weight_pct"] + doc["cash_weight_pct"] == pytest.approx(100)
    assert {t["ticker"] for t in doc["fills"] if t["side"] == "sell"} == {"PROFIT", "LOSS"}
    assert [b["ticker"] for b in doc["buy_candidates"]] == ["PENDING"]
    assert us["exit_pending"] and not kr["exit_pending"]


def test_digest_links_to_the_same_ledger_without_repeating_the_book(inputs):
    text = "\n".join(sender.sota_messages(build(inputs), "2026-10-27"))
    for expected in ("2026-10-27", "120,000원", "W_allweather_chandelier", "https://verdict-archive.vercel.app/portfolio"):
        assert expected in text
    assert "—" not in text and "·" not in text
    assert "ALREADY_PASSED" not in text and "FUTURE" not in text and "OPEN (" not in text
    assert "현금 100.00%" not in text


def test_stale_source_is_visible_and_future_evaluation_is_rejected(inputs):
    doc = build(inputs)
    assert "7일 이전" in "\n".join(sender.sota_messages(doc, "2026-11-03"))
    with pytest.raises(ValueError, match="future"):
        sender.sota_messages(doc, "2026-10-26")


def test_future_prices_cannot_change_the_model_book(inputs):
    expected = build(inputs)
    inputs[2]["MSFT"].loc[pd.Timestamp("2026-10-28"), "close"] = 999
    assert build(inputs) == expected


def test_empty_book_and_no_signals_are_explicit(inputs):
    model, result, _, _ = inputs
    result["open_positions"] = {}
    result["trades"] = []
    result["execution"].update(parked_krw=120_000, cash_krw=0, pending_exits=[], fills=[])
    model["signals"].update(open_positions=[], imminent_buys=[], slots={"max_positions": 20, "open": 0, "available": 20})
    text = "\n".join(sender.sota_messages(build(inputs), "2026-10-27"))
    assert "보유 0종목" in text


def test_a_new_headline_is_selected_from_source_and_not_hardcoded_to_w(inputs):
    model, result, _, _ = inputs
    model["multi_strategy"]["headline_key"] = result["label"] = model["signals"]["headline_strategy"] = "T-_kospi_core_regime"
    model["signals"]["regime"].update(applies=True, note="레짐 OFF — 유휴 잔액은 현금 이자 3%, 신규 진입 유지")
    doc = build(inputs)
    text = "\n".join(sender.sota_messages(doc, "2026-10-27"))
    assert doc["strategy"] == "T-_kospi_core_regime" and "T-_kospi_core_regime" in text
    assert "W는 레짐 필터 없음" not in text


@pytest.mark.parametrize("field,value", [("shares", 0), ("fx", float("nan")), ("current_price", -1),
                                         ("weight_pct", 5), ("pnl_krw", 999), ("return_pct", 999)])
def test_invalid_financial_numbers_are_rejected(inputs, field, value):
    doc = build(inputs)
    doc["positions"][0][field] = value
    with pytest.raises(ValueError):
        sota.validate(doc)


def test_mixed_runs_quote_drift_and_unbalanced_nav_are_rejected(inputs):
    doc = build(inputs)
    newer = copy.deepcopy(inputs[0]); newer["generated_at"] = "2026-10-28T10:00:00+00:00"
    with pytest.raises(ValueError, match="differs from current backtest"):
        sota.validate(doc, newer)
    inputs[2]["MSFT"].iloc[-1, 0] = 99
    with pytest.raises(ValueError, match="quote differs"):
        build(inputs)
    doc["parked_krw"] += 100
    doc["parked_weight_pct"] = doc["parked_krw"] / doc["nav_krw"] * 100
    with pytest.raises(ValueError, match="reconcile to NAV"):
        sota.validate(doc)


def test_sold_quantities_and_cash_flows_cannot_be_fabricated(inputs):
    doc = build(inputs)
    sale = next(f for f in doc["fills"] if f["side"] == "sell")
    sale["shares"] *= 2
    with pytest.raises(ValueError):
        sota.validate(doc)


def test_default_sender_uses_sota_validates_source_and_deduplicates_without_touching_paper(inputs, tmp_path, monkeypatch):
    book = tmp_path / "sota.json"
    web = tmp_path / "web"; web.mkdir()
    monkeypatch.setattr(lake, "GOLD_SOTA_PORTFOLIO", book)
    monkeypatch.setattr(lake, "WEB_DATA", web)
    monkeypatch.setattr(lake, "PAPER_PORTFOLIO", tmp_path / "must-not-read-paper.json")
    model, result, prices, calendar = inputs
    (web / "strategy-backtest.json").write_text(json.dumps(model), encoding="utf-8")
    sota.write(model, result, prices=prices, calendar=calendar)
    assert sota.main(["--check"]) == 0
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-recipient")
    class Response:
        status_code = 200
        def json(self): return {"ok": True}
    calls = []
    monkeypatch.setattr(sender.requests, "post", lambda *a, **kw: calls.append(kw["json"]["text"]) or Response())
    receipt = tmp_path / "receipt.json"
    assert sender.main(["--dry-run", "--receipt", str(receipt)]) == 0
    assert not calls and not receipt.exists()
    day = sender.dt.datetime.now(sender.ZoneInfo("Asia/Seoul")).date().isoformat()
    old_paper_receipt = sender.hashlib.sha256(f"{day}:test-recipient".encode()).hexdigest()
    receipt.write_text(json.dumps({"schema_version": "1.0", "delivered": [old_paper_receipt]}), encoding="utf-8")
    assert sender.main(["--receipt", str(receipt)]) == 0 and calls
    assert calls[0].startswith("SOTA")
    count = len(calls)
    assert sender.main(["--receipt", str(receipt)]) == 0 and len(calls) == count
    model["generated_at"] = "2026-10-28T10:00:00+00:00"
    (web / "strategy-backtest.json").write_text(json.dumps(model), encoding="utf-8")
    with pytest.raises(ValueError, match="differs from current backtest"):
        sender.main(["--force", "--receipt", str(receipt)])
    assert len(calls) == count


def test_committed_model_book_matches_the_committed_winner():
    model = json.loads((lake.WEB_DATA / "strategy-backtest.json").read_text(encoding="utf-8"))
    doc = json.loads(lake.GOLD_SOTA_PORTFOLIO.read_text(encoding="utf-8"))
    sota.validate(doc, model)
