"""Export the public REST API v2 (static JSON) to public/api/v2/ from gold inputs.

Resource model and design: docs/adr/0001-api-v2-and-medallion.md, docs/API.md.
Every document is `{schema_version, links, meta, data}` except the content-addressed strategy
revisions, whose bytes are shared with /api/v1/strategy-details (see build_strategy_details).

Inputs (gold, read-only):
  src/data/strategy-backtest.json                 strategies, trades, revisions, trade marks, latest signals
  src/data/report-performance.json                clubs, instruments, reports
  src/data/forward-record.json                    track record
  public/api/v1/signals/{YYYY-MM-DD}.json         append-only signal history (frozen at first publication)
  public/api/v2/instruments/{id}/prices.json      written by export_stock_charts.py; defines the instrument set

Outputs: public/api/v2/<uri>.json (`/api/v2` -> index.json), public/api/v2/openapi.json, and
src/data/api-v2-resources.json (every published URI; src/proxy.ts answers anything else with 404).
Dated snapshots and revisions are append-only; everything else is write-then-prune.

Usage:
    python scripts/export_api_v2.py
    python scripts/export_api_v2.py --check     # write nothing; exit 1 on drift (CI gate)
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import re
import sys
from pathlib import Path

import export_signals_api as v1
import lake
import sota_portfolio

ROOT = lake.ROOT
API = lake.API_V2
PREFIX = "/api/v2"
SITE = "https://verdict-archive.vercel.app"
SCHEMA_VERSION = "2.0"
API_VERSION = "2.1.0"

STRATEGY_SOURCE = lake.WEB_DATA / "strategy-backtest.json"
REPORTS_SOURCE = lake.WEB_DATA / "report-performance.json"
FORWARD_SOURCE = lake.WEB_DATA / "forward-record.json"
RESOURCE_INDEX = lake.WEB_DATA / "api-v2-resources.json"
V1_SIGNALS = lake.API_V1 / "signals"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
INSTRUMENT_ID_RE = re.compile(r"^(kr|us)-[a-z0-9.-]+$")
STRATEGY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_+.-]*$")

DISCLAIMER_EN = (
    "Research data derived from student investment-club reports and backtested simulations. "
    "It does NOT constitute investment advice. Past performance does not guarantee future results. "
    "All trading decisions and their consequences are solely your responsibility."
)
DISCLAIMER_KO = (
    "학생 투자동아리 리포트와 백테스트 시뮬레이션에서 파생된 연구용 데이터입니다. "
    "투자 권유가 아닙니다. 과거 성과가 미래 수익을 보장하지 않습니다. "
    "실매매로 인한 모든 손익의 책임은 전적으로 사용자에게 있습니다."
)

# Club ids are the school codes used across the pipeline (src/lib/report-model.ts SchoolSchema).
CLUBS: dict[str, dict[str, str]] = {
    "smic": {"name": "서울대 SMIC", "full_name": "서울대학교 투자연구회 SMIC"},
    "yig": {"name": "연세대 YIG", "full_name": "연세대학교 투자동아리 Yonsei Investment Group"},
    "star": {"name": "성균관대 STAR", "full_name": "성균관대학교 금융투자학회 S.T.A.R"},
    "kuvic": {"name": "고려대 KUVIC", "full_name": "고려대학교 가치투자연구회 KUVIC"},
    "ewha": {"name": "이화여대 EIA", "full_name": "이화여자대학교 가치투자학회 EIA"},
    "voera": {"name": "홍익대 Voera", "full_name": "홍익대학교 중앙 금융동아리 Voera"},
}

# Public subset of report-performance records. Left out as internal: source_file (repo path),
# *_raw (parser input text), filename_date, parse_issue, qa_flags, target_seq*, direction_latest
# (derivable), max_high_until_latest (price level behind max_high_return_pct), return_ytd_pct
# (calendar-year return unrelated to the report date).
REPORT_FIELDS = (
    "school", "report_type", "era", "report_date", "authored_date",
    "market", "ticker", "exchange", "company", "display_name",
    "rating", "rating_class", "target_price", "report_current_price", "stated_upside_pct",
    "first_trade_date", "start_close", "latest_trade_date", "latest_close",
    "return_latest_pct", "return_30d_pct", "return_90d_pct", "return_180d_pct", "return_365d_pct",
    "benchmark_return_pct", "alpha_latest_pct", "max_high_return_pct",
    "target_hit_until_latest", "first_target_hit_date", "days_to_target",
    "peak_return_24m_pct", "peak_date_24m", "age_days", "maturity",
    "performance_bucket", "bucket_peak", "data_issue",
    "source_name", "source_md_url", "source_pdf_url",
)
SCORECARD_FIELDS = ("reports", "priced_reports", "with_target", "up_latest", "down_latest", "target_hit",
                    "avg_return_latest_pct", "median_return_latest_pct")
FORWARD_SUMMARY_FIELDS = ("n_signals", "n_tracking", "n_unavailable", "n_pending", "coverage_pct",
                          "avg_return_pct", "win_rate_pct", "best_pct", "worst_pct",
                          "first_snapshot", "last_snapshot", "n_snapshots")
FORWARD_ENTRY_FIELDS = ("signal_date", "ticker", "market", "name", "basis_price", "entry_date", "entry_price",
                        "current_price", "return_pct", "peak_pct", "days", "status", "trigger_schools",
                        "headline_strategy")
SNAPSHOT_FIELDS = ("as_of", "headline_strategy", "regime", "slots", "counts",
                   "open_positions", "buy_signals", "sell_signals")
PERIOD_FIELDS = ("start", "end", "total_return_pct", "cagr_pct", "sharpe", "mdd_pct")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ── helpers ─────────────────────────────────────────────────────────────────


def href(*parts: str) -> str:
    return "/".join((PREFIX, *parts))


def file_for(uri: str) -> Path:
    """Physical file behind an extensionless v2 URI (next.config.ts rewrites URI -> file)."""
    rel = uri.removeprefix(PREFIX).lstrip("/")
    return API / (f"{rel}.json" if rel else "index.json")


def instrument_id(market: str | None, ticker: str) -> str:
    """Same slug rule as the web (`/stocks/kr-005930`) and the price files."""
    return f"{market or 'KR'}-{ticker}".lower()


def _load_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not v1._finite_tree(data):
        raise ValueError(f"{path.as_posix()}: expected a JSON object with finite numbers only")
    return data


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def document(self_href: str, data: object, *, generated_at: str, links: dict | None = None,
             as_of: str | None = None, count: int | None = None) -> dict:
    meta: dict = {"generated_at": generated_at}
    if as_of is not None:
        meta["as_of"] = as_of
    if count is not None:
        meta["count"] = count
    meta["disclaimer"] = DISCLAIMER_EN
    meta["disclaimer_ko"] = DISCLAIMER_KO
    return {"schema_version": SCHEMA_VERSION, "links": {"self": self_href, **(links or {})}, "meta": meta, "data": data}


def _instrument_links(item: dict, instruments: set[str]) -> dict:
    """Copy of a payload item with links.instrument when that instrument resource exists."""
    out = dict(item)
    ticker = item.get("ticker")
    if ticker:
        iid = instrument_id(item.get("market"), ticker)
        if iid in instruments:
            out["links"] = {"instrument": href("instruments", iid)}
    return out


def _require(mapping: dict, fields: tuple[str, ...], where: str) -> None:
    missing = [f for f in fields if f not in mapping]
    if missing:
        raise ValueError(f"{where}: missing {', '.join(missing)}")


# ── signals ─────────────────────────────────────────────────────────────────


def snapshot_data(v1_payload: dict, instruments: set[str], where: str) -> dict:
    _require(v1_payload, SNAPSHOT_FIELDS, where)
    data = {k: copy.deepcopy(v1_payload[k]) for k in SNAPSHOT_FIELDS}
    for key in ("open_positions", "buy_signals", "sell_signals"):
        data[key] = [_instrument_links(item, instruments) for item in data[key]]
    return data


def v1_dated_snapshots() -> dict[str, dict]:
    """The append-only signal history as first published under /api/v1."""
    out: dict[str, dict] = {}
    for path in sorted(V1_SIGNALS.glob("*.json")):
        if DATE_RE.match(path.stem):
            out[path.stem] = _load_json(path)
    return out


def build_signals(latest_v1: dict, history: dict[str, dict], instruments: set[str], now: str
                  ) -> tuple[dict[str, dict], set[str]]:
    """Signals collection, latest and dated snapshots. Returns ({uri: doc}, append-only uris)."""
    as_of = latest_v1["as_of"]
    snapshots = dict(history)
    snapshots.setdefault(as_of, latest_v1)  # first publication of today's snapshot
    docs: dict[str, dict] = {}
    append_only: set[str] = set()
    for date, payload in snapshots.items():
        if payload.get("as_of") != date:
            raise ValueError(f"signals/{date}: as_of {payload.get('as_of')!r} does not match its date")
        uri = href("signals", date)
        docs[uri] = document(uri, snapshot_data(payload, instruments, f"signals/{date}"),
                             generated_at=payload["generated_at"], as_of=date,
                             links={"collection": href("signals")})
        append_only.add(uri)
    docs[href("signals", "latest")] = document(
        href("signals", "latest"), snapshot_data(latest_v1, instruments, "signals/latest"),
        generated_at=now, as_of=as_of,
        links={"collection": href("signals"), "snapshot": href("signals", as_of)})
    index = [{"date": date, "headline_strategy": snapshots[date]["headline_strategy"],
              "counts": copy.deepcopy(snapshots[date]["counts"]), "links": {"self": href("signals", date)}}
             for date in sorted(snapshots, reverse=True)]
    docs[href("signals")] = document(href("signals"), index, generated_at=now, as_of=as_of, count=len(index),
                                     links={"latest": href("signals", "latest")})
    return docs, append_only


# ── strategies ──────────────────────────────────────────────────────────────


def _period(block: dict, where: str) -> dict:
    _require(block, PERIOD_FIELDS, where)
    return {k: block[k] for k in PERIOD_FIELDS}


def strategy_item(s: dict, headline: str, curated: set[str]) -> dict:
    key = s["key"]
    if not isinstance(key, str) or not STRATEGY_ID_RE.match(key):
        raise ValueError(f"invalid strategy id {key!r}")
    m = s["metrics"]
    _require(m, ("win_rate_pct", "avg_hold_days"), f"strategies/{key}.metrics")
    return {
        "id": key,
        "label": s["label"],
        "is_headline": key == headline,
        "is_curated": key in curated,
        "verdict": s.get("verdict"),
        "verdict_reason": s.get("verdict_reason"),
        "trade_count": s["trade_count"],
        "metrics": {**_period(m, f"strategies/{key}.metrics"),
                    "win_rate_pct": m["win_rate_pct"], "avg_hold_days": m["avg_hold_days"]},
        "in_sample": _period(s["in_sample"], f"strategies/{key}.in_sample"),
        "out_of_sample": _period(s["out_of_sample"], f"strategies/{key}.out_of_sample"),
        "best_trade": {"ticker": s.get("best_trade_ticker"), "name": s.get("best_trade_name"),
                       "return_pct": s.get("max_single_return_pct")},
        "top_decile_pnl_share_pct": s.get("top_decile_pnl_share_pct"),
        "benchmarks": {k: s.get(k) for k in ("kospi_dca_ratio", "kospi_dca_beats", "aw_dca_ratio", "aw_dca_beats")},
        "dsr": copy.deepcopy(s.get("dsr")),
        "walkforward": copy.deepcopy(s.get("walkforward")),
        "walkforward_oos": copy.deepcopy(s.get("walkforward_oos")),
    }


def build_strategies(data: dict, details: dict[str, tuple[str, dict]], instruments: set[str], now: str
                     ) -> dict[str, dict]:
    ms = data["multi_strategy"]
    headline = ms.get("headline_key", data["params"].get("headline_key", ""))
    curated = set(ms.get("curated_keys", []))
    as_of = data["signals"]["as_of"]
    trades = v1.build_trades(data)
    docs: dict[str, dict] = {}
    items = []
    for s in ms["strategies"]:
        item = strategy_item(s, headline, curated)
        sid = item["id"]
        self_href = href("strategies", sid)
        items.append({**item, "links": {"self": self_href}})
        docs[self_href] = document(self_href, item, generated_at=now, as_of=as_of, links={
            "collection": href("strategies"),
            "trades": href("strategies", sid, "trades"),
            "current_revision": href("strategies", sid, "revisions", details[sid][0]),
        })
        rows = [_instrument_links(t, instruments) for t in trades.get(sid, [])]
        docs[href("strategies", sid, "trades")] = document(
            href("strategies", sid, "trades"), rows, generated_at=now, as_of=as_of, count=len(rows),
            links={"strategy": self_href})
    docs[href("strategies")] = document(href("strategies"), items, generated_at=now, as_of=as_of, count=len(items))
    return docs


# ── reports, clubs, instruments ─────────────────────────────────────────────


def report_item(record: dict) -> dict:
    _require(record, REPORT_FIELDS, f"report {record.get('source_name')!r}")
    return {k: record[k] for k in REPORT_FIELDS}


def _report_order(records: list[dict]) -> list[dict]:
    # Newest first; undated reports last.
    return sorted(records, key=lambda r: (r["report_date"] or "", r["school"], r["ticker"] or "", r["source_name"]),
                  reverse=True)


def _dates(records: list[dict]) -> tuple[str | None, str | None]:
    dates = sorted(r["report_date"] for r in records if r["report_date"])
    return (dates[0], dates[-1]) if dates else (None, None)


def build_clubs(reports: dict, instruments: set[str], now: str) -> dict[str, dict]:
    as_of = reports["as_of"]
    records = reports["records"]
    unknown = sorted({r["school"] for r in records} - set(CLUBS))
    if unknown:
        raise ValueError(f"report-performance: unknown club id(s) {unknown} — add them to CLUBS")
    scorecards = {row["group"].removeprefix("school="): row for row in reports["summary"]
                  if str(row.get("group", "")).startswith("school=")}
    docs: dict[str, dict] = {}
    items = []
    for cid, names in CLUBS.items():
        own = _report_order([r for r in records if r["school"] == cid])
        first, latest = _dates(own)
        card = scorecards.get(cid)
        item = {
            "id": cid, **names,
            "report_count": len(own),
            "first_report_date": first,
            "latest_report_date": latest,
            "scorecard": {k: card[k] for k in SCORECARD_FIELDS} if card else None,
        }
        self_href = href("clubs", cid)
        items.append({**item, "links": {"self": self_href, "reports": href("clubs", cid, "reports")}})
        docs[self_href] = document(self_href, item, generated_at=now, as_of=as_of,
                                   links={"collection": href("clubs"), "reports": href("clubs", cid, "reports")})
        rows = [_instrument_links(report_item(r), instruments) for r in own]
        docs[href("clubs", cid, "reports")] = document(href("clubs", cid, "reports"), rows, generated_at=now,
                                                       as_of=as_of, count=len(rows), links={"club": self_href})
    docs[href("clubs")] = document(href("clubs"), items, generated_at=now, as_of=as_of, count=len(items))
    return docs


def frozen_instrument_refs() -> dict[str, dict]:
    """Instruments linked from published dated snapshots: {id: {ticker, market, name}}.

    Dated snapshots are append-only, so these links can never be rewritten: the instruments they
    point at must stay published forever (export_stock_charts.py never prunes their price series).
    """
    refs: dict[str, dict] = {}
    for path in sorted((API / "signals").glob("*.json")):
        if not DATE_RE.match(path.stem):
            continue
        data = json.loads(path.read_text(encoding="utf-8"))["data"]
        for key in ("open_positions", "buy_signals", "sell_signals"):
            for item in data[key]:
                link = item.get("links", {}).get("instrument")
                if link:
                    refs.setdefault(link.rsplit("/", 1)[-1], {"ticker": item["ticker"], "market": item["market"],
                                                              "name": item["name"]})
    return refs


def published_instruments() -> set[str]:
    """An instrument exists iff export_stock_charts.py published its price series.

    Every instrument a frozen snapshot links to must be among them; losing one would leave a
    permanently broken link, so that is a hard error rather than a silent prune.
    """
    ids = {p.parent.name for p in (API / "instruments").glob("*/prices.json")}
    bad = sorted(i for i in ids if not INSTRUMENT_ID_RE.match(i))
    if bad:
        raise ValueError(f"invalid instrument id(s): {bad}")
    lost = sorted(set(frozen_instrument_refs()) - ids)
    if lost:
        raise ValueError(f"instrument(s) linked from frozen snapshots lost their price series: {lost[:5]} — "
                         "export_stock_charts.py must keep them; restore the files from git")
    return ids


def build_instruments(reports: dict, marks: dict[str, dict], instruments: set[str], now: str,
                      frozen: dict[str, dict] | None = None) -> dict[str, dict]:
    as_of = reports["as_of"]
    frozen = frozen or {}
    by_id: dict[str, list[dict]] = {}
    for r in reports["records"]:
        if r.get("ticker") and r.get("market"):
            by_id.setdefault(instrument_id(r["market"], r["ticker"]), []).append(r)
    orphans = sorted(instruments - set(by_id) - set(frozen))
    if orphans:
        raise ValueError(f"price series without any report: {orphans[:5]} — re-run export_stock_charts.py")
    docs: dict[str, dict] = {}
    items = []
    for iid in sorted(instruments):
        own = _report_order(by_id.get(iid, []))
        counts: dict[str, int] = {}
        for r in own:
            counts[r["school"]] = counts.get(r["school"], 0) + 1
        first, latest = _dates(own)
        if own:
            newest = own[0]
            identity = {"market": newest["market"], "ticker": newest["ticker"],
                        "name": newest["display_name"] or newest["company"] or newest["ticker"],
                        "exchange": next((r["exchange"] for r in own if r["exchange"]), None)}
        else:  # kept only because a frozen snapshot links to it; its reports are gone
            ref = frozen[iid]
            identity = {"market": ref["market"], "ticker": ref["ticker"], "name": ref["name"], "exchange": None}
        item = {
            "id": iid,
            **identity,
            "report_count": len(own),
            "club_report_counts": dict(sorted(counts.items())),
            "first_report_date": first,
            "latest_report_date": latest,
        }
        self_href = href("instruments", iid)
        links = {"prices": href("instruments", iid, "prices")}
        if iid in marks:
            links["trade_marks"] = href("instruments", iid, "trade-marks")
        items.append({**item, "links": {"self": self_href, **links}})
        detail = {**item, "reports": [{**report_item(r), "links": {"club": href("clubs", r["school"])}} for r in own]}
        docs[self_href] = document(self_href, detail, generated_at=now, as_of=as_of,
                                   links={"collection": href("instruments"), **links})
    docs[href("instruments")] = document(href("instruments"), items, generated_at=now, as_of=as_of, count=len(items))
    return docs


def build_trade_marks(data: dict, instruments: set[str], now: str) -> tuple[dict[str, dict], dict[str, dict]]:
    """Headline-strategy buy/sell/stop marks per instrument (was /strategy-marks/{slug}.json)."""
    as_of = data["signals"]["as_of"]
    docs: dict[str, dict] = {}
    kept: dict[str, dict] = {}
    for slug, payload in sorted(v1.build_strategy_marks(data).items()):
        if slug not in instruments:
            print(f"  trade-marks: skipped {slug} (no price series, so no instrument resource)", flush=True)
            continue
        body = {"strategy_id": payload["strategy_key"], "ticker": payload["ticker"], "market": payload["market"],
                "name": payload["name"], "marks": payload["marks"], "open_stop": payload["open_stop"]}
        uri = href("instruments", slug, "trade-marks")
        links = {"instrument": href("instruments", slug)}
        if payload["strategy_key"]:
            links["strategy"] = href("strategies", payload["strategy_key"])
        docs[uri] = document(uri, body, generated_at=now, as_of=as_of, count=len(body["marks"]), links=links)
        kept[slug] = body
    return docs, kept


# ── track record ────────────────────────────────────────────────────────────


def build_track_record(forward: dict, instruments: set[str], now: str) -> dict[str, dict]:
    _require(forward, ("method", "summary", "entries", "headline_by_date"), "forward-record")
    summary = forward["summary"]
    _require(summary, ("n_signals", "n_tracking", "first_snapshot", "last_snapshot", "n_snapshots"),
             "forward-record.summary")
    entries = []
    for e in forward["entries"]:
        _require(e, FORWARD_ENTRY_FIELDS, "forward-record.entries[]")
        entries.append(_instrument_links({k: e[k] for k in FORWARD_ENTRY_FIELDS}, instruments))
    data = {
        "method": forward["method"],
        "summary": {k: summary[k] for k in FORWARD_SUMMARY_FIELDS if k in summary},
        "entries": entries,
        "headline_by_date": dict(sorted(forward["headline_by_date"].items())),
    }
    if isinstance(forward.get("provenance"), dict):
        data["provenance"] = dict(forward["provenance"])
    uri = href("track-record")
    return {uri: document(uri, data, generated_at=now, as_of=summary["last_snapshot"], count=len(entries),
                          links={"signals": href("signals")})}


# ── index ───────────────────────────────────────────────────────────────────


def build_index(as_of: str, now: str) -> dict[str, dict]:
    data = {
        "title": "Verdict Archive API",
        "api_version": API_VERSION,
        "description": ("Read-only REST API over the Verdict Archive research pipeline: club reports scored "
                        "point-in-time, instruments and prices, backtested strategies and their daily signals."),
    }
    return {PREFIX: document(PREFIX, data, generated_at=now, as_of=as_of, links={
        "openapi": href("openapi.json"),
        "signals": href("signals"),
        "latest_signals": href("signals", "latest"),
        "strategies": href("strategies"),
        "track_record": href("track-record"),
        "instruments": href("instruments"),
        "clubs": href("clubs"),
        "portfolio": href("portfolio"),
    })}


# ── OpenAPI 3.1 ─────────────────────────────────────────────────────────────


def _example(value: object) -> object:
    """Real payload trimmed for the spec: every array keeps its first item."""
    if isinstance(value, list):
        return [_example(v) for v in value[:1]]
    if isinstance(value, dict):
        return {k: _example(v) for k, v in value.items()}
    return value


def _nullable(schema: dict) -> dict:
    t = schema["type"]
    return {**schema, "type": [t, "null"] if isinstance(t, str) else [*t, "null"]}


def _obj(properties: dict, required: list[str] | None = None, description: str | None = None) -> dict:
    out: dict = {"type": "object"}
    if description:
        out["description"] = description
    out["required"] = required if required is not None else list(properties)
    out["properties"] = properties
    out["additionalProperties"] = False
    return out


def _ref(name: str) -> dict:
    return {"$ref": f"#/components/schemas/{name}"}


S = {"type": "string"}
N = {"type": "number"}
I = {"type": "integer"}
B = {"type": "boolean"}
DATE = {"type": "string", "format": "date"}
NS, NN, NI, NB, ND = _nullable(S), _nullable(N), _nullable(I), _nullable(B), _nullable(DATE)


def _links(*names: str, optional: tuple[str, ...] = ()) -> dict:
    return _obj({n: _ref("Href") for n in (*names, *optional)}, required=list(names))


def _document_schema(links: dict, data: dict, description: str) -> dict:
    return _obj({"schema_version": {"const": SCHEMA_VERSION}, "links": links, "meta": _ref("Meta"), "data": data},
                description=description)


def _component_schemas() -> dict:
    item_links = _obj({"instrument": _ref("Href")}, required=[],
                      description="Present when the referenced instrument resource exists.")
    trigger = _obj({"school": S, "report_date": DATE, "source_file": S, "target_price": NN,
                    "stated_upside_pct": NN}, required=["school", "report_date"],
                   description="A club report that triggered the entry. `school` is the club id.")
    period = _obj({"start": DATE, "end": DATE, "total_return_pct": NN, "cagr_pct": NN, "sharpe": NN, "mdd_pct": NN})
    report_props = {
        "school": _ref("ClubId"), "report_type": S, "era": {"type": "string", "enum": ["modern", "archive"]},
        "report_date": ND,
        "authored_date": {"type": ["string", "null"],
                          "description": "Date printed on the report, as transcribed (not validated; one archive "
                                         "report reads 2016-44-30). Use report_date for analysis."},
        "market": _ref("Market"), "ticker": S, "exchange": NS,
        "company": NS, "display_name": NS, "rating": NS,
        "rating_class": {"type": "string", "enum": ["buy", "soft_buy", "sell"]},
        "target_price": NN, "report_current_price": NN, "stated_upside_pct": NN,
        "first_trade_date": ND, "start_close": NN, "latest_trade_date": ND, "latest_close": NN,
        "return_latest_pct": NN, "return_30d_pct": NN, "return_90d_pct": NN, "return_180d_pct": NN,
        "return_365d_pct": NN, "benchmark_return_pct": NN, "alpha_latest_pct": NN, "max_high_return_pct": NN,
        "target_hit_until_latest": NB, "first_target_hit_date": ND, "days_to_target": NI,
        "peak_return_24m_pct": NN, "peak_date_24m": ND, "age_days": NI, "maturity": NS,
        "performance_bucket": S, "bucket_peak": S, "data_issue": NS,
        "source_name": S, "source_md_url": {"type": "string", "format": "uri"},
        "source_pdf_url": {"type": ["string", "null"], "format": "uri"},
    }
    instrument_props = {
        "id": _ref("InstrumentId"), "market": _ref("Market"), "ticker": S, "name": S, "exchange": NS,
        "report_count": I,
        "club_report_counts": {"type": "object", "description": "Reports per club id.",
                               "additionalProperties": I},
        "first_report_date": ND, "latest_report_date": ND,
    }
    club_props = {
        "id": _ref("ClubId"), "name": S, "full_name": S, "report_count": I,
        "first_report_date": ND, "latest_report_date": ND,
        "scorecard": {"anyOf": [_ref("Scorecard"), {"type": "null"}]},
    }
    strategy_props = {
        "id": _ref("StrategyId"), "label": S, "is_headline": B,
        "is_curated": {"type": "boolean", "description": "Member of the curated comparison set on /strategy."},
        "verdict": {"type": ["string", "null"], "description": "SOTA · 채택 (adopted) · 연구용 (research) · 기각 (rejected)"},
        "verdict_reason": NS, "trade_count": I,
        "metrics": _obj({**period["properties"], "win_rate_pct": NN, "avg_hold_days": NN},
                        description="Full backtest period."),
        "in_sample": _ref("PeriodMetrics"), "out_of_sample": _ref("PeriodMetrics"),
        "best_trade": _obj({"ticker": NS, "name": NS, "return_pct": NN}),
        "top_decile_pnl_share_pct": NN,
        "benchmarks": _obj({"kospi_dca_ratio": NN, "kospi_dca_beats": NB, "aw_dca_ratio": NN, "aw_dca_beats": NB},
                           description="Terminal wealth vs monthly dollar-cost averaging into KOSPI / All-Weather."),
        "dsr": {"anyOf": [_ref("DeflatedSharpe"), {"type": "null"}]},
        "walkforward": {"anyOf": [_ref("WalkForward"), {"type": "null"}]},
        "walkforward_oos": {"anyOf": [_ref("WalkForward"), {"type": "null"}]},
    }
    walkforward = _obj({"n_windows": I, "positive_pct": NN, "beat_kospi_pct": NN, "median_sharpe": NN,
                        "worst_sharpe": NN, "worst_window_return_pct": NN})
    return {
        # ── primitives ──
        "Href": {"type": "string", "pattern": r"^/api/v2(/\S*)?$",
                 "description": "Root-relative link to another v2 resource; resolve against the API origin."},
        "Market": {"type": "string", "enum": ["KR", "US"]},
        "StrategyId": {"type": "string", "pattern": STRATEGY_ID_RE.pattern, "examples": ["D+_chandelier_optuna"]},
        "InstrumentId": {"type": "string", "pattern": INSTRUMENT_ID_RE.pattern, "examples": ["kr-000020", "us-fix"]},
        "ClubId": {"type": "string", "enum": list(CLUBS)},
        "Meta": _obj({
            "generated_at": {"type": "string", "format": "date-time",
                             "description": "When the pipeline last produced different content for this document."},
            "as_of": {"type": "string", "format": "date", "description": "Market date the data refers to."},
            "count": {"type": "integer", "minimum": 0, "description": "Number of items in `data` (collections)."},
            "disclaimer": S, "disclaimer_ko": S,
        }, required=["generated_at", "disclaimer", "disclaimer_ko"]),
        "ProblemDetails": _obj({
            "type": {"type": "string", "format": "uri-reference"}, "title": S,
            "status": {"type": "integer", "minimum": 400, "maximum": 599}, "detail": S,
            "instance": {"type": "string", "format": "uri-reference"},
        }, required=["type", "title", "status"], description="RFC 9457 problem details."),
        # ── index ──
        "ApiIndex": _obj({"title": S, "api_version": S, "description": S}),
        # ── signals ──
        "TriggerReport": trigger,
        "ItemLinks": item_links,
        "Regime": _obj({"kospi_above_200ma": B, "state": {"type": "string", "enum": ["ON", "OFF"]},
                        "kospi_close": NN, "kospi_ma200": NN,
                        "parking": {"type": "string", "enum": ["allweather", "kospi", "cash"]}, "applies": B}),
        "Slots": _obj({"max_positions": I, "open": I, "available": I}),
        "SignalCounts": _obj({"open_positions": I, "buy_signals": I, "sell_signals": I}),
        "Position": _obj({
            "ticker": S, "market": _ref("Market"), "name": S, "entry_date": DATE, "entry_price": NN,
            "current_price": NN, "stop_level": NN, "unrealized_pct": NN, "days_held": NI, "entry_reason": S,
            "trigger_reports": {"type": "array", "items": _ref("TriggerReport")}, "links": _ref("ItemLinks"),
        }, required=["ticker", "market", "name", "entry_date", "entry_price", "current_price", "stop_level",
                     "unrealized_pct", "days_held", "entry_reason", "trigger_reports"]),
        "BuySignal": _obj({
            "ticker": S, "market": _ref("Market"), "name": S,
            "signal_date": {"type": ["string", "null"], "format": "date",
                            "description": "null only in snapshots published before 2026-09-10"},
            "entry_basis_price": NN, "entry_reason": S,
            "trigger_reports": {"type": "array", "items": _ref("TriggerReport")}, "links": _ref("ItemLinks"),
        }, required=["ticker", "market", "name", "signal_date", "entry_basis_price", "entry_reason",
                     "trigger_reports"]),
        "SellSignal": _obj({
            "ticker": S, "market": _ref("Market"), "name": S,
            "reason": {"type": "string", "enum": ["approaching_stop", "stop_hit"]},
            "stop_level": NN, "dist_to_stop_pct": NN, "links": _ref("ItemLinks"),
        }, required=["ticker", "market", "name", "reason", "stop_level", "dist_to_stop_pct"]),
        "SignalSnapshot": _obj({
            "as_of": DATE, "headline_strategy": _ref("StrategyId"), "regime": _ref("Regime"), "slots": _ref("Slots"),
            "counts": _ref("SignalCounts"),
            "open_positions": {"type": "array", "items": _ref("Position")},
            "buy_signals": {"type": "array", "items": _ref("BuySignal")},
            "sell_signals": {"type": "array", "items": _ref("SellSignal")},
        }),
        "SignalIndexEntry": _obj({"date": DATE, "headline_strategy": _ref("StrategyId"),
                                  "counts": _ref("SignalCounts"), "links": _links("self")}),
        # ── strategies ──
        "PeriodMetrics": period,
        "DeflatedSharpe": _obj({"psr": NN, "dsr": NN, "sr0_annualized": NN, "n_trials": I,
                                "significant_after_deflation": B},
                               description="Probabilistic / deflated Sharpe ratio (Bailey & López de Prado 2014)."),
        "WalkForward": walkforward,
        "Strategy": _obj(strategy_props),
        "StrategyItem": _obj({**strategy_props, "links": _links("self")}),
        "Trade": _obj({
            "ticker": S, "market": _ref("Market"), "name": S, "entry_date": DATE, "exit_date": DATE,
            "entry_price": NN, "exit_price": NN, "return_pct": NN, "days": NI, "exit_reason": S, "entry_reason": S,
            "trigger_reports": {"type": "array", "items": _ref("TriggerReport")}, "links": _ref("ItemLinks"),
        }, required=["ticker", "market", "name", "entry_date", "exit_date", "entry_price", "exit_price",
                     "return_pct", "days", "exit_reason", "entry_reason", "trigger_reports"]),
        "NavPoint": _obj({"date": DATE, "nav": N}),
        "YearlyReturn": _obj({"year": I, "return_pct": N}),
        "WealthPoint": _obj({"month": I, "date": DATE, "contributed": N, "strategy_value": N, "KOSPI_value": N,
                             "SP500_value": N, "NASDAQ_value": N, "AllWeather_value": N},
                            required=["month", "date", "contributed", "strategy_value"]),
        "WealthSim": _obj({"final_strategy_value": N, "strategy_gain_on_contributed_pct": NN, "strategy_mdd_pct": N,
                           "series": {"type": "array", "items": _ref("WealthPoint")}}),
        "BacktestTrade": _obj({
            "ticker": S, "market": _ref("Market"), "display_name": S, "source": S, "n_clubs": I,
            "entry_date": DATE, "exit_date": DATE, "entry": N, "exit": N, "return_pct": N, "days": I,
            "pnl_krw": {"type": "number", "description": "Realized monetary P&L in KRW after accounting costs; absent in older revisions."},
            "exit_reason": S, "entry_reason": S, "trigger_reports": {"type": "array", "items": _ref("TriggerReport")},
            "trigger_schools": {"type": "array", "items": S}, "trigger_target_prices": {"type": "array", "items": N},
        }, required=["ticker", "entry_date", "exit_date", "entry", "exit", "return_pct", "days"]),
        "BacktestOpenPosition": _obj({
            "ticker": S, "market": _ref("Market"), "display_name": S, "entry_date": DATE, "entry": N,
            "last_close": N, "stop": N, "return_pct": N, "source": S, "n_clubs": I, "extension": NN,
            "entry_reason": S,
        }, required=["ticker", "entry_date", "entry", "last_close", "return_pct"]),
        "StrategyRevision": _obj({
            "schema_version": {"const": v1.SCHEMA_VERSION,
                               "description": "Revision payload format; bytes are shared with /api/v1 strategy-details."},
            "strategy_key": _ref("StrategyId"),
            "nav": {"type": "array", "items": _ref("NavPoint")},
            "wealth": _ref("WealthSim"),
            "trades": {"type": "array", "items": _ref("BacktestTrade")},
            "open_positions": {"type": "array", "items": _ref("BacktestOpenPosition")},
            "yearly": {"type": "array", "items": _ref("YearlyReturn")},
            "content_hash": {"type": "string", "pattern": SHA256_RE.pattern,
                             "description": "sha256 of the canonical JSON (sorted keys, no whitespace, UTF-8) "
                                            "of this object without `content_hash`."},
        }, description=("Immutable, content-addressed strategy revision. Not wrapped in the envelope: the bytes are "
                        "the hashed payload and are served unchanged at the deprecated v1 URL too.")),
        # ── track record ──
        "ForwardSummary": _obj({
            "n_signals": I, "n_tracking": I, "n_unavailable": I, "n_pending": I, "coverage_pct": NN,
            "avg_return_pct": NN, "win_rate_pct": NN, "best_pct": NN, "worst_pct": NN,
            "first_snapshot": DATE, "last_snapshot": DATE, "n_snapshots": I,
        }, required=["n_signals", "n_tracking", "first_snapshot", "last_snapshot", "n_snapshots"]),
        "ForwardEntry": _obj({
            "signal_date": DATE, "ticker": S, "market": _ref("Market"), "name": S, "basis_price": NN,
            "entry_date": ND, "entry_price": NN, "current_price": NN, "return_pct": NN, "peak_pct": NN, "days": I,
            "status": {"type": "string", "enum": ["tracking", "pending", "unavailable"]},
            "trigger_schools": {"type": "array", "items": S}, "headline_strategy": S, "links": _ref("ItemLinks"),
        }, required=list(FORWARD_ENTRY_FIELDS)),
        "TrackRecord": _obj({
            "method": S, "summary": _ref("ForwardSummary"),
            "entries": {"type": "array", "items": _ref("ForwardEntry")},
            "headline_by_date": {"type": "object", "additionalProperties": S,
                                 "description": "Snapshot date → headline strategy id that day."},
            "provenance": {"type": "object", "additionalProperties": S},
        }, required=["method", "summary", "entries", "headline_by_date"]),
        # ── instruments ──
        "Instrument": _obj(instrument_props),
        "InstrumentItem": _obj({**instrument_props, "links": _links("self", "prices", optional=("trade_marks",))}),
        "Report": _obj(report_props, description="Documented public subset of a scored club report."),
        "InstrumentReport": _obj({**report_props, "links": _links("club")}),
        "ClubReport": _obj({**report_props, "links": _ref("ItemLinks")}, required=list(report_props)),
        "InstrumentDetail": _obj({**instrument_props,
                                  "reports": {"type": "array", "items": _ref("InstrumentReport")}}),
        "Candle": _obj({"time": DATE, "open": NN, "high": NN, "low": NN, "close": N, "volume": NI},
                       description="Daily candle in local currency. Missing fields are null."),
        "PriceSeries": _obj({
            "candles": {"type": "array", "items": _ref("Candle")},
            "report_dates": {"type": "array", "items": DATE,
                             "description": "Dates on which a club report on this instrument was published."},
        }),
        "TradeMark": _obj({"date": DATE, "side": {"type": "string", "enum": ["buy", "sell", "stop"]}, "price": N,
                           "reason": S}),
        "OpenStop": _obj({"stop_level": N, "entry_date": DATE, "as_of": DATE}),
        "TradeMarks": _obj({"strategy_id": _ref("StrategyId"), "ticker": S, "market": _ref("Market"), "name": S,
                            "marks": {"type": "array", "items": _ref("TradeMark")},
                            "open_stop": {"anyOf": [_ref("OpenStop"), {"type": "null"}]}}),
        # ── clubs ──
        "Scorecard": _obj({k: (NN if k.endswith("_pct") else I) for k in SCORECARD_FIELDS},
                          description="Scored subset only: buy-side reports since 2019-07 (methodology on /clubs)."),
        "Club": _obj(club_props),
        "ClubItem": _obj({**club_props, "links": _links("self", "reports")}),
        # ── documents ──
        "SotaPortfolio": sota_portfolio.json_schema(),
        "PortfolioDocument": _document_schema(_links("self", "strategy"), _ref("SotaPortfolio"), "Selected strategy's actual positions and complete buy/sell fill ledger."),
        "ApiIndexDocument": _document_schema(
            _links("self", "openapi", "signals", "latest_signals", "strategies", "track_record", "instruments",
                   "clubs", optional=("portfolio",)), _ref("ApiIndex"), "API entry point: links to every collection."),
        "SignalIndexDocument": _document_schema(
            _links("self", "latest"), {"type": "array", "items": _ref("SignalIndexEntry")},
            "Every published signal snapshot, newest first."),
        "SignalSnapshotDocument": _document_schema(
            _links("self", "collection", optional=("snapshot",)), _ref("SignalSnapshot"),
            "Headline-strategy signals for one market date. `links.snapshot` (latest only) names the dated copy."),
        "StrategyCollectionDocument": _document_schema(
            _links("self"), {"type": "array", "items": _ref("StrategyItem")}, "All strategies in selector order."),
        "StrategyDocument": _document_schema(
            _links("self", "collection", "trades", "current_revision"), _ref("Strategy"), "One strategy."),
        "StrategyTradesDocument": _document_schema(
            _links("self", "strategy"), {"type": "array", "items": _ref("Trade")}, "Closed-trade log of a strategy."),
        "TrackRecordDocument": _document_schema(
            _links("self", "signals"), _ref("TrackRecord"), "Forward (out-of-sample) record of published buy signals."),
        "InstrumentCollectionDocument": _document_schema(
            _links("self"), {"type": "array", "items": _ref("InstrumentItem")}, "Instruments with a price series."),
        "InstrumentDocument": _document_schema(
            _links("self", "collection", "prices", optional=("trade_marks",)), _ref("InstrumentDetail"),
            "Instrument summary and every club report on it, newest first."),
        "PriceSeriesDocument": _document_schema(
            _links("self", "instrument"), _ref("PriceSeries"), "Daily OHLCV candles of an instrument."),
        "TradeMarksDocument": _document_schema(
            _links("self", "instrument", optional=("strategy",)), _ref("TradeMarks"),
            "Headline-strategy buy/sell/stop marks on an instrument."),
        "ClubCollectionDocument": _document_schema(
            _links("self"), {"type": "array", "items": _ref("ClubItem")}, "All clubs."),
        "ClubDocument": _document_schema(_links("self", "collection", "reports"), _ref("Club"), "One club."),
        "ClubReportsDocument": _document_schema(
            _links("self", "club"), {"type": "array", "items": _ref("ClubReport")},
            "A club's scored reports, newest first."),
    }


DAILY_CACHE = "public, max-age=300, stale-while-revalidate=86400"
SNAPSHOT_CACHE = "public, max-age=3600, s-maxage=31536000"
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
NO_STORE = "no-store"

INFO_DESCRIPTION = f"""\
Read-only REST API over the **Verdict Archive** research pipeline: Korean university investment-club
reports scored point-in-time, the instruments they cover with daily prices, and the backtested strategies
built on them with their daily signals.

