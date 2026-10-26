import pytest

from paper_portfolio import replay
from send_telegram_portfolio import messages
import send_telegram_portfolio as sender
import json
import requests


def observation(day, price, *, observed_at=None):
    return {"schema_version": "1.0", "as_of": day, "strategy": "W_allweather_chandelier",
            "observed_at": observed_at or f"{day}T10:00:00+00:00", "weights": {"KR:A": 0.5},
            "quotes": {"KR:A": {"market": "KR", "name": "A", "price_krw": price, "date": day}}}


def test_no_backtest_profit_and_self_financing_next_close():
    a = observation("2026-10-19", 100)
    first = replay([a])
    assert first["return_pct"] == 0 and first["cash_weight_pct"] == 100
    b = observation("2026-10-20", 110)
    second = replay([a, b])
    assert second["fees_krw"] > 0
    assert second["return_pct"] < 0  # Entry at 110; the prior 100→110 move was never earned.
    assert second["positions"][0]["weight_pct"] == pytest.approx(50)
    assert second["nav_krw"] == pytest.approx(100_000_000 / 1.0015, abs=.01)
    c = observation("2026-10-21", 121)
    third = replay([a, b, c])
    assert 4.9 < third["daily_return_pct"] < 5.1
    assert sum(p["value_krw"] for p in third["positions"]) + third["cash_krw"] == pytest.approx(third["nav_krw"], abs=.02)


def test_late_publication_and_holiday_cannot_trade_old_closes():
    a = observation("2026-10-19", 100, observed_at="2026-10-20T08:00:00+00:00")  # after KR 20th close
    b = observation("2026-10-20", 110, observed_at="2026-10-20T10:00:00+00:00")
    assert replay([a, b])["cash_weight_pct"] == 100


def test_future_daily_close_is_rejected_at_observation_boundary():
    a = observation("2026-10-19", 100, observed_at="2026-10-19T01:00:00+00:00")
    with pytest.raises(ValueError, match="unavailable"):
        replay([a])


def test_telegram_contains_dates_weights_paper_status_and_chunks():
    d = replay([observation("2026-10-19", 100)])
    text = "\n".join(messages(d, "2026-10-20"))
    assert "100.00%" in text and "2026-10-19" in text and "체결 대기" in text
    assert "모의" in text
    d["positions"] = [{"id": f"A{i}", "name": "장문이름" * 20, "weight_pct": 1, "return_pct": 0, "quote_date": "2026-10-19"} for i in range(100)]
    d["cash_weight_pct"] = 0
    assert all(len(m) <= 3500 for m in messages(d, "2026-10-20"))


def test_telegram_receipt_deduplicates_success_and_never_records_failure(tmp_path, monkeypatch, capsys):
    data = tmp_path / "portfolio.json"
    data.write_text(json.dumps(replay([observation("2026-10-19", 100)])))
    monkeypatch.setattr(sender.lake, "PAPER_PORTFOLIO", data)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    receipt = tmp_path / "delivery.json"
    def fail(*a, **kw): raise requests.ConnectionError("token-bearing-url-secret-test-token")
    monkeypatch.setattr(sender.requests, "post", fail)
    assert sender.main(["--source", "paper", "--receipt", str(receipt)]) == 1
    assert not receipt.exists()
    assert "secret-test-token" not in capsys.readouterr().out
    class Response:
        status_code = 200
        def json(self): return {"ok": True}
    calls = []
    monkeypatch.setattr(sender.requests, "post", lambda *a, **kw: calls.append(kw) or Response())
    assert sender.main(["--source", "paper", "--receipt", str(receipt)]) == 0
    count = len(calls)
    assert sender.main(["--source", "paper", "--receipt", str(receipt)]) == 0 and len(calls) == count
    assert "test-chat" not in receipt.read_text()
