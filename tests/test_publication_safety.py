"""Finite JSON, data coverage and read-only diagnostics are publication contracts."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd
import pytest

import build_forward_record as forward
import pipeline_health as health


@pytest.mark.parametrize("open_price", [None, float("nan"), float("inf"), -float("inf"), 0.0, -1.0])
def test_invalid_open_never_becomes_nonfinite_return(monkeypatch, open_price):
    frame = pd.DataFrame({"open": [open_price], "close": [100.0]}, index=pd.to_datetime(["2024-01-03"]))
    monkeypatch.setattr(forward, "_load_price", lambda *args: frame)
    scored = forward._score_entry({"ticker": "TEST", "market": "KR"}, dt.date(2024, 1, 2))
    assert scored is not None
    # Either explicitly unavailable or the documented finite close proxy, never NaN.
    json.dumps(scored, allow_nan=False)
    if scored["entry_price"] is not None:
        assert scored["entry_price"] == 100.0
        assert scored["return_pct"] == 0.0


@pytest.mark.parametrize("bad_close", [None, float("nan"), float("inf"), 0.0, -1.0])
def test_missing_valuation_is_retained_as_unavailable(monkeypatch, bad_close):
    frame = pd.DataFrame({"open": [100.0], "close": [bad_close]}, index=pd.to_datetime(["2024-01-03"]))
    monkeypatch.setattr(forward, "_load_price", lambda *args: frame)
    scored = forward._score_entry({"ticker": "TEST", "market": "KR"}, dt.date(2024, 1, 2))
    assert scored is not None
    assert scored["return_pct"] is None
    json.dumps(scored, allow_nan=False)


def test_missing_price_file_does_not_remove_signal_from_coverage(monkeypatch):
    monkeypatch.setattr(forward, "_load_price", lambda *args: None)
    scored = forward._score_entry({"ticker": "MISSING", "market": "KR"}, dt.date(2024, 1, 2))
    assert scored is not None
    assert scored["ticker"] == "MISSING"
    assert scored["return_pct"] is None


def test_csv_health_count_handles_quoted_multiline_records(tmp_path):
    path = tmp_path / "records.csv"
    path.write_text('name,value\n"line1\nline2",3\nother,4\n', encoding="utf-8")
    assert health._csv_rows(path) == 2


def test_health_dry_run_does_not_write_state(tmp_path, monkeypatch):
    health_path = tmp_path / "health.json"
    original = '{"checked_at":"2024-01-01"}'
    health_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(health, "HEALTH_PATH", health_path)
    monkeypatch.setattr(health, "collect_metrics", lambda: {"checked_at": "2024-01-02"})
    monkeypatch.setattr(health, "diagnose", lambda *args: [])
    assert health.main(["--dry-run"]) == 0
    assert health_path.read_text(encoding="utf-8") == original


def test_health_failure_is_nonzero_without_telegram(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "HEALTH_PATH", tmp_path / "health.json")
    monkeypatch.setattr(health, "collect_metrics", lambda: {"checked_at": "2024-01-02"})
    monkeypatch.setattr(health, "diagnose", lambda *args: ["invalid generated data"])
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert health.main(["--dry-run"]) != 0
    assert not (tmp_path / "health.json").exists()


@pytest.mark.parametrize("text", ['{"value":NaN}', '{"value":Infinity}', '{"value":1e309}', '{"value":-1e309}', '{'],
                         ids=["nan", "infinity", "overflow", "negative-overflow", "truncated"])
def test_strict_publication_json_rejects_nonfinite_and_malformed(tmp_path, text):
    path = tmp_path / "invalid.json"
    path.write_text(text, encoding="utf-8")
    assert health._load_strict(path) is None
    assert not health._strict_json(path)


@pytest.fixture(scope="module")
def published_texts():
    root = Path(__file__).resolve().parents[1]
    return ((root / "src/data/strategy-backtest.json").read_text(encoding="utf-8"),
            (root / "public/api/v1/signals/latest.json").read_text(encoding="utf-8"))


@pytest.fixture
def published_shapes(published_texts, tmp_path, monkeypatch):
    back, latest = (json.loads(text) for text in published_texts)  # fresh copies: each case mutates them
    back_path = tmp_path / "back.json"
    latest_path = tmp_path / "latest.json"
    monkeypatch.setattr(health, "BACKTEST_PATH", back_path)
    monkeypatch.setattr(health, "SIGNALS_LATEST", latest_path)
    return back, latest, back_path, latest_path


SHAPE_CORRUPTIONS = {
    "backtest.multi_strategy=null": lambda back, latest: back.update(multi_strategy=None),
    "backtest.multi_strategy=[]": lambda back, latest: back.update(multi_strategy=[]),
    "latest.slots=str": lambda back, latest: latest.update(slots="invalid"),
    "latest.counts.open_positions=bool": lambda back, latest: latest["counts"].update(open_positions=True),
    "latest.counts.open_positions=999": lambda back, latest: latest["counts"].update(open_positions=999),
    "latest.headline_strategy=unknown": lambda back, latest: latest.update(headline_strategy="MISSING"),
}


@pytest.mark.parametrize("mutate", SHAPE_CORRUPTIONS.values(), ids=SHAPE_CORRUPTIONS.keys())
def test_shape_corruption_is_reported_not_silently_published(published_shapes, mutate):
    back, latest, back_path, latest_path = published_shapes
    mutate(back, latest)
    back_path.write_text(json.dumps(back), encoding="utf-8")
    latest_path.write_text(json.dumps(latest), encoding="utf-8")
    assert health._shape_issues()
