"""Gold search projection: no report-performance payload in the command palette bundle."""
import argparse
import json

import lake
from lake_prices import replace_atomic


def build() -> dict:
    doc = json.loads((lake.WEB_DATA / "report-performance.json").read_text(encoding="utf-8"))
    stocks = {}; reports = []
    for r in sorted(doc["records"], key=lambda r: r.get("report_date") or "", reverse=True):
        name = r.get("display_name") or r.get("company") or r.get("ticker") or r["source_name"]
        ticker = r.get("ticker"); market = r.get("market")
        if ticker and market:
            slug = f"{market}-{ticker}".lower()
            if slug not in stocks:
                stocks[slug] = {"id": f"s:{slug}", "kind": "stock", "title": name, "sub": f"{market} · {ticker}",
                                "href": f"/stocks/{slug}", "hay": f"{name} {ticker} {slug}".lower(), "ret": None, "count": 0}
            stocks[slug]["count"] += 1
        if r.get("era") == "modern" and r.get("report_date"):
            from urllib.parse import quote
            reports.append({"id": f"r:{r['source_name']}", "kind": "report", "title": name,
                            "sub": f"{r['school']} · {r['report_date']} · {ticker or ''}",
                            "href": f"/#r={quote(r['source_name'], safe='')}",
                            "hay": f"{name} {r.get('company') or ''} {ticker or ''} {r['source_name']}".lower(),
                            "ret": r.get("return_latest_pct")})
    entries = sorted(stocks.values(), key=lambda r: -r["count"])
    for r in entries:
        r["sub"] += f" · 리포트 {r.pop('count')}건"
    return {"schema_version": "1.0", "as_of": doc["as_of"], "entries": entries + reports}


def main() -> int:
    p = argparse.ArgumentParser(); p.add_argument("--check", action="store_true"); args = p.parse_args()
    text = json.dumps(build(), ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
    if args.check:
        return 0 if lake.SEARCH_INDEX.exists() and lake.SEARCH_INDEX.read_text(encoding="utf-8") == text else 1
    replace_atomic(lake.SEARCH_INDEX, lambda fh: fh.write(text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
