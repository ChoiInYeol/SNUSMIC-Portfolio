import datetime as dt

import pandas as pd

import build_forward_record as forward
from backtest import strategies


def test_first_post_signal_open_is_not_replaced_by_later_or_same_close(monkeypatch):
    df = pd.DataFrame({"open": [0, 11], "close": [10, 12]}, index=pd.to_datetime(["2026-10-20", "2026-10-21"]))
    monkeypatch.setattr(forward, "_load_price", lambda *args: df)
    result = forward._score_entry({"ticker": "A", "market": "KR"}, dt.date(2026, 10, 19))
    assert result["status"] == "unavailable" and result["entry_price"] is None


def test_open_entry_atr_independent_of_same_day_extreme(monkeypatch):
    day = dt.date(2026, 10, 20)
    idx = pd.to_datetime(["2026-10-19", "2026-10-20"])
    prices = {"A": pd.DataFrame({"open": [100., 100.], "close": [100., 100.], "atr": [2., 9999.]}, index=idx)}
    monkeypatch.setattr(strategies, "build_pending_entries", lambda *a, **kw: {day: [("A", "smic", 1)]})
    monkeypatch.setattr(strategies, "_try_enter", lambda *a, **kw: ({"entry_price": 100., "shares": 1., "last_close": 100., "cost": 100., "entry_date": day}, 1000.))
    monkeypatch.setattr(strategies, "_has_day", lambda *a: False)  # Observe only the open boundary, before close updates.
    monkeypatch.setattr(strategies, "_close_trade", lambda *a: {})
    monkeypatch.setattr(strategies, "_compute_result", lambda *a, **kw: kw)
    result = strategies.run_chandelier(prices, [], [day], "test")
    assert result["open_positions"]["A"]["stop"] == 100 - strategies.CHANDELIER_ATR_MULT * 2
