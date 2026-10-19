"""Published static API files must match public/api/v1/openapi.json.

Objects with documented `properties` are treated as closed: a field the writer
emits but the spec does not document is drift and fails here.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pytest

import export_signals_api as api

API = api.API_DIR
SPEC = json.loads((API / "openapi.json").read_text(encoding="utf-8"))
SCHEMAS = SPEC["components"]["schemas"]
DATED = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")
JSON_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool, "null": type(None),
    "integer": int, "number": (int, float),
}


def _errors(value: object, schema: dict, where: str) -> list[str]:
    if "$ref" in schema:
        return _errors(value, SCHEMAS[schema["$ref"].rsplit("/", 1)[-1]], where)
    types = schema.get("type")
    if types is not None:
        types = [types] if isinstance(types, str) else types
        ok = any(isinstance(value, JSON_TYPES[t]) and not (t in ("integer", "number") and isinstance(value, bool))
                 for t in types)
        if not ok:
            return [f"{where}: expected {types}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{where}: {value!r} not in {schema['enum']}"]
    if schema.get("format") == "date" and isinstance(value, str):
        try:
            dt.date.fromisoformat(value)
        except ValueError:
            return [f"{where}: {value!r} is not a date"]
    out: list[str] = []
    if isinstance(value, dict):
        props = schema.get("properties", {})
        out += [f"{where}: missing required {k}" for k in schema.get("required", []) if k not in value]
        extra = schema.get("additionalProperties")
        for key, item in value.items():
            if key in props:
                out += _errors(item, props[key], f"{where}.{key}")
            elif isinstance(extra, dict):
                out += _errors(item, extra, f"{where}.{key}")
            elif props and extra is not True:
                out.append(f"{where}: undocumented field {key}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            out += _errors(item, schema["items"], f"{where}[{i}]")
    return out


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
    assert _errors(payload, {"$ref": f"#/components/schemas/{schema}"}, path.name) == []


def test_every_v1_operation_is_deprecated_toward_v2():
    for route, item in SPEC["paths"].items():
        assert item["get"]["deprecated"] is True, route
        assert "/api/v2" in item["get"]["description"], route


def test_every_concrete_openapi_path_is_published():
    for route in SPEC["paths"]:
        if "{" in route or route == "/signals/latest":  # templated, or served by the route handler
            continue
        assert (API / route.lstrip("/")).is_file(), route


def test_manifest_points_at_published_detail_assets():
    manifest = json.loads((API / "strategy-details" / "manifest.json").read_text(encoding="utf-8"))
    latest = json.loads((API / "signals" / "latest.json").read_text(encoding="utf-8"))
    assert manifest["as_of"] == latest["as_of"]
    for key, asset in manifest["assets"].items():
        served = api.REVISIONS_DIR / key / "revisions" / f"{asset['content_hash']}.json"  # rewrite target
        assert asset["path"] == f"strategy-details/{key}/{asset['content_hash']}.json"
        detail = json.loads(served.read_text(encoding="utf-8"))
        assert detail["strategy_key"] == key
        assert detail["content_hash"] == asset["content_hash"]


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
