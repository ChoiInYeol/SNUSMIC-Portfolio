"""A lazy-delivery optimization must preserve real research, not hide empty stubs."""
from __future__ import annotations

import copy

import pytest

import export_signals_api as api
from _contract import canonical_sha256


@pytest.fixture
def source():
    return {"generated_at": "old", "multi_strategy": {
        "strategies": [{"key": "TEST", "label": "test"}],
        "equity_by_strategy": {"TEST": [{"date": "2024-01-02", "nav": 1.0}, {"date": "2024-01-03", "nav": 1.1}]},
        "strategy_wealth_sims": {"TEST": {"final_strategy_value": 120, "series": [{"date": "2024-01-03", "strategy_value": 120}]}},
        "trades_by_strategy": {"TEST": [{"ticker": "TEST", "entry": 100, "exit": 110, "return_pct": 10}]},
        "open_positions_by_strategy": {"TEST": [{"ticker": "OTHER", "entry": 50}]},
        "yearly_by_strategy": {"TEST": [{"year": 2024, "return_pct": 10}]},
    }}


def test_curves_trades_positions_and_wealth_are_preserved(source):
    digest, payload = api.build_strategy_details(source)["TEST"]
    assert payload["content_hash"] == digest
    assert payload["strategy_key"] == "TEST"
    assert payload["nav"][1]["nav"] == 1.1
    assert payload["wealth"]["final_strategy_value"] == 120
    assert payload["trades"][0]["entry"] == 100
    assert payload["trades"][0]["exit"] == 110
    assert payload["open_positions"][0]["ticker"] == "OTHER"
    assert payload["yearly"][0]["return_pct"] == 10
    body = {key: value for key, value in payload.items() if key != "content_hash"}
    assert canonical_sha256(body) == digest


def test_hash_reuses_identical_content_but_changes_with_curve(source):
    original = api.build_strategy_details(source)["TEST"][0]
    changed = copy.deepcopy(source)
    changed["generated_at"] = "new"
    assert api.build_strategy_details(changed)["TEST"][0] == original
    changed["multi_strategy"]["equity_by_strategy"]["TEST"][1]["nav"] = 0.9
    assert api.build_strategy_details(changed)["TEST"][0] != original


@pytest.mark.parametrize("field", ["equity_by_strategy", "strategy_wealth_sims", "trades_by_strategy", "open_positions_by_strategy", "yearly_by_strategy"])
def test_missing_source_contract_is_not_an_empty_fallback(source, field):
    del source["multi_strategy"][field]
    with pytest.raises(KeyError):
        api.build_strategy_details(source)


def test_atomic_json_rejection_preserves_previous_valid_artifact(tmp_path):
    output = tmp_path / "valid.json"
    output.write_text('{"valid":true}', encoding="utf-8")
    with pytest.raises(ValueError):
        api._write_json(output, {"invalid": float("nan")})
    assert output.read_text(encoding="utf-8") == '{"valid":true}'
    assert not list(tmp_path.glob(".valid.json.*"))
