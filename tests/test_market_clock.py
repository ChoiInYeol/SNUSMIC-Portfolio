import datetime as dt

import pandas as pd
import pytest

from market_clock import closed_bar_end


@pytest.mark.parametrize("market,instant,expected", [
    ("US", "2026-10-19T19:59:00+00:00", "2026-10-16"),
    ("US", "2026-10-19T20:15:00+00:00", "2026-10-19"),
    ("US", "2026-11-16T21:14:00+00:00", "2026-11-13"),
    ("US", "2026-11-16T21:15:00+00:00", "2026-11-16"),
    ("KR", "2026-10-19T06:44:00+00:00", "2026-10-16"),
    ("KR", "2026-10-19T06:45:00+00:00", "2026-10-19"),
    ("FX", "2026-10-19T23:59:00+00:00", "2026-10-16"),
])
def test_daily_bar_boundary_observes_market_timezone_and_dst(market, instant, expected):
    assert closed_bar_end(market, now=dt.datetime.fromisoformat(instant)).isoformat() == expected


def test_historical_as_of_is_preserved_and_future_dates_are_capped():
    instant = dt.datetime(2026, 10, 19, 12, tzinfo=dt.timezone.utc)
    assert closed_bar_end("US", dt.date(2024, 1, 2), now=instant) == dt.date(2024, 1, 2)
    assert closed_bar_end("US", dt.date(2027, 1, 1), now=instant) == dt.date(2026, 10, 16)
    with pytest.raises(ValueError, match="timezone-aware"):
        closed_bar_end("US", now=instant.replace(tzinfo=None))


def test_cached_benchmark_is_refreshed_before_it_is_used(tmp_path, monkeypatch):
    from backtest import warehouse
    import yfinance

    pd.DataFrame({"close": [100.]}, index=pd.to_datetime(["2026-06-09"])).to_csv(tmp_path / "IDX_GLD.csv")
    monkeypatch.setattr(warehouse, "PRICE_DIR", tmp_path)
    monkeypatch.setattr(warehouse, "closed_bar_end", lambda market: dt.date(2026, 10, 18))
    calls = []

    def download(ticker, **kwargs):
        calls.append(kwargs)
        return pd.DataFrame({"Close": [120.]}, index=pd.to_datetime(["2026-10-16"]))

    monkeypatch.setattr(yfinance, "download", download)
    monkeypatch.setattr(warehouse, "record_raw_prices", lambda *args, **kwargs: calls.append("bronze"))

    def silver(symbol, frame, **kwargs):
        assert calls[-1] == "bronze", "raw response must be recorded before silver is written"
        return frame

    monkeypatch.setattr(warehouse, "write_silver_prices", silver)
    result = warehouse.load_gld()
    assert result.index[-1].date() == dt.date(2026, 10, 16) and result.iloc[-1] == 120
    assert calls[0]["start"] == "2026-06-10" and calls[0]["end"] == "2026-10-19"


def test_empty_vendor_response_cannot_publish_a_four_month_stale_benchmark(tmp_path, monkeypatch):
    from backtest import warehouse
    import yfinance

    pd.DataFrame({"close": [100.]}, index=pd.to_datetime(["2026-06-09"])).to_csv(tmp_path / "IDX_GLD.csv")
    monkeypatch.setattr(warehouse, "PRICE_DIR", tmp_path)
    monkeypatch.setattr(warehouse, "closed_bar_end", lambda market: dt.date(2026, 10, 18))
    monkeypatch.setattr(yfinance, "download", lambda *args, **kwargs: pd.DataFrame())
    monkeypatch.setattr(warehouse, "record_raw_prices", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match="benchmark GLD is stale"):
        warehouse.load_gld()


def test_us_producer_bounds_the_vendor_request_before_recording_raw(monkeypatch):
    import build_report_performance as pipeline

    monkeypatch.setattr(pipeline, "closed_bar_end", lambda market, requested: dt.date(2026, 10, 16))
    calls = []

    def download(ticker, **kwargs):
        calls.append(kwargs)
        return pd.DataFrame({"Close": [123.]}, index=pd.to_datetime(["2026-10-16"]))

    monkeypatch.setattr(pipeline.yf, "download", download)
    monkeypatch.setattr(pipeline, "record_raw_prices", lambda *args, **kwargs: calls.append(kwargs["request"]))
    result = pipeline.fetch_us_prices("MSFT", dt.date(2026, 10, 1), dt.date(2026, 10, 19))
    assert calls[0]["end"] == "2026-10-17"  # yfinance end is exclusive
    assert calls[1]["end"] == "2026-10-16"  # Bronze records the actual inclusive request
    assert result["close"].iloc[-1] == 123
