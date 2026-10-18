"""Causal/accounting audit oracles; these expected values are never auto-updated."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import backtest_momentum as bt
import backtest.strategies as strategies
from financial_oracle_vectors import BORROW_INTEREST, FX_EXIT, INITIAL_COST


def test_initial_entry_cost_is_not_erased_from_metrics():
    result = bt._compute_result(INITIAL_COST["nav"], [], INITIAL_COST["initial_capital"], "cost")
    assert result["metrics"]["total_return_pct"] == INITIAL_COST["total_return_pct"]
    assert result["yearly"][0]["return_pct"] == INITIAL_COST["initial_year_return_pct"]
    assert result["metrics"]["mdd_pct"] == INITIAL_COST["mdd_pct"]


def test_leverage_interest_reduces_nav_exactly_once(monkeypatch):
    monkeypatch.setattr(strategies, "CASH_YIELD_DAILY", 0.0)
    monkeypatch.setattr(strategies, "BORROW_RATE_DAILY", BORROW_INTEREST["daily_rate"])
    dates = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
    result = strategies.run_core_satellite_leverage(
        pd.Series([1.0, 1.0, 1.0], index=dates),
        pd.Series([100.0, 80.0, 80.0], index=dates),
    )
    assert result["nav_df"].iloc[-1] == pytest.approx(BORROW_INTEREST["ending_nav"])
    assert result["nav_df"].iloc[-2] - result["nav_df"].iloc[-1] == BORROW_INTEREST["interest"]


def test_fx_sale_proceeds_and_return_match_hand_ledger():
    dates = pd.to_datetime(["2024-01-02", "2024-06-03"])
    bt.set_usdkrw(pd.Series([1300.0, FX_EXIT["exit_fx"]], index=dates))
    position = {
        "shares": FX_EXIT["shares"], "entry_price": FX_EXIT["entry_price"],
        "entry_date": dt.date(2024, 1, 2), "cost": FX_EXIT["cost"],
        "last_close": 100.0, "highest": 100.0, "source": "oracle",
        "n_clubs": 1, "display_name": "oracle", "market": "US",
        "target_price": None, "stop": None, "entry_reason": "oracle",
    }
    trade = bt._close_trade(
        "US0001", position, dt.date(2024, 6, 3), FX_EXIT["exit_price"], "oracle_exit",
        ticker_reports=None, record_full_trades=False, consensus_window=None,
    )
    assert trade["return_pct"] == FX_EXIT["return_pct"]
    assert trade["exit"] == FX_EXIT["exit_price"]
    assert trade["pnl_krw"] == FX_EXIT["pnl"]


def test_atr63_is_a_distinct_real_window(tmp_path):
    from conftest import flat_ohlcv, load_via_pipeline
    raw = flat_ohlcv(300, price=100.0, band=1.0)
    # Last 42 observations have true range 20; prior 21 have true range 2.
    raw.loc[raw.index[-42:], "high"] = 110.0
    raw.loc[raw.index[-42:], "low"] = 90.0
    frame = load_via_pipeline(raw, tmp_path, "ATR_ORACLE")
    assert frame["atr63"].iloc[-1] == pytest.approx(14.0)
    assert frame["atr"].iloc[-1] == pytest.approx(20.0)


def test_market_fill_queue_survives_other_market_holiday():
    import backtest.accounting as accounting
    dates = pd.to_datetime(["2024-01-02", "2024-01-04"])
    frame = pd.DataFrame({"open": [100.0, 100.0], "close": [100.0, 100.0]}, index=dates)
    calendar = [dt.date(2024, 1, 2), dt.date(2024, 1, 3), dt.date(2024, 1, 4)]
    pending = accounting.build_pending_entries(
        [(dt.date(2024, 1, 2), "US0001", "oracle", 1)], calendar,
        consensus_only=False, prices={"US0001": frame},
    )
    assert dt.date(2024, 1, 3) not in pending
    assert pending[dt.date(2024, 1, 4)][0][0] == "US0001"


def test_future_school_append_cannot_change_past_consensus():
    old = pd.DataFrame([
        {"ticker_key": "KR_000001", "report_date": "2020-01-02", "school": "smic"},
    ])
    extended = pd.concat([old, pd.DataFrame([
        {"ticker_key": "KR_000001", "report_date": "2025-01-02", "school": "yig"},
    ])], ignore_index=True)
    first = bt.point_in_time_school_counts(old)
    second = bt.point_in_time_school_counts(extended)
    key = ("KR_000001", dt.date(2020, 1, 2))
    assert first[key] == second[key] == 1
    assert second[("KR_000001", dt.date(2025, 1, 2))] == 2


def test_open_momentum_decision_does_not_read_same_day_ma():
    dates = pd.to_datetime(["2024-01-02", "2024-01-03"])
    frame = pd.DataFrame({"open": [100.0, 100.0], "close": [100.0, 100.0],
                          "ma200": [90.0, 500.0]}, index=dates)
    args = ("KR0001", "oracle", 1, dt.date(2024, 1, 3), {"KR0001": frame},
            {}, 100_000_000.0, 100_000_000.0, None)
    position, cash = bt._try_enter(*args, momentum_filter=True)
    assert position is not None
    frame.loc[dates[-1], "ma200"] = 1.0
    same_position, same_cash = bt._try_enter(*args, momentum_filter=True)
    assert same_position["shares"] == position["shares"]
    assert cash == same_cash


def test_tail_profit_share_uses_money_not_sum_of_percent_returns():
    from backtest.metrics import compute_tail_stats
    trades = [
        {"ticker": "SMALL", "return_pct": 100.0, "pnl_krw": 100.0, "days": 1, "exit_reason": "closed"},
        {"ticker": "LARGE", "return_pct": 10.0, "pnl_krw": 1000.0, "days": 1, "exit_reason": "closed"},
    ]
    result = compute_tail_stats(trades)
    assert result["top_decile_pnl_share_pct"] == 9.1
    del trades[0]["pnl_krw"]
    assert compute_tail_stats(trades)["top_decile_pnl_share_pct"] is None


def test_monthly_contribution_cannot_earn_return_before_it_is_deposited(monkeypatch):
    monkeypatch.setattr(bt, "DCA_INITIAL", 100.0)
    monkeypatch.setattr(bt, "DCA_BASE_MONTHLY", 10.0)
    monkeypatch.setattr(bt, "DCA_STEP", 0.0)
    dates = pd.to_datetime(["2024-01-31", "2024-02-01"])
    prices = pd.Series([100.0, 110.0], index=dates)
    result = bt.compute_wealth_simulation_multi(
        prices, {"KOSPI": prices}, dates[0].date(), dates[-1].date(),
    )
    # 100 * 1.10 + 10 close deposit; not (100 + 10) * 1.10 = 121.
    assert result["final_strategy_value"] == 120
    assert result["final_benchmark_values"]["KOSPI"] == 120
    assert result["final_contributed"] == 110


def test_benchmark_future_first_quote_is_not_backfilled_into_past():
    dates = pd.to_datetime(["2024-01-31", "2024-02-01"])
    nav = pd.Series([100.0, 110.0], index=dates)
    future_only = pd.Series([110.0], index=dates[-1:])
    with pytest.raises(ValueError, match="point-in-time"):
        bt.compute_wealth_simulation_multi(nav, {"KOSPI": future_only}, dates[0].date(), dates[-1].date())


def test_minervini_open_fill_is_invariant_to_that_days_close():
    dates = pd.to_datetime(["2023-12-01", "2024-01-02", "2024-01-03"])
    frame = pd.DataFrame({
        "open": [100.0, 150.0, 100.0], "close": [100.0, 150.0, 10.0],
        "ma50": [90.0, 100.0, 500.0], "ma150": [80.0, 90.0, 400.0],
        "ma200": [70.0, 80.0, 300.0], "hi52w": [150.0, 150.0, 500.0],
    }, index=dates)
    reports = [(dt.date(2024, 1, 2), "KR0001", "oracle", 1)]
    calendar = [dt.date(2024, 1, 2), dt.date(2024, 1, 3)]
    first = strategies.run_minervini({"KR0001": frame}, reports, calendar, "oracle")
    altered = frame.copy(deep=True)
    altered.loc[dates[-1], ["close", "ma50", "ma150", "ma200", "hi52w"]] = [600, 100, 90, 85, 600]
    second = strategies.run_minervini({"KR0001": altered}, reports, calendar, "oracle")
    for result in (first, second):
        assert len(result["trades"]) == 1
        assert result["trades"][0]["entry_date"] == "2024-01-03"
        assert result["trades"][0]["entry"] == 100.0


def test_parking_regime_gate_does_not_read_same_day_close():
    dates = pd.to_datetime(["2024-01-02", "2024-01-03"])
    index = pd.Series([100.0, 100.0], index=dates)
    ma200 = pd.Series([90.0, 90.0], index=dates)
    assert strategies._regime_risk_on(index, ma200, dt.date(2024, 1, 3))
    # A crash below the 200MA on the day itself cannot switch that day's return to cash.
    index.loc[dates[-1]] = 10.0
    ma200.loc[dates[-1]] = 500.0
    assert strategies._regime_risk_on(index, ma200, dt.date(2024, 1, 3))
    assert not strategies._regime_risk_on(index, ma200, dt.date(2024, 1, 4))


def test_scaleout_parking_follows_index_on_a_same_day_regime_break():
    # 210 rising weekdays ending Fri 2024-10-25, then a Monday crash far below the 200MA.
    days = pd.bdate_range(end="2024-10-25", periods=210).append(pd.DatetimeIndex(["2024-10-28"]))
    closes = [100.0 + i for i in range(210)] + [50.0]
    kospi = pd.Series(closes, index=days)
    calendar = [d.date() for d in days]
    result = strategies.run_kospi_core_chandelier_scaleout({}, [], calendar, "oracle", kospi, 42, 3.0, 10)
    nav = result["nav_df"]
    # Decided on Friday's close (above its 200MA): Monday's parked balance takes the index
    # return. A same-day gate would see 50 < 200MA and earn the cash yield instead.
    assert nav.iloc[-1] / nav.iloc[-2] == pytest.approx(50.0 / 309.0, rel=1e-9)


def test_regime_gate_reads_friday_close_on_monday():
    days = pd.to_datetime(["2024-01-05", "2024-01-08"])
    index = pd.Series([100.0, 10.0], index=days)
    ma200 = pd.Series([90.0, 500.0], index=days)
    assert strategies._regime_risk_on(index, ma200, dt.date(2024, 1, 8))