## Design

REST is an architectural style, not a protocol. This API applies it literally where it helps and stays
pragmatic where the domain is not CRUD.

1. **URIs identify resources; methods carry the semantics.** Paths are plural nouns without verbs or file
   extensions (`/api/v2/strategies/{{id}}/trades`). Identifiers are stable across versions: strategy id = the
   pipeline key (may contain `+`/`-`, e.g. `D+_chandelier_optuna`), instrument id = `{{market}}-{{ticker}}`
   lower-cased (`kr-005930`, `us-fix`), club id = school code (`smic`).
2. **HTTP semantics over CRUD mapping.** The API is a publication of pipeline output, so it is read-only:
   every resource answers `GET` and `HEAD` (safe, idempotent, cacheable). Every response allows any origin
   (`Access-Control-Allow-Origin: *`), and plain GETs need no CORS preflight. There are no write methods because nothing is written over HTTP — data changes only through the reviewed
   pipeline. Any request can be retried freely; retries cannot duplicate side effects because there are none.
3. **Pragmatism with consistency.** Non-CRUD domain events are their own read-only resources: a signal
   snapshot per market date, a content-addressed strategy revision per backtest result.

## Envelope and navigation

Every document is `{{"schema_version": "{SCHEMA_VERSION}", "links": {{"self": …}}, "meta": {{…}}, "data": …}}`.
Collection items carry `links.self`, payload items carry `links.instrument` when that instrument exists, so
clients navigate by link instead of building URIs. Links are root-relative. The only exception is
`StrategyRevision`, whose bytes are the hashed payload (see that schema).

