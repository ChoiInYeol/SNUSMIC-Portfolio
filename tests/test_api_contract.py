"""Published static API files must match public/api/v1/openapi.json.

Objects with documented `properties` are treated as closed: a field the writer
emits but the spec does not document is drift and fails here.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import export_signals_api as api
from _contract import openapi

API = api.API_DIR
OPENAPI = openapi(API / "openapi.json")
SPEC = OPENAPI.spec
DATED = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")


def _published() -> list[tuple[Path, str]]:
    files = [(API / "signals" / "latest.json", "SignalsPayload"),
             (API / "strategies.json", "StrategiesPayload"),
             (API / "forward.json", "ForwardRecord"),
             (API / "strategy-details" / "manifest.json", "StrategyDetailsManifest")]
    files += [(p, "SignalsPayload") for p in sorted((API / "signals").glob("*.json")) if DATED.match(p.name)]
    files += [(p, "TradesPayload") for p in sorted((API / "trades").glob("*.json"))]
    # v1 strategy-detail URLs are a next.config.ts rewrite to the shared v2 revision files.
    files += [(p, "StrategyDetail") for p in sorted(api.REVISIONS_DIR.glob("*/revisions/*.json"))]
    return files


@pytest.mark.parametrize("path,schema", _published(), ids=lambda v: v.name if isinstance(v, Path) else v)
def test_published_payload_matches_openapi(path: Path, schema: str):
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert OPENAPI.schema_errors(payload, schema, path.name) == []


def test_every_v1_operation_is_deprecated_toward_v2():
    for route, item in SPEC["paths"].items():
        assert item["get"]["deprecated"] is True, route
        assert "/api/v2" in item["get"]["description"], route


def test_every_concrete_openapi_path_is_published():
    for route in SPEC["paths"]:
        if "{" in route or route == "/signals/latest":  # templated, or served by the route handler
            continue
        assert (API / route.lstrip("/")).is_file(), route


def test_committed_api_artifacts_match_a_fresh_export():
    data = api._load_source(api.DEFAULT_SOURCE)
    outputs, append_only = api.plan_outputs(data)
    assert api.check_outputs(outputs, append_only) == []


def test_check_freezes_dated_snapshot_on_same_day_rerun(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "ROOT", tmp_path)
    snapshot, latest = tmp_path / "2026-10-13.json", tmp_path / "latest.json"
    snapshot.write_text('{"buy_signals": []}', encoding="utf-8")
    latest.write_text('{"buy_signals": []}', encoding="utf-8")
    rerun = {"buy_signals": [{"ticker": "NEW"}]}
    problems = api.check_outputs({snapshot: rerun, latest: rerun}, {snapshot})
    assert problems == ["stale latest.json"]
