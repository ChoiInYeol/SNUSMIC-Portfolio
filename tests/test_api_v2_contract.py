"""Published /api/v2 files must match public/api/v2/openapi.json and the REST rules in docs/API.md.

Objects with documented `properties` are closed (`additionalProperties: false`): a field the writer
emits but the spec does not document is drift and fails here.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import export_api_v2 as v2
import export_signals_api as v1
from _contract import canonical_sha256, changed_since_head, committed_blobs, openapi

API = v2.API
ROOT = v2.ROOT
OPENAPI = openapi(API / "openapi.json")
SPEC = OPENAPI.spec
_resolve = OPENAPI.resolve
_errors = OPENAPI.errors


# ── spec path ↔ published file ──────────────────────────────────────────────


def _param_regex(param_ref: dict) -> str:
    param = _resolve(param_ref)
    schema = _resolve(param["schema"])
    if "enum" in schema:
        return "(?:" + "|".join(re.escape(v) for v in schema["enum"]) + ")"
    return schema["pattern"].removeprefix("^").removesuffix("$")


def _routes() -> list[tuple[re.Pattern, str, str]]:
    """(uri regex, spec path, 200 schema name) — literal paths before templated ones."""
    routes = []
    for path, item in SPEC["paths"].items():
        if path.endswith(".json"):
            continue
        op = item["get"]
        params = {_resolve(p)["name"]: p for p in op.get("parameters", [])}
        pattern = re.sub(r"\{(\w+)\}", lambda m: f"(?:{_param_regex(params[m.group(1)])})", path)
        schema = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
        routes.append((re.compile(f"^{pattern}$"), path, schema))
    return sorted(routes, key=lambda r: r[1].count("{"))


ROUTES = _routes()


def _uri(path: Path) -> str:
    rel = path.relative_to(API).as_posix().removesuffix(".json")
    return v2.PREFIX if rel == "index" else f"{v2.PREFIX}/{rel}"


def _route(path: Path) -> tuple[str, str]:
    uri = _uri(path)
    for regex, spec_path, schema in ROUTES:
        if regex.match(uri):
            return spec_path, schema
    raise AssertionError(f"{uri} is not described by any path in openapi.json")


PUBLISHED = sorted(p for p in API.rglob("*.json") if p.name != "openapi.json")


def _id(path: Path) -> str:
    return path.relative_to(API).as_posix()


@pytest.mark.parametrize("path", PUBLISHED, ids=_id)
def test_published_document_matches_openapi(path: Path):
    _, schema = _route(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert _errors(payload, {"$ref": f"#/components/schemas/{schema}"}, _id(path)) == []


TRADE_MARKS = "/api/v2/instruments/{instrument_id}/trade-marks"  # optional: exists only once the headline strategy has traded


def test_every_spec_path_has_a_published_file():
    covered = {_route(p)[0] for p in PUBLISHED}
    expected = {path for path in SPEC["paths"] if not path.endswith(".json")}
    if not list(API.glob("instruments/*/trade-marks.json")):
        expected.discard(TRADE_MARKS)
    assert expected - covered == set()
    assert (API / "openapi.json").is_file()


def _hrefs(value: object):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "links" and isinstance(item, dict):
                yield from item.values()
            else:
                yield from _hrefs(item)
    elif isinstance(value, list):
        for item in value:
            yield from _hrefs(item)


@pytest.fixture(scope="module")
def published_links() -> dict[Path, tuple[str, list[str]]]:
    """(links.self, every href) per non-revision document, parsed once for the link tests below."""
    out = {}
    for path in PUBLISHED:
        if "revisions" in path.parts:
            continue  # revisions carry no links: they are addressed by content hash
        doc = json.loads(path.read_text(encoding="utf-8"))
        # prices.json links are fixed by construction: self + parent instrument.
        hrefs = list(doc["links"].values()) if path.name == "prices.json" else list(_hrefs(doc))
        out[path] = (doc["links"]["self"], hrefs)
    return out


def test_every_link_resolves_to_a_published_resource(published_links):
    files = {_uri(p) for p in PUBLISHED} | {f"{v2.PREFIX}/openapi.json"}
    broken = [f"{_id(path)} -> {target}"
              for path, (_, hrefs) in published_links.items() for target in hrefs if target not in files]
    assert broken == []


def test_self_links_match_their_uri(published_links):
    wrong = [f"{_id(path)}: {self_link}" for path, (self_link, _) in published_links.items() if self_link != _uri(path)]
    assert wrong == []


def test_operations_are_documented_and_extension_free():
    op_ids = []
    for path, item in SPEC["paths"].items():
        assert set(item) == {"get"}, f"{path}: the API is read-only"
        op = item["get"]
        op_ids.append(op["operationId"])
        assert op["tags"] and op["summary"] and op["description"], path
        assert {"200", "304"} <= set(op["responses"]), path
    assert len(op_ids) == len(set(op_ids))
    assert not re.search(r'"x-[^"]*"\s*:', json.dumps(SPEC)), "no x- extensions"


@pytest.mark.parametrize("path", sorted(p for p in SPEC["paths"] if not p.endswith(".json")))
def test_spec_examples_are_valid(path: str):
    content = SPEC["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]
    if "example" not in content:  # only the optional trade-marks resource may lack one, and only when unpublished
        assert path == TRADE_MARKS and not list(API.glob("instruments/*/trade-marks.json"))
        return
    assert _errors(content["example"], content["schema"], path) == []


# ── immutability ────────────────────────────────────────────────────────────


def test_dated_snapshots_mirror_the_append_only_v1_history():
    history = v2.v1_dated_snapshots()
    dated = {p.stem: p for p in API.glob("signals/*.json") if v2.DATE_RE.match(p.stem)}
    assert set(history) <= set(dated)
    for date, original in history.items():
        doc = json.loads(dated[date].read_text(encoding="utf-8"))
        assert doc["meta"]["generated_at"] == original["generated_at"], date
        data = doc["data"]
        for key in ("open_positions", "buy_signals", "sell_signals"):
            data = {**data, key: [{k: v for k, v in item.items() if k != "links"} for item in data[key]]}
        assert data == {k: original[k] for k in v2.SNAPSHOT_FIELDS}, date


def test_append_only_files_never_change_once_committed():
    frozen = [p for p in API.glob("signals/*.json") if v2.DATE_RE.match(p.stem)]
    frozen += list(API.glob("strategies/*/revisions/*.json"))
    assert committed_blobs(frozen, ROOT), "no committed frozen files found: the check would be vacuous"
    assert [_id(p) for p in changed_since_head(frozen, ROOT)] == []


@pytest.mark.parametrize("path", sorted(API.glob("strategies/*/revisions/*.json")), ids=_id)
def test_revision_is_content_addressed(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    body = {k: v for k, v in payload.items() if k != "content_hash"}
    assert canonical_sha256(body) == payload["content_hash"] == path.stem
    assert payload["strategy_key"] == path.parents[1].name


def test_v1_strategy_detail_urls_are_served_from_v2_revisions():
    # Detail identity (strategy_key, content_hash) is carried by test_revision_is_content_addressed.
    manifest = json.loads((v1.API_DIR / "strategy-details" / "manifest.json").read_text(encoding="utf-8"))
    latest = json.loads((v1.API_DIR / "signals" / "latest.json").read_text(encoding="utf-8"))
    assert manifest["as_of"] == latest["as_of"]
    for key, asset in manifest["assets"].items():
        assert asset["path"] == f"strategy-details/{key}/{asset['content_hash']}.json"
        target = API / "strategies" / key / "revisions" / f"{asset['content_hash']}.json"
        assert target.is_file(), f"next.config.ts rewrite target missing for {key}"
        strategy = json.loads((API / "strategies" / f"{key}.json").read_text(encoding="utf-8"))
        assert strategy["links"]["current_revision"].endswith(asset["content_hash"])
    assert not list((v1.API_DIR / "strategy-details").glob("*/*.json")), "v1 copies would duplicate v2 revisions"


# ── release gate ────────────────────────────────────────────────────────────


def test_committed_v2_artifacts_match_a_fresh_export():
    outputs, append_only = v2.plan_outputs()
    assert v2.check_outputs(outputs, append_only) == []


def test_check_reports_drift_and_orphans(tmp_path, monkeypatch):
    monkeypatch.setattr(v2, "ROOT", tmp_path)
    monkeypatch.setattr(v2, "API", tmp_path)
    (tmp_path / "clubs").mkdir()
    fresh, stale, orphan = tmp_path / "index.json", tmp_path / "clubs.json", tmp_path / "clubs" / "gone.json"
    snapshot = tmp_path / "signals" / "2026-10-13.json"
    snapshot.parent.mkdir()
    for path in (fresh, stale, orphan, snapshot):
        path.write_text('{"data": 1, "meta": {"generated_at": "old"}}', encoding="utf-8")
    expected = {"data": 1, "meta": {"generated_at": "new"}}
    problems = v2.check_outputs({fresh: expected, stale: {"data": 2}, snapshot: {"data": 3}}, {snapshot})
    assert problems == ["stale clubs.json", "orphan clubs/gone.json"]


def test_non_finite_input_fails_loudly(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"value": NaN}', encoding="utf-8")
    with pytest.raises(ValueError, match="finite"):
        v2._load_json(bad)


# ── frozen links, retired strategies, prune guard, optional examples ──────


def _snapshot_linking(api: Path, iid: str) -> None:
    item = {"ticker": iid.split("-", 1)[1].upper(), "market": iid.split("-", 1)[0].upper(), "name": "Gone Co",
            "links": {"instrument": f"/api/v2/instruments/{iid}"}}
    data = {"open_positions": [item], "buy_signals": [], "sell_signals": []}
    (api / "signals").mkdir(parents=True, exist_ok=True)
    (api / "signals" / "2026-01-02.json").write_text(json.dumps({"data": data}), encoding="utf-8")


def _prices(api: Path, iid: str) -> Path:
    path = api / "instruments" / iid / "prices.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    return path


def test_instrument_linked_from_a_frozen_snapshot_is_never_pruned(tmp_path, monkeypatch):
    import export_stock_charts as charts

    api, silver = tmp_path / "api", tmp_path / "silver"
    silver.mkdir()
    (silver / "KR_000001.csv").write_text("date,close\n", encoding="utf-8")
    monkeypatch.setattr(v2, "API", api)
    _snapshot_linking(api, "kr-999999")
    referenced = _prices(api, "kr-999999")  # its reports and price CSV are gone, so it is not fresh
    fresh = {_prices(api, f"kr-{i:06d}") for i in range(1, 40)}
    assert v2.frozen_instrument_refs() == {"kr-999999": {"ticker": "999999", "market": "KR", "name": "Gone Co"}}

    pruned, retained = charts.prune_price_series(api / "instruments", fresh, set(v2.frozen_instrument_refs()), silver)
    assert (pruned, retained) == ([], [referenced]) and referenced.exists()
    assert "kr-999999" in v2.published_instruments()

    reports = {"as_of": "2026-01-02", "records": []}
    docs = v2.build_instruments(reports, {}, {"kr-999999"}, "2026-01-02T00:00:00+00:00", v2.frozen_instrument_refs())
    detail = docs["/api/v2/instruments/kr-999999"]
    assert detail["data"]["report_count"] == 0 and detail["data"]["name"] == "Gone Co"
    assert _errors(detail, {"$ref": "#/components/schemas/InstrumentDocument"}, "detail") == []
    collection = docs["/api/v2/instruments"]
    assert _errors(collection, {"$ref": "#/components/schemas/InstrumentCollectionDocument"}, "collection") == []


def test_losing_a_frozen_instrument_fails_the_export(tmp_path, monkeypatch):
    monkeypatch.setattr(v2, "API", tmp_path)
    _snapshot_linking(tmp_path, "kr-999999")
    _prices(tmp_path, "kr-000001")
    with pytest.raises(ValueError, match="frozen snapshots lost their price series"):
        v2.published_instruments()


def test_prune_refuses_a_cold_cache_or_a_mass_deletion(tmp_path):
    import export_stock_charts as charts

    out, silver = tmp_path / "instruments", tmp_path / "silver"
    silver.mkdir()
    published = [_prices(tmp_path, f"kr-{i:06d}") for i in range(100)]
    with pytest.raises(charts.PruneRefused, match="no price files"):
        charts.prune_price_series(out, set(), set(), silver)
    (silver / "KR_000000.csv").write_text("date,close\n", encoding="utf-8")
    with pytest.raises(charts.PruneRefused, match="refusing to prune 6 of 100"):
        charts.prune_price_series(out, set(published[6:]), set(), silver)
    assert all(p.exists() for p in published)
    pruned, _ = charts.prune_price_series(out, set(published[5:]), set(), silver)
    assert pruned == published[:5] and not any(p.exists() for p in pruned)


def test_trade_marks_are_optional_when_the_headline_never_traded(monkeypatch):
    monkeypatch.setattr(v1, "build_strategy_marks", lambda data: {})
    outputs, _ = v2.plan_outputs()
    assert not [p for p in outputs if p.name == "trade-marks.json"]
    spec = outputs[API / "openapi.json"]
    content = spec["paths"][TRADE_MARKS]["get"]["responses"]["200"]["content"]["application/json"]
    assert "example" not in content
    items = outputs[API / "instruments.json"]["data"]
    assert items and not any("trade_marks" in item["links"] for item in items)


def test_revisions_of_retired_strategies_stay_valid_without_a_parent():
    # Schema validity of every revision, retired or not, is test_published_document_matches_openapi.
    current = {item["id"] for item in json.loads((API / "strategies.json").read_text(encoding="utf-8"))["data"]}
    for path in API.glob("strategies/*/revisions/*.json"):
        assert path.parents[1].name in current or not (API / "strategies" / f"{path.parents[1].name}.json").exists()
    # The export never prunes a revision, whatever the strategy collection says.
    retired = API / "strategies" / "RETIRED_x" / "revisions" / f"{'0' * 64}.json"
    assert v2._is_foreign_or_frozen(retired)
