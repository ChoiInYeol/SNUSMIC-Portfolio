"""Avoid repeated network/research work without changing financial results."""
import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

import build_report_performance as perf
from backtest import fx, strategies as s


@pytest.mark.parametrize("index", [False, True])
def test_pre_listing_history_is_requested_once_then_only_the_missing_tail(tmp_path, monkeypatch, index):
    monkeypatch.setattr(perf, "PRICE_CACHE_DIR", tmp_path / "prices")
    monkeypatch.setattr(perf.lake, "GOLD_CACHE", tmp_path / "cache")
    monkeypatch.setattr(perf, "closed_bar_end", lambda market, requested: requested)
    calls = []
    def fetch(*args):
        start, end = args[-2:]
        calls.append((start, end))
        dates = pd.bdate_range(max(start, dt.date(2024, 1, 2)), end)
        return pd.DataFrame({"close": 100.0}, index=dates)
    monkeypatch.setattr(perf, "fetch_index" if index else "fetch_prices", fetch)
    def load(start, end):
        return perf.fetch_index_cached("US", start, end) if index else perf.fetch_prices_cached("US", "IPO", start, end, 0)
    start = dt.date(2000, 1, 1)
    first = load(start, dt.date(2024, 1, 3))
    pd.testing.assert_frame_equal(first, load(start, dt.date(2024, 1, 3)), check_freq=False)
    assert len(calls) == 1
    assert load(start, dt.date(2024, 1, 4)).index[-1].date() == dt.date(2024, 1, 4)
    assert calls[-1] == (dt.date(2024, 1, 4), dt.date(2024, 1, 4))
    earlier = dt.date(1999, 1, 1)
    load(earlier, dt.date(2024, 1, 4))
    assert calls[-1][0] == earlier and len(calls) == 3
    load(earlier, dt.date(2024, 1, 4))
    assert len(calls) == 3
    coverage = next((tmp_path / "cache" / "price_coverage").glob("*.json"))
    doc = json.loads(coverage.read_text())
    doc["first_quote"] = "2023-01-02"  # a different/replaced silver history invalidates coverage
    coverage.write_text(json.dumps(doc))
    load(earlier, dt.date(2024, 1, 4))
    assert len(calls) == 4


def test_monthly_cache_reuses_unchanged_months_and_matches_a_cold_run(monkeypatch):
    dates = pd.bdate_range("2023-11-01", "2024-04-02")
    prices = {}
    for i, ticker in enumerate(("A", "B", "C")):
        close = 100 + np.arange(len(dates)) * (0.1 + i * 0.02) + np.sin(np.arange(len(dates)))
        prices[ticker] = pd.DataFrame({"open": close, "close": close}, index=dates)
    reports = [(dt.date(2023, 11, 1), t, "report", 1) for t in prices]
    calls = []
    def weights(frame):
        calls.append(frame.index[-1])
        return {t: 1 / len(frame.columns) for t in frame.columns}
    monkeypatch.setattr(s, "_hrp_weights", weights)
    memo = {}
    early = [d.date() for d in dates if d <= pd.Timestamp("2024-03-29")]
    s.run_portfolio_opt(prices, reports, early, "memo", weights_memo=memo, record_full_trades=True)
    initial_calls = len(calls)
    assert initial_calls >= 3
    full = [d.date() for d in dates]
    warm = s.run_portfolio_opt(prices, reports, full, "memo", weights_memo=memo, record_full_trades=True)
    assert len(calls) == initial_calls + 1  # only the new partial month's matrix
    cold = s.run_portfolio_opt(prices, reports, full, "memo", record_full_trades=True)
    pd.testing.assert_series_equal(warm["nav_df"], cold["nav_df"])
    assert warm["trades"] == cold["trades"] and warm["metrics"] == cold["metrics"]
    prices["A"].loc[pd.Timestamp("2024-01-15"), "close"] *= 1.02
    before = len(calls)
    revised = s.run_portfolio_opt(prices, reports, full, "memo", weights_memo=memo, record_full_trades=True)
    assert len(calls) > before  # historical corrections invalidate affected matrices
    revised_cold = s.run_portfolio_opt(prices, reports, full, "memo", record_full_trades=True)
    pd.testing.assert_series_equal(revised["nav_df"], revised_cold["nav_df"])
    assert revised["trades"] == revised_cold["trades"]


def test_optuna_reuses_training_search_but_recomputes_current_oos(tmp_path, monkeypatch):
    import optuna
    monkeypatch.setattr(s, "OPTUNA_CACHE_PATH", tmp_path / "optuna.json")
    monkeypatch.setattr(s, "OPTUNA_N_TRIALS", 1)
    monkeypatch.setattr(s, "FORCE_RETUNE", False)
    dates = pd.bdate_range("2020-01-02", "2024-02-02")
    prices = {"A": pd.DataFrame({"open": 100.0, "close": 100.0}, index=dates)}
    reports = [(dt.date(2020, 1, 2), "A", "report", 1)]
    fx.set_usdkrw(pd.Series(1300.0, index=dates))
    searches = []
    create = optuna.create_study
    def study(*args, **kwargs):
        searches.append(True)
        return create(*args, **kwargs)
    monkeypatch.setattr(optuna, "create_study", study)
    def simulate(prices, reports, calendar, label, **kwargs):
        nav = pd.Series(100 + np.arange(len(calendar)) + np.sin(np.arange(len(calendar))), index=pd.to_datetime(calendar))
        return {"nav_df": nav, "in_sample": {"sharpe": 1.0}, "out_of_sample": {"sharpe": calendar[-1].day}}
    monkeypatch.setattr(s, "run_chandelier_parametric", simulate)
    first_calendar = [d.date() for d in dates[:-1]]
    first = s.run_optuna_chandelier(prices, reports, first_calendar, dataset_fingerprint="first")
    second = s.run_optuna_chandelier(prices, reports, [d.date() for d in dates], dataset_fingerprint="new-oos")
    assert len(searches) == 1
    assert first["optuna_meta"] == second["optuna_meta"]
    assert second["out_of_sample"]["sharpe"] == dates[-1].day != first["out_of_sample"]["sharpe"]
    prices["A"].loc[pd.Timestamp("2023-06-01"), "close"] = 110
    s.run_optuna_chandelier(prices, reports, [d.date() for d in dates], dataset_fingerprint="corrected-is")
    assert len(searches) == 2


def test_refresh_caches_include_research_work_and_survive_later_gate_failures():
    for name in ("refresh-daily.yml", "refresh-reports.yml"):
        text = (perf.lake.ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert "optuna_cache.json" in text and "s_weights_cache.pkl" in text and "spo_cache.pkl" in text
        assert "price_coverage" in text
        for cache in ("price", "research"):
            block = text.split(f"- name: Save {cache} cache", 1)[1].split("\n      - name:", 1)[0]
            assert "if: always()" in block and "actions/cache/save@v4" in block
