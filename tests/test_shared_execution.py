import datetime as dt
import json

import pandas as pd
import pytest

from backtest import fx
from backtest.parking_engine import ENGINE_VERSION
from backtest.strategies import run_parking_core_chandelier
from paper_execution import replay
from build_strategy_robustness import delayed_schedule, ticker_contributions


def frame(opens=(100, 100, 100), closes=(100, 100, 50), dates=("2026-10-19", "2026-10-20", "2026-10-21")):
    return pd.DataFrame({"open": opens, "close": closes, "atr": [2.] * len(dates)}, index=pd.to_datetime(dates))


def run(prices, parking=(100, 100, 100), schedule=None):
    dates = sorted({i.date() for f in prices.values() for i in f.index})
    series = pd.Series(parking, index=pd.to_datetime(dates))
    return run_parking_core_chandelier(prices, [], dates, "oracle", series, 42, 5, 20,
                                      entry_schedule=schedule or {dates[1]: [("A", "signal", 1)]})


def test_open_budget_does_not_see_todays_parking_return(monkeypatch):
    monkeypatch.setattr(fx, "_USDKRW", None)
    one = run({"A": frame()})
    two = run({"A": frame()}, parking=(100, 1000, 1000))
    assert one["execution"]["fills"][0]["shares"] == two["execution"]["fills"][0]["shares"]
    expected = (100_000_000 * .9995 * .05) * .9995 * .997 / (100 * 1300)
    assert one["execution"]["fills"][0]["shares"] == pytest.approx(expected)


def test_kr_open_cannot_use_later_us_exit_to_reuse_a_slot():
    dates = [dt.date(2026, 10, d) for d in (19, 20, 21)]
    prices = {"US": frame(closes=(100, 50, 50)), "KR": frame(closes=(100, 100, 100))}
    meta = {"US": [{"report_date": dates[0] - dt.timedelta(days=1), "market": "US", "display_name": "US", "target_price": None, "school": "published"}],
            "KR": [{"report_date": dates[0], "market": "KR", "display_name": "KR", "target_price": None, "school": "published"}]}
    park = pd.Series(100., index=pd.to_datetime(dates))
    r = run_parking_core_chandelier(prices, [], dates, "test", park, 42, 5, 1, ticker_reports=meta,
        entry_schedule={dates[0]: [("US", "seed", 1)], dates[2]: [("KR", "later", 1)]})
    assert not any(f["ticker"] == "KR" for f in r["execution"]["fills"])
    assert any(f["side"] == "sell" and f["ticker"] == "US" for f in r["execution"]["fills"])


def test_us_open_fx_is_independent_of_same_day_close(monkeypatch):
    idx = frame().index
    monkeypatch.setattr(fx, "_USDKRW", pd.Series([1000., 1000., 1000.], index=idx))
    first = run({"A": frame()})["execution"]["fills"][0]
    # Default A is US; changing entry day's FX close cannot change its opening shares.
    monkeypatch.setattr(fx, "_USDKRW", pd.Series([1000., 9000., 1000.], index=idx))
    second = run({"A": frame()})["execution"]["fills"][0]
    assert first["shares"] == second["shares"] and first["fx"] == second["fx"] == 1000


def observation(day="2026-10-19", observed_at="2026-10-19T23:00:00+00:00", complete="2026-10-16", rows=None):
    return {"schema_version": "2.0", "as_of": day, "complete_through": complete, "observed_at": observed_at,
            "policy": {"strategy": "W_allweather_chandelier", "engine_version": ENGINE_VERSION, "atr_period": 42,
                       "atr_mult": 5., "max_positions": 20, "position_weight": .05, "cost_per_side": .003, "parking_cost": .0005},
            "known_report_ids": [], "report_events": [],
            "bootstrap": [{"ticker": "A", "market": "KR", "name": "A", "source": "bootstrap", "clubs": 1,
                           "report_date": "2026-10-19", "observed_at": observed_at}],
            "bars": {"A": rows or [{"date": "2026-10-19", "open": 100., "close": 100., "atr": 2.}]},
            "parking": [{"date": "2026-10-16", "value": 100.}],
            "fx": [{"date": "2026-10-16", "value": 1000.}]}


def test_paper_protocol_matches_backtest_engine_open_fills_and_nav():
    first = observation()
    second = observation("2026-10-21", "2026-10-21T10:00:00+00:00", "2026-10-20", [
        {"date": "2026-10-19", "open": 100., "close": 100., "atr": 2.},
        {"date": "2026-10-20", "open": 110., "close": 121., "atr": 2.}])
    second["bootstrap"] = first["bootstrap"]
    second["parking"] += [{"date": "2026-10-20", "value": 110.}]
    second["fx"] += [{"date": "2026-10-20", "value": 1000.}]
    actual = replay([first, second])
    prices = {"A": pd.DataFrame(second["bars"]["A"]).set_index("date")}; prices["A"].index = pd.to_datetime(prices["A"].index)
    expected = run_parking_core_chandelier(prices, [], [dt.date(2026, 10, 20)], "test", pd.Series([100., 110.], index=pd.to_datetime(["2026-10-19", "2026-10-20"])),
        42, 5, 20, entry_schedule={dt.date(2026, 10, 20): [("A", "bootstrap", 1)]},
        ticker_reports={"A": [{"report_date": dt.date(2026, 10, 19), "market": "KR", "display_name": "A", "target_price": None, "school": "published"}]})
    assert actual["nav_krw"] == expected["nav_df"].iloc[-1]
    assert actual["fills"][0]["price"] == 110.  # Never the 121 close.
    assert actual["fills"][0]["shares"] == expected["execution"]["fills"][0]["shares"]