## Caching and conditional requests

| Mutability | Resources | Cache-Control |
|---|---|---|
| daily | everything not listed below | `{DAILY_CACHE}` |
| immutable | `/api/v2/signals/{{date}}` | `{SNAPSHOT_CACHE}` |
| immutable | `/api/v2/strategies/{{id}}/revisions/{{sha256}}` | `{IMMUTABLE_CACHE}` |

Responses carry an `ETag`; send it back as `If-None-Match` to get `304 Not Modified` without a body.
Errors are never cached: `404`, `405` and the `OPTIONS` preflight send `Cache-Control: {NO_STORE}`, so a date,
revision or instrument published later is visible on the first request after its deploy.

## Status codes and retries

`200` found · `304` not modified · `404` unknown resource (`application/problem+json`, RFC 9457) ·
`405` unsupported method on any v2 URI (`application/problem+json`, `Allow: GET, HEAD, OPTIONS`).
A guard in front of the static files answers OPTIONS, writes and unknown URIs (checked against the list of
published resources) before file resolution. On network errors or `5xx`, retry with exponential backoff
(e.g. 1 s, 2 s, 4 s; honour `Retry-After` if present). Data refreshes once per trading day (KST evening), so
polling more often than `max-age` only returns `304`.

## Versioning

`/api/v1` is frozen and deprecated (responses carry `Deprecation` and `Link: </api/v2>; rel="successor-version"`).
Additive fields keep `schema_version` 2.x; clients must ignore unknown fields. Breaking changes ship as `/api/v3`.