def test_paper_cannot_trade_an_open_before_actual_publication():
    first = observation(observed_at="2026-10-20T02:00:00+00:00")  # KR 20th open already passed.
    second = observation("2026-10-20", "2026-10-21T00:00:00+00:00", "2026-10-20")
    second["bootstrap"] = first["bootstrap"]
    second["bars"]["A"] += [{"date": "2026-10-20", "open": 110., "close": 121., "atr": 2.}]
    result = replay([first, second])
    assert not result["fills"]


def test_future_completed_session_and_quotes_are_rejected():
    first = observation(observed_at="2026-10-19T22:00:00+00:00")
    first["complete_through"] = "2026-10-19"
    with pytest.raises(ValueError, match="seal"):
        replay([first])  # FX's UTC day has not finished.
    first = observation()
    first["bars"]["A"][0]["close"] = 0
    with pytest.raises(ValueError, match="stock quote"):
        replay([first])


def test_fixed_mandate_and_bootstrap_cannot_change():
    first = observation(); second = json.loads(json.dumps(first))
    second["policy"]["atr_mult"] = 9
    with pytest.raises(ValueError, match="policy changed"):
        replay([first, second])
    second = json.loads(json.dumps(first)); second["bootstrap"][0]["name"] = "changed"
    with pytest.raises(ValueError, match="bootstrap changed"):
        replay([first, second])


def test_late_bar_cannot_backfill_a_sealed_open():
    first = observation(observed_at="2026-10-20T02:00:00+00:00")
    first["complete_through"] = "2026-10-19"
    second = observation("2026-10-21", "2026-10-22T00:00:00+00:00", "2026-10-21")
    second["bootstrap"] = first["bootstrap"]
    second["bars"]["A"] += [{"date": "2026-10-21", "open": 110., "close": 110., "atr": 2.}]
    third = json.loads(json.dumps(second)); third["observed_at"] = "2026-10-23T00:00:00+00:00"
    third["bars"]["A"] += [{"date": "2026-10-20", "open": 1., "close": 1., "atr": 2.}]
    before = replay([first, second]); after = replay([first, second, third])
    assert before["fills"] == after["fills"] and before["nav_krw"] == after["nav_krw"]
    assert any("late bar" in issue for issue in after["data_issues"])


def test_prior_observed_prices_are_not_rewritten():
    first = observation()
    second = observation("2026-10-20", "2026-10-21T00:00:00+00:00", "2026-10-20")
    second["bootstrap"] = first["bootstrap"]
    second["bars"]["A"] += [{"date": "2026-10-20", "open": 110., "close": 110., "atr": 2.}]
    third = json.loads(json.dumps(second)); third["observed_at"] = "2026-10-22T00:00:00+00:00"
    third["bars"]["A"][-1]["open"] = 1
    assert replay([first, second])["fills"] == replay([first, second, third])["fills"]


def test_stop_sells_only_at_the_following_available_open():
    dates = [dt.date(2026, 10, d) for d in (19, 20, 21)]
    result = run({"A": frame(opens=(100, 100, 70), closes=(100, 50, 70))},
                 schedule={dates[0]: [("A", "seed", 1)]})
    sells = [f for f in result["execution"]["fills"] if f["side"] == "sell"]
    assert len(sells) == 1 and sells[0]["date"] == "2026-10-21" and sells[0]["price"] == 70
    actual_net = sum(f["cash_flow_krw"] for f in result["execution"]["fills"])
    assert result["trades"][0]["pnl_krw"] == round(actual_net, 2)


def test_winner_ranking_uses_net_krw_not_percentage():
    result = {"execution": {"fills": [{"ticker": "small", "cash_flow_krw": -100}, {"ticker": "small", "cash_flow_krw": 1000},
                                      {"ticker": "big", "cash_flow_krw": -10000}, {"ticker": "big", "cash_flow_krw": 12000}]},
              "trades": [], "open_positions": {}}
    assert ticker_contributions(result)[0]["ticker"] == "big"


def test_entry_delay_counts_each_instruments_actual_sessions():
    df = frame(dates=("2026-10-19", "2026-10-21", "2026-10-26"))
    dates = [i.date() for i in df.index]
    schedule = delayed_schedule([(dt.date(2026, 10, 18), "A", "s", 1)], dates, {"A": df}, 1)
    assert list(schedule) == [dt.date(2026, 10, 21)]