## Disclaimer

{DISCLAIMER_EN}

{DISCLAIMER_KO}
"""


def build_openapi(examples: dict[str, object]) -> dict:
    def ok(schema: str, example_key: str, description: str, cache: str, extra_headers: dict | None = None) -> dict:
        return {
            "description": description,
            "headers": {"Cache-Control": {"$ref": f"#/components/headers/{cache}"},
                        "ETag": {"$ref": "#/components/headers/ETag"},
                        "Access-Control-Allow-Origin": {"$ref": "#/components/headers/AccessControlAllowOrigin"},
                        **(extra_headers or {})},
            "content": {"application/json": {"schema": _ref(schema),
                                             **({"examples": {"current": {"externalValue": SITE + href("portfolio")}}} if example_key == "portfolio" else
                                                {"example": examples[example_key]} if example_key in examples
                                                else {})}},
        }

    def op(op_id: str, tag: str, summary: str, description: str, response: dict, params: list[str] = (),
           not_found: bool = True) -> dict:
        responses = {"200": response, "304": {"$ref": "#/components/responses/NotModified"}}
        if not_found:
            responses["404"] = {"$ref": "#/components/responses/NotFound"}
        operation: dict = {"tags": [tag], "operationId": op_id, "summary": summary,
                           "description": description + " Safe and idempotent; `HEAD` returns the same headers."}
        if params:
            operation["parameters"] = [{"$ref": f"#/components/parameters/{p}"} for p in params]
        operation["responses"] = responses
        return {"get": operation}

    paths = {
        href("portfolio"): op("getPortfolio", "Strategies", "Current SOTA portfolio and execution ledger",
            "Contains actual fractional quantities, entry dates, both buy and sell fills, fees and FX. The data schema is 2.0. This is a backtest model, not a funded account.",
            ok("PortfolioDocument", "portfolio", "SOTA ledger", "CacheControlDaily")),
        PREFIX: op("getApiIndex", "Meta", "API entry point",
                   "Links to every top-level collection and to this specification.",
                   ok("ApiIndexDocument", "index", "Index document", "CacheControlDaily"), not_found=False),
        href("openapi.json"): {"get": {
            "tags": ["Meta"], "operationId": "getOpenApiDocument", "summary": "This OpenAPI 3.1 document",
            "description": "Machine-readable contract of every v2 resource. Keeps its `.json` name by convention.",
            "responses": {
                "200": {"description": "OpenAPI document",
                        "headers": {"Cache-Control": {"$ref": "#/components/headers/CacheControlDaily"},
                                    "ETag": {"$ref": "#/components/headers/ETag"}},
                        "content": {"application/json": {"schema": {"type": "object"}}}},
                "304": {"$ref": "#/components/responses/NotModified"},
            }}},
        href("signals"): op("listSignalSnapshots", "Signals", "Signal snapshot index",
                            "Every published daily snapshot of the headline strategy's signals, newest first.",
                            ok("SignalIndexDocument", "signals", "Snapshot index", "CacheControlDaily"),
                            not_found=False),
        href("signals", "latest"): op(
            "getLatestSignalSnapshot", "Signals", "Latest signal snapshot",
            "Open positions, buy signals and sell alerts of the headline strategy as of the latest market date. "
            "`links.snapshot` points at the immutable dated copy.",
            ok("SignalSnapshotDocument", "latest", "Latest snapshot", "CacheControlDaily"), not_found=False),
        href("signals", "{date}"): op(
            "getSignalSnapshot", "Signals", "Signal snapshot for a market date",
            "Immutable: frozen at first publication (append-only). Dates without a published snapshot "
            "(weekends, holidays, the future) are 404.",
            ok("SignalSnapshotDocument", "snapshot", "Dated snapshot", "CacheControlSnapshot"), ["SnapshotDate"]),
        href("strategies"): op("listStrategies", "Strategies", "All strategies",
                               "Every backtested strategy with full-period, in-sample and out-of-sample metrics.",
                               ok("StrategyCollectionDocument", "strategies", "Strategy collection",
                                  "CacheControlDaily"), not_found=False),
        href("strategies", "{strategy_id}"): op(
            "getStrategy", "Strategies", "One strategy",
            "Metrics of one strategy plus `links.trades` and `links.current_revision` (curves, positions).",
            ok("StrategyDocument", "strategy", "Strategy", "CacheControlDaily"), ["StrategyId"]),
        href("strategies", "{strategy_id}", "trades"): op(
            "listStrategyTrades", "Strategies", "Closed-trade log",
            "Every closed trade of the strategy in the backtest, oldest first.",
            ok("StrategyTradesDocument", "trades", "Trade log", "CacheControlDaily"), ["StrategyId"]),
        href("strategies", "{strategy_id}", "revisions", "{sha256}"): op(
            "getStrategyRevision", "Strategies", "Content-addressed strategy revision",
            "NAV curve, wealth simulation, raw trades, open positions and yearly returns, addressed by the sha256 "
            "of their canonical JSON: new bytes, new URI. Verify integrity by recomputing the hash. The same bytes "
            "answer the deprecated `/api/v1/strategy-details/{strategy_id}/{sha256}.json`.",
            ok("StrategyRevision", "revision", "Strategy revision (example arrays truncated)", "CacheControlImmutable",
               {"Link": {"$ref": "#/components/headers/LinkUp"}}),
            ["StrategyId", "RevisionHash"]),
        href("track-record"): op(
            "getTrackRecord", "Track record", "Forward record of published buy signals",
            "Each buy signal from the append-only snapshots scored with prices after its publication "
            "(entry = next session's open).",
            ok("TrackRecordDocument", "track_record", "Track record", "CacheControlDaily"), not_found=False),
        href("instruments"): op("listInstruments", "Instruments", "Instruments with a price series",
                                "Every instrument covered by a club report that has a published price series.",
                                ok("InstrumentCollectionDocument", "instruments", "Instrument collection",
                                   "CacheControlDaily"), not_found=False),
        href("instruments", "{instrument_id}"): op(
            "getInstrument", "Instruments", "One instrument and its club reports",
            "Identity, report counts per club, and every club report on the instrument, newest first.",
            ok("InstrumentDocument", "instrument", "Instrument", "CacheControlDaily"), ["InstrumentId"]),
        href("instruments", "{instrument_id}", "prices"): op(
            "getInstrumentPrices", "Instruments", "Daily OHLCV candles",
            "Full daily history in local currency (KRW for KR, USD for US) plus the report publication dates. "
            "Replaces `/prices/{slug}.json`, which now answers 308 here.",
            ok("PriceSeriesDocument", "prices", "Price series (example truncated)", "CacheControlDaily"),
            ["InstrumentId"]),
        href("instruments", "{instrument_id}", "trade-marks"): op(
            "getInstrumentTradeMarks", "Instruments", "Headline-strategy trade marks",
            "Backtest buy/sell/stop points of the headline strategy on this instrument and the live trailing stop "
            "if held. Only instruments the headline strategy traded have this resource. Replaces "
            "`/strategy-marks/{slug}.json`, which now answers 308 here.",
            ok("TradeMarksDocument", "trade_marks", "Trade marks", "CacheControlDaily"), ["InstrumentId"]),
        href("clubs"): op("listClubs", "Clubs", "All clubs", "Every club with report counts and its scorecard.",
                          ok("ClubCollectionDocument", "clubs", "Club collection", "CacheControlDaily"),
                          not_found=False),
        href("clubs", "{club_id}"): op("getClub", "Clubs", "One club", "Club summary, scorecard and links.",
                                       ok("ClubDocument", "club", "Club", "CacheControlDaily"), ["ClubId"]),
        href("clubs", "{club_id}", "reports"): op(
            "listClubReports", "Clubs", "A club's reports",
            "Every report of the club with point-in-time performance, newest first (undated last).",
            ok("ClubReportsDocument", "club_reports", "Club reports (example truncated)", "CacheControlDaily"),
            ["ClubId"]),
    }

    def header(description: str, example: str) -> dict:
        return {"description": description, "schema": {"type": "string"}, "example": example}

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Verdict Archive API",
            "version": API_VERSION,
            "summary": "Read-only REST API: club reports, instruments, prices, strategies and signals.",
            "description": INFO_DESCRIPTION,
            "contact": {"name": "Verdict Archive", "url": SITE},
            "license": {"name": "MIT", "identifier": "MIT"},
        },
        "servers": [{"url": SITE, "description": "Production (Vercel static hosting)"},
                    {"url": "/", "description": "This deployment (same origin as the loaded specification)"}],
        "tags": [
            {"name": "Signals", "description": "Daily snapshots of the headline strategy's positions and signals."},
            {"name": "Strategies", "description": "Backtested strategies, their trades and content-addressed revisions."},
            {"name": "Track record", "description": "Out-of-sample scoring of signals after publication."},
            {"name": "Instruments", "description": "Covered stocks: reports, daily prices and strategy trade marks."},
            {"name": "Clubs", "description": "Investment clubs and their scored reports."},
            {"name": "Meta", "description": "Entry point and this specification."},
        ],
        "paths": paths,
        "components": {
            "schemas": _component_schemas(),
            "parameters": {
                "SnapshotDate": {"name": "date", "in": "path", "required": True,
                                 "description": "Market date `YYYY-MM-DD` (KST trading day).",
                                 "schema": {"type": "string", "format": "date", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
                                 "examples": {"first": {"value": "2026-06-11", "summary": "First snapshot"}}},
                "StrategyId": {"name": "strategy_id", "in": "path", "required": True,
                               "description": "Strategy key. `+` is a literal path character (no encoding needed; "
                                              "`%2B` also works).",
                               "schema": _ref("StrategyId"),
                               # Swagger pre-fills the first example of each parameter: the headline strategy
                               # owns the RevisionHash example, so it comes first. All ids are published ones.
                               "examples": {name: {"value": sid} for name, sid in examples["strategy_ids"].items()}},
                "RevisionHash": {"name": "sha256", "in": "path", "required": True,
                                 "description": "Content hash from `links.current_revision` of the strategy.",
                                 "schema": {"type": "string", "pattern": SHA256_RE.pattern},
                                 "examples": {"current": {"value": examples["revision_hash"],
                                                          "summary": "Current revision of the headline strategy"}}},
                "InstrumentId": {"name": "instrument_id", "in": "path", "required": True,
                                 "description": "`{market}-{ticker}` lower-cased.",
                                 "schema": _ref("InstrumentId"),
                                 "examples": {name: {"value": iid}
                                              for name, iid in examples["instrument_ids"].items()}},
                "ClubId": {"name": "club_id", "in": "path", "required": True, "description": "Club (school) code.",
                           "schema": _ref("ClubId"), "examples": {"smic": {"value": "smic"}}},
            },
            "headers": {
                "CacheControlDaily": header("Mutable resource, refreshed once per trading day.", DAILY_CACHE),
                "CacheControlSnapshot": header(
                    "Immutable dated snapshot (browser revalidates hourly; CDN keeps it until the next deploy).",
                    SNAPSHOT_CACHE),
                "CacheControlImmutable": header("Content-addressed: new bytes, new URI.", IMMUTABLE_CACHE),
                "ETag": header("Validator of the response bytes; send as `If-None-Match`.", '"5f2b0c-19a8e3"'),
                "AccessControlAllowOrigin": header("Any origin may read the API.", "*"),
                "CacheControlNoStore": header(
                    "Errors are not cached: the resource may be published by a later deploy.", NO_STORE),
                "LinkUp": header("The strategy this revision belongs to.",
                                 '</api/v2/strategies/D+_chandelier_optuna>; rel="up"'),
            },
            "responses": {
                "NotModified": {"description": "The `If-None-Match` ETag still matches; no body.",
                                "headers": {"ETag": {"$ref": "#/components/headers/ETag"},
                                            "Cache-Control": {"$ref": "#/components/headers/CacheControlDaily"}}},
                "NotFound": {"description": "No such resource (yet); never cached.",
                             "headers": {"Cache-Control": {"$ref": "#/components/headers/CacheControlNoStore"},
                                         "Access-Control-Allow-Origin": {
                                             "$ref": "#/components/headers/AccessControlAllowOrigin"}},
                             "content": {"application/problem+json": {
                                 "schema": _ref("ProblemDetails"),
                                 "example": {"type": "about:blank", "title": "Not Found", "status": 404,
                                             "detail": "No /api/v2 resource at this URI. "
                                                       "Start from /api/v2 and follow links.",
                                             "instance": "/api/v2/strategies/UNKNOWN"}}}},
            },
        },
    }


# ── plan / check / write ────────────────────────────────────────────────────


def plan_outputs(now: str | None = None) -> tuple[dict[Path, dict], set[Path]]:
    """Every file this export owns → payload, plus the set of append-only paths."""
    now = now or _now()
    data = v1._load_source(STRATEGY_SOURCE)
    reports = _load_json(REPORTS_SOURCE)
    forward = _load_json(FORWARD_SOURCE)
    instruments = published_instruments()
    if not instruments:
        raise ValueError("no price series under public/api/v2/instruments — run export_stock_charts.py first")

    latest_v1 = v1.build_latest_signals(data)
    as_of = latest_v1["as_of"]
    details = v1.build_strategy_details(data)

    docs: dict[str, dict] = {}
    signal_docs, append_only_uris = build_signals(latest_v1, v1_dated_snapshots(), instruments, now)
    docs.update(signal_docs)
    docs.update(build_strategies(data, details, instruments, now))
    docs.update(build_track_record(forward, instruments, now))
    mark_docs, marks = build_trade_marks(data, instruments, now)
    docs.update(mark_docs)
    docs.update(build_instruments(reports, marks, instruments, now, frozen_instrument_refs()))
    docs.update(build_clubs(reports, instruments, now))
    docs.update(build_index(as_of, now))
    book = _load_json(lake.GOLD_SOTA_PORTFOLIO)
    sota_portfolio.validate(book, data)
    docs[href("portfolio")] = document(href("portfolio"), book, generated_at=now, as_of=book["as_of"],
        links={"strategy": href("strategies", book["strategy"])})

    outputs: dict[Path, dict] = {file_for(uri): doc for uri, doc in docs.items()}
    append_only = {file_for(uri) for uri in append_only_uris}
    for sid, (digest, payload) in details.items():
        path = API / "strategies" / sid / "revisions" / f"{digest}.json"
        outputs[path] = payload
        append_only.add(path)

    headline = data["multi_strategy"]["headline_key"]
    # The headline strategy may have traded nothing yet: then there is no trade-marks example.
    sample_instrument = next((i for i in sorted(marks) if i in instruments), sorted(instruments)[0])
    sample_club = next(iter(CLUBS))
    prices_doc = json.loads((API / "instruments" / sample_instrument / "prices.json").read_text(encoding="utf-8"))
    examples = {
        "index": docs[PREFIX],
        "signals": docs[href("signals")],
        "latest": docs[href("signals", "latest")],
        "snapshot": docs[href("signals", as_of)],
        "strategies": docs[href("strategies")],
        "strategy": docs[href("strategies", headline)],
        "trades": docs[href("strategies", headline, "trades")],
        "revision": details[headline][1],
        "revision_hash": details[headline][0],
        "strategy_ids": _example_ids(("headline", headline), sorted(details), ("+", "plus"), ("-", "minus")),
        "instrument_ids": _example_ids(("traded", sample_instrument), sorted(instruments), ("us-", "us")),
        "track_record": docs[href("track-record")],
        "instruments": docs[href("instruments")],
        "instrument": docs[href("instruments", sample_instrument)],
        "prices": prices_doc,
        "trade_marks": docs.get(href("instruments", sample_instrument, "trade-marks")),
        "clubs": docs[href("clubs")],
        "club": docs[href("clubs", sample_club)],
        "club_reports": docs[href("clubs", sample_club, "reports")],
    }
    raw = {"revision_hash", "strategy_ids", "instrument_ids"}
    examples = {k: (v if k in raw else _example(v)) for k, v in examples.items() if v is not None}
    outputs[API / "openapi.json"] = build_openapi(examples)
    outputs[RESOURCE_INDEX] = resource_index(outputs)
    return outputs, append_only


def _example_ids(first: tuple[str, str], ids: list[str], *wanted: tuple[str, str]) -> dict[str, str]:
    """Parameter examples drawn from published ids: `first` (which the other examples depend on), then the first
    id containing each marker, so no example goes stale when an instrument or strategy is retired."""
    out = dict([first])
    for marker, name in wanted:
        match = next((i for i in ids if marker in i and i not in out.values()), None)
        if match:
            out[name] = match
    return out


def resource_index(outputs: dict[Path, dict]) -> dict:
    """Every URI under /api/v2 that answers 200: this export's files plus the foreign/append-only ones on disk.

    Under `next start`, next.config.ts header rules are applied before any route runs and a route handler
    cannot override them, so a fallback 404 would inherit the long cache of the URI it was asked for. The method
    guard (src/proxy.ts) answers URIs missing from this list itself, with an explicit no-store 404.
    """
    files = {p for p in outputs if p.is_relative_to(API)}
    if API.exists():
        files |= {p for p in API.rglob("*.json") if not p.name.startswith(".") and _is_foreign_or_frozen(p)}
    keys = sorted(p.relative_to(API).as_posix().removesuffix(".json") for p in files)
    return {"schema_version": "1.0", "prefix": PREFIX, "resources": keys}


def _is_foreign_or_frozen(path: Path) -> bool:
    """Files under API this export never prunes: price series (export_stock_charts.py) and append-only assets."""
    rel = path.relative_to(API).parts
    if rel[-1] == "prices.json" and len(rel) == 3 and rel[0] == "instruments":
        return True
    if len(rel) == 2 and rel[0] == "signals" and DATE_RE.match(Path(rel[1]).stem):
        return True
    return len(rel) == 4 and rel[0] == "strategies" and rel[2] == "revisions" and SHA256_RE.match(Path(rel[3]).stem)


def _orphans(outputs: dict[Path, dict]) -> list[Path]:
    if not API.exists():
        return []
    return sorted(p for p in API.rglob("*.json")
                  if not p.name.startswith(".") and p not in outputs and not _is_foreign_or_frozen(p))


def check_outputs(outputs: dict[Path, dict], append_only: set[Path]) -> list[str]:
    """Committed v2 artifacts must equal a fresh export (generated_at ignored; append-only only need to exist)."""
    problems: list[str] = []
    for path, expected in outputs.items():
        rel = path.relative_to(ROOT).as_posix()
        if not path.exists():
            problems.append(f"missing {rel}")
            continue
        if path in append_only:
            continue
        actual = json.loads(path.read_text(encoding="utf-8"))
        if v1._comparable(actual) != v1._comparable(expected):
            problems.append(f"stale {rel}")
    problems += [f"orphan {p.relative_to(ROOT).as_posix()}" for p in _orphans(outputs)]
    return problems


def write_outputs(outputs: dict[Path, dict], append_only: set[Path]) -> tuple[int, int, int]:
    """Write-then-prune. Unchanged documents keep their bytes (and generated_at), so a no-op re-run is a no-op."""
    written = kept = 0
    for path, payload in outputs.items():
        if path.exists():
            if path in append_only:
                kept += 1
                continue
            try:
                same = v1._comparable(json.loads(path.read_text(encoding="utf-8"))) == v1._comparable(payload)
            except ValueError:
                same = False
            if same:
                kept += 1
                continue
        v1._write_json(path, payload)
        written += 1
    orphans = _orphans(outputs)
    for old in orphans:
        old.unlink()
        print(f"  pruned {old.relative_to(ROOT).as_posix()}", flush=True)
    for d in sorted((p for p in API.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()
    return written, kept, len(orphans)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="write nothing; exit 1 if committed artifacts differ from a fresh export (CI gate)")
    args = parser.parse_args(argv)

    outputs, append_only = plan_outputs()
    if args.check:
        problems = check_outputs(outputs, append_only)
        for problem in problems:
            print(f"  DRIFT {problem}", file=sys.stderr)
        if problems:
            print(f"\n{len(problems)} artifact(s) out of sync — run scripts/export_api_v2.py and commit.",
                  file=sys.stderr)
            return 1
        print(f"  OK  {len(outputs)} v2 artifacts match the sources", flush=True)
        return 0

    written, kept, pruned = write_outputs(outputs, append_only)
    print(f"\nDone. public/api/v2: {written} written, {kept} unchanged, {pruned} pruned", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
